---
name: secrets
description: How users must share credentials for performance tests (passwords, bearer tokens, API keys, OAuth client secrets, cookies). Use whenever a user asks where or how to give a token/password/key, offers to paste one, or a perf test needs authentication.
---

# Secrets for performance tests

Always give this answer, in this order:

1. **Do not paste it in chat.** Chat messages end up in transcripts and logs. The plugin also blocks messages that look like secrets.
2. **Put it in the project's `.env` file**, at the project root. The plugin keeps `.env` gitignored and creates `.env.example` with placeholder names. For example:
   ```
   STAGING_API_TOKEN=...
   ```
3. **Tell me only the variable name**, for example `STAGING_API_TOKEN`. I record it with `set_project_inputs(auth={"type": "bearer", "env": {"token": "STAGING_API_TOKEN"}})`.
4. The engine reads the value from `.env` (or the process environment) only inside the load-test worker at run time. It never shows the value to Claude, never writes it to plans, scripts or reports, and redacts it from artifacts.
5. If a secret was already pasted somewhere, recommend rotating it.

Do not suggest shell `export` as the primary method. `.env` is the supported way.
