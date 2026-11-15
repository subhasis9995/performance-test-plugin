---
name: status
description: Show the status of a performance run, series, plan, baseline or background job. Use for "/perf status <id>", "is my perf run done", "what happened in run_...".
argument-hint: "<run|series|plan|baseline|job id>"
allowed-tools: mcp__plugin_perf_perf-engine__status, mcp__plugin_perf_perf-engine__run_status, mcp__plugin_perf_perf-engine__get_analysis
---

For ids starting `job_`, call `run_status`. For anything else, call `status`. For plans, include the latest verdict from `get_analysis`. Report status, timings, key metrics and artifact paths (traces, HAR, Lighthouse JSON, raw k6 NDJSON). Never print secret values. None are stored.
