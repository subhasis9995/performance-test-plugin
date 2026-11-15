---
name: history
description: Show performance history and trends for an application or one endpoint/journey (series over time, p95 medians, kind benchmark/feature/diagnostic, feature ids). Use for "/perf history", "performance trend for /search", "has latency been creeping up".
argument-hint: "[target]"
allowed-tools: mcp__plugin_perf_perf-engine__history, mcp__plugin_perf_perf-engine__baselines
---

Call `history(target)` and `baselines`. Show a table of the series, newest first: workload, kind, feature, p95 median, and runs. Point out drift versus the active baseline. A trend is informational and never moves the baseline by itself.
