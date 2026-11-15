"""Deterministic frontend journey analysis (Core Web Vitals + lab metrics).

SLOs come only from NFRs (e.g. metric 'lcp' scope 'Search'). Policy budgets are reported
separately as lab budgets — they are not NFRs. FID is never used.
"""

from __future__ import annotations

import operator
import statistics
from typing import Any

from perfeng.analysis import stats
from perfeng.domain.models import Verdict

FE_METRICS = ["lcp_ms", "cls", "inp_ms", "tbt_ms", "ttfb_ms", "fcp_ms", "speed_index_ms", "journey_ms",
              "long_tasks", "long_task_ms", "js_exec_ms", "resource_count", "transfer_kb", "failed_requests",
              "console_errors", "lh_performance_score"]
LOWER_IS_BETTER = {m for m in FE_METRICS if m != "lh_performance_score"}
_OPS = {"<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge}
# Minimum absolute change before a frontend delta can count as a regression (lab noise floor).
ABS_FLOOR = {"lcp_ms": 100, "tbt_ms": 50, "journey_ms": 150, "inp_ms": 50, "ttfb_ms": 30, "fcp_ms": 100, "cls": 0.02}
_BUDGET_KEYS = {"lcp_ms": "lcp_ms", "cls": "cls", "tbt_ms": "tbt_ms", "inp_ms": "inp_ms"}


def journey_samples(runs_metrics: list[dict[str, Any]], journey: str) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    for m in runs_metrics:
        j = (m.get("frontend") or {}).get(journey) or {}
        for k in FE_METRICS:
            v = j.get(k)
            if v is not None:
                out.setdefault(k, []).append(float(v))
    return out


def frontend_verdict(engine, plan, series, runs, nfrs, policy) -> dict[str, Any]:
    journey = series.target
    cur = journey_samples([r.metrics or {} for r in runs if r.status == "completed"], journey)
    current = {k: statistics.median(v) for k, v in cur.items()}
    bl = None
    if series.kind != "benchmark":
        bl = engine.reg.active_baseline(plan.application_id, plan.environment, journey, series.workload_key)
    reasons: list[str] = []
    slo = []
    for n in nfrs:
        if n["scope"] != journey:
            continue
        key = n["metric"].split(".")[0]
        key = key if key in current else f"{key}_ms" if f"{key}_ms" in current else key
        obs = current.get(key)
        passed = None if obs is None else bool(_OPS[n["comparator"]](obs, n["threshold"]))
        slo.append({"nfr_id": n["id"], "metric": n["metric"], "threshold": n["threshold"], "observed": obs, "passed": passed})
    budgets = []
    for k, bk in _BUDGET_KEYS.items():
        b = policy["frontend"]["budgets"].get(bk)
        if b is not None and current.get(k) is not None:
            budgets.append({"metric": k, "budget": b, "observed": current[k], "within": current[k] <= b, "source": "POLICY"})
    regression = []
    if bl:
        reg = policy["regression"]
        for k in ("lcp_ms", "tbt_ms", "journey_ms", "inp_ms", "ttfb_ms", "fcp_ms", "cls"):
            c, b = cur.get(k, []), (bl.samples or {}).get(k, [])
            if not c or not b:
                continue
            cm, bm = statistics.median(c), statistics.median(b)
            d = stats.pct_delta(cm, bm)
            p = stats.mann_whitney_greater(c, b)
            sig = None if p is None else p < reg["significance_alpha"]
            if sig is False and stats.min_achievable_p(len(c), len(b)) >= reg["significance_alpha"]:
                ci = stats.bootstrap_ratio_ci(c, b)
                sig = None if ci is None else ci[0] > 0
            if abs(cm - bm) < ABS_FLOOR.get(k, 0):
                d_level = "none"
            elif d is None:
                d_level = "unknown"
            elif d >= reg["fail_pct"] and sig is not False:
                d_level = "fail"
            elif d >= reg["warn_pct"] and sig is not False:
                d_level = "warn"
            else:
                d_level = "none"
            regression.append({"metric": k, "baseline": bm, "current": cm, "delta_pct": d, "p_value": p, "level": d_level})
    if not cur:
        verdict = Verdict.INCONCLUSIVE
        reasons.append("no frontend samples collected")
    elif any(s["passed"] is False for s in slo):
        verdict = Verdict.FAIL_SLO
        reasons += [f"SLO {s['nfr_id']} {s['metric']}={s['observed']:.1f} breached" for s in slo if s["passed"] is False]
    elif any(r["level"] == "fail" for r in regression):
        verdict = Verdict.FAIL_REGRESSION
        reasons += [f"{r['metric']} {r['delta_pct']:+.1f}% vs baseline" for r in regression if r["level"] == "fail"]
    elif any(r["level"] == "warn" for r in regression):
        verdict = Verdict.PASS_WITH_REGRESSION_WARNING
        reasons += [f"{r['metric']} {r['delta_pct']:+.1f}% vs baseline" for r in regression if r["level"] == "warn"]
    elif not slo and not regression:
        verdict = Verdict.INCONCLUSIVE
        reasons.append("no approved SLO and no approved baseline to compare against (characterization only)")
    else:
        verdict = Verdict.PASS
    cvs = {k: stats.cv(v) for k, v in cur.items() if k in ("lcp_ms", "journey_ms", "tbt_ms")}
    stable = all(v is not None and v <= max(policy["stability"]["max_cv_p95"] * 2, 0.15) for v in cvs.values()) and \
        len(cur.get("journey_ms", [])) >= int(policy["frontend"]["repeats"])
    if series.kind == "benchmark":
        engine.reg.update(type(series), series.id, stability={"stable": stable, "cv": cvs, "runs": len(cur.get("journey_ms", []))})
    return {"target": journey, "kind": series.kind, "series_id": series.id, "workload_key": series.workload_key,
            "verdict": verdict.value, "reasons": reasons, "current": current, "slo": slo, "lab_budgets": budgets,
            "regression": regression, "baseline_id": bl.id if bl else None,
            "stability": {"stable": stable, "cv": cvs}, "promotion_eligible": series.kind == "benchmark" and stable,
            "frontend": True}
