# ADR-0004 Human gates the model cannot self-approve; secrets only via .env
Status: accepted

Approvals (plan, assumptions, stress/capacity, baseline promotion, diagnostic re-run, target/cap change) are recorded by `approve_*` MCP tools. The plugin's PreToolUse hook returns `permissionDecision: "ask"` for those tools, so Claude Code shows its native permission prompt to the human. Execution tools refuse without an approval whose spec hash matches.
Secrets are referenced as `env:NAME`, resolved only inside the engine at execution time from the process environment or the project `.env`. Hooks block secret-looking chat prompts and deny Claude reading `.env*` files. Artifacts are redacted.
