"""Deterministic series aggregation, stability, saturation, and the SLO × baseline verdict matrix.

The verdict keeps three references separate:
  1. approved SLO/NFR  -> slo_status
  2. approved baseline -> regression (delta %, significance)
  3. recent trend      -> trend (informational, never changes the baseline)
"""

from __future__ import annotations

import operator
import statistics
from dataclasses import asdict, dataclass, field
from typing import Any

from perfeng.analysis import stats
from perfeng.domain.models import Verdict

LATENCY_KEYS = ("p50", "p90", "p95", "p99")
_OPS = {"<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge}


# ---------------------------------------------------------------- aggregation


def series_samples(run_metrics: list[dict[str, Any]], target: str) -> dict[str, list[float]]:
    """Per-run values for one target across a series: {'p95': [r1, r2..], 'error_rate': [...], ...}."""
    out: dict[str, list[float]] = {}
    for m in run_metrics:
        t = (m.get("per_target") or {}).get(target)
        if t is None and target == "__overall__":
            t = m.get("overall")
        if not t:
            continue
        for k in (*LATENCY_KEYS, "mean", "max", "error_rate", "rps", "count"):
            v = t.get(k)
            if v is not None:
                out.setdefault(k, []).append(float(v))
        for k, v in (t.get("extra") or {}).items():
            if v is not None:
                out.setdefault(k, []).append(float(v))
    return out


def aggregate(samples: dict[str, list[float]]) -> dict[str, float | None]:
    return {k: (statistics.median(v) if v else None) for k, v in samples.items()}


# ---------------------------------------------------------------- stability / saturation


@dataclass
class StabilityResult:
    stable: bool
    runs: int
    cv_p95: float | None
    error_rate_spread: float | None
    min_samples: float | None
    saturated_runs: int
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def generator_saturated(run_metrics: dict[str, Any], policy: dict) -> list[str]:
    reasons = []
    if (run_metrics.get("dropped_iterations") or 0) > 0:
        reasons.append(f"dropped_iterations={run_metrics['dropped_iterations']}")
    gen = run_metrics.get("generator") or {}
    cpu = gen.get("cpu_pct_p95")
    if cpu is not None and cpu >= policy["safety"]["generator_cpu_saturation_pct"]:
        reasons.append(f"load generator CPU p95 {cpu:.0f}%")
    if gen.get("max_vus_reached"):
        reasons.append("max VUs reached (arrival rate not sustainable)")
    return reasons


def evaluate_stability(run_metrics: list[dict[str, Any]], target: str, policy: dict, required_runs: int) -> StabilityResult:
    s = series_samples(run_metrics, target)
    reasons: list[str] = []
    n = len(s.get("p95", []))
    sat = sum(1 for m in run_metrics if generator_saturated(m, policy))
    aborted = sum(1 for m in run_metrics if m.get("aborted_reason"))
    cvp = stats.cv(s.get("p95", []))
    ers = s.get("error_rate", [])
    spread = (max(ers) - min(ers)) if ers else None
    min_samples = min(s.get("count", [0])) if s.get("count") else None
    pol = policy["stability"]
    if n < required_runs:
        reasons.append(f"only {n} runs, need {required_runs}")
    if cvp is None:
        reasons.append("cannot compute variance (need >= 2 runs)")
    elif cvp > pol["max_cv_p95"]:
        reasons.append(f"p95 CV {cvp:.3f} > {pol['max_cv_p95']}")
    if spread is not None and spread > pol["max_error_rate_spread"]:
        reasons.append(f"error-rate spread {spread:.3f} > {pol['max_error_rate_spread']}")
    if min_samples is not None and min_samples < pol["min_samples_per_run"]:
        reasons.append(f"min samples/run {min_samples:.0f} < {pol['min_samples_per_run']}")
    if sat:
        reasons.append(f"{sat} run(s) show load-generator saturation")
    if aborted:
        reasons.append(f"{aborted} run(s) aborted by safety stop")
    return StabilityResult(not reasons, n, cvp, spread, min_samples, sat, reasons)


# ---------------------------------------------------------------- verdict


@dataclass
class SloCheck:
    nfr_id: str
    metric: str
    comparator: str
    threshold: float
    observed: float | None
    passed: bool | None


@dataclass
class RegressionCheck:
    metric: str
    baseline: float | None
    current: float | None
    delta_pct: float | None
    p_value: float | None
    ci_pct: tuple[float, float] | None
    level: str  # none|warn|fail|unknown
    significant: bool | None
    note: str | None = None


@dataclass
class TargetVerdict:
    target: str
    verdict: Verdict
    reasons: list[str]
    current: dict[str, float | None]
    slo: list[SloCheck]
    regression: list[RegressionCheck]
    stability: dict[str, Any] | None
    baseline_id: str | None
    trend: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["verdict"] = self.verdict.value
        return d


def _nfr_metric_value(current: dict[str, float | None], metric: str) -> float | None:
    # metric like "http_req_duration.p95" | "p95" | "error_rate" | "lcp.p75"
    key = metric.split(".")[-1] if metric.startswith("http_req_duration") else metric
    if key in current:
        return current[key]
    return current.get(metric)


def check_slos(current: dict[str, float | None], nfrs: list[dict[str, Any]]) -> list[SloCheck]:
    out = []
    for n in nfrs:
        obs = _nfr_metric_value(current, n["metric"])
        passed = None if obs is None else bool(_OPS[n["comparator"]](obs, n["threshold"]))
        out.append(SloCheck(n["id"], n["metric"], n["comparator"], n["threshold"], obs, passed))
    return out


def check_regression(
    cur_samples: dict[str, list[float]], base_samples: dict[str, list[float]], policy: dict
) -> list[RegressionCheck]:
    reg = policy["regression"]
    out: list[RegressionCheck] = []
    for metric in ("p95", "p99", "p50"):
        c, b = cur_samples.get(metric, []), base_samples.get(metric, [])
        if not c or not b:
            continue
        cm, bm = statistics.median(c), statistics.median(b)
        d = stats.pct_delta(cm, bm)
        p = stats.mann_whitney_greater(c, b)
        ci = stats.bootstrap_ratio_ci(c, b)
        sig = None if p is None else p < reg["significance_alpha"]
        if sig is False and stats.min_achievable_p(len(c), len(b)) >= reg["significance_alpha"]:
            # rank test cannot reach alpha with this few repeats -> fall back to the bootstrap CI
            sig = None if ci is None else ci[0] > 0
        note = None
        if d is None:
            level = "unknown"
        elif d >= reg["fail_pct"] and sig is not False:
            level = "fail"
        elif d >= reg["warn_pct"] and sig is not False:
            level = "warn"
        elif d >= reg["warn_pct"]:
            level = "none"
            note = f"delta {d:+.1f}% not statistically significant (p={p:.3f})"
        else:
            level = "none"
        if sig is None and d is not None and d >= reg["warn_pct"]:
            note = "too few repeats for significance test; delta used alone (low statistical power)"
        out.append(RegressionCheck(metric, bm, cm, d, p, ci, level, sig, note))
    ce, be = cur_samples.get("error_rate", []), base_samples.get("error_rate", [])
    if ce and be:
        cm, bm = statistics.median(ce), statistics.median(be)
        diff = cm - bm
        level = "fail" if diff >= reg["error_rate_fail_abs"] else "warn" if diff >= reg["error_rate_warn_abs"] else "none"
        out.append(RegressionCheck("error_rate", bm, cm, diff * 100, None, None, level, None, "absolute percentage-point change"))
    return out


def decide(
    target: str,
    cur_samples: dict[str, list[float]],
    base_samples: dict[str, list[float]] | None,
    nfrs: list[dict[str, Any]],
    stability: StabilityResult | None,
    policy: dict,
    baseline_id: str | None = None,
    trend: dict[str, Any] | None = None,
) -> TargetVerdict:
    current = aggregate(cur_samples)
    reasons: list[str] = []
    slo = check_slos(current, nfrs)
    regression = check_regression(cur_samples, base_samples, policy) if base_samples else []

    inconclusive = []
    if stability is not None and not stability.stable:
        # instability only blocks a verdict when it is about measurement validity
        blocking = [r for r in stability.reasons if "saturation" in r or "aborted" in r or "samples" in r]
        inconclusive += blocking
    if not cur_samples.get("p95"):
        inconclusive.append("no latency samples")

    slo_failed = [s for s in slo if s.passed is False]
    fail_reg = [r for r in regression if r.level == "fail"]
    warn_reg = [r for r in regression if r.level == "warn"]

    if inconclusive and not slo_failed:
        verdict = Verdict.INCONCLUSIVE
        reasons += inconclusive
    elif slo_failed:
        verdict = Verdict.FAIL_SLO
        reasons += [f"SLO {s.nfr_id}: {s.metric}={s.observed:.1f} !{s.comparator} {s.threshold}" for s in slo_failed]
    elif fail_reg:
        verdict = Verdict.FAIL_REGRESSION
        reasons += [f"{r.metric} {r.delta_pct:+.1f}% vs baseline (fail >= {policy['regression']['fail_pct']}%)" for r in fail_reg]
    elif warn_reg:
        verdict = Verdict.PASS_WITH_REGRESSION_WARNING
        reasons += [f"{r.metric} {r.delta_pct:+.1f}% vs baseline (warn >= {policy['regression']['warn_pct']}%)" for r in warn_reg]
    elif not slo and not regression:
        verdict = Verdict.INCONCLUSIVE
        reasons.append("no approved SLO and no approved baseline to compare against (characterization only)")
    else:
        verdict = Verdict.PASS
        if slo:
            reasons.append("all SLOs met")
        if regression:
            reasons.append("no material regression vs baseline")
        if not slo:
            reasons.append("no approved SLO/NFR exists for this target")
    return TargetVerdict(
        target, verdict, reasons, current, slo, regression,
        stability.to_dict() if stability else None, baseline_id, trend,
    )


_SEVERITY = [Verdict.PASS, Verdict.PASS_WITH_REGRESSION_WARNING, Verdict.INCONCLUSIVE, Verdict.FAIL_REGRESSION, Verdict.FAIL_SLO]


def overall(verdicts: list[TargetVerdict]) -> Verdict:
    if not verdicts:
        return Verdict.INCONCLUSIVE
    return max((v.verdict for v in verdicts), key=_SEVERITY.index)
