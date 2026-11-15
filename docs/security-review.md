# Security review: third-party skills, MCP servers and tools

Reviewed 2026-11-15. Rule: community skills and MCP servers are pinned and reviewed before they are bundled or recommended.

## grafana/skills: `skills/grafana-k6/k6` (commit `8fb72a52`)
- License Apache-2.0. Content: k6 authoring recipe plus example scripts.
- Findings:
  1. Step "validate every script by running it". The skill tells the agent to run `k6 run` directly against the user's target. That bypasses our policy gate: no allowlist check, no VU/RPS/duration caps, no human approval, no audit.
  2. It installs and uses the `xk6-docs` CLI and fetches grafana.com docs. That is fine in principle but adds network and tool dependencies.
- Decision: **not bundled**. The plugin's own `k6-scripting` skill covers authoring custom scenarios *as TestRunSpec inputs*. The engine compiles every k6 script from a validated TestRunSpec. Users may install the Grafana skill themselves. The plugin's PreToolUse hook still blocks raw `k6 run` from the Bash tool, so the Grafana skill cannot generate load outside the gate.

## microsoft/playwright-mcp (`@playwright/mcp` 0.0.85)
- Used only for interactive journey discovery and generation, never in the measured run path.
- It is not bundled in `.mcp.json`, because a browser-control MCP should not start by default. The `frontend-journeys` skill documents opt-in use: `claude mcp add playwright -- npx @playwright/mcp@0.0.85`. It is pinned and stdio-only, never exposed over the network.

## grafana/mcp-k6 (v0.7.0)
- Optional script validation and docs. It is not bundled for the same reason: it can execute k6. The engine runs k6 itself under the watchdog.

## Community Playwright + Lighthouse MCP servers
- Rejected as critical dependencies (maintenance risk). Lighthouse runs directly in the browser worker.

## BMAD-METHOD 6.12.4 / TEA module
- Optional, advisory only (risk analysis, NFR assessment, release-gate reasoning). It is not installed by the plugin. The `nfr-provenance` skill explains how to feed TEA's `nfr-assess` with the evidence the engine produces (analysis JSON plus reports). Project-specific behaviour goes through `bmad-customize` overrides, not a fork.

## Engine boundaries (self-review)
- Target allowlist and ownership confirmation per environment. Production targets are refused in this release.
- Hard caps on VUs, arrival rate and duration (`policy/defaults.yaml`, which environments can only lower). k6 abort thresholds plus a wall-clock watchdog. The capacity ladder stops at the first breach.
- Approvals are bound to content hashes. Model-like approver names (`claude`, `assistant`, ...) are refused. The `approve_*` MCP tools are gated by a PreToolUse `ask` hook. The CLI approval commands are denied from Claude's Bash tool.
- Secrets are only ever `env:NAME` references. Values are resolved inside worker processes from the environment or `.env`, and redacted from artifacts. A UserPromptSubmit hook blocks secret-looking prompts. Read/Grep/Bash access to `.env*` is denied.
- The MCP server runs over stdio inside the user's machine. Nothing listens on the network.
