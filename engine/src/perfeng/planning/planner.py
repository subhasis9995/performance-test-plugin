"""Performance Test Planner. Produces PerformancePlans whose every value carries provenance.

Order of precedence for each value: approved NFR > approved baseline workload > platform POLICY.
Anything else (LLM/agent suggestions, inferred endpoints, synthetic data) is an ASSUMPTION
that must be confirmed by a human before execution.
"""

from __future__ import annotations

from typing import Any

from perfeng.domain.models import (
    FeatureImpactModel,
    PerformancePlan,
    PlannedTest,
    ProvenancedValue,
    Source,
    TestType,
    WorkloadStep,
)
from perfeng.registry.db import new_id

API_MEASUREMENTS = ["http_req_duration p50/p90/p95/p99", "throughput (req/s)", "business transaction rate",
                    "error rate", "dropped iterations", "bytes sent/received", "load-generator CPU",
                    "server CPU/memory (when telemetry configured)"]
FE_MEASUREMENTS = ["LCP", "CLS", "INP (when interactions exist)", "TBT (lab)", "TTFB", "FCP", "Speed Index",
                   "long tasks", "JS execution time", "resource count/size", "network waterfall (HAR)",
                   "failed requests", "console errors", "journey duration"]


def pol(value, key: str, unit: str | None = None) -> ProvenancedValue:
    return ProvenancedValue(value=value, unit=unit, source=Source.POLICY, ref=f"policy:{key}")


def workload_key(test_type: str, rate: float, duration_s: int) -> str:
    return f"{test_type}@{rate:g}rps/{duration_s}s"


def _nfr_thresholds(nfrs: list[dict], target: str) -> dict[str, ProvenancedValue]:
    out = {}
    for n in nfrs:
        if n["scope"] in (target, "*"):
            out[n["metric"]] = ProvenancedValue(value=n["threshold"], unit=n.get("unit"), source=Source.NFR,
                                                ref=n["id"], confirmed=True)
    return out


def _nfr_rate(nfrs: list[dict], target: str) -> ProvenancedValue | None:
    for n in nfrs:
        if n["scope"] in (target, "*") and n["metric"] in ("throughput_rps", "rps", "peak_rps"):
            return ProvenancedValue(value=n["threshold"], unit="req/s", source=Source.NFR, ref=n["id"], confirmed=True)
    return None


def _target_assumptions(target_meta: dict[str, Any]) -> list[str]:
    notes = []
    if target_meta.get("confidence", 1.0) < 0.6:
        notes.append(f"{target_meta['key']}: endpoint inferred by {target_meta.get('source')} (confidence "
                     f"{target_meta.get('confidence')}) — confirm it is a real, safe-to-load endpoint")
    return notes


def plan_benchmark(
    application_id: str,
    environment: str,
    targets: list[dict[str, Any]],  # [{key, source, confidence, journey?: bool}]
    policy: dict,
    nfrs: list[dict],
    include_capacity: bool = False,
    journeys: list[str] | None = None,
) -> PerformancePlan:
    b = policy["benchmark"]
    tests: list[PlannedTest] = []
    notes: list[str] = []
    for t in targets:
        key = t["key"]
        notes += _target_assumptions(t)
        nfr_rate = _nfr_rate(nfrs, key)
        thresholds = _nfr_thresholds(nfrs, key)
        smoke = PlannedTest(
            name=f"smoke:{key}", type=TestType.SMOKE, targets=[key],
            workload=[WorkloadStep(rate=pol(b["smoke_rate"], "benchmark.smoke_rate", "req/s"),
                                   duration_s=pol(b["smoke_duration_s"], "benchmark.smoke_duration_s", "s"))],
            repeats=pol(1, "smoke.repeats"), thresholds=thresholds, rationale="verify target works before load",
        )
        tests.append(smoke)
        rates = [nfr_rate] if nfr_rate else [pol(r, "benchmark.characterization_rates", "req/s") for r in b["characterization_rates"]]
        for r in rates:
            tests.append(PlannedTest(
                name=f"load:{key}@{r.value:g}", type=TestType.LOAD, targets=[key],
                workload=[WorkloadStep(rate=r, duration_s=pol(b["step_duration_s"], "benchmark.step_duration_s", "s"))],
                repeats=pol(b["repeats"], "benchmark.repeats"), thresholds=thresholds,
                rationale="performance characterization under controlled constant arrival rate",
            ))
        if include_capacity:
            ladder = [x for x in b["capacity_ladder"] if x <= policy["limits"]["max_arrival_rate"]]
            tests.append(PlannedTest(
                name=f"capacity:{key}", type=TestType.CAPACITY, targets=[key],
                workload=[WorkloadStep(rate=pol(x, "benchmark.capacity_ladder", "req/s"),
                                       duration_s=pol(b["capacity_step_duration_s"], "benchmark.capacity_step_duration_s", "s"))
                          for x in ladder],
                repeats=pol(1, "capacity.repeats"), thresholds=thresholds,
                rationale="find where degradation begins; stops at first safety/latency/error breach; capped by policy",
            ))
    for j in journeys or []:
        tests.append(PlannedTest(
            name=f"frontend:{j}", type=TestType.FRONTEND, targets=[j], workload=[],
            repeats=pol(policy["frontend"]["repeats"], "frontend.repeats"),
            thresholds={**_nfr_thresholds(nfrs, j)},
            rationale="real-browser journey (Playwright) + Lighthouse lab audit",
        ))
    if not nfrs:
        notes.append("No approved NFRs/SLOs found: verdicts will be baseline-only; no SLO thresholds were invented.")
    reg = policy["regression"]
    return PerformancePlan(
        id=new_id("plan"), application_id=application_id, environment=environment, workflow="benchmark",
        tests=tests, measurements=API_MEASUREMENTS + (FE_MEASUREMENTS if journeys else []),
        regression_warn_pct=pol(reg["warn_pct"], "regression.warn_pct", "%"),
        regression_fail_pct=pol(reg["fail_pct"], "regression.fail_pct", "%"), notes=notes,
    )


def plan_feature(
    application_id: str,
    environment: str,
    impact: FeatureImpactModel,
    targets_meta: dict[str, dict[str, Any]],
    baselines: dict[str, dict[str, Any]],  # target -> {id, workload_key, test_type, rate, duration_s}
    policy: dict,
    nfrs: list[dict],
    include_peak: bool = False,
) -> PerformancePlan:
    f = policy["feature"]
    tests: list[PlannedTest] = []
    notes: list[str] = []
    baseline_refs: dict[str, str] = {}
    for key in impact.endpoints():
        meta = targets_meta.get(key, {"key": key, "source": "repo", "confidence": 0.5})
        notes += _target_assumptions(meta)
        thresholds = _nfr_thresholds(nfrs, key)
        tests.append(PlannedTest(
            name=f"smoke:{key}", type=TestType.SMOKE, targets=[key],
            workload=[WorkloadStep(rate=pol(policy["benchmark"]["smoke_rate"], "benchmark.smoke_rate", "req/s"),
                                   duration_s=pol(policy["benchmark"]["smoke_duration_s"], "benchmark.smoke_duration_s", "s"))],
            repeats=pol(1, "smoke.repeats"), thresholds=thresholds, rationale="changed endpoint still works",
        ))
        bl = baselines.get(key)
        if bl:
            # Compare like-for-like: reuse the approved baseline's workload exactly.
            rate = ProvenancedValue(value=bl["rate"], unit="req/s", source=Source.BASELINE, ref=bl["id"], confirmed=True)
            dur = ProvenancedValue(value=bl["duration_s"], unit="s", source=Source.BASELINE, ref=bl["id"], confirmed=True)
            baseline_refs[key] = bl["id"]
        else:
            rate = _nfr_rate(nfrs, key) or pol(f["load_rate"], "feature.load_rate", "req/s")
            dur = pol(f["load_duration_s"], "feature.load_duration_s", "s")
            notes.append(f"{key}: no approved baseline — regression cannot be measured; run Benchmark first or accept SLO-only verdict")
        tests.append(PlannedTest(
            name=f"load:{key}@{rate.value:g}", type=TestType.LOAD, targets=[key],
            workload=[WorkloadStep(rate=rate, duration_s=dur)], repeats=pol(f["repeats"], "feature.repeats"),
            thresholds=thresholds, rationale="normal load on changed endpoint, same workload as baseline",
        ))
        if include_peak:
            tests.append(PlannedTest(
                name=f"peak:{key}", type=TestType.PEAK, targets=[key],
                workload=[WorkloadStep(rate=pol(f["peak_rate"], "feature.peak_rate", "req/s"),
                                       duration_s=pol(f["peak_duration_s"], "feature.peak_duration_s", "s"))],
                repeats=pol(f["repeats"], "feature.repeats"), thresholds=thresholds, rationale="peak load on changed endpoint",
            ))
    for j in impact.journeys():
        tests.append(PlannedTest(
            name=f"frontend:{j}", type=TestType.FRONTEND, targets=[j], workload=[],
            repeats=pol(policy["frontend"]["repeats"], "frontend.repeats"), thresholds=_nfr_thresholds(nfrs, j),
            rationale="affected frontend journey",
        ))
        bl = baselines.get(j)
        if bl:
            baseline_refs[j] = bl["id"]
    if not tests:
        notes.append("Impact model found no endpoints or journeys to test — refine the impact model or service map.")
    if not nfrs:
        notes.append("No approved NFRs/SLOs found: verdicts will be baseline-only; no SLO thresholds were invented.")
    notes += [f"unknown: {u}" for u in impact.unknowns]
    reg = policy["regression"]
    return PerformancePlan(
        id=new_id("plan"), application_id=application_id, environment=environment, feature_id=impact.feature_id,
        workflow="feature", impact=impact, tests=tests,
        measurements=API_MEASUREMENTS + (FE_MEASUREMENTS if impact.journeys() else []), baseline_refs=baseline_refs,
        regression_warn_pct=pol(reg["warn_pct"], "regression.warn_pct", "%"),
        regression_fail_pct=pol(reg["fail_pct"], "regression.fail_pct", "%"), notes=notes,
    )


def apply_overrides(plan: PerformancePlan, overrides: dict[str, Any], rationale: str) -> PerformancePlan:
    """Agent/user-proposed changes become ASSUMPTION values (need confirmation) — never NFR."""
    d = plan.model_dump()
    for path, value in overrides.items():
        # path like "tests.<name>.repeats" | "tests.<name>.step0.rate" | "regression_warn_pct"
        pv = {"value": value, "source": "ASSUMPTION", "ref": rationale, "confirmed": False, "unit": None}
        if path in ("regression_warn_pct", "regression_fail_pct"):
            d[path] = pv
            continue
        _, name, *rest = path.split(".")
        t = next((t for t in d["tests"] if t["name"] == name), None)
        if t is None:
            raise KeyError(f"no test named {name}")
        if rest == ["repeats"]:
            t["repeats"] = pv
        elif len(rest) == 2 and rest[0].startswith("step"):
            t["workload"][int(rest[0][4:])][rest[1]] = pv
        else:
            raise KeyError(path)
    d["version"] = plan.version + 1
    return PerformancePlan.model_validate(d)


def confirm_assumptions(plan: PerformancePlan) -> PerformancePlan:
    d = plan.model_dump()

    def fix(v):
        if isinstance(v, dict) and v.get("source") == "ASSUMPTION":
            v["confirmed"] = True
        elif isinstance(v, dict):
            for x in v.values():
                fix(x)
        elif isinstance(v, list):
            for x in v:
                fix(x)

    fix(d)
    return PerformancePlan.model_validate(d)
