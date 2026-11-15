"""Human-readable rendering. Numbers come from deterministic analysis only."""

from __future__ import annotations

import html as _html
import json
from typing import Any


def _f(v, unit: str = "", nd: int = 1) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.{nd}f}{unit}"
    return f"{v}{unit}"


def render_plan(v: dict[str, Any]) -> str:
    L = [f"## Performance Plan {v['plan_id']} (v{v['version']}, {v['status']})",
         f"Workflow: **{v['workflow']}**  Env: **{v['environment']}**" + (f"  Feature: **{v['feature_id']}**" if v.get("feature_id") else "")]
    if v.get("impact"):
        im = v["impact"]
        L.append("\n### Impact")
        for sec in ("backend", "frontend", "dependencies"):
            for it in im.get(sec, []):
                L.append(f"- {sec}: {it['kind']} **{it['name']}** - {'; '.join(it['evidence'][:2])}")
        for u in im.get("unknowns", []):
            L.append(f"- unknown: {u}")
    L.append("\n### Tests")
    L.append("| test | type | workload | repeats | why |\n|---|---|---|---|---|")
    for t in v["tests"]:
        L.append(f"| {t['name']} | {t['type']} | {', '.join(t['workload']) or 'browser journey'} | {t['repeats']} | {t['rationale']} |")
    L.append("\n### Where every value comes from")
    for src in ("NFR", "BASELINE", "POLICY", "ASSUMPTION"):
        vals = v["values_by_source"].get(src, [])
        L.append(f"- **{src}** ({len(vals)}): " + ("; ".join(vals[:8]) + (" …" if len(vals) > 8 else "") if vals else "none"))
    if v["unconfirmed_assumptions"]:
        L.append("\n**Needs your confirmation (G3):** " + ", ".join(v["unconfirmed_assumptions"]))
    if v["high_risk_tests_needing_approval"]:
        L.append("\n**High-risk tests needing explicit approval (G4):** " + ", ".join(v["high_risk_tests_needing_approval"]))
    L.append(f"\nEstimated load time: ~{v['estimated_load_seconds'] // 60} min {v['estimated_load_seconds'] % 60} s")
    for n in v.get("notes", []):
        L.append(f"- note: {n}")
    return "\n".join(L)


def render_analysis(a: dict[str, Any]) -> str:
    L = [f"## Analysis for plan {a['plan_id']}"]
    if a.get("overall_verdict"):
        L.append(f"**Overall: {a['overall_verdict']}**")
    if a.get("characterization"):
        L.append("\n### Characterization (benchmark)")
        L.append("| target | workload | p50 | p90 | p95 | p99 | err | rps | CV p95 | stable |\n|---|---|---|---|---|---|---|---|---|---|")
        for c in [c for c in a["characterization"] if not c.get("frontend")]:
            L.append(f"| {c['target']} | {c['workload']} | {_f(c['p50'])} | {_f(c['p90'])} | {_f(c['p95'])} | {_f(c['p99'])} | "
                     f"{_f((c['error_rate'] or 0) * 100, '%', 2)} | {_f(c['rps'])} | {_f(c['cv_p95'], '', 3)} | {'yes' if c['stable'] else 'no'} |")
    fe = [c for c in a.get("characterization", []) if c.get("frontend")]
    if fe:
        L.append("\n### Frontend journeys (lab, medians)")
        L.append("| journey | LCP | CLS | INP | TBT | TTFB | FCP | Speed Index | journey ms | LH score | stable |\n"
                 "|---|---|---|---|---|---|---|---|---|---|---|")
        for c in fe:
            L.append(f"| {c['target']} | {_f(c.get('lcp_ms'))} | {_f(c.get('cls'), '', 3)} | {_f(c.get('inp_ms'))} | {_f(c.get('tbt_ms'))} | "
                     f"{_f(c.get('ttfb_ms'))} | {_f(c.get('fcp_ms'))} | {_f(c.get('speed_index_ms'))} | {_f(c.get('journey_ms'))} | "
                     f"{_f(c.get('lh_performance_score'), '', 2)} | {'yes' if c['stable'] else 'no'} |")
    for t in a["targets"]:
        if t.get("kind") == "benchmark":
            continue
        L.append(f"\n### {t['target']} - {t['verdict']}")
        cur = t.get("current", {})
        if t.get("frontend"):
            L.append(", ".join(f"{k}={_f(v)}" for k, v in cur.items()))
        else:
            L.append(f"p50 {_f(cur.get('p50'))} ms · p95 {_f(cur.get('p95'))} ms · p99 {_f(cur.get('p99'))} ms · "
                     f"errors {_f((cur.get('error_rate') or 0) * 100, '%', 2)} · {_f(cur.get('rps'))} req/s")
        for s in t.get("slo", []):
            s = s if isinstance(s, dict) else s.__dict__
            L.append(f"- SLO {s['nfr_id']}: {s['metric']} {s.get('comparator', '<')} {s['threshold']} → observed {_f(s['observed'])} "
                     f"→ {'PASS' if s['passed'] else 'FAIL' if s['passed'] is False else 'n/a'}")
        for r in t.get("regression", []):
            ci = r.get("ci_pct")
            L.append(f"- vs baseline {t.get('baseline_id')}: {r['metric']} {_f(r['baseline'])} → {_f(r['current'])} "
                     f"({_f(r['delta_pct'], '%')}{', p=' + _f(r['p_value'], '', 3) if r.get('p_value') is not None else ''}"
                     f"{', 95% CI ' + _f(ci[0]) + '..' + _f(ci[1]) + '%' if ci else ''}) → {r['level']}")
        tr = t.get("trend") or {}
        if tr.get("drift_pct") is not None:
            L.append(f"- recent trend: drift {_f(tr['drift_pct'], '%')} over {int(tr['n'])} series (informational; baseline unchanged)")
        for r in t.get("reasons", []):
            L.append(f"- {r}")
    for n in a.get("notes", []):
        L.append(f"- note: {n}")
    return "\n".join(L)


def render(obj: Any) -> str:
    if isinstance(obj, dict) and "values_by_source" in obj:
        return render_plan(obj)
    if isinstance(obj, dict) and "targets" in obj and "plan_id" in obj and "workflow" in obj:
        return render_analysis(obj)
    if isinstance(obj, dict) and "hypotheses" in obj:
        return render_diagnosis(obj)
    return "```json\n" + json.dumps(obj, indent=2, default=str) + "\n```"


def render_diagnosis(d: dict[str, Any]) -> str:
    L = [f"## Diagnosis for plan {d['plan_id']} (iteration {d.get('iteration', 1)})"]
    for h in d["hypotheses"]:
        L.append(f"\n### [{h['status']}/{h['confidence']}] {h['claim']}")
        if not h["evidence"]:
            L.append("- evidence: **insufficient** - " + (h.get("next_test") or "collect telemetry"))
        for e in h["evidence"]:
            L.append(f"- evidence: {e.get('source')}: {e.get('detail')}")
        if h.get("next_test"):
            L.append(f"- next test: {h['next_test']}")
    if d.get("diagnostic_plan_id"):
        L.append(f"\nProposed Diagnostic Plan: **{d['diagnostic_plan_id']}** (needs approval G6 before it runs)")
    for n in d.get("notes", []):
        L.append(f"- note: {n}")
    return "\n".join(L)


def plan_report(engine, plan_id: str, html: bool = False) -> str:
    """Full professional report (see perfeng.report.full)."""
    from perfeng.report.full import build_report

    return build_report(engine, plan_id, html=html)
