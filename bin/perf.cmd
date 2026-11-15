@echo off
rem perf CLI wrapper (Windows). On the Bash tool PATH while the plugin is enabled.
set "ROOT=%~dp0.."
if defined CLAUDE_PLUGIN_DATA set "UV_PROJECT_ENVIRONMENT=%CLAUDE_PLUGIN_DATA%\venv"
uv run --quiet --frozen --project "%ROOT%" perf %*
