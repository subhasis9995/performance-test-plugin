# Evaluating the perf plugin

The plugin is only called "working" for what these suites exercise. They use **safe, local, open-source targets**: OWASP Juice Shop (pinned, run natively on 127.0.0.1 behind our fault proxy) and an in-process mini server. No public website is ever load tested.

| Layer | What it proves | How |
|---|---|---|
| L0 `engine/tests` | percentile math vs numpy, verdict matrix, stability, significance, policy gate, approvals, baseline rules; real k6 against an in-process server (benchmark, baseline, feature PASS, injected regression, capacity stop) | `uv run pytest engine/tests` |
| L1 `evals/e2e` | install + validate + MCP handshake + hooks; then the real workflows against Juice Shop: discovery, provenance, gates, characterization (5→3 fast repeats), Web Vitals + trace + HAR + Lighthouse, promotion rules, unstable series refused, capacity safety stop, policy rejections, KEY-22 impact and negative control, regression detection, SLO-vs-baseline separation, diagnostic loop v2, insufficient-evidence path, baseline protection, compare/report, secrets never leak | `uv run pytest evals/e2e` |
| L2 `plugin-evals/` | Claude's behaviour with the plugin (engine mocked): skill triggering, intake questions and `.env` rule, no self-approval, baseline protection, evidence-based diagnosis, no invented NFRs, no raw k6, stays out of unrelated work, with a no-plugin baseline arm | `claude plugin eval . --runs 3 --trust-plugin --no-publish` |
| L3 `evals/headless` | the plugin drives a real run end to end: `/perf:feature KEY-22` stops at the human gate, the human approves, `/perf:run` executes and reports FAIL_REGRESSION, `/perf:diagnose` cites evidence. Checked against the registry | `uv run pytest evals/headless` |

Scorecard: `uv run python evals/scorecard.py`, which writes `evals/reports/latest/scorecard.{json,html}`.

Set `EVAL_FULL_POLICY=1` to run L1 with the full platform policy (5 repeats, 60 s steps, 25/50/100 req/s) instead of the fast eval policy.
L2 and L3 make billed model calls and need `claude` to be authenticated.
