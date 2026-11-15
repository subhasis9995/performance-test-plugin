"""Merge evaluation layers into one scorecard (JSON + HTML).

    uv run python evals/scorecard.py [--run]     # --run executes L0, L1 and L3 first (L2 needs `claude plugin eval`)

Inputs (written by the suites):
  evals/reports/latest/l0.xml, l1.xml, l3.xml   pytest junit
  evals/reports/latest/l1-evidence.jsonl        per-scenario evidence
  evals/reports/latest/l2.json                  `claude plugin eval --json` result
Every skipped or missing layer is listed with its reason — nothing is reported as passing that did not run.
"""

from __future__ import annotations

import argparse
import html
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "evals" / "reports" / "latest"


def junit(path: Path) -> dict:
    if not path.exists():
        return {"status": "not run", "cases": []}
    root = ET.parse(path).getroot()
    cases = []
    for tc in root.iter("testcase"):
        st = "passed"
        msg = ""
        for tag in ("failure", "error", "skipped"):
            el = tc.find(tag)
            if el is not None:
                st = {"failure": "failed", "error": "error", "skipped": "skipped"}[tag]
                msg = (el.get("message") or "")[:300]
        cases.append({"name": f"{tc.get('classname', '').split('.')[-1]}::{tc.get('name')}", "status": st,
                      "time_s": float(tc.get("time", 0)), "message": msg})
    n = {s: sum(1 for c in cases if c["status"] == s) for s in ("passed", "failed", "error", "skipped")}
    status = "passed" if n["passed"] and not n["failed"] and not n["error"] else ("failed" if n["failed"] or n["error"] else "skipped")
    return {"status": status, "counts": n, "cases": cases}


def l2(path: Path) -> dict:
    if not path.exists():
        return {"status": "not run", "reason": "run `claude plugin eval . --json evals/reports/latest/l2.json` (needs Claude auth)"}
    d = json.loads(path.read_text(encoding="utf-8"))
    cases = []
    for c in d.get("cases", []):
        w = [r["score"] for r in c.get("arms", {}).get("with", [])]
        wo = [r["score"] for r in c.get("arms", {}).get("without", [])]
        ws = sum(w) / len(w) if w else None
        wos = sum(wo) / len(wo) if wo else None
        cases.append({"name": c.get("name"), "with": round(ws, 2) if ws is not None else None,
                      "without": round(wos, 2) if wos is not None else None,
                      "delta": round(ws - wos, 2) if ws is not None and wos is not None else None,
                      "passed": ws is not None and ws >= 1.0})
    ok = all(c["passed"] for c in cases) if cases else False
    return {"status": "passed" if ok else "failed", "cases": cases, "raw_keys": list(d)[:20]}


def evidence() -> list[dict]:
    f = OUT / "l1-evidence.jsonl"
    return [json.loads(ln) for ln in f.read_text(encoding="utf-8").splitlines()] if f.exists() else []


def run_layers() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    subprocess.run(["uv", "run", "pytest", "engine/tests", "-q", f"--junitxml={OUT / 'l0.xml'}"], cwd=REPO)
    subprocess.run(["uv", "run", "pytest", "evals/e2e", "-q", f"--junitxml={OUT / 'l1.xml'}"], cwd=REPO)
    subprocess.run(["uv", "run", "pytest", "evals/headless", "-q", f"--junitxml={OUT / 'l3.xml'}"], cwd=REPO)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true")
    a = ap.parse_args()
    if a.run:
        run_layers()
    card = {
        "generated_at": datetime.now(UTC).isoformat(),
        "layers": {
            "L0 unit/property/integration (engine)": junit(OUT / "l0.xml"),
            "L1 deterministic E2E vs Juice Shop": junit(OUT / "l1.xml"),
            "L2 agent behaviour (claude plugin eval)": l2(OUT / "l2.json"),
            "L3 headless plugin journey": junit(OUT / "l3.xml"),
        },
        "evidence": evidence(),
        "not_covered": ["Kubernetes / k6-operator / ECS / Cloud Run / ACA executors (Phase 2: contract only)",
                        "Docker executor live run (only when a Docker daemon is present)",
                        "gRPC / WebSocket protocols (extension points, not executed in v1)"],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "scorecard.json").write_text(json.dumps(card, indent=2), encoding="utf-8")
    rows = []
    for name, layer in card["layers"].items():
        cnt = layer.get("counts") or {}
        rows.append(f"<tr><td>{html.escape(name)}</td><td class='{layer['status'].replace(' ', '-')}'>{layer['status']}</td>"
                    f"<td>{cnt.get('passed', '')}</td><td>{cnt.get('failed', 0) + cnt.get('error', 0) if cnt else ''}</td>"
                    f"<td>{cnt.get('skipped', '')}</td><td>{html.escape(layer.get('reason', ''))}</td></tr>")
    detail = []
    for name, layer in card["layers"].items():
        for c in layer.get("cases", []):
            st = c.get("status") or ("passed" if c.get("passed") else "failed")
            detail.append(f"<tr><td>{html.escape(name.split(' ')[0])}</td><td>{html.escape(str(c['name']))}</td>"
                          f"<td class='{st}'>{st}</td><td>{html.escape(c.get('message', '') or str(c.get('with', '')))}</td></tr>")
    ev = "".join(f"<tr><td>{html.escape(e['scenario'])}</td><td><code>{html.escape(json.dumps({k: v for k, v in e.items() if k not in ('scenario', 'at')})[:400])}</code></td></tr>"
                 for e in card["evidence"])
    page = f"""<!doctype html><meta charset=utf-8><title>perf plugin eval scorecard</title>
<style>body{{font-family:system-ui;max-width:1200px;margin:2rem auto;padding:0 1rem}}table{{border-collapse:collapse;width:100%;margin:1rem 0}}
td,th{{border:1px solid #ccc;padding:4px 8px;font-size:14px;text-align:left}}.passed{{background:#d9f7d9}}.failed,.error{{background:#f8d7d7}}
.skipped,.not-run{{background:#fff3cd}}code{{font-size:12px}}</style>
<h1>perf plugin: evaluation scorecard</h1><p>{card['generated_at']}</p>
<table><tr><th>layer</th><th>status</th><th>passed</th><th>failed</th><th>skipped</th><th>note</th></tr>{''.join(rows)}</table>
<h2>Cases</h2><table><tr><th>layer</th><th>case</th><th>status</th><th>detail</th></tr>{''.join(detail)}</table>
<h2>L1 evidence</h2><table>{ev}</table>
<h2>Not covered in this phase</h2><ul>{''.join(f'<li>{html.escape(x)}</li>' for x in card['not_covered'])}</ul>"""
    (OUT / "scorecard.html").write_text(page, encoding="utf-8")
    print(f"scorecard: {OUT / 'scorecard.html'}")
    for name, layer in card["layers"].items():
        print(f"  {layer['status']:<8} {name} {layer.get('counts', '')}")
    return 0 if all(l["status"] in ("passed",) for l in card["layers"].values()) else 1


if __name__ == "__main__":
    sys.exit(main())
