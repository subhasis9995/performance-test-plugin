---
name: diagnose
description: Diagnose a performance regression or SLO breach with evidence-backed hypotheses and an optional focused Diagnostic Plan v2 (e.g. step load to separate fixed delay from queueing). Use for "/perf diagnose <plan>", "why is it slower", "what is the bottleneck", "investigate this regression".
argument-hint: "<plan-id>"
allowed-tools: AskUserQuestion, mcp__plugin_perf_perf-engine__diagnose, mcp__plugin_perf_perf-engine__plan_view, mcp__plugin_perf_perf-engine__start_run, mcp__plugin_perf_perf-engine__run_status, mcp__plugin_perf_perf-engine__get_analysis
---

# Diagnose

1. Call `diagnose(plan_id)`. Present the hypotheses grouped as *supported*, *insufficient evidence* and *refuted*. Each one comes with its evidence: metric names, baseline and current values, sources.
2. Rank and explain in plain language, but **do not add causes that have no evidence**. If evidence is insufficient, say so and give the next step that would produce it (for example "configure telemetry").
3. If `diagnostic_plan_id` is set, show `plan_view` and ask (AskUserQuestion) whether to run it. This is gate G6, approved through `approve_plan` with the user's name. Then call `start_run`, wait for completion, and call `diagnose(<diagnostic plan id>)` for the revised conclusion.
4. Stop at the iteration limit the engine reports.
