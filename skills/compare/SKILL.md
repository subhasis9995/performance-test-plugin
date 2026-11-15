---
name: compare
description: Compare two performance runs, series or baselines (b relative to a) with percentile deltas and a significance test. Use for "/perf compare <a> <b>", "compare this run with the baseline", "what changed between run X and Y".
argument-hint: "<id-a> <id-b>"
allowed-tools: mcp__plugin_perf_perf-engine__compare, mcp__plugin_perf_perf-engine__status
---

Call `compare(a, b)`. Present a table per target: p50/p90/p95/p99, error rate and rps, as a -> b with delta %. Add the one-sided p-value (is b slower than a?). Mention targets that are only present on one side. With one or two runs per side, say that significance is low. Do not draw conclusions the numbers do not support.
