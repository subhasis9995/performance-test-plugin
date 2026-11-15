"""L3 — the PLUGIN ITSELF drives a real workflow end to end (headless `claude -p --plugin-dir`).

Turn 1  `/perf:feature KEY-22 perf-qa`  -> Claude uses the plugin's skill + MCP tools to build the impact model and plan,
        and must STOP at the human gate (approve_* is 'ask' -> denied in headless mode). No load is generated.
Human   the harness approves the plan through the CLI (acting as the human).
Turn 2  `/perf:run <plan>` with a 200 ms search regression injected -> Claude starts the run, waits, reports FAIL_REGRESSION.
Turn 3  `/perf:diagnose <plan>` -> evidence-based hypothesis naming latency added in front of the app.

Every assertion is checked against the registry / artifacts, not only Claude's prose.
Makes billed model calls: marked `agent`; skipped when `claude` is not authenticated.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
from pathlib import Path

import pytest

from conftest import REPO, SEARCH, have_claude, perf_cli, perf_json, set_faults, clear_faults

pytestmark = pytest.mark.agent
T = "mcp__plugin_perf_perf-engine__"
READ_TOOLS = ["Skill", "Read", "Glob", "Grep", "Bash(git log:*)", "Bash(git diff:*)", "Bash(git show:*)"]
ENGINE = [T + t for t in ("intake_status", "discover", "plan_feature", "plan_view", "plan_benchmark", "baselines", "status",
                          "run_status", "get_analysis", "diagnose", "write_report", "doctor", "history", "compare")]


def _claude(project: Path, prompt: str, tools: list[str], max_turns: int = 25, timeout: int = 1800) -> dict:
    env = {**os.environ, "PERF_TOOLS_DIR": str(Path.home() / ".perfeng" / "tools"), "MCP_TIMEOUT": "180000",
           "PYTHONIOENCODING": "utf-8"}
    # prompt first: --allowedTools is variadic and would swallow a trailing prompt
    cmd = ["claude", "-p", prompt, "--plugin-dir", str(REPO), "--output-format", "stream-json", "--verbose",
           "--max-turns", str(max_turns), f"--allowedTools={','.join(tools)}"]
    p = subprocess.run(cmd, cwd=project, env=env, capture_output=True, text=True, encoding="utf-8", timeout=timeout,
                       stdin=subprocess.DEVNULL)
    events = [json.loads(ln) for ln in p.stdout.splitlines() if ln.strip().startswith("{")]
    calls, denials, result = [], [], None
    for e in events:
        if e.get("type") == "assistant":
            calls += [c for c in e["message"]["content"] if c.get("type") == "tool_use"]
        if e.get("type") == "result":
            result = e
            denials = e.get("permission_denials") or []
    log = REPO / "evals" / "reports" / "latest" / "l3"
    log.mkdir(parents=True, exist_ok=True)
    (log / (re.sub(r"\W+", "_", prompt)[:40] + ".jsonl")).write_text(p.stdout, encoding="utf-8")
    return {"calls": calls, "names": [c["name"] for c in calls], "denials": denials, "result": result,
            "text": (result or {}).get("result") or "", "rc": p.returncode, "stderr": p.stderr}


@pytest.fixture(scope="module")
def authed():
    if not have_claude():
        pytest.skip("claude CLI not installed")
    p = subprocess.run(["claude", "-p", "--max-turns", "1", "Reply with OK"], capture_output=True, text=True,
                       encoding="utf-8", timeout=180, stdin=subprocess.DEVNULL)
    if "Failed to authenticate" in (p.stdout + p.stderr) or p.returncode != 0:
        pytest.skip("claude is not authenticated for headless runs (run `claude` and /login)")


@pytest.fixture(scope="module")
def baselined_project(tmp_path_factory, sut):
    """A fresh project with an approved baseline for the search endpoint (set up through the CLI as a human would)."""
    from juice_project import build

    d = tmp_path_factory.mktemp("l3-project")
    build(d, fast=True)
    cfg = d / ".perf" / "project.yaml"
    import yaml

    c = yaml.safe_load(cfg.read_text())
    c["journeys"] = []  # API-only journey keeps the agent eval short
    c["journeys_declared_none"] = True
    cfg.write_text(yaml.safe_dump(c, sort_keys=False))
    perf_cli(d, "discover", "--env", "perf-qa")
    cand, tried = None, []
    for _ in range(3):  # a noisy laptop can make one series unstable; the engine correctly refuses it
        v = perf_json(d, "plan", "--benchmark", "--env", "perf-qa", "--target", SEARCH)
        perf_cli(d, "approve", v["plan_id"], "--approver", "eval-human", "--yes")
        perf_cli(d, "run", v["plan_id"], "--only", f"load:{SEARCH}@10")
        cs = perf_json(d, "baseline", "candidates", "--plan-id", v["plan_id"])
        tried.append([c["reasons"] for c in cs])
        cand = next((c for c in cs if c["eligible"]), None)
        if cand:
            break
    assert cand, f"no stable benchmark series for the baseline: {tried}"
    perf_cli(d, "baseline", "promote", cand["series_id"], "--approver", "eval-human", "--reason", "L3 setup", "--yes")
    return d


def _db(p: Path):
    return sqlite3.connect(p / ".perf" / "state" / "registry.db")


def test_plugin_feature_journey_end_to_end(authed, baselined_project, evidence):
    proj = baselined_project
    # ---- turn 1: feature request -> plan, stop at the human gate
    t1 = _claude(proj, "/perf:feature KEY-22 perf-qa", READ_TOOLS + ENGINE + [T + "approve_plan", T + "start_run"])
    # a slash command expands the plugin skill directly (no Skill tool call); the skill's tools prove it ran
    assert T + "plan_feature" in t1["names"], t1["names"]
    with _db(proj) as c:
        plans = c.execute("select id, status from plans where feature_id='KEY-22' order by created_at desc").fetchall()
        assert plans, "plugin created no feature plan"
        plan_id = plans[0][0]
        approvals = c.execute("select count(*) from approvals where subject_id=?", (plan_id,)).fetchone()[0]
        runs = c.execute("select count(*) from runs where plan_id=?", (plan_id,)).fetchone()[0]
    assert approvals == 0, "the model must not be able to approve its own plan"
    assert runs == 0, "no load may be generated before human approval"
    assert re.search(r"approv", t1["text"], re.I), t1["text"][-1500:]
    evidence.record("L3.turn1", plan_id=plan_id, tools=t1["names"], denials=[d.get("tool_name") for d in t1["denials"]])

    # ---- human approval (out of band, as the person)
    perf_cli(proj, "approve", plan_id, "--approver", "eval-human", "--yes")

    # ---- turn 2: run with an injected regression
    set_faults("search-latency-200ms")
    try:
        t2 = _claude(proj, f"/perf:run {plan_id}", READ_TOOLS + ENGINE + [T + "start_run"], max_turns=40, timeout=2400)
    finally:
        clear_faults()
    assert T + "start_run" in t2["names"], t2["names"]
    with _db(proj) as c:
        n = c.execute("select count(*) from runs where plan_id=? and status='completed'", (plan_id,)).fetchone()[0]
        verdict = c.execute("select verdict from analyses where subject_id=? order by created_at desc", (plan_id,)).fetchone()
    assert n >= 4, f"expected smoke + repeated load runs, got {n}"
    assert verdict and verdict[0] == "FAIL_REGRESSION", verdict
    assert re.search(r"regress", t2["text"], re.I), t2["text"][-1500:]
    evidence.record("L3.turn2", completed_runs=n, verdict=verdict[0], tools=t2["names"])

    # ---- turn 3: diagnosis grounded in evidence
    t3 = _claude(proj, f"/perf:diagnose {plan_id}", READ_TOOLS + ENGINE)
    assert T + "diagnose" in t3["names"], t3["names"]
    assert re.search(r"(proxy|gateway|network|in front)", t3["text"], re.I), t3["text"][-1500:]
    assert re.search(r"2\d\d(\.\d)?\s*ms", t3["text"]), t3["text"][-1500:]
    evidence.record("L3.turn3", tools=t3["names"])
