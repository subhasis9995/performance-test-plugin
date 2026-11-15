# perf: automated performance engineering for Claude Code

A Claude Code plugin plus a deterministic Python engine (`perfeng`). It answers two questions:

1. **Benchmark an existing system.** How does it perform today, with no baseline, scripts or NFRs required?
2. **Test a feature.** What performance impact did my change (`KEY-22`, `JIRA-123`, `ADO-456`) introduce?

It runs k6 (APIs), Playwright and Lighthouse (frontend). No Docker or Kubernetes is needed. Nothing generates load until **you** approve the exact plan.

- [Quick start](#quick-start) · [New project walkthrough](#new-project-walkthrough) · [Giving inputs](#giving-inputs) · [Get report + scripts](#after-a-run-get-your-report-and-your-scripts) · [Running scripts manually](#running-the-generated-scripts-manually) · [Skills: what and when](#skills-what-and-when-to-use-them) · [Concepts](#core-concepts) · [Safety](#safety-gates-and-secrets) · [CLI](#cli-reference) · [Files](#where-things-live) · [Limits](#current-limits) · [Troubleshooting](#troubleshooting)
- Architecture: [docs/architecture.md](docs/architecture.md) · Evaluation: [evals/README.md](evals/README.md) · Decisions: [docs/adr/](docs/adr/) · Security review: [docs/security-review.md](docs/security-review.md)

---

## Quick start

**Prerequisites:** Claude Code ≥ 2.1.269, [uv](https://docs.astral.sh/uv/), Node ≥ 20 (frontend journeys only), Git, network access to GitHub releases and npm. Docker is optional.

```bash
# 1. install the pinned toolchain (k6 with SHA-256 check; Playwright + Chromium + Lighthouse with --browser)
cd C:\Workspace\performance-test-plugin
uv run perf setup --browser
uv run perf doctor

# 2. load the plugin in Claude Code (pick one)
claude --plugin-dir C:\Workspace\performance-test-plugin
#   or install from the local marketplace:
claude plugin marketplace add C:\Workspace\performance-test-plugin
claude plugin install perf@perf-local
```

Then, from **your own project folder**, start Claude Code and type `/perf:benchmark`.

**Try it on a safe demo app first** (OWASP Juice Shop, run natively on localhost):
```bash
python sut/juice-shop/install.py --install
python sut/juice-shop/install.py --start --start-proxy   # app :3000, fault proxy :3100
uv run python evals/fixtures/juice_project.py C:\work\demo --fast
cd C:\work\demo   # then run claude with the plugin and: /perf:feature KEY-22 perf-qa
```

---

## New project walkthrough

1. **Open Claude Code in your project folder** (with the plugin loaded).
2. **`/perf:benchmark`**. Claude runs `doctor`, installs tools if missing, then interviews you:
   target (app, environment, base URL, "I own or may load test this"), API spec, test data, auth (env var names), NFRs, frontend journeys, optional telemetry.
   It writes `.perf/project.yaml`, adds `.env` to `.gitignore` and creates `.env.example`.
3. **Put secrets in `.env`**, never in chat:
   ```
   PERF_API_TOKEN=...
   ```
   Tell Claude only the variable name, `PERF_API_TOKEN`.
4. **Discovery.** The engine lists endpoints (from your spec, a probed `/openapi.json`, or a bounded GET crawl), frontend routes, and existing k6/Playwright/Lighthouse tests. You choose targets.
5. **Plan.** Claude shows the tests, workloads, repeats (default 5) and, for every number, whether it came from an **NFR, BASELINE, POLICY or ASSUMPTION**.
6. **You approve** (Claude asks; Claude Code then shows its own permission prompt). Only then does it run.
7. **Results.** p50/p90/p95/p99, throughput, error rate, dropped iterations, stability (CV), Core Web Vitals per journey. Results are labelled *local-lab* when run on a laptop.
8. **`/perf:baseline`** (a separate human decision). A stable benchmark series may be promoted to **Baseline v1**.
9. **From now on, per feature:** `/perf:feature KEY-22 perf-qa`. The plan reuses the baseline's workload, so the comparison is like for like.
10. **If it regresses:** `/perf:diagnose <plan-id>` gives hypotheses with evidence and, optionally, a focused Diagnostic Plan v2 (also needs your approval).

---

## Giving inputs

Two ways, same result. Everything lives in `.perf/project.yaml` (**no secret values, ever**).

### A. Through chat
Claude asks only for what is missing. Answer with a **workspace path** or a **URL**: `docs/openapi.yaml`, `https://staging.example.com/v3/api-docs`, `test-data/catalog.csv`, `nfrs.yaml`. "None" is a valid answer for spec, data, NFRs and journeys.

### B. Edit the files yourself
```bash
uv run perf init --app myshop --env qa --base-url https://qa.example.com --i-own-this-target
```
then edit `.perf/project.yaml`:
```yaml
application_id: myshop
environments:
  qa:
    base_url: https://qa.example.com
    frontend_url: https://qa.example.com
    allowed_hosts: [qa.example.com]
    ownership_confirmed: true            # your explicit confirmation; required
    auth:
      type: bearer                       # none|basic|bearer|api_key|oauth2_client_credentials|login_flow|cookie
      env: {token: PERF_API_TOKEN}       # ENV VAR NAMES only; values go in .env
    telemetry:                           # optional; powers diagnosis
      app: scrape:https://qa.example.com/metrics      # Prometheus text endpoint
      app_process: process:port=8080                  # CPU/memory of a local process
      # prom: prom:http://prometheus:9090|rate(http_server_requests_seconds_sum[1m])
api_specs:
  - {kind: openapi, location: docs/openapi.yaml}      # or a Swagger URL; kinds: openapi|postman|graphql|har
test_data: [test-data/catalog.csv]
nfr_files: [nfrs.yaml]
targets: ["GET /api/products/{id}", "GET /rest/search"]  # optional; default = all discovered GET endpoints
request_overrides:                       # data-driven requests, POST/PUT bodies
  "GET /api/products/{id}": {path: "/api/products/${data.id}"}
  "POST /api/cart": {path: /api/cart, body: {productId: "${data.id}", qty: 1}, expect_status: [200, 201]}
journeys:
  - {name: Search, steps: [Home, Search, Results], spec: .perf/journeys/search.spec.ts}
repository: ../my-service-repo           # for feature diffs
service_map: .perf/service-map.yaml
```

| Input | Format |
|---|---|
| **Secrets** | `.env` at project root: `PERF_API_TOKEN=...`. Referenced as variable names only. |
| **Test data** | CSV or JSON; columns are used as `${data.<column>}` in paths/bodies. |
| **Auth `login_flow`** | `login_request: {method: POST, path: /login, body_template: {"email": "${username}", "password": "${password}"}, token_json_path: authentication.token}` with `env: {username: X_USER, password: X_PASS}` |
| **NFRs** `nfrs.yaml` | `nfrs: [{id: NFR-1, scope: "GET /rest/search", metric: http_req_duration.p95, comparator: "<", threshold: 500, unit: ms, approved_by: PO}]`. Import with `perf import-nfrs nfrs.yaml`. Scope can be an endpoint key, a journey name, or `*`. |
| **Work item** `.perf/workitems/KEY-22.yaml` | `title`, `description`, `acceptance_criteria`, `branch`, `base`, optional `endpoints:` / `journeys:` lists. |
| **Service map** `.perf/service-map.yaml` | `mappings: [{name, paths: ["src/search/**"], endpoints: ["GET /rest/search"], services: [...], components: [...], journeys: [Search], dependencies: [...]}]`. Maps changed files to what must be tested. |
| **Journeys** `.perf/journeys/*.spec.ts` | Deterministic Playwright code exporting `journey(page, ctx)`. See `/perf:frontend-journeys` or `evals/fixtures/journeys/`. |
| **Policy overrides** `.perf/policy.yaml` | Partial override of [`defaults.yaml`](engine/src/perfeng/policy/defaults.yaml): rates, repeats, regression %, caps. |

Check what is still missing at any time: `uv run perf intake --env qa`. Verify your spec parsed: `uv run perf discover --env qa`.

---

## After a run: get your report and your scripts

Run these from your **project folder** (the one with `.perf/`). If you are inside Claude Code, you can also just ask: *"write the report for this plan"* or *"export the scripts for this plan"*.

**1. Find your plan id** (every run belongs to a plan; Claude also prints it):
```bash
uv run perf plans
# plan_20261115103720_ea67c5  feature   KEY-22  perf-qa  executed  runs=7  2026-11-15 10:37
```
Omit the plan id in the commands below to use the **latest executed plan**.

**2. Get the performance test report**
```bash
uv run perf report <plan-id> --open        # writes .perf/reports/<plan-id>.html and opens it in your browser
uv run perf report <plan-id>               # Markdown: .perf/reports/<plan-id>.md
uv run perf report --stdout                # print the Markdown in the terminal
```
`/perf:feature` and `/perf:benchmark` also write both files to `.perf/reports/` at the end of a run. Open the `.html` file by double-clicking it (Windows: `start .perf\reports\<plan-id>.html`). It is one self-contained file you can email or attach to a ticket; print it to PDF from the browser if you need a PDF.

**3. Get the scripts to run by hand**
```bash
uv run perf export <plan-id>               # writes .perf/export/<plan-id>/  (or --out ./my-folder)
```
Then follow [Running the generated scripts manually](#running-the-generated-scripts-manually) below.

---

## The performance test report

Sections: **1** executive summary and verdict · **2** objectives, scope, Feature Impact Model · **3** inputs (target and allowlist, ownership, auth variable *names* and whether they are set, specs, test data with row counts, NFRs, journeys, effective policy, where each plan value came from, approvals with approver and time) · **4** environment and tools (OS, CPU, memory, k6/Playwright/Chromium/Lighthouse versions, load-generator CPU) · **5** test plan and run log · **6** API results (summary, throughput achieved vs planned, per-repeat detail, p95 charts with baseline and SLO lines) · **7** capacity ladder · **8** frontend journeys (LCP, CLS, INP, TBT, TTFB, FCP, Speed Index, resources, slowest resources vs lab budgets) · **9** success and failure (requests by outcome, HTTP status counts, aborted runs, verdict per target) · **10** SLO vs baseline vs trend · **11** server-side telemetry · **12** diagnosis · **13** observations and recommendations (rule-based) · **14** assumptions and limitations · appendix (run ids, artifact folders, reproduction, audit trail).
A sample from a real run: [docs/samples/sample-feature-report.html](docs/samples/sample-feature-report.html) (Markdown: [sample-feature-report.md](docs/samples/sample-feature-report.md)). Secrets never appear in reports.

---

## Running the generated scripts manually

Yes. The k6 scripts the engine runs are ordinary k6 scripts. Export them (step 3 above), then:

```bash
cd .perf/export/<plan-id>              # the bundle folder; read its README.md first
cp .env.example .env                   # fill in your own values (names only are listed); never commit .env
./k6/run.sh k6/load_GET_rest_products_search_10/script.js       # Git Bash / macOS / Linux
.\k6\run.ps1 k6\load_GET_rest_products_search_10\script.js     # PowerShell
# or directly:  k6 run k6/<test>/script.js     (k6 must be installed; the engine used v2.3.0)
```
- `perf setup` installs k6 at `~/.perfeng/tools/k6/v2.3.0/k6` (`k6.exe` on Windows). Add that folder to your PATH, or install k6 yourself.
- Each run writes `raw.ndjson` (every sample) and `summary.json` next to the script. Repeat each script about 5 times and compare the **median p95**, not a single run.
- Frontend journeys: `cd playwright && npm install && npx playwright install chromium && npx playwright test` (set `BASE_URL` to change the target). Lighthouse commands are in `lighthouse.md`.
- The bundle `README.md` lists every script, its workload, and how many repeats the engine used.

**Manual runs bypass the safeguards**: host allowlist and ownership check, the human approval record, hard caps, the watchdog, repeated-run orchestration, stability and baseline analysis, telemetry, registry and audit. The scripts keep their error/latency abort thresholds. Run them only against targets you may test. Claude's own shell cannot run `k6` (a hook blocks it): run the scripts in your own terminal. For the full analysis and report, run the plan through the plugin (`/perf:run`) or `perf run <plan-id>`.

---

## Skills: what and when to use them

| Skill | Use it when | What it does |
|---|---|---|
| `/perf:benchmark` | You have no baseline yet, or want a fresh measurement ("how fast is my API today?") | Intake, discovery, plan, approval, repeated runs, characterization incl. Web Vitals |
| `/perf:feature <ID> [env]` | A feature is done and you ask "what perf impact did it have?" | Impact model from work item + diff + service map, baseline-aligned plan, run, SLO/baseline/trend verdict, diagnosis |
| `/perf:plan <ID\|benchmark>` | You want to see the plan, with no load | Impact, tests, provenance of every value, assumptions |
| `/perf:run <plan\|ID>` | Execute a plan | Approval gate, background run, verdict |
| `/perf:status <id>` | Check a run, series, plan, baseline or job | Status, timings, artifact paths |
| `/perf:compare <a> <b>` | Compare runs, series or baselines | Deltas for p50/p90/p95/p99, errors, rps; one-sided p-value |
| `/perf:baseline` | List baselines or promote a stable benchmark | Human gate; feature runs never promotable |
| `/perf:diagnose <plan>` | A regression or SLO breach needs explaining | Evidence-backed hypotheses, Diagnostic Plan v2 |
| `/perf:history [target]` | Look at trends over time | Series history and drift vs baseline |
| `/perf:setup` | First use or tool problems | Install/check k6, Playwright, Chromium, Lighthouse |
| `/perf:frontend-journeys` | Add or change a browser journey | Writes a frozen Playwright journey; no LLM needed at run time |
| `/perf:k6-scripting` | You want a POST/data-driven API scenario | Explains `request_overrides`; no hand-written k6 |
| `/perf:nfr-provenance` | Talking about SLOs vs baselines vs defaults | The NFR / BASELINE / POLICY / ASSUMPTION rules, TEA hand-off |
| `/perf:results-explainer` | Explaining a verdict or "is this good?" | How to read percentiles, CV, significance, Web Vitals |
| `/perf:secrets` | Anyone asks how to share a token or password | The `.env` rule |

Skills also trigger from plain language ("what performance impact did my change introduce on perf-qa?").

**Agents** (read-only roles, used on request): `context-agent` (refines impact), `test-architect` (reviews plan risk and provenance), `analyzer` (explains results), `diagnostic-planner` (hypotheses and diagnostic plans), `baseline-manager` (promotion readiness).

---

## Core concepts

- **Benchmark ≠ Baseline.** A benchmark is one measurement series (default 5 repeats). A baseline is an **approved, versioned** reference created only from a *stable* benchmark series (p95 CV ≤ policy, stable errors, no generator saturation) by a human. Feature passes never move it, so gradual degradation (400 → 420 → 440 ms) is not normalized.
- **SLO ≠ baseline ≠ trend.** Example: SLO p95 < 500 ms, baseline 408 ms, current 470 ms → SLO PASS, +15.2% vs baseline → **PASS_WITH_REGRESSION_WARNING**.
- **Verdicts:** `PASS`, `PASS_WITH_REGRESSION_WARNING`, `FAIL_REGRESSION`, `FAIL_SLO`, `INCONCLUSIVE` (unstable, saturated, too few samples, or nothing to compare).
- **Provenance:** every planned value is tagged `NFR`, `BASELINE`, `POLICY` or `ASSUMPTION`. NFRs are never invented. Assumptions need your confirmation.
- **Deterministic analysis, LLM explanation.** The engine computes percentiles, deltas, bootstrap CIs, rank tests and verdicts. Claude only explains them and proposes through validated tools.
- **Diagnostics:** every hypothesis lists its evidence (metric, baseline vs current, source). With no evidence it says "insufficient evidence" and names the telemetry that would settle it.

---

## Safety, gates and secrets

| Gate | When |
|---|---|
| G1 intake | target ownership confirmed; production is refused |
| G2 plan | you approve the exact plan (content-hash bound) |
| G3 assumptions | ASSUMPTION values confirmed |
| G4 high-risk tests | capacity, stress, spike, soak need explicit approval |
| G5 baseline promotion | only for stable benchmark series |
| G6 diagnostic plan | before a Diagnostic Plan vN runs |
| G7 target change | changing a target is audited |

- `approve_*` tools trigger Claude Code's own permission prompt (plugin hook). The model cannot self-approve, and names like "claude" are refused.
- Caps on VUs, arrival rate and duration. k6 abort thresholds plus a wall-clock watchdog. The capacity ladder stops at the first breach.
- Raw `k6 run`, `locust`, `jmeter`, `ab`, `wrk`, `vegeta` and `artillery` from Claude's shell are blocked: all load goes through validated specs.
- **Secrets:** only `env:NAME` references. A hook blocks secret-looking chat messages and denies Claude reading `.env*`. Artifacts are redacted. If you pasted a secret somewhere, rotate it.

---

## CLI reference

`uv run perf <command>` (add `--json` for machine output). Run from your project folder, or set `PERF_PROJECT_DIR`.

`setup [--browser]` · `doctor` · `init` · `intake` · `discover` · `import-nfrs <file>` · `plan [--benchmark | --feature ID] --env E [--target K] [--capacity] [--peak]` · `approve <plan> --approver <you>` · `run <plan> [--only test]` · `status <id>` · `compare <a> <b>` · `history [--target T]` · `diagnose <plan>` · `plans` · `report [plan] [--html] [--open] [--out F]` · `export [plan] [--out DIR]` · `baseline list|candidates|promote <series>`

`plans` lists plan ids; `report [plan] [--open]` writes the full report to `.perf/reports/`; `export [plan]` writes the manual-run bundle to `.perf/export/<plan>/`. Plan id defaults to the latest executed plan.

`approve` and `baseline promote` are human gates: they ask for interactive confirmation, and Claude's shell may not run them.

Environment variables: `PERF_PROJECT_DIR`, `PERF_TOOLS_DIR` (toolchain location), `PERF_DB_URL` (registry; default SQLite), `PERF_POLICY_FILE`, `PERF_EXECUTOR` (`auto|local-process|docker`).

---

## Where things live

In **your project**:
```
.perf/project.yaml        inputs (no secrets)       .perf/policy.yaml        optional overrides
.perf/discovery.json      last discovery            .perf/service-map.yaml   code -> endpoints/journeys
.perf/workitems/*.yaml    work items                .perf/journeys/*.spec.ts frozen Playwright journeys
.perf/state/registry.db   plans, runs, series, baselines, approvals, audit
.perf/artifacts/<run>/    raw k6 NDJSON, script, trace.zip, HAR, screenshots, Lighthouse JSON, result.json
                             .perf/reports/<plan>.md/.html reports; .perf/export/<plan>/ manual-run bundles           .env / .env.example      secrets / placeholders
```
Tools install to the plugin data folder (or `~/.perfeng/tools`).

In **this repo:** `engine/` (Python), `skills/` `agents/` `hooks/` `.mcp.json` `bin/` (the plugin), `sut/` (Juice Shop + fault proxy), `evals/` and `plugin-evals/` (tests), `docs/`.

---

## Current limits

- Proven live only against OWASP Juice Shop on one machine. Local runs share CPU between load generator, browser and app, so the default cap is 100 req/s and results are "local-lab" (good for before/after, not for capacity claims).
- Only the `login_flow` auth was exercised live. `bearer`, `api_key`, `basic` and OAuth2 client-credentials are implemented but untested against real services.
- OpenAPI, Postman, GraphQL and HAR parsers were not run against real-world specs in the live tests, and write endpoints (POST/PUT) were not load tested. Check `perf discover` output first.
- Work items come from `.perf/workitems/<ID>.yaml`. Jira, Azure DevOps and GitHub adapters are not built.
- Feature impact needs a service map or route strings in the diff; otherwise little is found.
- gRPC and WebSocket are extension points only. Kubernetes, k6-operator, ECS, Cloud Run and ACA are Phase 2. The Docker worker image exists but is untested.
- The "no raw k6" eval needs a Bash sandbox (WSL2/Linux/CI).

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Every prompt is blocked after installing | The guard hook needs `uv` on PATH (it uses a uv-managed Python). Check `uv --version`. |
| `perf doctor` shows k6 missing | `uv run perf setup` (frontend tools: `--browser`) |
| "ownership not confirmed" | Set `ownership_confirmed: true` for that environment after you confirm you may load test it |
| Run rejected: "host not in allowlist" / "exceeds cap" | Fix `allowed_hosts` or lower the workload; caps can only be lowered per environment |
| "missing secret env vars" | Add the variable to the project `.env` |
| Series not eligible for baseline | Reasons are listed (few runs, p95 CV too high, saturation). Re-run on a quiet machine. |
| Verdict `INCONCLUSIVE` | No SLO/baseline to compare, or the run was unstable/saturated |
| Frontend journey failed | Open `.perf/artifacts/<run>/final.png`, `trace.zip`, `browser.log` |
| Plugin eval refuses the plugin | uv must copy files (`link-mode = "copy"` is set in `pyproject.toml`); recreate `.venv` |

---

## Evaluating the plugin

`uv run pytest engine/tests` (L0) · `uv run pytest evals/e2e` (L1, real Juice Shop) · `claude plugin eval .` (L2, Claude's behaviour) · `uv run pytest evals/headless` (L3, plugin drives a real run) · `uv run python evals/scorecard.py`. Details: [evals/README.md](evals/README.md).
