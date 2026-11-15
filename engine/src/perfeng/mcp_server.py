"""perf-engine MCP server (stdio). The plugin's skills drive the workflow through these tools.

Safety model:
- Tools validate every input against schemas and policy; the agent cannot pass free-form load scripts.
- `approve_*` tools are gated by the plugin's PreToolUse hook (permissionDecision "ask"), so a human
  confirms in Claude Code's own permission prompt. Execution refuses without a matching approval.
- No tool ever returns secret values; configuration accepts only env var NAMES.
- Long runs execute as background jobs (`start_run` + `run_status`) so tool calls stay short.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

from perfeng import __version__

mcp = MCPServer(
    name="perf-engine",
    version=__version__,
    instructions=(
        "Deterministic performance-engineering engine. Workflow: intake_status -> configure_project -> discover -> "
        "plan_benchmark/plan_feature -> show plan_view to the user -> approve_plan (human prompt) -> start_run -> run_status -> "
        "get_analysis -> diagnose. Never invent NFRs. Never ask users to paste secrets: they go in the project .env file."
    ),
)


def _eng():
    from perfeng.orchestrator.engine import Engine

    return Engine()


def _safe(fn, *a, **kw) -> dict[str, Any]:
    try:
        r = fn(*a, **kw)
        return json.loads(json.dumps(r, default=str)) if not isinstance(r, dict) else json.loads(json.dumps(r, default=str))
    except Exception as e:  # noqa: BLE001 - surfaced to the agent as structured error
        return {"error": f"{type(e).__name__}: {e}"}


# ------------------------------------------------------------------ intake / configuration
@mcp.tool()
def intake_status(workflow: str = "benchmark", environment: str | None = None) -> dict[str, Any]:
    """What is known and what must still be asked (target, ownership, API spec, test data, auth env var names,
    NFRs, repository, journeys, telemetry). Includes the secrets rule to tell the user."""
    return _safe(lambda: _eng().intake_status(workflow, environment))


@mcp.tool()
def configure_project(application_id: str, environment: str, base_url: str, ownership_confirmed: bool,
                      frontend_url: str | None = None, allowed_hosts: list[str] | None = None) -> dict[str, Any]:
    """Create/update the project + environment. ownership_confirmed must reflect the USER's explicit answer that
    they own or may load test the target. Changing an existing target is audited (gate G7)."""
    def f():
        e = _eng()
        path = e.init_project(application_id, environment, base_url, ownership_confirmed=ownership_confirmed,
                              allowed_hosts=allowed_hosts, frontend_url=frontend_url, actor="agent-on-behalf-of-user")
        return {"written": str(path), "env_hygiene": ".gitignore has .env; .env.example created",
                "secrets_rule": __import__("perfeng.orchestrator.engine", fromlist=["SECRETS_RULE"]).SECRETS_RULE}
    return _safe(f)


@mcp.tool()
def set_project_inputs(api_specs: list[dict[str, str]] | None = None, test_data: list[str] | None = None,
                       nfr_file: str | None = None, nfrs_none: bool | None = None, auth: dict[str, Any] | None = None,
                       auth_environment: str | None = None, journeys: list[dict[str, Any]] | None = None,
                       journeys_none: bool | None = None, api_spec_none: bool | None = None, test_data_none: bool | None = None,
                       repository: str | None = None, targets: list[str] | None = None,
                       request_overrides: dict[str, dict[str, Any]] | None = None,
                       telemetry: dict[str, str] | None = None) -> dict[str, Any]:
    """Record intake answers. api_specs: [{kind: openapi|postman|graphql|har|proto, location: path-or-url}].
    auth: {type, header?, env: {username|password|token|client_id|client_secret: ENV_VAR_NAME}, token_url?, login_request?}
    — ENV VAR NAMES ONLY, values live in .env. test_data: workspace paths to CSV/JSON. journeys: [{name, steps, spec?}]."""
    def f():
        from perfeng.policy.gate import find_literal_secrets

        e = _eng()
        payload = {"auth": auth, "request_overrides": request_overrides, "telemetry": telemetry}
        if find_literal_secrets(payload):
            raise ValueError("refused: values look like secrets. Ask the user to put them in .env and give only the variable names.")
        fields: dict[str, Any] = {}
        if api_specs is not None:
            fields["api_specs"] = api_specs
        if api_spec_none is not None:
            fields["api_spec_declared_none"] = api_spec_none
        if test_data is not None:
            fields["test_data"] = test_data
        if test_data_none is not None:
            fields["test_data_declared_none"] = test_data_none
        if nfrs_none is not None:
            fields["nfrs_declared_none"] = nfrs_none
        if journeys is not None:
            fields["journeys"] = journeys
        if journeys_none is not None:
            fields["journeys_declared_none"] = journeys_none
        if repository is not None:
            fields["repository"] = repository
        if targets is not None:
            fields["targets"] = targets
        if request_overrides is not None:
            fields["request_overrides"] = request_overrides
        cfg = e.require_cfg()
        env_name = auth_environment or (next(iter(cfg.environments)) if cfg.environments else None)
        if auth is not None:
            fields["auth_declared"] = True
            fields[f"env.{env_name}.auth"] = auth
        if telemetry is not None:
            fields[f"env.{env_name}.telemetry"] = telemetry
        e.update_project(actor="agent-on-behalf-of-user", **fields)
        out: dict[str, Any] = {"updated": sorted(fields)}
        if nfr_file:
            out["nfrs_imported"] = len(e.import_nfrs(nfr_file, actor="agent-on-behalf-of-user"))
        out["intake"] = e.intake_status()
        return out
    return _safe(f)


@mcp.tool()
def discover(environment: str, crawl: bool = True) -> dict[str, Any]:
    """Discover endpoints (from provided specs, probed OpenAPI, bounded same-origin GET crawl), frontend routes,
    existing k6/Playwright/Lighthouse tests and approved NFRs. Never invents requirements."""
    def f():
        d = _eng().discover(environment, crawl)
        d["endpoints"] = [{k: e[k] for k in ("method", "path", "source", "confidence")} for e in d["endpoints"]][:200]
        return d
    return _safe(f)


# ------------------------------------------------------------------ planning
@mcp.tool()
def plan_benchmark(environment: str, targets: list[str] | None = None, include_capacity: bool = False,
                   journeys: list[str] | None = None) -> dict[str, Any]:
    """Create a Benchmark plan (characterization at policy rates, repeated runs). include_capacity adds a capped
    step ladder that needs high-risk approval. Returns the plan view with provenance for the user."""
    return _safe(lambda: (lambda e: e.plan_view(e.plan_benchmark(environment, targets, include_capacity, journeys).id))(_eng()))


@mcp.tool()
def plan_feature(feature_id: str, environment: str, repository: str | None = None, base: str | None = None,
                 head: str | None = None, include_peak: bool = False) -> dict[str, Any]:
    """Build the Feature Impact Model (work item + git diff + service map) and a Performance Plan that reuses the
    approved baseline workload. Returns the plan view (impact, tests, value provenance, assumptions)."""
    return _safe(lambda: (lambda e: e.plan_view(e.plan_feature(feature_id, environment, repository, base, head, include_peak).id))(_eng()))


@mcp.tool()
def plan_view(plan_id: str) -> dict[str, Any]:
    """Show a plan for human review: tests, workloads, where each value comes from, assumptions, high-risk tests."""
    return _safe(lambda: _eng().plan_view(plan_id))


@mcp.tool()
def revise_plan(plan_id: str, overrides: dict[str, Any], rationale: str) -> dict[str, Any]:
    """Propose changes (e.g. {"tests.<name>.repeats": 3, "tests.<name>.step0.rate": 30}). Every override becomes an
    ASSUMPTION that the user must confirm; it never becomes an NFR. Creates plan vN+1."""
    return _safe(lambda: (lambda e: e.plan_view(e.revise_plan(plan_id, overrides, rationale).id))(_eng()))


# ------------------------------------------------------------------ human gates (PreToolUse hook -> ask)
@mcp.tool()
def approve_plan(plan_id: str, approver: str, confirm_assumptions: bool = False, approve_high_risk: bool = False,
                 reason: str | None = None) -> dict[str, Any]:
    """HUMAN GATE (G2/G3/G4/G6). Call only after showing plan_view and the user said yes in chat. Claude Code will
    also ask the user to confirm this call. approver = the user's name."""
    return _safe(lambda: _eng().approve_plan(plan_id, approver, confirm_assumptions, approve_high_risk, reason))


@mcp.tool()
def approve_baseline(series_id: str, approver: str, reason: str) -> dict[str, Any]:
    """HUMAN GATE (G5). Promote a STABLE benchmark series to a new baseline version. Never call after a feature run
    just because it passed; baselines move only by explicit human decision."""
    return _safe(lambda: _eng().approve_baseline(series_id, approver, reason))


# ------------------------------------------------------------------ execution (background jobs)
def _jobs_dir() -> Path:
    from perfeng.config import perf_dir

    d = perf_dir() / "jobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


@mcp.tool()
def start_run(plan_id: str, only: list[str] | None = None, wait_seconds: int = 0) -> dict[str, Any]:
    """Execute an APPROVED plan as a background job (validated TestRunSpecs only). Returns job_id; poll run_status.
    wait_seconds (<= 900) blocks until the job finishes or the wait elapses."""
    def f():
        e = _eng()
        if e._plan_approval(plan_id) is None:
            return {"error": "plan is not approved (gate G2). Show plan_view to the user and ask for approval first."}
        job = f"job_{plan_id}_{int(time.time())}"
        jf = _jobs_dir() / f"{job}.json"
        log = _jobs_dir() / f"{job}.log"
        cmd = [sys.executable, "-m", "perfeng.cli", "run", plan_id, "--json"] + sum([["--only", o] for o in (only or [])], [])
        flags = 0x00000200 | 0x00000008 if os.name == "nt" else 0  # CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS
        with open(log, "w", encoding="utf-8") as lf, open(jf.with_suffix(".out"), "w", encoding="utf-8") as of:
            p = subprocess.Popen(cmd, stdout=of, stderr=lf, env={**os.environ, "PERF_PROJECT_DIR": str(e.root)},
                                 creationflags=flags, start_new_session=os.name != "nt")
        jf.write_text(json.dumps({"job_id": job, "plan_id": plan_id, "pid": p.pid, "started": time.time()}), encoding="utf-8")
        if wait_seconds:
            deadline = time.time() + min(wait_seconds, 900)
            while time.time() < deadline and p.poll() is None:
                time.sleep(2)
        return run_status(job)
    return _safe(f)


@mcp.tool()
def run_status(job_id: str) -> dict[str, Any]:
    """Progress / result of a background run job."""
    def f():
        jf = _jobs_dir() / f"{job_id}.json"
        if not jf.exists():
            return {"error": f"unknown job {job_id}"}
        meta = json.loads(jf.read_text(encoding="utf-8"))
        import psutil

        running = psutil.pid_exists(meta["pid"]) and psutil.Process(meta["pid"]).status() != psutil.STATUS_ZOMBIE
        prog = (jf.with_suffix(".log")).read_text(encoding="utf-8", errors="replace").strip().splitlines()
        out = {"job_id": job_id, "plan_id": meta["plan_id"], "running": running, "progress": prog[-8:],
               "elapsed_s": round(time.time() - meta["started"])}
        res = jf.with_suffix(".out")
        if not running and res.exists() and res.stat().st_size:
            try:
                full = json.loads(res.read_text(encoding="utf-8"))
                a = full["analysis"]
                out["overall_verdict"] = a.get("overall_verdict")
                out["analysis_id"] = a.get("analysis_id")
                out["summary"] = _summarize(a)
            except (json.JSONDecodeError, KeyError):
                out["error"] = "run failed: " + "\n".join(prog[-5:])
        elif not running:
            out["error"] = "run ended without result: " + "\n".join(prog[-5:])
        return out
    return _safe(f)


def _summarize(a: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for t in a.get("targets", []):
        r = {"target": t["target"], "kind": t.get("kind"), "verdict": t["verdict"], "reasons": t.get("reasons", [])[:4],
             "series_id": t.get("series_id"), "promotion_eligible": t.get("promotion_eligible")}
        cur = t.get("current", {})
        r["current"] = {k: cur.get(k) for k in ("p50", "p95", "p99", "error_rate", "rps", "lcp_ms", "cls", "inp_ms", "tbt_ms", "journey_ms") if k in cur}
        r["regression"] = [{k: x.get(k) for k in ("metric", "baseline", "current", "delta_pct", "level")} for x in t.get("regression", [])]
        rows.append(r)
    return rows


# ------------------------------------------------------------------ results
@mcp.tool()
def get_analysis(plan_id: str) -> dict[str, Any]:
    """Deterministic analysis (SLO vs baseline vs trend, stability, verdicts) and a Markdown rendering."""
    def f():
        from perfeng.report.markdown import render_analysis

        a = _eng().latest_analysis(plan_id)
        if not a:
            return {"error": "no analysis yet"}
        return {"overall_verdict": a.get("overall_verdict"), "summary": _summarize(a),
                "characterization": a.get("characterization"), "markdown": render_analysis(a)}
    return _safe(f)


@mcp.tool()
def diagnose(plan_id: str) -> dict[str, Any]:
    """Evidence-backed hypotheses (each lists its evidence; 'insufficient_evidence' when none) and, when useful,
    a Diagnostic Plan vN that needs approve_plan (G6) before start_run."""
    def f():
        from perfeng.diagnostics.loop import diagnose as dg
        from perfeng.report.markdown import render_diagnosis

        d = dg(_eng(), plan_id)
        d["markdown"] = render_diagnosis(d)
        return d
    return _safe(f)


@mcp.tool()
def status(ident: str) -> dict[str, Any]:
    """Status of a run / series / plan / baseline / analysis id."""
    return _safe(lambda: _eng().status(ident))


@mcp.tool()
def compare(a: str, b: str) -> dict[str, Any]:
    """Compare two runs/series/baselines (b relative to a), with deltas and a one-sided rank test."""
    return _safe(lambda: _eng().compare(a, b))


@mcp.tool()
def history(target: str | None = None, limit: int = 20) -> dict[str, Any]:
    """Performance history for the app or one target."""
    return _safe(lambda: {"series": _eng().history(target, limit)})


@mcp.tool()
def baselines(plan_id: str | None = None) -> dict[str, Any]:
    """Active/retired baselines plus benchmark series eligible for promotion (eligibility != promotion)."""
    return _safe(lambda: (lambda e: {"baselines": e.list_baselines(), "candidates": e.baseline_candidates(plan_id)})(_eng()))


@mcp.tool()
def write_report(plan_id: str, path: str | None = None, html: bool = True) -> dict[str, Any]:
    """Write the professional performance test report (inputs, environment, execution, results, success/failure,
    SLO/baseline analysis, telemetry, diagnosis, observations, recommendations, appendix) as Markdown, plus a
    self-contained HTML version with charts, into the project."""
    def f():
        from perfeng.report.markdown import plan_report

        e = _eng()
        p = Path(path) if path else e.root / ".perf" / "reports" / f"{plan_id}.md"
        p = p if p.is_absolute() else e.root / p
        if not p.resolve().is_relative_to(e.root):
            raise ValueError("report path must be inside the project")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(plan_report(e, plan_id), encoding="utf-8")
        out = {"written": str(p.relative_to(e.root))}
        if html:
            h = p.with_suffix(".html")
            h.write_text(plan_report(e, plan_id, html=True), encoding="utf-8")
            out["written_html"] = str(h.relative_to(e.root))
        return out
    return _safe(f)


@mcp.tool()
def export_scripts(plan_id: str, out_dir: str | None = None) -> dict[str, Any]:
    """Export the plan's generated k6 scripts, test data, Playwright journeys and run helpers into a bundle the user can
    run MANUALLY. Manual runs bypass the engine's safety layers (allowlist, approval record, watchdog, analysis): tell
    the user so. Secrets are exported as variable NAMES only (.env.example)."""
    def f():
        from perfeng.report.export import export_plan

        e = _eng()
        p = Path(out_dir) if out_dir else e.root / ".perf" / "export" / plan_id
        p = p if p.is_absolute() else e.root / p
        if not p.resolve().is_relative_to(e.root):
            raise ValueError("export path must be inside the project")
        r = export_plan(e, plan_id, p)
        r["out"] = str(p.relative_to(e.root))
        r["warning"] = "Manual runs bypass the allowlist, human approval record, watchdog, and analysis. Only run against targets you may test."
        return r
    return _safe(f)


@mcp.tool()
def doctor() -> dict[str, Any]:
    """Toolchain status (k6, node, Playwright, Chromium, Lighthouse, optional Docker)."""
    from perfeng.toolchain.installer import doctor as d

    return {"tools": [r.__dict__ for r in d()]}


@mcp.tool()
def setup_toolchain(browser: bool = False) -> dict[str, Any]:
    """Install pinned k6 (SHA-256 verified) and optionally Playwright/Chromium/Lighthouse. No Docker needed."""
    def f():
        from perfeng.toolchain.installer import install_browser, install_k6

        out = {"k6": str(install_k6())}
        if browser:
            out["browser"] = str(install_browser())
        return out
    return _safe(f)


def main() -> None:
    mcp.run("stdio")


if __name__ == "__main__":
    main()
