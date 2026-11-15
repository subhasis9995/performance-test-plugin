# perf plugin: `claude plugin eval` suite (L2, agent behaviour)

Run from the repository (plugin) root:

```bash
claude plugin eval . --runs 3 --trust-plugin --no-publish --judge-model sonnet --json evals/reports/latest/l2.json --max-cost-usd 15
```

- The perf-engine MCP server is **mocked** (see `mocks/perf-engine/`). These cases test what Claude does with the plugin: whether skills trigger, whether intake asks the right questions, gates, the secrets rule, provenance, baseline protection and evidence-based diagnosis. Real execution is covered by `evals/e2e` (L1) and `evals/headless` (L3).
- Cases tagged `needs-bash` grant Bash and need an OS sandbox. Native Windows has none, so run them under WSL2/Linux: `--tag needs-bash --allow-tools "Bash(k6 *)"`. Exclude them elsewhere by running the other tags.

Note: headless eval children do not get the AskUserQuestion tool, so intake cases grade the questions Claude asks in its reply. Interactive sessions use AskUserQuestion as the skills instruct.
