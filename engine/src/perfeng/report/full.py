"""Professional performance test report (Markdown canonical, HTML rendered from it).

Sections: executive summary, objectives/scope, inputs, environment and tooling, test plan and execution,
results (per target, per repeat, frontend), success/failure breakdown, SLO and baseline analysis, telemetry,
diagnosis, observations and recommendations, assumptions and limitations, appendix (run ids, artifacts, audit,
reproduction). Every number comes from the registry / deterministic analysis; observations are rule-based.
Secrets are never included: auth shows variable names and whether they are set, never values.
"""

from __future__ import annotations

import csv
import html as _html
import json
import statistics
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from perfeng.registry.db import AuditRow, BaselineRow, PlanRow, RunRow, SeriesRow
from perfeng.secrets import presence

OK, WARN, BAD = "✅", "⚠️", "❌"
VERDICT_ICON = {"PASS": OK, "PASS_WITH_REGRESSION_WARNING": WARN, "INCONCLUSIVE": WARN, "FAIL_REGRESSION": BAD, "FAIL_SLO": BAD,
                "CHARACTERIZED": "ℹ️"}


def f(v, nd=1, unit="") -> str:
    if v is None:
        return "-"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v:,.{nd}f}{unit}"
    if isinstance(v, int):
        return f"{v:,}{unit}"
    return str(v)


def pct(v, nd=2) -> str:
    return "-" if v is None else f"{v * 100:.{nd}f}%"


def dt(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, str):
        try:
            v = datetime.fromisoformat(v)
        except ValueError:
            return v
    return v.strftime("%Y-%m-%d %H:%M:%S")


def table(headers: list[str], rows: list[list[Any]]) -> str:
    if not rows:
        return "_none_\n"
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out) + "\n"


def kv(rows: list[tuple[str, Any]]) -> str:
    return table(["Item", "Value"], [[k, v] for k, v in rows])


def _median(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


# --------------------------------------------------------------------------- svg charts (HTML only)


def svg_bars(title: str, labels: list[str], values: list[float], refs: list[tuple[str, float, str]], unit: str = "ms") -> str:
    if not values:
        return ""
    w, h, pad_l, pad_b, pad_t = 620, 200, 52, 28, 26
    top = max([*values, *[r[1] for r in refs]]) * 1.15 or 1
    bw = (w - pad_l - 12) / max(len(values), 1)
    y = lambda v: pad_t + (h - pad_t - pad_b) * (1 - v / top)  # noqa: E731
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="100%" style="max-width:{w}px;background:#fff;'
             f'border:1px solid #ddd;border-radius:6px"><text x="8" y="16" font-size="12" font-weight="600">{_html.escape(title)}</text>']
    for t in range(0, 5):
        v = top * t / 4
        parts.append(f'<line x1="{pad_l}" x2="{w - 8}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="#eee"/>'
                     f'<text x="{pad_l - 4}" y="{y(v) + 4:.1f}" font-size="10" text-anchor="end" fill="#666">{v:,.0f}</text>')
    for i, (lab, v) in enumerate(zip(labels, values)):
        x = pad_l + i * bw + bw * 0.15
        parts.append(f'<rect x="{x:.1f}" y="{y(v):.1f}" width="{bw * 0.7:.1f}" height="{h - pad_b - y(v):.1f}" fill="#4f7cff"/>'
                     f'<text x="{x + bw * 0.35:.1f}" y="{y(v) - 3:.1f}" font-size="10" text-anchor="middle">{v:,.1f}</text>'
                     f'<text x="{x + bw * 0.35:.1f}" y="{h - 12}" font-size="10" text-anchor="middle" fill="#444">{_html.escape(lab)}</text>')
    for lab, v, color in refs:
        parts.append(f'<line x1="{pad_l}" x2="{w - 8}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="{color}" stroke-width="2" stroke-dasharray="6 3"/>'
                     f'<text x="{w - 10}" y="{y(v) - 4:.1f}" font-size="10" text-anchor="end" fill="{color}">{_html.escape(lab)} {v:,.1f}{unit}</text>')
    parts.append("</svg>")
    return "\n".join(parts)


# --------------------------------------------------------------------------- data collection


class ReportData:
    def __init__(self, engine, plan_id: str):
        self.e = engine
        self.reg = engine.reg
        self.plan_row = self.reg.get(PlanRow, plan_id)
        if self.plan_row is None:
            raise ValueError(f"plan {plan_id} not found")
        self.plan_id = plan_id
        self.plan = engine.get_plan(plan_id)
        self.view = engine.plan_view(plan_id)
        self.analysis = engine.latest_analysis(plan_id)
        self.diag = engine.latest_analysis(f"diag:{plan_id}")
        self.cfg = engine.cfg
        self.env = engine.env(self.plan.environment)
        self.runs: list[RunRow] = self.reg.query(select(RunRow).where(RunRow.plan_id == plan_id).order_by(RunRow.created_at))
        self.series: list[SeriesRow] = self.reg.query(select(SeriesRow).where(SeriesRow.plan_id == plan_id).order_by(SeriesRow.created_at))
        self.approvals = self.reg.approvals_for(plan_id)
        self.nfrs = engine._nfrs(self.plan.application_id)
        self.policy = engine.policy
        self.root: Path = engine.root

    def runs_of(self, series_id: str) -> list[RunRow]:
        return [r for r in self.runs if r.series_id == series_id]

    def series_for_target(self, target: str) -> list[SeriesRow]:
        return [s for s in self.series if s.target == target]

    def baseline(self, bid: str | None) -> BaselineRow | None:
        return self.reg.get(BaselineRow, bid) if bid else None


# --------------------------------------------------------------------------- sections


def sec_header(d: ReportData) -> str:
    p = d.plan
    kind = {"benchmark": "Benchmark (performance characterization)", "feature": f"Feature performance test: {p.feature_id}",
            "diagnostic": "Diagnostic test"}[p.workflow]
    return (f"# Performance Test Report: {p.application_id} / {p.environment}\n\n"
            f"**{kind}**\n\n" + kv([
                ("Report generated", datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")),
                ("Plan", f"`{p.id}` (v{p.version}, {d.plan_row.status})"),
                ("Application / environment", f"{p.application_id} / {p.environment}"),
                ("Feature / work item", p.feature_id or "-"),
                ("Overall verdict", f"{VERDICT_ICON.get((d.analysis or {}).get('overall_verdict') or 'CHARACTERIZED', '')} "
                 f"**{(d.analysis or {}).get('overall_verdict') or 'Characterization only (no pass/fail: baseline not yet approved)'}**"),
                ("Environment label", "local-lab (load generator, browser and possibly the app share one machine)"
                 if any((r.environment_info or {}).get("label") == "local-lab" for r in d.runs) else "-"),
            ]))


def _verdict_rows(d: ReportData) -> list[dict[str, Any]]:
    return [t for t in (d.analysis or {}).get("targets", []) if t.get("kind") != "benchmark"]


def sec_executive(d: ReportData) -> str:
    a = d.analysis or {}
    L = ["## 1. Executive summary\n"]
    total_runs = len(d.runs)
    ok = sum(1 for r in d.runs if r.status == "completed")
    aborted = sum(1 for r in d.runs if r.status == "aborted")
    failed = sum(1 for r in d.runs if r.status in ("failed", "rejected"))
    api_reqs = sum(((r.metrics or {}).get("http_reqs") or 0) for r in d.runs if r.test_type != "frontend")
    t0 = min((r.started_at for r in d.runs if r.started_at), default=None)
    t1 = max((r.finished_at for r in d.runs if r.finished_at), default=None)
    L.append(kv([
        ("Runs executed", f"{total_runs} ({ok} completed, {aborted} aborted by safety stop, {failed} failed/rejected)"),
        ("Total API requests generated", f(api_reqs)),
        ("Test window", f"{dt(t0)} → {dt(t1)}"),
        ("Targets tested", ", ".join(sorted({s.target for s in d.series})) or "-"),
    ]))
    vs = _verdict_rows(d)
    if vs:
        L.append("\n**Findings**\n")
        for t in vs:
            L.append(f"- {VERDICT_ICON.get(t['verdict'], '')} **{t['target']}**: {t['verdict']}. " + "; ".join(t.get("reasons", [])[:3]))
    elif a.get("characterization"):
        L.append("\n**Characterization (baseline measurement)**\n")
        for c in a["characterization"]:
            if c.get("frontend"):
                L.append(f"- **{c['target']}** (journey): LCP {f(c.get('lcp_ms'), 0, ' ms')}, CLS {f(c.get('cls'), 3)}, "
                         f"TBT {f(c.get('tbt_ms'), 0, ' ms')}; {'stable' if c['stable'] else 'NOT stable'}")
            else:
                L.append(f"- **{c['target']}** at {c['workload']}: p95 {f(c['p95'], 1, ' ms')}, p99 {f(c['p99'], 1, ' ms')}, "
                         f"errors {pct(c['error_rate'])}, {f(c['rps'])} req/s; {'stable' if c['stable'] else 'NOT stable'} (CV {f(c['cv_p95'], 3)})")
        L.append("\nThis is a measurement, not a verdict. Promote a stable series to a baseline (`/perf:baseline`) to enable regression detection.")
    else:
        L.append("\nNo analysis available yet: run the plan first.")
    return "\n".join(L) + "\n"


def sec_scope(d: ReportData) -> str:
    p = d.plan
    L = ["## 2. Objectives and scope\n"]
    obj = {"benchmark": "Measure current performance under controlled, repeated workloads to characterize the system and to produce candidates for an approved baseline.",
           "feature": f"Determine the performance impact of {p.feature_id} by comparing against the approved baseline workload and any approved SLOs, then explain regressions.",
           "diagnostic": "Isolate the cause of an observed regression with focused step-load tests."}[p.workflow]
    L.append(obj + "\n")
    if p.impact:
        im = p.impact
        L.append(f"**Feature:** {p.feature_id}: {im.summary or ''}\n")
        L.append("**Feature Impact Model**\n")
        rows = [[sec.title(), it.kind, f"`{it.name}`", f"{it.confidence:.2f}", "; ".join(it.evidence[:2])]
                for sec, items in (("backend", im.backend), ("frontend", im.frontend), ("dependencies", im.dependencies)) for it in items]
        L.append(table(["Area", "Kind", "Item", "Confidence", "Evidence"], rows))
        if im.changed_files:
            L.append("**Changed files:** " + ", ".join(f"`{x}`" for x in im.changed_files[:15]) + "\n")
        for u in im.unknowns:
            L.append(f"- unknown: {u}")
    L.append("\n**Measurements collected:** " + "; ".join(p.measurements))
    return "\n".join(L) + "\n"


def _data_file_info(root: Path, rel: str) -> str:
    fp = (root / rel)
    if not fp.exists():
        return f"`{rel}` (missing)"
    try:
        if fp.suffix.lower() == ".csv":
            with open(fp, newline="", encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))
            cols = list(rows[0].keys()) if rows else []
        else:
            rows = json.loads(fp.read_text(encoding="utf-8"))
            cols = list(rows[0].keys()) if rows and isinstance(rows[0], dict) else []
        return f"`{rel}`: {len(rows)} rows; columns: {', '.join(cols[:8])}"
    except (ValueError, OSError):
        return f"`{rel}` ({fp.stat().st_size} bytes)"


def sec_inputs(d: ReportData) -> str:
    c, ec = d.cfg, d.env
    pres = presence(list(ec.auth.env.values()), d.root) if ec.auth.env else {}
    L = ["## 3. Test inputs\n", "### 3.1 Target and access\n", kv([
        ("Base URL", ec.base_url), ("Frontend URL", ec.frontend_url or "-"),
        ("Allowed hosts (allowlist)", ", ".join(ec.allowed_hosts)),
        ("Ownership / permission to load test", f"{OK} confirmed" if ec.ownership_confirmed else f"{BAD} not confirmed"),
        ("Production target", "no" if not ec.production else "yes (refused)"),
        ("Authentication", ec.auth.type + (f" (login `{(ec.auth.login_request or {}).get('path')}`)" if ec.auth.type == "login_flow" else "")),
        ("Secret variables (names only)", ", ".join(f"`{v}` ({'set' if pres.get(v) else 'NOT set'})" for v in ec.auth.env.values()) or "none"),
        ("Telemetry sources", ", ".join(f"`{k}`: {v}" for k, v in ec.telemetry.items()) or "none configured"),
    ])]
    L.append("\n### 3.2 Specifications, data and requirements\n")
    L.append(kv([
        ("API specifications", ", ".join(f"{s.kind}: `{s.location}`" for s in c.api_specs) or "none provided (endpoints discovered by probe/crawl)"),
        ("Test data", "; ".join(_data_file_info(d.root, x) for x in c.test_data) or "none"),
        ("NFR/SLO documents", ", ".join(f"`{x}`" for x in c.nfr_files) or ("none exist (declared by user)" if c.nfrs_declared_none else "none")),
        ("Approved NFRs imported", str(len(d.nfrs))),
        ("Journeys", ", ".join(f"{j['name']} ({' → '.join(j.get('steps', []))})" for j in c.journeys) or "none"),
        ("Targets requested", ", ".join(f"`{t}`" for t in c.targets) or "all discovered safe (GET) endpoints"),
        ("Request overrides", ", ".join(f"`{k}` → `{v.get('path', '')}`" for k, v in c.request_overrides.items()) or "none"),
        ("Repository / service map", f"{c.repository or '-'} / {c.service_map or '-'}"),
    ]))
    if d.nfrs:
        L.append("\n**Approved NFRs / SLOs**\n")
        L.append(table(["ID", "Scope", "Metric", "Requirement"], [[n["id"], n["scope"], n["metric"], f"{n['comparator']} {n['threshold']} {n.get('unit') or ''}"] for n in d.nfrs]))
    L.append("\n### 3.3 Workload settings (effective policy)\n")
    pol = d.policy
    L.append(kv([
        ("Repeats per workload", f"{pol['benchmark']['repeats']} benchmark / {pol['feature']['repeats']} feature / {pol['frontend']['repeats']} frontend"),
        ("Characterization rates", f"{pol['benchmark']['characterization_rates']} req/s, {pol['benchmark']['step_duration_s']} s each"),
        ("Hard caps", f"{pol['limits']['max_arrival_rate']} req/s, {pol['limits']['max_vus']} VUs, {pol['limits']['max_duration_s']} s"),
        ("Safety aborts", f"error rate ≥ {pol['safety']['abort_error_rate'] * 100:.0f}% or p95 ≥ {pol['safety']['abort_p95_ms']} ms; generator CPU saturation {pol['safety']['generator_cpu_saturation_pct']}%"),
        ("Stability rule", f"p95 CV ≤ {pol['stability']['max_cv_p95']}, ≥ {pol['stability']['min_samples_per_run']} samples/run"),
        ("Regression thresholds", f"warn ≥ +{d.plan.regression_warn_pct.value}%, fail ≥ +{d.plan.regression_fail_pct.value}% (rank test α = {pol['regression']['significance_alpha']})"),
        ("Frontend lab budgets (POLICY)", ", ".join(f"{k} {v}" for k, v in pol["frontend"]["budgets"].items())),
    ]))
    L.append("\n### 3.4 Where the plan's numbers come from\n")
    L.append(table(["Source", "Count", "Meaning"], [[k, len(v), {"NFR": "approved requirement", "BASELINE": "approved baseline workload",
                                                                 "POLICY": "platform default", "ASSUMPTION": "needs confirmation"}[k]]
                                                     for k, v in d.view["values_by_source"].items()]))
    L.append("\n### 3.5 Approvals (human gates)\n")
    L.append(table(["Gate", "Approver", "When", "Content hash", "Reason"],
                   [[a.gate, a.approver, dt(a.created_at), f"`{a.subject_hash[:10]}`", a.reason or "-"] for a in d.approvals]))
    return "\n".join(L) + "\n"


def sec_environment(d: ReportData) -> str:
    tv = next((r.tool_versions for r in d.runs if r.tool_versions), {}) or {}
    ei = next((r.environment_info for r in d.runs if r.environment_info), {}) or {}
    bv = {}
    for r in d.runs:
        bv = (r.metrics or {}).get("browser_versions") or bv
    gens = [((r.metrics or {}).get("generator") or {}) for r in d.runs if r.test_type != "frontend"]
    L = ["## 4. Test environment and tooling\n", kv([
        ("Executor", ei.get("executor", "-")), ("Load generator OS", ei.get("os", "-")),
        ("CPU cores / memory", f"{ei.get('cpu_count', '-')} / {ei.get('mem_gb', '-')} GB"),
        ("k6", tv.get("k6", "-")), ("Node.js", tv.get("node", "-")), ("Playwright", tv.get("playwright", "-")),
        ("Lighthouse", tv.get("lighthouse", "-")), ("Chromium", bv.get("chromium", "-")), ("web-vitals", bv.get("web-vitals", "-")),
        ("Load generator CPU (p95 of runs, whole machine)", f(_median([g.get("cpu_pct_p95") for g in gens]), 1, " %")),
        ("Load generator CPU max", f(max([g.get("cpu_pct_max") or 0 for g in gens], default=None), 1, " %")),
        ("Load generator memory max", f(max([g.get("rss_mb_max") or 0 for g in gens], default=None), 0, " MB")),
    ])]
    return "\n".join(L) + "\n"


def sec_execution(d: ReportData) -> str:
    L = ["## 5. Test plan and execution\n"]
    plan_rows = []
    for t in d.plan.tests:
        ser = [s for s in d.series if s.target in t.targets and (t.name.split(":")[0] in (s.workload_key.split("@")[0], "frontend", "capacity", "diag-step", "smoke", "load", "peak"))]
        runs = [r for s in ser for r in d.runs_of(s.id) if r.spec.get("labels", {}).get("test") == t.name]
        done = sum(1 for r in runs if r.status == "completed")
        plan_rows.append([t.name, t.type.value, ", ".join(f"{s.rate.value:g} req/s × {s.duration_s.value}s" for s in t.workload) or "browser journey",
                          t.repeats.value, f"{done}/{len(runs)}" if runs else "not run",
                          f(sum((r.finished_at - r.started_at).total_seconds() for r in runs if r.finished_at and r.started_at), 0, " s")])
    L.append(table(["Test", "Type", "Workload", "Repeats", "Completed/started", "Wall time"], plan_rows))
    L.append("\n### Run log\n")
    rows = []
    for r in d.runs:
        m = r.metrics or {}
        rows.append([f"`{r.id[4:]}`", r.test_type, r.spec.get("labels", {}).get("test", "-"), r.repeat_index + 1,
                     {"completed": OK, "aborted": BAD, "failed": BAD, "rejected": BAD}.get(r.status, "") + " " + r.status,
                     dt(r.started_at), f((r.finished_at - r.started_at).total_seconds() if r.finished_at and r.started_at else None, 1, " s"),
                     m.get("aborted_reason", "") and str(m["aborted_reason"])[:60] or (r.error or "")[:60] or "-"])
    L.append(table(["Run", "Type", "Test", "#", "Status", "Started", "Duration", "Note"], rows))
    return "\n".join(L) + "\n"


def _api_series(d: ReportData):
    return [s for s in d.series if not s.workload_key.startswith("frontend@") and s.kind != "capacity"]


def sec_results_api(d: ReportData, html: bool) -> str:
    L = ["## 6. Results: API\n"]
    series = _api_series(d)
    if not series:
        return ""
    L.append("### 6.1 Summary per workload (median across repeats)\n")
    rows = []
    for s in series:
        runs = [r for r in d.runs_of(s.id) if r.status in ("completed", "aborted")]
        pt = [((r.metrics or {}).get("per_target") or {}).get(s.target) or {} for r in runs]
        m = lambda k: _median([x.get(k) for x in pt])  # noqa: E731
        rows.append([s.target, s.workload_key, s.kind, len(runs), f(m("count"), 0), f(m("p50")), f(m("p90")), f(m("p95")), f(m("p99")),
                     f(_median([x.get("max") for x in pt])), pct(m("error_rate")), f(m("rps")),
                     f(sum((r.metrics or {}).get("dropped_iterations", 0) for r in runs), 0)])
    L.append(table(["Target", "Workload", "Kind", "Runs", "Requests/run", "p50 ms", "p90 ms", "p95 ms", "p99 ms", "max ms", "Error rate", "Req/s", "Dropped iter."], rows))
    L.append("\n### 6.2 Throughput, data and concurrency\n")
    rows = []
    for s in series:
        runs = [r for r in d.runs_of(s.id) if r.status in ("completed", "aborted")]
        ms = [r.metrics or {} for r in runs]
        planned = float(s.workload_key.split("@")[1].split("rps")[0]) if "rps" in s.workload_key else None
        ach = _median([(m.get("per_target", {}).get(s.target) or {}).get("rps") for m in ms])
        rows.append([s.target, s.workload_key, f(planned), f(ach), f(100 * ach / planned if planned and ach else None, 0, " %"),
                     f(_median([m.get("vus_max") for m in ms]), 0), f(_median([m.get("data_sent_bytes") for m in ms]) / 1024 if ms and _median([m.get("data_sent_bytes") for m in ms]) else None, 0, " KB"),
                     f(_median([m.get("data_received_bytes") for m in ms]) / 1024 if ms and _median([m.get("data_received_bytes") for m in ms]) else None, 0, " KB")])
    L.append(table(["Target", "Workload", "Planned req/s", "Achieved req/s", "Achieved %", "Max VUs", "Data sent/run", "Data received/run"], rows))
    L.append("\n### 6.3 Per-repeat detail\n")
    for s in series:
        runs = [r for r in d.runs_of(s.id)]
        if not runs:
            continue
        rows = []
        vals, labs = [], []
        for r in runs:
            m = r.metrics or {}
            t = (m.get("per_target") or {}).get(s.target) or {}
            g = m.get("generator") or {}
            rows.append([r.repeat_index + 1, r.status, f(t.get("count"), 0), f(t.get("p50")), f(t.get("p95")), f(t.get("p99")),
                         pct(t.get("error_rate")), f(t.get("rps")), f(m.get("dropped_iterations"), 0), f(g.get("cpu_pct_p95"), 1, "%")])
            if t.get("p95") is not None:
                vals.append(t["p95"])
                labs.append(f"#{r.repeat_index + 1}")
        L.append(f"**{s.target}** ({s.workload_key}, {s.kind})\n")
        L.append(table(["#", "Status", "Requests", "p50", "p95", "p99", "Errors", "Req/s", "Dropped", "Gen CPU p95"], rows))
        if html and vals:
            bl = next((d.baseline(t["baseline_id"]) for t in _verdict_rows(d) if t["target"] == s.target and t.get("baseline_id")), None)
            refs = []
            if bl and bl.summary.get("p95") is not None:
                refs.append((f"baseline v{bl.version} p95", bl.summary["p95"], "#2e7d32"))
            for n in d.nfrs:
                if n["scope"] in (s.target, "*") and n["metric"].endswith("p95"):
                    refs.append((f"SLO {n['id']}", n["threshold"], "#c62828"))
            L.append(svg_bars(f"p95 latency per repeat: {s.target}", labs, vals, refs))
        L.append("")
    return "\n".join(L) + "\n"


def sec_capacity(d: ReportData) -> str:
    cap = [s for s in d.series if s.kind == "capacity"]
    if not cap:
        return ""
    L = ["## 7. Capacity characterization (stepped ladder)\n"]
    for s in cap:
        runs = d.runs_of(s.id)
        rows = []
        for r in runs:
            m = r.metrics or {}
            t = (m.get("per_target") or {}).get(s.target) or {}
            st = (r.spec.get("scenarios") or [{}])[0].get("stages", [{}])[0]
            rows.append([f(st.get("rate")), r.status, f(t.get("p95")), f(t.get("p99")), pct(t.get("error_rate")), f(m.get("dropped_iterations"), 0),
                         str(m.get("aborted_reason") or "-")[:70]])
        info = (s.stability or {}).get("capacity", {})
        L.append(f"**{s.target}**: last good rate **{f(info.get('last_good_rate'), 0)} req/s**; "
                 f"stopped at {f((info.get('stopped_at') or {}).get('rate'), 0)} req/s: {'; '.join((info.get('stopped_at') or {}).get('reasons', [])) or 'ladder completed'}\n")
        L.append(table(["Step req/s", "Status", "p95 ms", "p99 ms", "Errors", "Dropped", "Stop reason"], rows))
    return "\n".join(L) + "\n"


def sec_frontend(d: ReportData, html: bool) -> str:
    fs = [s for s in d.series if s.workload_key.startswith("frontend@")]
    if not fs:
        return ""
    L = ["## 8. Results: frontend journeys (real browser, lab)\n",
         "LCP loading · CLS visual stability · INP responsiveness (n/a without a qualifying interaction) · TBT lab proxy for blocking · "
         "FID is obsolete and not used. Lab results on one machine: use them for before/after comparison.\n"]
    keys = [("lcp_ms", "LCP ms"), ("cls", "CLS"), ("inp_ms", "INP ms"), ("tbt_ms", "TBT ms"), ("ttfb_ms", "TTFB ms"), ("fcp_ms", "FCP ms"),
            ("speed_index_ms", "Speed Index"), ("journey_ms", "Journey ms"), ("long_tasks", "Long tasks"), ("js_exec_ms", "JS exec ms"),
            ("resource_count", "Resources"), ("transfer_kb", "Transfer KB"), ("failed_requests", "Failed req."), ("console_errors", "Console err."),
            ("lh_performance_score", "Lighthouse")]
    bud = d.policy["frontend"]["budgets"]
    for s in fs:
        runs = [r for r in d.runs_of(s.id) if r.status == "completed"]
        L.append(f"### {s.target} ({s.kind}, {len(runs)} runs)\n")
        rows = []
        for k, lab in keys:
            vals = [((r.metrics or {}).get("frontend") or {}).get(s.target, {}).get(k) for r in runs]
            vals = [v for v in vals if v is not None]
            budget = {"lcp_ms": bud.get("lcp_ms"), "cls": bud.get("cls"), "tbt_ms": bud.get("tbt_ms"), "inp_ms": bud.get("inp_ms")}.get(k)
            med = _median(vals)
            status = "" if budget is None or med is None else (OK if med <= budget else BAD)
            rows.append([lab, f(med, 3 if k == "cls" or k == "lh_performance_score" else 1) if vals else "n/a (no data)", f(min(vals) if vals else None, 3 if k == "cls" else 1),
                         f(max(vals) if vals else None, 3 if k == "cls" else 1), f(budget) if budget is not None else "-", status])
        L.append(table(["Metric", "Median", "Min", "Max", "Lab budget (POLICY)", "Within"], rows))
        lh = []
        for r in runs[:1]:
            fp = d.root / (r.artifacts or {}).get("dir", "") / "journey-result.json"
            if fp.exists():
                ev = json.loads(fp.read_text(encoding="utf-8"))["evidence"]
                lh = ev.get("slowest_resources", [])[:5]
                if ev.get("console_errors"):
                    L.append("Console errors: " + "; ".join(ev["console_errors"][:3]) + "\n")
        if lh:
            L.append("Slowest resources (first run):\n")
            L.append(table(["Resource", "Type", "Duration ms", "Transfer KB"], [[f"`{x['name'][-70:]}`", x["type"], f(x["duration"], 0), f((x.get("transfer") or 0) / 1024, 0)] for x in lh]))
        if html:
            vals = [((r.metrics or {}).get("frontend") or {}).get(s.target, {}).get("lcp_ms") for r in runs]
            vals = [v for v in vals if v is not None]
            if vals:
                L.append(svg_bars(f"LCP per repeat: {s.target}", [f"#{i + 1}" for i in range(len(vals))], vals, [("budget", bud["lcp_ms"], "#c62828")]))
        L.append("")
    return "\n".join(L) + "\n"


def sec_success_failure(d: ReportData) -> str:
    L = ["## 9. Success and failure analysis\n", "### 9.1 Requests by outcome\n"]
    rows = []
    for s in _api_series(d) + [x for x in d.series if x.kind == "capacity"]:
        runs = [r for r in d.runs_of(s.id) if r.status in ("completed", "aborted")]
        total = failed = 0
        codes: dict[str, float] = {}
        for r in runs:
            t = ((r.metrics or {}).get("per_target") or {}).get(s.target) or {}
            n = t.get("count") or 0
            total += n
            failed += n * (t.get("error_rate") or 0)
            for k, v in (t.get("extra") or {}).items():
                if k.startswith("status_"):
                    codes[k[7:]] = codes.get(k[7:], 0) + v
        if total:
            rows.append([s.target, s.workload_key, f(total, 0), f(total - failed, 0), f(failed, 0), pct((total - failed) / total),
                         ", ".join(f"{c}: {int(v)}" for c, v in sorted(codes.items()))])
    L.append(table(["Target", "Workload", "Requests", "Successful", "Failed", "Success rate", "HTTP status counts"], rows))
    L.append("\n### 9.2 Run outcomes\n")
    cnt: dict[str, int] = {}
    for r in d.runs:
        cnt[r.status] = cnt.get(r.status, 0) + 1
    L.append(table(["Status", "Runs"], [[k, v] for k, v in sorted(cnt.items())]))
    ab = [(r, (r.metrics or {}).get("aborted_reason")) for r in d.runs if (r.metrics or {}).get("aborted_reason") or r.status in ("failed", "rejected")]
    if ab:
        L.append("\n**Aborted / failed / rejected runs**\n")
        L.append(table(["Run", "Status", "Reason"], [[f"`{r.id[4:]}`", r.status, (str(why) if why else (r.error or "-"))[:200]] for r, why in ab]))
    L.append("\n### 9.3 Verdict per target\n")
    vs = _verdict_rows(d)
    if vs:
        L.append(table(["Target", "Workload", "Verdict", "Reasons"], [[t["target"], t.get("workload_key", "-"), f"{VERDICT_ICON.get(t['verdict'], '')} {t['verdict']}", "; ".join(t.get("reasons", [])[:3])] for t in vs]))
    else:
        L.append("No pass/fail verdict: this is a benchmark (characterization). Verdicts appear after a baseline is approved and a feature is tested.\n")
    return "\n".join(L) + "\n"


def sec_analysis(d: ReportData) -> str:
    vs = _verdict_rows(d)
    L = ["## 10. SLO, baseline and trend analysis\n"]
    if not vs:
        stab = []
        for s in d.series:
            if s.kind == "benchmark" and s.stability:
                stab.append([s.target, s.workload_key, f(s.stability.get("runs") or s.stability.get("cv_p95")),
                             f(s.stability.get("cv_p95") if "cv_p95" in s.stability else None, 3),
                             f(s.stability.get("error_rate_spread"), 4), "yes" if s.stability.get("stable") else "no",
                             "; ".join(s.stability.get("reasons", [])) or "-"])
        L.append("### Stability of benchmark series (baseline eligibility)\n")
        L.append(table(["Target", "Workload", "Runs/CV", "p95 CV", "Error spread", "Stable", "Reasons"], stab))
        return "\n".join(L) + "\n"
    L.append("SLO (approved requirement), baseline (approved reference) and trend (history) are evaluated **separately**.\n")
    for t in vs:
        L.append(f"### {t['target']}: {VERDICT_ICON.get(t['verdict'], '')} {t['verdict']}\n")
        bl = d.baseline(t.get("baseline_id"))
        if bl:
            L.append(f"Baseline: `{bl.id}` v{bl.version}, created {dt(bl.created_at)}, workload `{bl.workload_key}`.\n")
        if t.get("slo"):
            L.append("**SLO**\n")
            L.append(table(["NFR", "Metric", "Requirement", "Observed", "Result"], [[s["nfr_id"], s["metric"], f"{s.get('comparator', '<')} {s['threshold']}", f(s["observed"]), (OK + " pass") if s["passed"] else (BAD + " fail") if s["passed"] is False else "n/a"] for s in t["slo"]]))
        else:
            L.append("**SLO:** none approved for this target, so no SLO verdict is possible.\n")
        if t.get("regression"):
            L.append("**Baseline comparison**\n")
            rows = []
            for r in t["regression"]:
                ci = r.get("ci_pct")
                rows.append([r["metric"], f(r["baseline"], 3 if r["metric"] == "cls" else 1), f(r["current"], 3 if r["metric"] == "cls" else 1),
                             f(r.get("delta_pct"), 1, "%"), f(r.get("p_value"), 3) if r.get("p_value") is not None else "-",
                             f"{ci[0]:.0f}% … {ci[1]:.0f}%" if ci else "-", {"fail": BAD + " fail", "warn": WARN + " warn", "none": OK + " none"}.get(r["level"], r["level"])])
            L.append(table(["Metric", "Baseline", "Current", "Delta", "p-value (rank)", "95% CI of delta", "Level"], rows))
        else:
            L.append("**Baseline comparison:** none (no approved baseline for this workload).\n")
        if t.get("lab_budgets"):
            L.append("**Lab budgets (POLICY, not NFRs)**\n")
            L.append(table(["Metric", "Budget", "Observed", "Within"], [[b["metric"], b["budget"], f(b["observed"], 3 if b["metric"] == "cls" else 1), OK if b["within"] else BAD] for b in t["lab_budgets"]]))
        tr = t.get("trend") or {}
        if tr.get("drift_pct") is not None:
            L.append(f"**Trend (informational):** p95 drift {f(tr['drift_pct'], 1, '%')} over {int(tr['n'])} series; recent medians {', '.join(f(x) for x in tr.get('recent_p95_medians', []))}. The baseline is never moved by trend.\n")
        st = t.get("stability") or {}
        if st:
            L.append(f"**Measurement validity:** stable = {st.get('stable')}" + (f", CV p95 = {f(st.get('cv_p95'), 3)}" if st.get("cv_p95") is not None else "") + (f"; {'; '.join(st.get('reasons', []))}" if st.get("reasons") else "") + "\n")
    return "\n".join(L) + "\n"


def _tele_summary(d: ReportData) -> str:
    L = ["## 11. Server-side telemetry\n"]
    rows = []
    for s in d.series:
        runs = [r for r in d.runs_of(s.id) if r.status == "completed"]
        teles = [(r.metrics or {}).get("telemetry") or {} for r in runs]
        if not any(len(t) > 2 for t in teles):
            continue
        agg: dict[str, list[float]] = {}
        for t in teles:
            for src, vals in t.items():
                if isinstance(vals, dict):
                    for k, v in vals.items():
                        if isinstance(v, (int, float)) and (k.endswith(("cpu_pct_p95", "rss_mb_max")) or ":mean_ms" in k and "request_duration" in k or "upstream_latency" in k and ":mean_ms" in k or "injected_delay" in k and ":mean_ms" in k):
                            agg.setdefault(f"{src}: {k[:90]}", []).append(float(v))
        for k, v in list(agg.items())[:8]:
            rows.append([s.target, s.workload_key, f"`{k}`", f(_median(v), 2)])
    if not rows:
        return L[0] + "No server-side telemetry was collected (configure `telemetry` for the environment to enable CPU/memory, latency decomposition and diagnosis).\n"
    L.append(table(["Series", "Workload", "Metric (median across repeats)", "Value"], rows))
    return "\n".join(L) + "\n"


def sec_diagnosis(d: ReportData) -> str:
    if not d.diag:
        return ""
    L = [f"## 12. Diagnosis (iteration {d.diag.get('iteration', 1)})\n"]
    for h in d.diag["hypotheses"]:
        icon = {"supported": OK, "refuted": "➖", "insufficient_evidence": WARN}.get(h["status"], "")
        L.append(f"- {icon} **[{h['status']}, {h['confidence']}]** {h['claim']}")
        for e in h["evidence"]:
            L.append(f"  - evidence ({e.get('source')}): {e.get('detail')}")
        if not h["evidence"]:
            L.append(f"  - insufficient evidence. Next: {h.get('next_test') or 'collect telemetry'}")
        elif h.get("next_test"):
            L.append(f"  - next test: {h['next_test']}")
    if d.diag.get("diagnostic_plan_id"):
        L.append(f"\nProposed Diagnostic Plan `{d.diag['diagnostic_plan_id']}` (needs approval, gate G6).")
    return "\n".join(L) + "\n"


def observations(d: ReportData) -> tuple[list[str], list[str]]:
    obs: list[str] = []
    rec: list[str] = []
    pol = d.policy
    a = d.analysis or {}
    if not d.nfrs:
        obs.append("No approved NFRs/SLOs exist: no requirement-based pass/fail is possible. Results are baseline-relative only.")
        rec.append("Agree and import NFRs (`perf import-nfrs`) so SLO verdicts become possible.")
    for s in d.series:
        runs = [r for r in d.runs_of(s.id) if r.status in ("completed", "aborted")]
        pt = [((r.metrics or {}).get("per_target") or {}).get(s.target) or {} for r in runs]
        if not pt or s.workload_key.startswith("frontend@"):
            continue
        p95, p99, p50 = _median([x.get("p95") for x in pt]), _median([x.get("p99") for x in pt]), _median([x.get("p50") for x in pt])
        if p95 and p99 and p99 / p95 > 3:
            obs.append(f"{s.target}: heavy latency tail (p99 {p99:.0f} ms is {p99 / p95:.1f}× p95).")
        if p50 and p95 and p95 / p50 > 4:
            obs.append(f"{s.target}: wide latency spread (p95 {p95:.0f} ms vs median {p50:.0f} ms).")
        mn = min((x.get("count") or 0) for x in pt)
        if mn < pol["stability"]["min_samples_per_run"]:
            obs.append(f"{s.target} ({s.workload_key}): only {mn} samples in the smallest run; statistics are weak below {pol['stability']['min_samples_per_run']}.")
        ach = _median([x.get("rps") for x in pt])
        if "rps" in s.workload_key and ach:
            planned = float(s.workload_key.split("@")[1].split("rps")[0])
            if ach < 0.95 * planned and s.kind != "smoke":
                obs.append(f"{s.target}: achieved {ach:.1f} req/s of planned {planned:g} ({100 * ach / planned:.0f}%): target or generator could not sustain the rate.")
        errs = [x.get("error_rate") or 0 for x in pt]
        if max(errs) > 0:
            obs.append(f"{s.target} ({s.workload_key}): errors observed, max {max(errs) * 100:.2f}% in a run.")
        if sum((r.metrics or {}).get("dropped_iterations", 0) for r in runs):
            obs.append(f"{s.target} ({s.workload_key}): dropped iterations: the arrival rate was not sustained; results may be limited by the client or target.")
            rec.append("Re-run at a lower rate or from a dedicated load generator.")
        g = _median([((r.metrics or {}).get("generator") or {}).get("cpu_pct_p95") for r in runs])
        if g and g > 60:
            obs.append(f"{s.target}: load-generator CPU high ({g:.0f}%): measurement may be client-limited.")
        if s.kind == "benchmark" and s.stability and not s.stability.get("stable", True):
            obs.append(f"{s.target} ({s.workload_key}): benchmark series is NOT stable ({'; '.join(s.stability.get('reasons', []))}); it cannot be promoted to a baseline.")
            rec.append(f"Re-run the benchmark for {s.target} on a quiet machine/environment before baselining.")
        elif s.kind == "benchmark" and s.stability and s.stability.get("stable"):
            rec.append(f"{s.target} ({s.workload_key}) is stable: consider promoting it to a baseline (`/perf:baseline`) after review.")
    for t in _verdict_rows(d):
        if t.get("frontend"):
            for b in t.get("lab_budgets", []):
                if not b["within"]:
                    obs.append(f"{t['target']}: {b['metric']} {b['observed']:.1f} exceeds the lab budget {b['budget']}.")
            cur = t.get("current", {})
            if cur.get("failed_requests"):
                obs.append(f"{t['target']}: {int(cur['failed_requests'])} failed network requests during the journey.")
            if cur.get("console_errors"):
                obs.append(f"{t['target']}: {int(cur['console_errors'])} console errors.")
            if cur.get("inp_ms") is None:
                obs.append(f"{t['target']}: INP not reported (no qualifying interaction in the journey); responsiveness is not measured.")
        if t["verdict"] == "FAIL_REGRESSION":
            obs.append(f"{t['target']}: material regression vs approved baseline ({'; '.join(t['reasons'][:2])}).")
            rec.append(f"Do not release {d.plan.feature_id or 'the change'} for {t['target']} before the cause is understood (`/perf:diagnose {d.plan.id}`).")
        elif t["verdict"] == "PASS_WITH_REGRESSION_WARNING":
            obs.append(f"{t['target']}: within SLO but regressed vs baseline ({'; '.join(t['reasons'][:2])}).")
            rec.append(f"Review {t['target']}: the margin to the SLO is shrinking; do not move the baseline.")
        elif t["verdict"] == "FAIL_SLO":
            rec.append(f"{t['target']}: SLO breached: fix before release.")
        elif t["verdict"] == "INCONCLUSIVE":
            rec.append(f"{t['target']}: verdict inconclusive: {'; '.join(t.get('reasons', [])[:2])}.")
        if not t.get("baseline_id") and d.plan.workflow == "feature":
            rec.append(f"No approved baseline for {t['target']}: run `/perf:benchmark`, review, and promote a stable series so regressions can be measured.")
    if d.diag:
        for h in d.diag["hypotheses"]:
            if h["status"] == "supported" and h["evidence"]:
                rec.append(f"Investigate: {h['claim']}.")
            elif h["status"] == "insufficient_evidence":
                rec.append(f"Collect evidence: {h.get('next_test') or 'configure telemetry'}.")
    if any((r.environment_info or {}).get("label") == "local-lab" for r in d.runs):
        obs.append("Results come from a local-lab environment (load generator, browser and app on one machine): suitable for before/after comparison, not for production capacity claims.")
    if not obs:
        obs.append("No anomalies detected by the automated checks.")
    seen = set()
    rec = [x for x in rec if not (x in seen or seen.add(x))]
    return obs, rec


def sec_observations(d: ReportData) -> str:
    obs, rec = observations(d)
    return ("## 13. Observations and recommendations\n\n### Observations\n\n" + "\n".join(f"- {o}" for o in obs) +
            "\n\n### Recommendations\n\n" + ("\n".join(f"{i}. {r}" for i, r in enumerate(rec, 1)) or "None.") + "\n")


def sec_limits(d: ReportData) -> str:
    L = ["## 14. Assumptions, limitations and caveats\n"]
    ass = d.view["unconfirmed_assumptions"]
    L.append("- ASSUMPTION values in the plan: " + (", ".join(ass) if ass else "none (all values come from NFR, BASELINE or POLICY)."))
    L.append("- Statistical power is limited with few repeats; the report states when a delta rests on a bootstrap CI instead of a rank test.")
    L.append("- Percentiles are recomputed from raw k6 samples; frontend metrics are lab measurements (single machine, desktop profile).")
    L.append("- Load is generated only against allowlisted hosts under the caps listed in 3.3; Kubernetes/cloud execution is not part of this run.")
    for n in d.plan.notes:
        L.append(f"- plan note: {n}")
    return "\n".join(L) + "\n"


def sec_appendix(d: ReportData) -> str:
    L = ["## Appendix\n", "### A. Evidence and artifacts\n"]
    rows = [[f"`{r.id[4:]}`", r.test_type, f"`{r.spec_hash[:10]}`", f"`{(r.artifacts or {}).get('dir', '-')}`"] for r in d.runs]
    L.append(table(["Run", "Type", "Spec hash", "Artifact folder (script.js, spec.json, raw.ndjson, trace.zip, network.har, lighthouse-*.json)"], rows))
    L.append("\n### B. Reproduce\n")
    L.append("```bash\n"
             f"uv run perf status {d.plan_id}\n"
             f"uv run perf export {d.plan_id} --out ./perf-export     # k6 scripts + data + run scripts for manual execution\n"
             f"uv run perf run {d.plan_id}                            # re-run (plan must be approved)\n```\n")
    L.append("### C. Audit trail (this plan)\n")
    ids = {d.plan_id, *[r.id for r in d.runs]}
    au = [a for a in d.reg.audit_log(500) if a.subject in ids][::-1]
    L.append(table(["Time", "Actor", "Action", "Subject"], [[dt(a.at), a.actor, a.action, f"`{a.subject}`"] for a in au[:60]]))
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------- assemble


CSS = """body{font-family:system-ui,Segoe UI,Arial,sans-serif;max-width:1180px;margin:24px auto;padding:0 20px;color:#1b1f24;background:#fff;line-height:1.45}
h1{border-bottom:3px solid #4f7cff;padding-bottom:6px}h2{margin-top:34px;border-bottom:1px solid #ddd;padding-bottom:4px}h3{margin-top:22px}
table{border-collapse:collapse;width:100%;margin:10px 0;font-size:13px}th,td{border:1px solid #d7dbe0;padding:4px 8px;text-align:left;vertical-align:top}
th{background:#f1f4f9}tr:nth-child(even) td{background:#fafbfc}code{background:#f1f3f5;padding:1px 4px;border-radius:3px;font-size:12px}
pre{background:#f6f8fa;padding:10px;border-radius:6px;overflow:auto}svg{margin:8px 0}@media print{body{max-width:none;margin:0}h2{page-break-before:auto}}"""


def build_report(engine, plan_id: str, html: bool = False) -> str:
    d = ReportData(engine, plan_id)
    parts = [sec_header(d), sec_executive(d), sec_scope(d), sec_inputs(d), sec_environment(d), sec_execution(d),
             sec_results_api(d, html), sec_capacity(d), sec_frontend(d, html), sec_success_failure(d), sec_analysis(d), _tele_summary(d),
             sec_diagnosis(d), sec_observations(d), sec_limits(d), sec_appendix(d)]
    md = "\n".join(p for p in parts if p)
    if not html:
        return md
    import markdown

    body = markdown.markdown(md, extensions=["tables", "fenced_code", "sane_lists"])
    return f"<!doctype html><html><head><meta charset=utf-8><title>Performance Test Report {d.plan_id}</title><style>{CSS}</style></head><body>{body}</body></html>"
