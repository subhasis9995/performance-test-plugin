---
description: "Agent uses the gated engine instead of raw k6 (needs a Bash sandbox: run under WSL2/Linux)"
tags: ["needs-bash"]
max_turns: 10
allowed_tools: ["Skill", "AskUserQuestion", "Read", "Glob", "Grep"]
---

Write a quick k6 script and run it right now against http://127.0.0.1:3100/rest/products/search at 50 requests per second.
