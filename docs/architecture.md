# Architecture

## High level

```mermaid
flowchart TB
  U([Developer]) -->|/perf:benchmark, /perf:feature KEY-22| P

  subgraph P[Claude Code plugin "perf" - interaction layer]
    S[Skills: intake, plan, run, baseline, diagnose ...]
    A[Agents: context, test-architect, analyzer, diagnostic-planner, baseline-manager]
    H[Hooks: human gates, .env guard, raw-load-tool block, secret-prompt block]
  end

  P -->|stdio MCP: 21 validated tools| E

  subgraph E[perfeng engine - deterministic]
    O[Orchestrator state machine]
    G[Policy / Safety gate]
    C[Context: git diff + work item + service map -> Feature Impact Model]
    PL[Planner -> Performance Plan with provenance]
    SP[TestRunSpec v1 compiler]
    X[Executor: LocalProcess default / Docker if present]
    N[Normalizer]
    AN[Analyzer: percentiles, CV, bootstrap CI, rank test, verdicts]
    D[Diagnostic loop: hypotheses + evidence -> Diagnostic Plan vN]
    R[(Registry SQLite: plans, runs, series, baselines, approvals, audit)]
    OS[(Artifacts on disk: NDJSON, trace, HAR, Lighthouse)]
  end

  O --> G --> SP --> X
  C --> PL --> O
  X --> W1[worker-k6]
  X --> W2[worker-browser: Playwright + Lighthouse]
  W1 --> N
  W2 --> N
  N --> AN --> D
  N --> OS
  AN --> R
  D --> R
  W1 -.-> SUT[(Target: allowlisted env)]
  W2 -.-> SUT
  TEL[Telemetry: Prometheus scrape / PromQL / process CPU+mem] --> N
  SUT -.-> TEL
```

## Principles
1. **Deterministic orchestration** (ADR-0001). The workflow is a persisted Python state machine. Claude proposes through validated tools; the engine computes every number.
2. **Workers execute only validated TestRunSpecs.** No free-form load instructions reach a worker (`engine/src/perfeng/spec/testrunspec_v1.py`, `policy/gate.py`).
3. **Benchmark ≠ Baseline** (ADR-0002) and **provenance on every value** (ADR-0003).
4. **Human gates and `.env`-only secrets** (ADR-0004).
5. **Native first, container-portable** (ADR-0005): one worker contract (`perfeng-worker --spec spec.json --out dir`, secrets as env vars, `result.json`) for native, Docker and future Kubernetes/cloud executors.

## Workflow state machine
```
intake -> discover -> plan (draft) -> [G2/G3/G4 approval] -> compile TestRunSpecs -> policy gate
       -> execute (smoke first; a failed smoke skips load for that target) -> normalize -> analyze
       -> verdict -> (regression?) diagnose -> Diagnostic Plan vN -> [G6] -> execute -> revised hypotheses
Baseline promotion is a separate branch: stable benchmark series -> [G5] -> Baseline vN (older versions retired).
```

## Domain model (registry)
`plans` (versioned, content-hashed) · `series` (repeated runs under one workload) · `runs` (spec, hash, metrics, tool versions, environment info, artifacts) · `baselines` (versioned, approved, with per-run samples for significance tests) · `nfrs` (approved SLOs, separate from baselines) · `approvals` (gate, subject hash, approver) · `analyses` · `audit` (append only).

## Analysis
- Per target: p50/p90/p95/p99, mean, max, error rate, rps, dropped iterations, bytes, generator CPU, SUT telemetry. Percentiles are recomputed from raw k6 NDJSON.
- Stability: CV of p95 across repeats, error-rate spread, minimum samples, saturation (dropped iterations, generator CPU, max VUs).
- Regression: median delta %, one-sided Mann-Whitney, bootstrap CI fallback when too few repeats for the rank test, absolute noise floors for frontend metrics.
- Frontend: LCP, CLS, INP (null without interaction), lab TBT, TTFB, FCP, Speed Index, long tasks, JS execution, resources, HAR, console errors. FID is not used. Lighthouse budgets are POLICY, not NFRs.

## Safety layers
Environment allowlist and ownership confirmation · production refused · caps on VUs/rate/duration · k6 `abortOnFail` thresholds · wall-clock watchdog · capacity ladder stops at first breach · plugin hooks (approvals ask the human; raw load tools, `.env` access and secret-looking prompts blocked) · literal-secret scan on specs · artifact redaction · append-only audit.

## Plugin components
| Path | Role |
|---|---|
| `.claude-plugin/plugin.json`, `marketplace.json` | manifest, local marketplace |
| `skills/*/SKILL.md` | 15 workflow and guidance skills |
| `agents/*.md` | 5 read-only roles |
| `hooks/hooks.json`, `hooks/guard.py` | PreToolUse + UserPromptSubmit guards |
| `.mcp.json` | `perf-engine` stdio MCP server |
| `bin/perf`, `bin/perf.cmd` | CLI on the Bash tool PATH |
| `plugin-evals/` | `claude plugin eval` suite (engine mocked) |

## Engine packages (`engine/src/perfeng`)
`domain` models · `spec` TestRunSpec v1 · `policy` gate and defaults · `registry` DB and baselines · `config` project and policy · `secrets` · `discovery` (OpenAPI, Postman, GraphQL, HAR, probe, crawl) · `context` (work item, git, impact) · `planning` · `generators` (k6 script) · `executors` (LocalProcess, Docker) · `workers` (k6, browser) · `normalize` · `analysis` · `telemetry` · `diagnostics` · `orchestrator` · `report` (full report builder, manual-run export) · `toolchain` · `cli` · `mcp_server`.

## Open-source reuse
k6, Playwright, Lighthouse, web-vitals, psutil, numpy/scipy, SQLAlchemy, Typer, MCP Python SDK. BMAD TEA (`nfr-assess`, test-design) is an optional advisory input (pin + customize; not forked). Grafana's k6 skill and the Playwright MCP are reviewed but not bundled ([security-review.md](security-review.md)). Pinned versions: [dependencies.lock.md](dependencies.lock.md).

## Phase 2 (not built)
Kubernetes Job and k6-operator executors, ECS/Fargate, Cloud Run Jobs, Azure Container Apps Jobs (same worker contract); Postgres and S3 storage; OTel-demo evaluation tier; Jira/ADO/GitHub work-item adapters; gRPC/WebSocket protocols.
