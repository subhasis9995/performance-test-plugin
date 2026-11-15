---
name: baseline
description: List, inspect or promote performance baselines. A baseline is an approved, versioned reference created only from a stable repeated benchmark series by explicit human decision. Use for "/perf baseline", "promote this benchmark to baseline", "what is the current baseline", "show baseline history".
argument-hint: "[list | promote <series-id>]"
allowed-tools: AskUserQuestion, mcp__plugin_perf_perf-engine__baselines, mcp__plugin_perf_perf-engine__status, mcp__plugin_perf_perf-engine__history
---

# Baselines (benchmark is not baseline)

- **List**: call `baselines`. Show the active and retired versions per target and workload, with the approval id. Show the candidate series, with `eligible` and the reasons (runs, p95 CV, error spread, saturation).
- **Promote** (gate G5):
  1. Only for a series where `eligible` is true. If it is not eligible, explain why (for example "CV 0.18 > 0.10", "only 1 run") and suggest re-benchmarking.
  2. Explain the consequence: future feature runs will be compared against this version. The old version is retired, not deleted.
  3. Ask with AskUserQuestion: Promote / Cancel. If they choose Promote, call `approve_baseline(series_id, approver=<user name>, reason=<user's words>)`. Claude Code will ask them to confirm.
- Never promote after a feature run just because it passed. That normalizes gradual degradation (400 -> 420 -> 440 ms...). Feature series are never promotable.
