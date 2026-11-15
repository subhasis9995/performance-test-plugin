---
name: plan
description: Create or review a Performance Test Plan without running load, for a feature id (KEY-22) or a benchmark. Shows impact, tests, workloads and where each value comes from (NFR / baseline / policy / assumption). Use for "plan perf tests for KEY-22", "what would you test for this feature", "show the performance plan".
argument-hint: "<feature-id|benchmark> [environment]"
allowed-tools: AskUserQuestion, mcp__plugin_perf_perf-engine__intake_status, mcp__plugin_perf_perf-engine__plan_feature, mcp__plugin_perf_perf-engine__plan_benchmark, mcp__plugin_perf_perf-engine__plan_view, mcp__plugin_perf_perf-engine__revise_plan
---

# Performance Plan

1. Call `intake_status`. If the target or environment is missing, ask (AskUserQuestion) or point the user to `/perf:benchmark` intake.
2. For a feature id, call `plan_feature`. For `benchmark`, call `plan_benchmark`.
3. Present: the impact model (with evidence), the tests table, and the values grouped by **NFR / BASELINE / POLICY / ASSUMPTION**. Also list unconfirmed assumptions, high-risk tests, notes (missing baselines or NFRs) and estimated time.
4. Never present a POLICY or ASSUMPTION value as a requirement. Never invent NFRs.
5. Changes go through `revise_plan` and become ASSUMPTIONS. Nothing runs until `/perf:run` and human approval.
