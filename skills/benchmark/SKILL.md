---
name: benchmark
description: Benchmark an existing system's performance (API latency percentiles, throughput, errors, frontend Core Web Vitals) when there is no baseline yet or a fresh measurement is needed. Use for "benchmark my service", "measure current performance", "establish a performance baseline", "how fast is my API today", capacity characterization. Runs an intake interview first, then a provenance-tagged plan that needs human approval.
argument-hint: "[app-name] [environment] [base-url]"
allowed-tools: AskUserQuestion, Read, Glob, mcp__plugin_perf_perf-engine__intake_status, mcp__plugin_perf_perf-engine__configure_project, mcp__plugin_perf_perf-engine__set_project_inputs, mcp__plugin_perf_perf-engine__discover, mcp__plugin_perf_perf-engine__plan_benchmark, mcp__plugin_perf_perf-engine__plan_view, mcp__plugin_perf_perf-engine__start_run, mcp__plugin_perf_perf-engine__run_status, mcp__plugin_perf_perf-engine__get_analysis, mcp__plugin_perf_perf-engine__baselines, mcp__plugin_perf_perf-engine__write_report, mcp__plugin_perf_perf-engine__export_scripts, mcp__plugin_perf_perf-engine__doctor, mcp__plugin_perf_perf-engine__setup_toolchain
---

# Benchmark Existing System

A **benchmark** is a measurement. A **baseline** is an *approved* reference. A benchmark never becomes a baseline on its own.

## 0. Toolchain
Call `doctor`. If k6 is missing, tell the user you will install pinned tools (no Docker needed) and call `setup_toolchain` (with `browser: true` only if they want frontend journeys).

## 1. Intake interview (ask only for what is missing)
Call `intake_status` (workflow `benchmark`). Then **call the AskUserQuestion tool** (do not ask in plain prose), in short rounds of 1-4 questions with answer options, for each item in `missing`. If AskUserQuestion is unavailable, list the same questions and stop until the user answers:
1. **Target**: application name, environment name, base URL, and an explicit "I own or am allowed to load test this target". Never assume ownership. Never accept production.
2. **API spec**: "Do you have an OpenAPI/Swagger file or URL, a Postman collection, a GraphQL schema, a HAR capture, or a .proto?" They give a workspace path or URL. "None" is a valid answer (`api_spec_none: true`), and discovery then crawls with lower confidence.
3. **Test data**: CSV or JSON files with ids, search terms, users or payloads. Or "none".
4. **Auth**: type (none, basic, bearer, API key, OAuth2 client-credentials, login flow, cookie) and the **environment variable names only**.
5. **NFRs/SLOs**: a document path, or "none". Never invent NFRs. If there are none, say verdicts will be baseline-only.
6. **Frontend**: URL and important journeys (login, search, checkout...), or "none".
7. Optional **telemetry**: a Prometheus or metrics URL, or the local port of the app process.

**Secrets rule.** State it before any auth question: *"Never paste passwords, tokens or API keys here. Put them in the project's `.env` file (it is gitignored) and tell me only the variable name, e.g. `PERF_API_TOKEN`."* If the user pastes a secret anyway, do not repeat it. Ask them to move it to `.env` and rotate it.

Save answers with `configure_project` and `set_project_inputs`. Then call `intake_status` again until `ready` is true. Re-check which secret variables are present. Never read `.env` yourself.

## 2. Discover
Call `discover`. Summarize the endpoints by source and confidence, the routes, any existing k6/Playwright/Lighthouse tests, and the NFRs found. Let the user choose the targets. Default to safe GET endpoints. Write endpoints need test data plus a request override (see the `k6-scripting` skill).

## 3. Plan (no load yet)
Call `plan_benchmark` (set `include_capacity` only if the user asks for capacity or breakpoint). Present the plan view:
- tests, workloads, repeats (default 5), estimated time
- **where every value comes from**: NFR / BASELINE / POLICY / ASSUMPTION
- assumptions that need confirmation, and high-risk tests (capacity, stress, spike, soak)

## 4. Human approval (gate)
Ask with AskUserQuestion: "Approve this plan?", with options Approve, Change, Cancel. Only after a yes, call `approve_plan` with `approver` = the user's name. Claude Code will show its own confirmation prompt. Never approve on the user's behalf. Never retry after a denial.

## 5. Run
Call `start_run`, then poll `run_status` every ~30-60 s and relay progress briefly. Benchmarks take minutes.

## 6. Results
Call `get_analysis`. Report p50/p90/p95/p99, throughput, error rate, dropped iterations and stability (CV) per target. Report Core Web Vitals for journeys. Label results "local-lab" when they come from a laptop.
Call `write_report` and give the user the Markdown and HTML report paths. If they want to run the generated k6 scripts by hand, call `export_scripts` (manual runs bypass the safeguards; say so).
Call `baselines`. If a series is `eligible`, explain that it *may* be promoted to a baseline, and that promotion is a separate human decision (`/perf:baseline`). Do not promote in this skill.
