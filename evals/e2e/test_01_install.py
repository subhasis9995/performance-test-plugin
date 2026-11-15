"""Eval 1 — Plugin installation: manifest valid, installable from a marketplace, components discovered,
MCP server starts and lists its tools, hooks enforce the gates."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import REPO, have_claude

EXPECTED_SKILLS = {"benchmark", "feature", "plan", "run", "status", "compare", "baseline", "diagnose", "history", "setup"}
EXPECTED_TOOLS = {"intake_status", "configure_project", "set_project_inputs", "discover", "plan_benchmark", "plan_feature",
                  "plan_view", "revise_plan", "approve_plan", "approve_baseline", "start_run", "run_status", "get_analysis",
                  "diagnose", "status", "compare", "history", "baselines", "write_report", "doctor", "setup_toolchain"}


@pytest.mark.skipif(not have_claude(), reason="claude CLI not installed")
def test_manifest_validates_strict(evidence):
    p = subprocess.run(["claude", "plugin", "validate", str(REPO), "--strict"], capture_output=True, text=True, encoding="utf-8")
    evidence.record("E1.validate", rc=p.returncode, tail=p.stdout[-300:])
    assert p.returncode == 0 and "Validation passed" in p.stdout, p.stdout[-2000:]


@pytest.mark.skipif(not have_claude(), reason="claude CLI not installed")
def test_install_from_local_marketplace(tmp_path, evidence):
    """Install into an ISOLATED Claude config dir so the user's own settings are untouched."""
    env = {**os.environ, "CLAUDE_CONFIG_DIR": str(tmp_path / "claude-config")}
    add = subprocess.run(["claude", "plugin", "marketplace", "add", str(REPO)], env=env, capture_output=True, text=True, encoding="utf-8")
    assert add.returncode == 0, add.stdout + add.stderr
    ins = subprocess.run(["claude", "plugin", "install", "perf@perf-local"], env=env, capture_output=True, text=True, encoding="utf-8")
    assert ins.returncode == 0, ins.stdout + ins.stderr
    ls = subprocess.run(["claude", "plugin", "list", "--json"], env=env, capture_output=True, text=True, encoding="utf-8")
    assert ls.returncode == 0, ls.stderr
    data = json.loads(ls.stdout)
    items = data if isinstance(data, list) else data.get("plugins", data.get("installed", []))
    names = json.dumps(items)
    evidence.record("E1.install", listed="perf@perf-local" in names or '"perf"' in names)
    assert "perf" in names


def test_components_present():
    skills = {p.parent.name for p in (REPO / "skills").glob("*/SKILL.md")}
    assert EXPECTED_SKILLS <= skills
    agents = {p.stem for p in (REPO / "agents").glob("*.md")}
    assert {"context-agent", "test-architect", "analyzer", "diagnostic-planner", "baseline-manager"} <= agents
    hooks = json.loads((REPO / "hooks" / "hooks.json").read_text())
    assert "PreToolUse" in hooks["hooks"] and "UserPromptSubmit" in hooks["hooks"]
    mcp = json.loads((REPO / ".mcp.json").read_text())
    assert "perf-engine" in mcp["mcpServers"]
    for f in (REPO / "skills").glob("*/SKILL.md"):
        head = f.read_text(encoding="utf-8").split("---")[1]
        assert "name:" in head and "description:" in head, f


def test_mcp_server_handshake_lists_tools(tmp_path, evidence):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def go():
        params = StdioServerParameters(command="uv", args=["run", "--quiet", "--project", str(REPO), "perfeng-mcp"],
                                       env={**os.environ, "PERF_PROJECT_DIR": str(tmp_path)})
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                tools = await s.list_tools()
                res = await s.call_tool("intake_status", {"workflow": "benchmark"})
                return [t.name for t in tools.tools], res

    names, res = asyncio.run(go())
    evidence.record("E1.mcp", tools=len(names))
    assert EXPECTED_TOOLS <= set(names), set(EXPECTED_TOOLS) - set(names)
    text = json.dumps([c.model_dump() for c in res.content])
    assert "Never paste" in text and "target" in text


def _hook(mode: str, event: dict) -> dict:
    p = subprocess.run([sys.executable, str(REPO / "hooks" / "guard.py"), mode], input=json.dumps(event),
                       capture_output=True, text=True)
    return json.loads(p.stdout) if p.stdout.strip() else {}


@pytest.mark.parametrize("cmd", ["k6 run load.js", "C:/tools/k6.exe run x.js", "locust -f l.py", "jmeter -n -t a.jmx",
                                 "ab -n 1000 http://x", "perf approve plan_1 --approver me --yes",
                                 "uv run perf baseline promote ser_1 --approver x --reason y --yes", "cat .env", "type .env.local"])
def test_hook_denies_dangerous_bash(cmd):
    out = _hook("pretool", {"tool_name": "Bash", "tool_input": {"command": cmd}})
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny", cmd


@pytest.mark.parametrize("cmd", ["git diff main...HEAD", "uv run perf status plan_1", "cat .env.example", "ls -la"])
def test_hook_allows_safe_bash(cmd):
    assert _hook("pretool", {"tool_name": "Bash", "tool_input": {"command": cmd}}) == {}


def test_hook_asks_human_for_approvals():
    for tool in ("approve_plan", "approve_baseline"):
        out = _hook("pretool", {"tool_name": f"mcp__plugin_perf_perf-engine__{tool}", "tool_input": {"approver": "dana"}})
        assert out["hookSpecificOutput"]["permissionDecision"] == "ask"


@pytest.mark.parametrize("path,denied", [("C:/p/.env", True), ("/p/.env.local", True), ("/p/.env.example", False),
                                         ("/p/src/env.ts", False)])
def test_hook_env_file_reads(path, denied):
    out = _hook("pretool", {"tool_name": "Read", "tool_input": {"file_path": path}})
    assert (out.get("hookSpecificOutput", {}).get("permissionDecision") == "deny") is denied


@pytest.mark.parametrize("prompt,blocked", [
    ("my token is Bearer abcdefghijklmnopqrstuvwxyz0123456789", True),
    ("password: Sup3rSecret!x", True),
    ("api_key=sk-ant-abcdefghijklmnopqrstuvwx", True),
    ("the password is in PERF_PASSWORD in .env", False),
    ("/perf:feature KEY-22 perf-qa", False),
])
def test_hook_blocks_secrets_in_prompt(prompt, blocked):
    out = _hook("prompt", {"prompt": prompt})
    assert (out.get("decision") == "block") is blocked
    if blocked:
        assert ".env" in out["reason"] and "variable name" in out["reason"]
