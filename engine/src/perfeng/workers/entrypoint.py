"""Worker contract (same for native process, Docker, and Phase-2 cloud executors).

    perfeng-worker --spec <dir>/spec.json --out <dir> [--project <root>]

Inputs : TestRunSpec JSON; secrets as process env vars named by the spec's env: refs.
Outputs: <out>/result.json  {status, exit_code, metrics, artifacts, error, tool_versions}
Exit   : 0 completed, 2 aborted by safety stop, 1 failed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser("perfeng-worker")
    ap.add_argument("--spec", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--project", default=os.environ.get("PERF_PROJECT_DIR", "."))
    a = ap.parse_args(argv)

    from perfeng.secrets import redact_file
    from perfeng.spec.testrunspec_v1 import TestRunSpec
    from perfeng.toolchain.installer import install_k6, tool_versions

    spec = TestRunSpec.model_validate_json(Path(a.spec).read_text(encoding="utf-8"))
    out = Path(a.out)
    root = Path(a.project).resolve()
    secret_names = {r.ref.split(":", 1)[1] for r in spec.secret_refs}
    au = spec.auth
    for ref in (au.username, au.password, au.token, au.client_id, au.client_secret):
        if ref:
            secret_names.add(ref.ref.split(":", 1)[1])
    secret_env = {n: os.environ[n] for n in secret_names if n in os.environ}

    kinds = {s.kind for s in spec.scenarios}
    try:
        if kinds == {"api"}:
            from perfeng.workers.k6_runner import run

            res = run(spec, out, install_k6(), root, secret_env)
        elif kinds == {"browser"}:
            from perfeng.workers.browser_runner import run as brun

            res = brun(spec, out, root, secret_env)
        else:
            raise ValueError("a TestRunSpec must contain only api or only browser scenarios")
    except Exception as e:  # noqa: BLE001 - reported in contract
        res = {"status": "failed", "exit_code": None, "metrics": {}, "artifacts": {}, "error": f"{type(e).__name__}: {e}"}
    res["tool_versions"] = tool_versions()
    (out / "result.json").write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
    vals = list(secret_env.values())
    if vals:
        for f in out.rglob("*"):
            if f.is_file() and f.suffix in {".json", ".ndjson", ".log", ".js", ".har", ".txt", ".html"}:
                redact_file(f, vals)
    return {"completed": 0, "aborted": 2}.get(res["status"], 1)


if __name__ == "__main__":
    sys.exit(main())
