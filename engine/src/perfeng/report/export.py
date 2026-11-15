"""Export a plan's tests as a self-contained, manually runnable bundle.

    perf export <plan-id> --out ./perf-export

Bundle layout
  README.md                 how to run, what is and is not protected when running manually
  .env.example              secret variable NAMES the scripts read (fill values in your own .env; never commit it)
  data/*.json               test data (CSV converted to JSON)
  k6/<test>/script.js       generated k6 script (relative data paths), one per test step
  k6/<test>/spec.json       the validated TestRunSpec the script was generated from
  k6/run.ps1, k6/run.sh     helpers that load .env and run a script with k6
  playwright/               frozen journeys + playwright.config.ts + package.json (frontend tests)
  lighthouse.md             Lighthouse commands per journey page

Manual runs bypass the engine's safety layers (allowlist check, human approval record, watchdog, registry,
analysis). The scripts keep their abortOnFail thresholds, but you are responsible for running them only
against targets you are permitted to test.
"""

from __future__ import annotations

import csv
import json
import re
import shutil
from pathlib import Path

from perfeng.domain.models import TestType
from perfeng.generators.k6_script import generate
from perfeng.secrets import referenced_names


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_")[:80]


def export_plan(engine, plan_id: str, out: Path) -> dict:
    plan = engine.get_plan(plan_id)
    cfg = engine.require_cfg()
    ec = engine.env(plan.environment)
    out.mkdir(parents=True, exist_ok=True)
    data_dir = out / "data"
    data_dir.mkdir(exist_ok=True)
    rel_data: dict[str, str] = {}
    for ref in cfg.test_data:
        src = (engine.root / ref).resolve()
        if not src.is_relative_to(engine.root) or not src.exists():
            continue
        dst = data_dir / (src.stem + ".json")
        if src.suffix.lower() == ".csv":
            with open(src, newline="", encoding="utf-8") as fh:
                dst.write_text(json.dumps(list(csv.DictReader(fh))), encoding="utf-8")
        else:
            shutil.copy2(src, dst)
        rel_data[src.stem] = f"../../data/{dst.name}"  # relative to k6/<test>/script.js
    secret_names: set[str] = set()
    scripts: list[dict] = []
    for test in plan.tests:
        if test.type == TestType.FRONTEND:
            continue
        steps = list(range(len(test.workload))) if test.type == TestType.CAPACITY else [None]
        for si in steps:
            spec = engine.compile_spec(plan, test, si, f"export_{_slug(test.name)}" + (f"_{si}" if si is not None else ""))
            secret_names.update(referenced_names(spec))
            d = out / "k6" / (_slug(test.name) + (f"_step{si}" if si is not None else ""))
            d.mkdir(parents=True, exist_ok=True)
            (d / "script.js").write_text(generate(spec, rel_data), encoding="utf-8")
            (d / "spec.json").write_text(spec.model_dump_json(indent=2), encoding="utf-8")
            st = spec.scenarios[0].stages
            scripts.append({"dir": f"k6/{d.name}", "test": test.name, "type": test.type.value,
                            "workload": ", ".join(f"{s.rate:g} req/s x {s.duration_s}s" for s in st), "repeats": test.repeats.value})
    (out / "k6" / "run.sh").write_text(
        '#!/usr/bin/env sh\n# usage: ./run.sh k6/<test>/script.js [extra k6 args]   (run from the bundle root)\n'
        'set -e\n[ -f .env ] && set -a && . ./.env && set +a\n'
        'exec k6 run --out json="${SCRIPT%.js}.ndjson" --summary-export="${SCRIPT%.js}.summary.json" "$@"\n'.replace('"${SCRIPT%.js}.ndjson"', '"$(dirname "$1")/raw.ndjson"').replace('"${SCRIPT%.js}.summary.json"', '"$(dirname "$1")/summary.json"'),
        encoding="utf-8", newline="\n")
    (out / "k6" / "run.ps1").write_text(
        '# usage: .\\k6\\run.ps1 k6\\<test>\\script.js   (run from the bundle root)\n'
        'param([Parameter(Mandatory=$true)][string]$Script)\n'
        'if (Test-Path .env) { Get-Content .env | Where-Object { $_ -match "^\\s*[A-Za-z_][A-Za-z0-9_]*=" } | ForEach-Object {\n'
        '  $k,$v = $_ -split "=",2; Set-Item -Path "Env:$($k.Trim())" -Value $v.Trim().Trim(\'"\') } }\n'
        '$dir = Split-Path $Script\n'
        'k6 run --out "json=$dir\\raw.ndjson" --summary-export "$dir\\summary.json" $Script\n', encoding="utf-8")
    (out / ".env.example").write_text("# Fill in your own values in a file named .env next to this file. Never commit .env.\n" +
                                      "".join(f"{n}=\n" for n in sorted(secret_names)) or "# no secrets needed\n", encoding="utf-8")
    journeys = [j for j in cfg.journeys if j.get("spec") and plan.tests and any(t.type == TestType.FRONTEND and j["name"] in t.targets for t in plan.tests)]
    pw_lines = []
    if journeys:
        pw = out / "playwright"
        (pw / "journeys").mkdir(parents=True, exist_ok=True)
        for j in journeys:
            src = engine.root / j["spec"]
            if src.exists():
                shutil.copy2(src, pw / "journeys" / src.name)
        (pw / "package.json").write_text(json.dumps({"private": True, "type": "module", "devDependencies": {
            "@playwright/test": "1.65.1", "lighthouse": "13.6.0"}}, indent=2), encoding="utf-8")
        (pw / "playwright.config.ts").write_text(
            "import { defineConfig } from '@playwright/test';\n"
            "export default defineConfig({ testDir: './journeys', timeout: 120000,\n"
            f"  use: {{ baseURL: process.env.BASE_URL || '{ec.frontend_url or ec.base_url}', trace: 'on', screenshot: 'on' }} }});\n", encoding="utf-8")
        pw_lines = [f"- `{j['name']}`: `playwright/journeys/{Path(j['spec']).name}`" for j in journeys]
        lh = ["# Lighthouse (lab) commands\n"]
        for j in journeys:
            text = (engine.root / j["spec"]).read_text(encoding="utf-8") if (engine.root / j["spec"]).exists() else ""
            m = re.search(r"lighthouseUrl\s*=\s*'([^']+)'", text)
            url = (ec.frontend_url or ec.base_url).rstrip("/") + (m.group(1) if m else "/")
            lh.append(f"```bash\nnpx lighthouse \"{url}\" --only-categories=performance --form-factor=desktop "
                      f"--screenEmulation.disabled --output=json --output=html --output-path=./lighthouse-{_slug(j['name'])}\n```")
        (out / "lighthouse.md").write_text("\n".join(lh) + "\n", encoding="utf-8")
    rows = "\n".join(f"| `{s['dir']}/script.js` | {s['type']} | {s['workload']} | {s['repeats']} |" for s in scripts)
    readme = f"""# Manual run bundle for plan `{plan_id}`

Application **{plan.application_id}**, environment **{plan.environment}**, target `{ec.base_url}`.
Generated by the perf plugin from the approved plan. These are the same k6 scripts the engine runs.

## Prerequisites
- [k6](https://grafana.com/docs/k6/latest/set-up/install-k6/) (the engine used **v2.3.0**: `k6 version`)
- Secrets: copy `.env.example` to `.env` and fill in the values. Do not commit `.env` or paste values in chat.
- Node >= 20 for the Playwright/Lighthouse parts.

## Scripts
| Script | Type | Workload | Repeats used by the engine |
|---|---|---|---|
{rows}

## Run a k6 script
```bash
# from this folder
./k6/run.sh k6/{scripts[0]['dir'].split('/')[-1] if scripts else '<test>'}/script.js          # macOS/Linux/Git Bash
.\\k6\\run.ps1 k6\\{scripts[0]['dir'].split('/')[-1] if scripts else '<test>'}\\script.js       # PowerShell
# or directly (load .env into your shell first):  k6 run k6/<test>/script.js
```
Each run writes `raw.ndjson` and `summary.json` next to the script. Repeat each script {max((s['repeats'] for s in scripts), default=5)}x for a stable
picture and compare the **median p95**, not a single run. The scripts keep their abort thresholds
(error rate and latency) so a failing target stops the run.

## Frontend journeys
{chr(10).join(pw_lines) or 'none in this plan'}
```bash
cd playwright && npm install && npx playwright install chromium
BASE_URL={ec.frontend_url or ec.base_url} npx playwright test
```
Lighthouse commands: see `lighthouse.md`.

## What manual runs do NOT give you
Running scripts by hand **bypasses** the engine's protections: the host allowlist and ownership check, the
human approval record, hard caps, the wall-clock watchdog, repeated-run orchestration, stability and baseline
analysis, telemetry capture, the registry and the audit trail. Only run these against targets you are
permitted to load test. To get the full analysis, run the plan through the plugin (`/perf:run`) or
`perf run {plan_id}`.
"""
    (out / "README.md").write_text(readme, encoding="utf-8")
    return {"out": str(out), "scripts": scripts, "journeys": [j["name"] for j in journeys], "secrets": sorted(secret_names)}
