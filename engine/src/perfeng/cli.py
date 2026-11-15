"""`perf` CLI — thin wrapper over the deterministic Engine. JSON output with --json.

Human gates: `perf approve` and `perf baseline promote` must be run by a person (they ask for
an approver name and an interactive confirmation unless --yes is given by that person).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import typer

for _s in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

app = typer.Typer(help="Automated performance engineering: benchmark systems, test features, compare runs.",
                  no_args_is_help=True, add_completion=False)
baseline_app = typer.Typer(help="Approved baselines (benchmark != baseline).", no_args_is_help=True)
app.add_typer(baseline_app, name="baseline")


def _engine(root: Optional[Path] = None):
    from perfeng.orchestrator.engine import Engine

    return Engine(root)


def _out(obj, as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps(obj, indent=2, default=str))
        return
    from perfeng.report.markdown import render

    typer.echo(render(obj))


def _fail(e: Exception) -> None:
    typer.secho(f"error: {e}", fg="red", err=True)
    raise typer.Exit(1)


@app.command()
def setup(browser: bool = typer.Option(False, "--browser", help="also install Playwright/Chromium/Lighthouse")):
    """Install the pinned toolchain (k6; optionally browser tools). No Docker required."""
    from perfeng.toolchain.installer import install_browser, install_k6

    typer.echo(f"k6: {install_k6()}")
    if browser:
        typer.echo(f"browser tools: {install_browser()}")


@app.command()
def doctor(as_json: bool = typer.Option(False, "--json")):
    """Check toolchain and environment."""
    from perfeng.toolchain.installer import doctor as d

    rows = [r.__dict__ for r in d()]
    if as_json:
        typer.echo(json.dumps(rows, indent=2))
    else:
        for r in rows:
            typer.echo(f"{'OK ' if r['ok'] else 'MISSING'}  {r['name']:<18} {r['version'] or ''}  {r['detail'] or ''}")
    if not all(r["ok"] for r in rows if r["name"] == "k6"):
        raise typer.Exit(1)


@app.command()
def init(app_id: str = typer.Option(..., "--app"), env: str = typer.Option(..., "--env"),
         base_url: str = typer.Option(..., "--base-url"), frontend_url: Optional[str] = typer.Option(None),
         i_own_this_target: bool = typer.Option(False, "--i-own-this-target",
                                                help="confirm you own / are permitted to load test this target")):
    """Configure a project + environment (.perf/project.yaml). Never put secrets here."""
    try:
        f = _engine().init_project(app_id, env, base_url, ownership_confirmed=i_own_this_target, frontend_url=frontend_url)
        typer.echo(f"wrote {f}")
    except Exception as e:  # noqa: BLE001
        _fail(e)


@app.command()
def intake(workflow: str = "benchmark", env: Optional[str] = None, as_json: bool = typer.Option(False, "--json")):
    """Show what information is still missing (never shows secret values)."""
    _out(_engine().intake_status(workflow, env), as_json)


@app.command()
def discover(env: str = typer.Option(..., "--env"), no_crawl: bool = False, as_json: bool = typer.Option(False, "--json")):
    """Discover APIs/routes/existing tests from specs and a bounded GET crawl."""
    try:
        _out(_engine().discover(env, crawl=not no_crawl), as_json)
    except Exception as e:  # noqa: BLE001
        _fail(e)


@app.command()
def plan(feature: Optional[str] = typer.Option(None, "--feature"), env: str = typer.Option(..., "--env"),
         benchmark: bool = typer.Option(False, "--benchmark"), target: list[str] = typer.Option([], "--target"),
         capacity: bool = False, peak: bool = False, base: Optional[str] = None, head: Optional[str] = None,
         as_json: bool = typer.Option(False, "--json")):
    """Create a Performance Test Plan (no load is generated)."""
    e = _engine()
    try:
        if benchmark or not feature:
            p = e.plan_benchmark(env, targets=target or None, include_capacity=capacity)
        else:
            p = e.plan_feature(feature, env, base=base, head=head, include_peak=peak)
        _out(e.plan_view(p.id), as_json)
    except Exception as ex:  # noqa: BLE001
        _fail(ex)


@app.command()
def approve(plan_id: str, approver: str = typer.Option(..., "--approver", help="your name (a human)"),
            confirm_assumptions: bool = False, approve_high_risk: bool = False, reason: Optional[str] = None,
            yes: bool = typer.Option(False, "--yes", help="skip the interactive confirmation")):
    """Human gate: approve a plan (G2), confirming assumptions (G3) / high-risk tests (G4) if needed."""
    e = _engine()
    if not yes:
        if not sys.stdin.isatty():
            _fail(RuntimeError("approval needs an interactive terminal or --yes from the human approver"))
        typer.echo(json.dumps(e.plan_view(plan_id), indent=2, default=str))
        typer.confirm(f"{approver}, approve plan {plan_id}?", abort=True)
    try:
        _out(e.approve_plan(plan_id, approver, confirm_assumptions, approve_high_risk, reason), True)
    except Exception as ex:  # noqa: BLE001
        _fail(ex)


@app.command()
def run(plan_id: Optional[str] = typer.Argument(None), feature: Optional[str] = typer.Option(None, "--feature"),
        env: Optional[str] = typer.Option(None, "--env"), only: list[str] = typer.Option([], "--only", help="run only these test names"),
        as_json: bool = typer.Option(False, "--json")):
    """Execute an APPROVED plan. With --feature/--env, runs the latest approved plan for that feature."""
    e = _engine()
    try:
        if plan_id is None:
            plan_id = _latest_plan(e, feature, env, status="approved")
        res = e.execute_plan(plan_id, only=only or None, progress=lambda m: typer.echo(m, err=True))
        _out(res["analysis"] if not as_json else res, as_json)
    except Exception as ex:  # noqa: BLE001
        _fail(ex)


def _latest_plan(e, feature, env, status=None) -> str:
    from sqlalchemy import select

    from perfeng.registry.db import PlanRow

    stmt = select(PlanRow).order_by(PlanRow.created_at.desc())
    if feature:
        stmt = stmt.where(PlanRow.feature_id == feature)
    if env:
        stmt = stmt.where(PlanRow.environment == env)
    if status:
        stmt = stmt.where(PlanRow.status == status)
    rows = e.reg.query(stmt.limit(1))
    if not rows:
        raise RuntimeError(f"no {status or ''} plan found; create one with `perf plan` and get it approved")
    return rows[0].id


@app.command()
def status(ident: str, as_json: bool = typer.Option(False, "--json")):
    """Status of a run, series, plan, baseline or analysis."""
    try:
        _out(_engine().status(ident), as_json)
    except Exception as ex:  # noqa: BLE001
        _fail(ex)


@app.command()
def compare(a: str, b: str, as_json: bool = typer.Option(False, "--json")):
    """Compare two runs / series / baselines (b relative to a)."""
    try:
        _out(_engine().compare(a, b), as_json)
    except Exception as ex:  # noqa: BLE001
        _fail(ex)


@app.command()
def history(target: Optional[str] = None, limit: int = 20, as_json: bool = typer.Option(False, "--json")):
    """Performance history (series) for the application or one target."""
    _out(_engine().history(target, limit), as_json)


@app.command()
def diagnose(plan_id: str, as_json: bool = typer.Option(False, "--json")):
    """Evidence-based hypotheses for an analyzed plan and a proposed Diagnostic Plan vN."""
    from perfeng.diagnostics.loop import diagnose as dg

    try:
        _out(dg(_engine(), plan_id), as_json)
    except Exception as ex:  # noqa: BLE001
        _fail(ex)


@app.command()
def plans(limit: int = 15, as_json: bool = typer.Option(False, "--json")):
    """List plans (newest first) with their ids, so you can pass one to report / export / run / diagnose."""
    from sqlalchemy import select

    from perfeng.registry.db import PlanRow, RunRow

    e = _engine()
    rows = e.reg.query(select(PlanRow).order_by(PlanRow.created_at.desc()).limit(limit))
    out = [{"plan_id": r.id, "workflow": r.workflow, "feature": r.feature_id, "environment": r.environment, "status": r.status,
            "runs": len(e.reg.query(select(RunRow).where(RunRow.plan_id == r.id))), "created": r.created_at.strftime("%Y-%m-%d %H:%M")}
           for r in rows]
    if as_json:
        typer.echo(json.dumps(out, indent=2))
        return
    if not out:
        typer.echo("no plans yet: run /perf:benchmark or `perf plan --benchmark --env <env>`")
    for o in out:
        typer.echo(f"{o['plan_id']}  {o['workflow']:<10} {o['feature'] or '-':<8} {o['environment']:<10} {o['status']:<9} runs={o['runs']:<3} {o['created']}")


def _resolve_plan(e, plan_id: Optional[str]) -> str:
    return plan_id or _latest_plan(e, None, None, status="executed")


@app.command()
def report(plan_id: Optional[str] = typer.Argument(None, help="plan id (default: latest executed plan; see `perf plans`)"),
           out: Optional[Path] = typer.Option(None, "--out", help="file to write (default: .perf/reports/<plan>.html|md)"),
           html: bool = typer.Option(False, "--html", help="self-contained HTML with charts"),
           open_: bool = typer.Option(False, "--open", help="open the report in your browser/editor (implies --html unless --out ends in .md)"),
           stdout: bool = typer.Option(False, "--stdout", help="print Markdown to the terminal instead of writing a file")):
    """Professional performance test report: inputs, environment, execution, results, success/failure, SLO/baseline, observations."""
    from perfeng.config import perf_dir
    from perfeng.report.markdown import plan_report

    e = _engine()
    try:
        pid = _resolve_plan(e, plan_id)
        if stdout:
            typer.echo(plan_report(e, pid, html=False))
            return
        html = html or open_ or bool(out and out.suffix.lower() in (".html", ".htm"))
        if out is None:
            out = perf_dir(e.root) / "reports" / f"{pid}.{'html' if html else 'md'}"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(plan_report(e, pid, html=html), encoding="utf-8")
    except Exception as ex:  # noqa: BLE001
        _fail(ex)
    typer.echo(f"report for {pid}: {out.resolve()}")
    if open_:
        import webbrowser

        webbrowser.open(out.resolve().as_uri())


@app.command()
def export(plan_id: Optional[str] = typer.Argument(None, help="plan id (default: latest executed plan; see `perf plans`)"),
           out: Optional[Path] = typer.Option(None, "--out", help="folder (default: .perf/export/<plan>)"),
           as_json: bool = typer.Option(False, "--json")):
    """Export the plan's k6 scripts, data, Playwright journeys and run helpers for MANUAL execution (bypasses engine safeguards)."""
    from perfeng.config import perf_dir
    from perfeng.report.export import export_plan

    e = _engine()
    try:
        pid = _resolve_plan(e, plan_id)
        dest = (out or perf_dir(e.root) / "export" / pid).resolve()
        r = export_plan(e, pid, dest)
    except Exception as ex:  # noqa: BLE001
        _fail(ex)
    if as_json:
        typer.echo(json.dumps(r, indent=2))
    else:
        typer.echo(f"exported {len(r['scripts'])} k6 scripts + {len(r['journeys'])} journeys to {r['out']}")
        typer.echo(f"next: open {r['out']}/README.md  (fill .env.example -> .env; secrets needed: {', '.join(r['secrets']) or 'none'})")


@app.command("import-nfrs")
def import_nfrs(path: str):
    """Import approved NFRs/SLOs from a YAML file (nfrs: [{id, scope, metric, comparator, threshold, unit}])."""
    try:
        typer.echo(f"imported {len(_engine().import_nfrs(path))} NFRs")
    except Exception as ex:  # noqa: BLE001
        _fail(ex)


@baseline_app.command("list")
def baseline_list(as_json: bool = typer.Option(False, "--json")):
    _out(_engine().list_baselines(), as_json)


@baseline_app.command("candidates")
def baseline_candidates(plan_id: Optional[str] = None, as_json: bool = typer.Option(False, "--json")):
    """Benchmark series and whether they are stable enough to promote."""
    _out(_engine().baseline_candidates(plan_id), as_json)


@baseline_app.command("promote")
def baseline_promote(series_id: str, approver: str = typer.Option(..., "--approver"),
                     reason: str = typer.Option(..., "--reason"), yes: bool = typer.Option(False, "--yes")):
    """Human gate G5: promote a stable benchmark series to a new baseline version."""
    e = _engine()
    if not yes:
        if not sys.stdin.isatty():
            _fail(RuntimeError("promotion needs an interactive terminal or --yes from the human approver"))
        typer.confirm(f"{approver}, promote series {series_id} to baseline?", abort=True)
    try:
        _out(e.approve_baseline(series_id, approver, reason), True)
    except Exception as ex:  # noqa: BLE001
        _fail(ex)


if __name__ == "__main__":
    app(prog_name="perf")
