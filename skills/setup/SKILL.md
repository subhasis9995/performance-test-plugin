---
name: setup
description: Install or check the perf plugin toolchain (pinned k6, Playwright + Chromium, Lighthouse) without Docker, and explain prerequisites. Use for "set up perf testing", "/perf setup", "perf doctor", "is k6 installed".
allowed-tools: mcp__plugin_perf_perf-engine__doctor, mcp__plugin_perf_perf-engine__setup_toolchain
---

Call `doctor`. If something is missing, say what will be installed and where (the plugin data directory, checksum-verified), then call `setup_toolchain(browser=<true if they want frontend tests>)`.
Prerequisites: Python >= 3.12 with uv, Node >= 20 for frontend journeys, and network access to GitHub releases and npm. Docker is optional. Kubernetes and cloud executors are planned for a later phase.
