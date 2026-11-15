---
name: feature
description: Test the performance impact of a feature or work item (e.g. KEY-22, JIRA-123, ADO-456) on an environment. Use for "what performance impact did my change introduce", "perf test KEY-22", "did my feature regress latency", "check my PR for performance". Builds the Feature Impact Model from the work item and git diff, plans focused tests against approved baselines, gets human approval, runs, analyzes SLO vs baseline vs trend, and diagnoses regressions.
argument-hint: "<feature-id> [environment]"
allowed-tools: AskUserQuestion, Read, Glob, Grep, Bash(git log:*), Bash(git diff:*), Bash(git show:*), mcp__plugin_perf_perf-engine__intake_status, mcp__plugin_perf_perf-engine__set_project_inputs, mcp__plugin_perf_perf-engine__configure_project, mcp__plugin_perf_perf-engine__discover, mcp__plugin_perf_perf-engine__plan_feature, mcp__plugin_perf_perf-engine__plan_view, mcp__plugin_perf_perf-engine__revise_plan, mcp__plugin_perf_perf-engine__start_run, mcp__plugin_perf_perf-engine__run_status, mcp__plugin_perf_perf-engine__get_analysis, mcp__plugin_perf_perf-engine__diagnose, mcp__plugin_perf_perf-engine__baselines, mcp__plugin_perf_perf-engine__write_report, mcp__plugin_perf_perf-engine__export_scripts, mcp__plugin_perf_perf-engine__doctor
---

# Test a Feature

Input is small: a feature id plus an environment (`$ARGUMENTS`, e.g. `KEY-22 perf-qa`). Do not ask the developer for VUs, durations, k6 or Playwright details. The engine derives them from baselines, NFRs and policy.

## 1. Intake
Call `intake_status` (workflow `feature`, environment). Use AskUserQuestion only for the gaps:
- environment, if not given or not configured (target, base URL, ownership confirmation)
- repository path and branch or PR, if no commits mention the feature id
- the work item, if `.perf/workitems/<ID>.yaml` is missing: ask them to paste the title and acceptance criteria (non-secret text) or give a file
- API spec, test data, auth env var names and NFRs, exactly as in the benchmark intake

Secrets rule: never paste secrets in chat; use `.env` and give variable names only.

## 2. Impact + plan
Call `plan_feature`. Show the **Feature Impact Model**: backend endpoints, services, frontend components, journeys, dependencies, each with its evidence (changed files, service-map rule, route literal, work-item mention), plus the unknowns.
Then show the plan: tests, the workload reused from the approved baseline (like-for-like comparison), and the provenance of every value. Point out:
- targets with **no approved baseline**: regression cannot be measured, so suggest `/perf:benchmark` first, or accept an SLO-only verdict
- that there are no NFRs, if so (never invent them)
- assumptions that need confirmation

If the user wants changes, use `revise_plan`. Overrides become ASSUMPTIONS.

## 3. Human approval (gate)
AskUserQuestion: Approve / Change / Cancel. Only on yes, call `approve_plan(approver=<user name>)`. Claude Code then asks for confirmation as well.

## 4. Run, analyze
Call `start_run` and poll `run_status`, then call `get_analysis`. Report per target:
- **SLO** result (from approved NFRs only)
- **Baseline delta** (p95/p99/p50, CI, significance) and the error-rate change
- **Trend** (informational)
- the verdict: PASS, PASS_WITH_REGRESSION_WARNING, FAIL_REGRESSION, FAIL_SLO or INCONCLUSIVE

Keep these separate. Example: "SLO PASS (470 < 500 ms), +15.2% vs baseline, so PASS WITH REGRESSION WARNING."

## 5. Diagnose when regressed
For any warning or failure, call `diagnose`. Present the hypotheses ranked with **their evidence**. Say clearly when evidence is insufficient. If a Diagnostic Plan v2 is proposed, show it and ask for approval (gate G6) before running it. Then run `diagnose` again on the diagnostic plan and give the revised conclusion.

## 6. Report
Call `write_report` (Markdown plus an HTML version with charts) and give the user the paths. The report has the inputs, environment and tools, run log, per-repeat results, success/failure, SLO vs baseline vs trend, observations and recommendations. If the user wants to run the generated scripts themselves, call `export_scripts` and point to the bundle's README, and say that manual runs bypass the engine's safeguards. **Never** promote a baseline because a feature passed. Baselines move only through `/perf:baseline` with explicit human approval.
