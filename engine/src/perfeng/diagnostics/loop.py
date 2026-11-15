"""Diagnostic loop: deterministic, evidence-backed hypotheses -> Diagnostic Plan vN -> re-execute -> revise.

Every hypothesis lists the evidence (metric, source, values) that supports it. If a rule fires
without evidence it is reported as ``insufficient_evidence``; nothing is guessed. An LLM may rank
or explain these hypotheses for humans, but it does not create the evidence.
"""

from __future__ import annotations

import json
import re
import statistics
from typing import Any

from sqlalchemy import select

from perfeng.domain.models import (
    GateKind,
    Hypothesis,
    PerformancePlan,
    PlannedTest,
    ProvenancedValue,
    Source,
    TestType,
    WorkloadStep,
)
from perfeng.registry.db import AnalysisRow, BaselineRow, PlanRow, RunRow, SeriesRow, new_id


def _median(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def _route_of(target: str) -> str:
    return target.split(" ", 1)[-1]


def _tele_runs(engine, series_id: str) -> list[dict[str, Any]]:
    return [(r.metrics or {}).get("telemetry") or {} for r in engine.reg.runs_in_series(series_id) if r.status == "completed"]


def _metric_values(teles: list[dict], pattern: str) -> dict[str, list[float]]:
    """Collect per-run values of telemetry keys matching regex `pattern` (across all sources)."""
    out: dict[str, list[float]] = {}
    rx = re.compile(pattern)
    for t in teles:
        for src, vals in t.items():
            if not isinstance(vals, dict):
                continue
            for k, v in vals.items():
                if isinstance(v, (int, float)) and rx.search(k):
                    out.setdefault(f"{src}:{k}", []).append(float(v))
    return out


def _route_rx(route: str) -> str:
    # match label route="/rest/products/search" or templated /api/Products/{id}
    return re.escape(f'route="{route}"').replace(r"\{id\}", r"\{id\}")


def _ev(source: str, detail: str, **values) -> dict[str, Any]:
    return {"source": source, "detail": detail, **values}


def api_hypotheses(engine, target_verdict: dict[str, Any], plan: PerformancePlan) -> list[Hypothesis]:
    hs: list[Hypothesis] = []
    target = target_verdict["target"]
    route = _route_of(target)
    cur_teles = _tele_runs(engine, target_verdict["series_id"])
    bl = engine.reg.get(BaselineRow, target_verdict["baseline_id"]) if target_verdict.get("baseline_id") else None
    base_teles = _tele_runs(engine, bl.series_id) if bl else []
    reg = {r["metric"]: r for r in target_verdict.get("regression", [])}
    p95 = reg.get("p95", {})
    latency_regressed = p95.get("level") in ("warn", "fail")
    err_regressed = reg.get("error_rate", {}).get("level") in ("warn", "fail")
    has_tele = any(k != "window_s" and k != "sources" for t in cur_teles for k in t)

    # ---- measurement validity first
    runs = [r for r in engine.reg.runs_in_series(target_verdict["series_id"]) if r.status == "completed"]
    dropped = sum((r.metrics or {}).get("dropped_iterations", 0) for r in runs)
    gen_cpu = _median([((r.metrics or {}).get("generator") or {}).get("cpu_pct_p95") for r in runs])
    if dropped or (gen_cpu or 0) > engine.policy["safety"]["generator_cpu_saturation_pct"]:
        hs.append(Hypothesis(id=new_id("hyp"), category="load_generator_saturation",
                             claim="Load generator could not sustain the planned arrival rate; latency may be partly client-side",
                             evidence=[_ev("k6", "dropped iterations / generator CPU", dropped_iterations=dropped, generator_cpu_p95=gen_cpu)],
                             confidence="high", status="supported",
                             next_test="Re-run at lower rate or on a dedicated load generator"))

    if latency_regressed:
        # decomposition: injected/edge delay vs server-side (upstream) latency for the route
        rr = _route_rx(route)
        edge_cur = _metric_values(cur_teles, rf"(delay|queue).*{rr}.*:mean_ms")
        up_cur = _metric_values(cur_teles, rf"(upstream|server|request_duration|latency).*{rr}.*:mean_ms")
        up_cur = {k: v for k, v in up_cur.items() if "delay" not in k}
        up_base = _metric_values(base_teles, rf"(upstream|server|request_duration|latency).*{rr}.*:mean_ms")
        up_base = {k: v for k, v in up_base.items() if "delay" not in k}
        if any("upstream" in k for k in up_cur):  # prefer pure server-side time over gateway totals
            up_cur = {k: v for k, v in up_cur.items() if "upstream" in k}
            up_base = {k: v for k, v in up_base.items() if "upstream" in k}
        client_delta = (p95.get("current") or 0) - (p95.get("baseline") or 0)
        if edge_cur:
            k, vals = max(edge_cur.items(), key=lambda kv: _median(kv[1]) or 0)
            m = _median(vals)
            if m and m > 0.3 * client_delta:
                hs.append(Hypothesis(
                    id=new_id("hyp"), category="edge_or_network_latency",
                    claim=f"Added latency on {target} is introduced in front of the application (gateway/proxy/network), not by app processing",
                    evidence=[_ev("telemetry", f"{k} median {m:.1f} ms per request", metric=k, value_ms=m),
                              _ev("analysis", f"client p95 increased by {client_delta:.1f} ms", delta_ms=client_delta)],
                    confidence="high" if m > 0.7 * client_delta else "medium", status="supported",
                    next_test="Step load at 0.5x/1x/2x: a constant added delay is load-independent (not queueing)"))
        for k, vals in up_cur.items():
            bk = next((b for b in up_base if b.split(":", 1)[1] == k.split(":", 1)[1]), None)
            cm, bm = _median(vals), _median(up_base.get(bk, [])) if bk else None
            if cm is None:
                continue
            if bm is not None and bm > 0 and (cm - bm) / bm > 0.25 and cm - bm > 5:
                hs.append(Hypothesis(
                    id=new_id("hyp"), category="app_server_latency",
                    claim=f"Server-side processing time for {route} increased (application/database work, not network)",
                    evidence=[_ev("telemetry", f"{k}: baseline {bm:.1f} ms -> current {cm:.1f} ms", metric=k, baseline_ms=bm, current_ms=cm)],
                    confidence="medium", status="supported",
                    next_test="Trace the endpoint / profile DB queries; step load to see if it grows with load (pool/queueing)"))
            elif bm is not None and abs(cm - bm) <= max(5, 0.25 * bm):
                hs.append(Hypothesis(
                    id=new_id("hyp"), category="app_server_latency",
                    claim=f"Application/server-side processing on {route} is the cause (server-side time is unchanged)",
                    evidence=[_ev("telemetry", f"{k}: baseline {bm:.1f} ms vs current {cm:.1f} ms", metric=k, baseline_ms=bm, current_ms=cm)],
                    confidence="medium", status="refuted"))
        # CPU / event loop / GC / memory of the app process
        for cat, pat, claim, thr in [
            ("cpu_saturation", r"cpu_pct_p95$", "Application CPU is saturated or much higher", 2.0),
            ("event_loop_lag", r"eventloop_lag.*(:mean|_seconds$)", "Event-loop lag increased (blocking work / GC)", 2.0),
            ("gc_pressure", r"gc_duration.*:mean", "Garbage collection time increased", 2.0),
            ("memory_growth", r"rss_mb_max$|resident_memory", "Memory footprint grew", 1.3),
        ]:
            cv = {k: v for k, v in _metric_values(cur_teles, pat).items() if not k.startswith("__host__")}
            bv = _metric_values(base_teles, pat)
            for k, vals in cv.items():
                cm, bm = _median(vals), _median(bv.get(k, []))
                if cm is None or bm is None or bm <= 0:
                    continue
                ratio = cm / bm
                if ratio >= thr or (cat == "cpu_saturation" and cm >= 80):
                    hs.append(Hypothesis(id=new_id("hyp"), category=cat, claim=claim,
                                         evidence=[_ev("telemetry", f"{k}: baseline {bm:.2f} -> current {cm:.2f} (x{ratio:.1f})",
                                                       metric=k, baseline=bm, current=cm)],
                                         confidence="medium", status="supported",
                                         next_test="Profile the process under the same workload"))
                elif cat == "cpu_saturation" and ratio < 1.3 and cm < 50:
                    hs.append(Hypothesis(id=new_id("hyp"), category=cat, claim="Application CPU saturation is the cause",
                                         evidence=[_ev("telemetry", f"{k}: baseline {bm:.1f}% vs current {cm:.1f}%", metric=k,
                                                       baseline=bm, current=cm)], confidence="medium", status="refuted"))
    if err_regressed:
        rr = _route_rx(route)
        inj = _metric_values(cur_teles, rf"(injected_errors|upstream_errors|5..|error).*{rr}.*:increase")
        status_codes = {}
        for r in runs:
            ex = (((r.metrics or {}).get("per_target") or {}).get(target) or {}).get("extra", {})
            for k, v in ex.items():
                if k.startswith("status_"):
                    status_codes[k] = status_codes.get(k, 0) + v
        ev = [_ev("k6", "response status counts across repeats", **status_codes)]
        ev += [_ev("telemetry", f"{k} total {sum(v):.0f}", metric=k, total=sum(v)) for k, v in inj.items()]
        hs.append(Hypothesis(id=new_id("hyp"), category="error_spike",
                             claim=f"Error rate on {target} increased" + (" with errors raised before the app (gateway/upstream)" if inj else ""),
                             evidence=ev, confidence="high" if inj else "medium", status="supported",
                             next_test="Low-rate smoke to check whether errors are load-dependent"))
    if (latency_regressed or err_regressed) and not has_tele:
        hs.append(Hypothesis(id=new_id("hyp"), category="unknown",
                             claim=f"Cause of the regression on {target} cannot be determined: no server-side telemetry was collected",
                             evidence=[], confidence="low", status="insufficient_evidence",
                             next_test="Configure telemetry for this environment (Prometheus scrape/PromQL or process:port=N) and re-run"))
    elif (latency_regressed or err_regressed) and not any(h.status == "supported" and h.category != "load_generator_saturation" for h in hs):
        hs.append(Hypothesis(id=new_id("hyp"), category="unknown",
                             claim=f"Telemetry does not explain the regression on {target}",
                             evidence=[_ev("telemetry", "collected sources", sources=list((cur_teles[0] if cur_teles else {}).get("sources", {})))],
                             confidence="low", status="insufficient_evidence",
                             next_test="Add tracing or database metrics; run focused step-load diagnostic"))
    return hs


def frontend_hypotheses(engine, tv: dict[str, Any]) -> list[Hypothesis]:
    hs = []
    reg = {r["metric"]: r for r in tv.get("regression", [])}
    bad = {k: r for k, r in reg.items() if r["level"] in ("warn", "fail")}
    if not bad:
        return hs
    cur = tv.get("current", {})
    runs = [r for r in engine.reg.runs_in_series(tv["series_id"]) if r.status == "completed"]
    if "tbt_ms" in bad or "journey_ms" in bad:
        ev = [_ev("browser", f"TBT {bad.get('tbt_ms', {}).get('baseline')} -> {cur.get('tbt_ms')} ms; long tasks {cur.get('long_tasks')}, "
                             f"JS exec {cur.get('js_exec_ms')} ms")]
        hs.append(Hypothesis(id=new_id("hyp"), category="frontend_long_tasks", claim="More main-thread blocking (long tasks / JS execution)",
                             evidence=ev if cur.get("long_tasks") else [], confidence="medium",
                             status="supported" if cur.get("long_tasks") else "insufficient_evidence",
                             next_test="Inspect Playwright trace / Lighthouse bootup-time for the heaviest scripts"))
    if "lcp_ms" in bad:
        ttfb = reg.get("ttfb_ms", {})
        slow = []
        for r in runs[:1]:
            f = engine.root / (r.artifacts or {}).get("dir", "") / "journey-result.json"
            if f.exists():
                slow = json.loads(f.read_text(encoding="utf-8"))["evidence"]["slowest_resources"][:5]
        if ttfb.get("level") in ("warn", "fail"):
            claim, cat = "LCP regressed because the document/server response (TTFB) is slower", "server_response"
        else:
            claim, cat = "LCP regressed with unchanged TTFB: slower critical resources or rendering", "critical_resources"
        hs.append(Hypothesis(id=new_id("hyp"), category=cat, claim=claim,
                             evidence=[_ev("browser", f"LCP {bad['lcp_ms']['baseline']:.0f} -> {bad['lcp_ms']['current']:.0f} ms"),
                                       *[_ev("har", f"slow resource {s['name'][-80:]} {s['duration']:.0f} ms", url=s["name"],
                                             duration_ms=s["duration"]) for s in slow]],
                             confidence="medium", status="supported", next_test="Throttle network / inspect critical request chain"))
    return hs


def _diagnostic_plan(engine, plan: PerformancePlan, hyps: list[Hypothesis], iteration: int) -> PerformancePlan | None:
    focus = []
    for tv in (engine.latest_analysis(plan.id) or {}).get("targets", []):
        if tv.get("frontend") or tv["verdict"] not in ("FAIL_REGRESSION", "PASS_WITH_REGRESSION_WARNING", "FAIL_SLO"):
            continue
        focus.append(tv)
    if not focus:
        return None
    tests = []
    factors = engine.policy.get("diagnostics", {}).get("step_factors", [0.5, 1.0, 2.0])
    cap = engine.policy["limits"]["max_arrival_rate"]
    for tv in focus:
        bl = engine.reg.get(BaselineRow, tv["baseline_id"]) if tv.get("baseline_id") else None
        m = re.match(r"\w+@([\d.]+)rps/(\d+)s", tv["workload_key"])
        base_rate = float(m.group(1)) if m else engine.policy["feature"]["load_rate"]
        dur = int(m.group(2)) if m else engine.policy["feature"]["load_duration_s"]
        for f in factors:
            rate = min(cap, max(1.0, round(base_rate * f, 2)))
            tests.append(PlannedTest(
                name=f"diag-step:{tv['target']}@{rate:g}", type=TestType.LOAD, targets=[tv["target"]],
                workload=[WorkloadStep(rate=ProvenancedValue(value=rate, unit="req/s", source=Source.POLICY,
                                                             ref=f"policy:diagnostics.step_factors x{f} of {'baseline ' + bl.id if bl else 'feature load'}"),
                                       duration_s=ProvenancedValue(value=dur, unit="s", source=Source.POLICY, ref="policy:diagnostics.duration"))],
                repeats=ProvenancedValue(value=engine.policy.get("diagnostics", {}).get("repeats", 2), source=Source.POLICY, ref="policy:diagnostics.repeats"),
                rationale="focused step load: separates constant added latency from load-dependent queueing/saturation",
            ))
    return PerformancePlan(
        id=new_id("plan"), version=plan.version + iteration, application_id=plan.application_id, environment=plan.environment,
        feature_id=plan.feature_id, workflow="diagnostic", impact=plan.impact, tests=tests,
        measurements=["latency vs arrival rate", "server-side telemetry vs rate", "error rate vs rate"],
        baseline_refs=plan.baseline_refs, regression_warn_pct=plan.regression_warn_pct, regression_fail_pct=plan.regression_fail_pct,
        notes=[f"Diagnostic Plan v{iteration + 1} for {plan.id}; hypotheses: " + "; ".join(h.category for h in hyps if h.status == "supported")],
    )


def _load_dependence(engine, diag_plan_id: str) -> list[Hypothesis]:
    """After a step-load diagnostic run: is latency constant across rates (fixed delay) or growing (queueing)?"""
    out = []
    series = engine.reg.query(select(SeriesRow).where(SeriesRow.plan_id == diag_plan_id))
    by_target: dict[str, list[tuple[float, float]]] = {}
    for s in series:
        m = re.match(r"\w+@([\d.]+)rps", s.workload_key)
        if not m:
            continue
        p95s = [((r.metrics or {}).get("per_target") or {}).get(s.target, {}).get("p95") for r in engine.reg.runs_in_series(s.id)
                if r.status == "completed"]
        med = _median(p95s)
        if med is not None:
            by_target.setdefault(s.target, []).append((float(m.group(1)), med))
    for t, pts in by_target.items():
        pts.sort()
        if len(pts) < 2:
            continue
        lo, hi = pts[0], pts[-1]
        growth = (hi[1] - lo[1]) / lo[1] * 100 if lo[1] else None
        ev = [_ev("diagnostic-run", f"p95 {lo[1]:.1f} ms @ {lo[0]:g} req/s -> {hi[1]:.1f} ms @ {hi[0]:g} req/s",
                  points=[{"rate": r, "p95_ms": p} for r, p in pts])]
        if growth is not None and growth < 20:
            out.append(Hypothesis(id=new_id("hyp"), category="constant_added_latency",
                                  claim=f"Latency on {t} is load-independent: a fixed per-request delay, not queueing or saturation",
                                  evidence=ev, confidence="high", status="supported"))
        elif growth is not None:
            out.append(Hypothesis(id=new_id("hyp"), category="queueing_or_saturation",
                                  claim=f"Latency on {t} grows with load (+{growth:.0f}% from {lo[0]:g} to {hi[0]:g} req/s): queueing, pool or saturation",
                                  evidence=ev, confidence="medium", status="supported",
                                  next_test="Check connection pool / thread pool / DB metrics under step load"))
    return out


def diagnose(engine, plan_id: str) -> dict[str, Any]:
    row = engine.reg.get(PlanRow, plan_id)
    if row is None:
        raise ValueError(f"plan {plan_id} not found")
    plan = PerformancePlan.model_validate(row.content)
    root_id, iteration = plan_id, 0
    while True:
        r = engine.reg.get(PlanRow, root_id)
        if r.workflow != "diagnostic" or not r.parent_plan_id:
            break
        root_id, iteration = r.parent_plan_id, iteration + 1
    analysis = engine.latest_analysis(plan_id)
    if analysis is None:
        raise ValueError("plan has no analysis yet; run it first")
    hyps: list[Hypothesis] = []
    notes = []
    if plan.workflow == "diagnostic":
        hyps += _load_dependence(engine, plan_id)
        prev = engine.latest_analysis(f"diag:{root_id}")
        if prev:
            for h in prev["hypotheses"]:
                hyps.append(Hypothesis.model_validate(h))
    for tv in analysis.get("targets", []):
        if tv.get("kind") == "benchmark":
            continue
        if tv.get("frontend"):
            hyps += frontend_hypotheses(engine, tv)
        elif tv["verdict"] in ("FAIL_REGRESSION", "PASS_WITH_REGRESSION_WARNING", "FAIL_SLO") and plan.workflow != "diagnostic":
            hyps += api_hypotheses(engine, tv, plan)
    if not hyps:
        notes.append("No regression or SLO breach to diagnose." if analysis.get("overall_verdict") in ("PASS", None)
                     else "No rule produced a hypothesis; evidence insufficient.")
    max_iter = engine.policy["diagnostics"]["max_iterations"]
    diag_plan = None
    if plan.workflow != "diagnostic" and iteration < max_iter and any(h.status == "supported" for h in hyps):
        diag_plan = _diagnostic_plan(engine, plan, hyps, iteration + 1)
        if diag_plan:
            engine._save_plan(diag_plan, parent=plan_id)
    elif iteration >= max_iter:
        notes.append(f"Diagnostic iteration limit ({max_iter}) reached.")
    order = {"supported": 0, "insufficient_evidence": 1, "open": 2, "refuted": 3}
    conf = {"high": 0, "medium": 1, "low": 2}
    hyps.sort(key=lambda h: (order[h.status], conf[h.confidence]))
    result = {"plan_id": plan_id, "root_plan_id": root_id, "iteration": iteration + 1,
              "overall_verdict": analysis.get("overall_verdict"),
              "hypotheses": [h.model_dump() for h in hyps],
              "diagnostic_plan_id": diag_plan.id if diag_plan else None,
              "diagnostic_plan_gate": GateKind.DIAGNOSTIC_RUN.value if diag_plan else None, "notes": notes}
    engine.reg.add(AnalysisRow(id=new_id("dia"), subject_id=f"diag:{root_id}", content=json.loads(json.dumps(result, default=str)),
                               verdict=analysis.get("overall_verdict") or "n/a"))
    return result
