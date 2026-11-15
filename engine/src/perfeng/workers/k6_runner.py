"""API performance worker: run a TestRunSpec with k6 under a safety watchdog."""

from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import psutil

from perfeng.generators.k6_script import generate
from perfeng.normalize.k6 import parse_ndjson
from perfeng.spec.testrunspec_v1 import TestRunSpec


def _prepare_data(spec: TestRunSpec, root: Path, out: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for i, ref in enumerate(spec.test_data_refs):
        src = (root / ref).resolve()
        if not src.is_relative_to(root.resolve()):
            raise ValueError(f"test data must live inside the project: {ref}")
        dst = out / f"data{i}.json"
        if src.suffix.lower() == ".csv":
            with open(src, newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            dst.write_text(json.dumps(rows), encoding="utf-8")
        else:
            shutil.copy2(src, dst)
        files[src.stem] = dst.as_posix()
    return files


class _Sampler(threading.Thread):
    """Samples load-generator CPU / memory once per second."""

    def __init__(self, pid: int):
        super().__init__(daemon=True)
        self.pid = pid
        self.cpu: list[float] = []
        self.rss: list[float] = []
        self.stop_evt = threading.Event()

    def run(self) -> None:
        try:
            p = psutil.Process(self.pid)
            p.cpu_percent(None)
            ncpu = psutil.cpu_count() or 1
            while not self.stop_evt.wait(1.0):
                self.cpu.append(p.cpu_percent(None) / ncpu)  # normalized to whole machine
                self.rss.append(p.memory_info().rss / 1e6)
        except psutil.Error:
            pass


def run(
    spec: TestRunSpec,
    out_dir: Path,
    k6_bin: Path,
    project_root: Path,
    secret_env: dict[str, str] | None = None,
    grace_s: int = 30,
    telemetry_cb=None,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    data = _prepare_data(spec, project_root, out_dir)
    script = out_dir / "script.js"
    script.write_text(generate(spec, data), encoding="utf-8")
    (out_dir / "spec.json").write_text(spec.model_dump_json(indent=2), encoding="utf-8")
    raw = out_dir / "raw.ndjson"
    summary = out_dir / "summary.json"
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PERF_SECRET", "ANTHROPIC"))}
    env.update(secret_env or {})
    env["K6_NO_USAGE_REPORT"] = "true"
    cmd = [str(k6_bin), "run", "--quiet", "--no-color", f"--out=json={raw.as_posix()}",
           f"--summary-export={summary.as_posix()}", script.as_posix()]
    limit = min(spec.limits.max_duration_s, spec.total_duration_s() + grace_s) + 60  # + setup/teardown
    started = time.time()
    log = open(out_dir / "k6.log", "w", encoding="utf-8")
    proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, env=env, cwd=out_dir)
    sampler = _Sampler(proc.pid)
    sampler.start()
    tele_thread = None
    if telemetry_cb:
        tele_thread = threading.Thread(target=telemetry_cb, args=(proc,), daemon=True)
        tele_thread.start()
    killed = None
    try:
        proc.wait(timeout=limit)
    except subprocess.TimeoutExpired:
        killed = f"watchdog: exceeded wall-clock limit {limit}s"
        proc.kill()
        proc.wait()
    finally:
        sampler.stop_evt.set()
        log.close()
    elapsed = time.time() - started
    k6_log = (out_dir / "k6.log").read_text(encoding="utf-8", errors="replace")
    # k6 exit codes: 0 ok, 99 thresholds crossed (all our k6 thresholds are abortOnFail safety stops;
    # SLOs are evaluated by the analyzer, not k6), 105/107/108 script/setup abort.
    aborted = None
    if killed:
        aborted = killed
    elif proc.returncode == 99:
        crossed = [ln.strip() for ln in k6_log.splitlines() if "crossed" in ln or "threshold" in ln.lower()]
        aborted = "safety threshold breached: " + ("; ".join(crossed[:3]) or "k6 exit 99")
    elif proc.returncode in (105, 107, 108):
        aborted = f"k6 aborted (exit {proc.returncode}): " + (k6_log.strip().splitlines() or ["script abort"])[-1][:300]
    metrics: dict[str, Any] = parse_ndjson(raw) if raw.exists() else {}
    cpu = sorted(sampler.cpu)
    metrics["generator"] = {
        "cpu_pct_p95": cpu[int(0.95 * (len(cpu) - 1))] if cpu else None,
        "cpu_pct_max": cpu[-1] if cpu else None,
        "rss_mb_max": max(sampler.rss) if sampler.rss else None,
        "max_vus_reached": bool(metrics.get("vus_max")) and metrics.get("vus_max", 0) >= spec.peak_vus()
        and (metrics.get("dropped_iterations") or 0) > 0,
    }
    metrics["aborted_reason"] = aborted
    status = "aborted" if aborted else ("completed" if proc.returncode == 0 else "failed")
    return {
        "status": status,
        "exit_code": proc.returncode,
        "elapsed_s": elapsed,
        "metrics": metrics,
        "artifacts": {"script": "script.js", "raw": "raw.ndjson", "summary": "summary.json", "log": "k6.log", "spec": "spec.json"},
        "error": None if status != "failed" else k6_log[-2000:],
    }
