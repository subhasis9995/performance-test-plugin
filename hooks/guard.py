#!/usr/bin/env python3
"""perf plugin guard hooks (stdlib only).

  guard.py pretool   PreToolUse:
      - approve_* perf-engine tools        -> "ask"  (human must confirm in Claude Code's permission prompt)
      - Bash running raw load tools         -> "deny" (k6 run, locust, jmeter, gatling, ab, wrk, hey, vegeta, artillery)
      - Bash running human-only CLI gates   -> "deny" (perf approve, perf baseline promote)
      - Read/Grep/Glob/Bash touching .env*  -> "deny" (except .env.example) so Claude never sees secret values
  guard.py prompt    UserPromptSubmit: block prompts that contain secret-looking values.
"""

from __future__ import annotations

import json
import re
import sys

LOAD_TOOLS = re.compile(
    r"(?:^|[\s;&|(`'\"/\\])(?:k6(?:\.exe)?\s+(?:run|cloud)|locust\b|jmeter\b|gatling(?:\.sh|\.bat)?\b|ab\s+-[nc]|wrk\s+-|"
    r"hey\s+-|vegeta\s+attack|artillery\s+(?:run|quick)|xk6\b)", re.I)
HUMAN_GATES = re.compile(r"\bperf(?:\.cmd)?\s+(?:approve|baseline\s+promote)\b|perfeng\.cli\s+(?:approve|baseline)", re.I)
ENV_FILE = re.compile(r"(?:^|[\s/\\'\"=])\.env(?:\.(?!example\b)[\w.-]+)?(?=$|[\s'\";|&)])")
SECRETS = [
    (re.compile(r"\bbearer\s+[A-Za-z0-9\-_.=]{20,}", re.I), "a bearer token"),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"), "a JWT"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "an AWS access key"),
    (re.compile(r"\b(?:ghp|gho|ghs|github_pat)_[A-Za-z0-9_]{20,}"), "a GitHub token"),
    (re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9\-_]{20,}"), "an API key"),
    (re.compile(r"\bxox[abpr]-[A-Za-z0-9-]{10,}"), "a Slack token"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "a private key"),
    (re.compile(r"(?i)\b(?:password|passwd|pwd|secret|api[_-]?key|token|client[_-]?secret)\b\s*(?:is|=|:)\s*[\"']?(?!\$\{|<|ENV|env:)[^\s\"'<>]{6,}"),
     "a password/secret value"),
]
ENV_MSG = ("Please do not paste passwords, tokens or API keys into the chat. Put the value in your project's .env file "
           "(it is gitignored), e.g.  PERF_API_TOKEN=...  and tell me only the variable name (PERF_API_TOKEN).")


def _out(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj))


def pretool(ev: dict) -> None:
    name = ev.get("tool_name", "")
    ti = ev.get("tool_input") or {}
    if re.match(r"mcp__plugin_perf_perf-engine__approve_", name):
        what = "baseline promotion (G5)" if name.endswith("approve_baseline") else "this performance plan (G2)"
        _out({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                                     "permissionDecisionReason": f"Human approval required: approve {what} "
                                     f"as '{ti.get('approver', '?')}'? Load will be generated against the approved target only."}})
        return
    if name == "Bash":
        cmd = ti.get("command", "")
        if LOAD_TOOLS.search(cmd):
            _out({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                         "permissionDecisionReason": "Raw load generation is blocked. Use the perf plugin "
                                         "(plan -> human approval -> start_run) so policy limits, allowlists and audit apply."}})
            return
        if HUMAN_GATES.search(cmd):
            _out({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                         "permissionDecisionReason": "Approvals are human gates. Use the approve_plan / "
                                         "approve_baseline tools so the user confirms in Claude Code, or ask the user to run the CLI."}})
            return
        if ENV_FILE.search(cmd):
            _out({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                         "permissionDecisionReason": ".env files hold secrets and must not be read by Claude. "
                                         "The perf engine reads them itself at execution time."}})
            return
    if name in ("Read", "Grep", "Glob", "Edit", "Write", "NotebookEdit"):
        target = str(ti.get("file_path") or ti.get("path") or "") + " " + str(ti.get("pattern") or "") + " " + str(ti.get("glob") or "")
        if ENV_FILE.search(" " + target.replace("\\", "/")):
            _out({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                         "permissionDecisionReason": ".env files hold secrets; Claude must not read or write them. "
                                         "Ask the user to edit .env themselves (only variable names are needed)."}})


def prompt(ev: dict) -> None:
    text = ev.get("prompt", "") or ""
    for rx, label in SECRETS:
        if rx.search(text):
            _out({"decision": "block", "reason": f"This message looks like it contains {label}. {ENV_MSG}"})
            return


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "pretool"
    try:
        ev = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    (pretool if mode == "pretool" else prompt)(ev)
    return 0


if __name__ == "__main__":
    sys.exit(main())
