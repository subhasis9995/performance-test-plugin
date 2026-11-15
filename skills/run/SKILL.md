---
name: run
description: Execute an approved performance plan (by plan id, or the latest plan for a feature id and environment) and report the verdict. Use for "run the perf plan", "/perf run KEY-22", "execute plan_...". Requires human approval of the exact plan first.
argument-hint: "<plan-id | feature-id> [environment]"
allowed-tools: AskUserQuestion, mcp__plugin_perf_perf-engine__plan_view, mcp__plugin_perf_perf-engine__plan_feature, mcp__plugin_perf_perf-engine__start_run, mcp__plugin_perf_perf-engine__run_status, mcp__plugin_perf_perf-engine__get_analysis, mcp__plugin_perf_perf-engine__status
---

# Run a plan

1. Resolve the plan. If given a feature id, call `plan_feature` (or reuse the plan id the user names) and show `plan_view`.
2. If the plan is not approved, show the plan summary and provenance, then ask (AskUserQuestion) for approval. On yes, call `approve_plan` with the user's name. Claude Code asks them to confirm too. On no, stop.
3. Call `start_run` and poll `run_status` until it finishes. Show short progress updates.
4. Call `get_analysis` and summarize the verdict per target, with SLO, baseline delta and trend kept separate. Suggest `/perf:diagnose <plan>` for regressions.

Never run raw load tools (k6, locust, jmeter...) through Bash. They are blocked by policy.
