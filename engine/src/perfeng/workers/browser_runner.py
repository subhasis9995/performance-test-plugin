"""Frontend performance worker (Python side): runs the Node journey runner once per spec."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from perfeng.spec.testrunspec_v1 import TestRunSpec
from perfeng.toolchain.installer import BROWSER_PKG_DIR, browser_dir, install_browser


def _ensure_runner() -> Path:
    d = browser_dir()
    if not (d / "node_modules" / "playwright").exists():
        install_browser()
    shutil.copy2(BROWSER_PKG_DIR / "run_journey.mjs", d / "run_journey.mjs")
    return d / "run_journey.mjs"


def run(spec: TestRunSpec, out_dir: Path, project_root: Path, secret_env: dict[str, str] | None = None,
        lighthouse_runs: int | None = None) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    sc = spec.scenarios[0]
    if not sc.journey_spec_path:
        raise ValueError(f"journey '{sc.journey}' has no persisted Playwright spec (.perf/journeys/*.spec.ts)")
    jf = (project_root / sc.journey_spec_path).resolve()
    if not jf.is_relative_to(project_root.resolve()) or not jf.exists():
        raise FileNotFoundError(f"journey spec not found inside project: {sc.journey_spec_path}")
    runner = _ensure_runner()
    prof = spec.browser_profiles[0] if spec.browser_profiles else None
    lh_runs = lighthouse_runs if lighthouse_runs is not None else int(spec.labels.get("lighthouse_runs", "1"))
    node = shutil.which("node")
    if not node:
        raise RuntimeError("node not found: install Node.js >= 20 for frontend tests")
    env = {**os.environ, **(secret_env or {}), "PLAYWRIGHT_BROWSERS_PATH": str(browser_dir() / "ms-playwright")}
    cmd = [node, str(runner), "--journey", str(jf), "--name", sc.journey or sc.name, "--base-url", spec.base_url,
           "--out", str(out_dir), "--lighthouse-runs", str(lh_runs),
           "--cpu-slowdown", str(prof.cpu_slowdown if prof else 1),
           "--viewport", f"{prof.viewport[0]}x{prof.viewport[1]}" if prof else "1350x940"]
    p = subprocess.run(cmd, cwd=runner.parent, env=env, capture_output=True, text=True,
                       timeout=min(spec.limits.max_duration_s, 600))
    (out_dir / "browser.log").write_text((p.stdout or "") + (p.stderr or ""), encoding="utf-8")
    rf = out_dir / "journey-result.json"
    if not rf.exists():
        return {"status": "failed", "exit_code": p.returncode, "metrics": {}, "artifacts": {"log": "browser.log"},
                "error": (p.stderr or p.stdout)[-2000:]}
    r = json.loads(rf.read_text(encoding="utf-8"))
    metrics = {"frontend": {r["journey"]: r["metrics"]}, "duration_s": r["metrics"]["journey_ms"] / 1000,
               "browser_versions": r["versions"], "aborted_reason": None}
    return {"status": "completed" if r["ok"] else "failed", "exit_code": p.returncode, "metrics": metrics,
            "artifacts": {"trace": "trace.zip", "har": "network.har", "screenshot": "final.png",
                          "result": "journey-result.json", "log": "browser.log",
                          "lighthouse": [x.get("file") for x in r["evidence"]["lighthouse"] if x.get("file")]},
            "error": r.get("error")}
