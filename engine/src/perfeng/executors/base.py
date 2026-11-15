"""Executors dispatch a validated TestRunSpec to a worker using the worker contract.

LocalProcess (default, no Docker) and Docker (auto-detected) are implemented.
Kubernetes Job, k6-operator, ECS/Fargate, Cloud Run Jobs and ACA Jobs are Phase 2 and
plug in by implementing `Executor.execute` with the same contract.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from perfeng.spec.testrunspec_v1 import TestRunSpec


class Executor(ABC):
    name = "abstract"

    @abstractmethod
    def execute(self, spec: TestRunSpec, out_dir: Path, project_root: Path, secret_env: dict[str, str]) -> dict[str, Any]:
        ...

    @staticmethod
    def _read_result(out_dir: Path, rc: int, log: str) -> dict[str, Any]:
        f = out_dir / "result.json"
        if not f.exists():
            return {"status": "failed", "exit_code": rc, "metrics": {}, "artifacts": {}, "error": log[-2000:]}
        return json.loads(f.read_text(encoding="utf-8"))


class LocalProcessExecutor(Executor):
    name = "local-process"

    def execute(self, spec, out_dir, project_root, secret_env):
        out_dir.mkdir(parents=True, exist_ok=True)
        spec_file = out_dir / "spec.json"
        spec_file.write_text(spec.model_dump_json(indent=2), encoding="utf-8")
        env = {**os.environ, **secret_env, "PERF_PROJECT_DIR": str(project_root)}
        p = subprocess.run(
            [sys.executable, "-m", "perfeng.workers.entrypoint", "--spec", str(spec_file), "--out", str(out_dir),
             "--project", str(project_root)],
            env=env, capture_output=True, text=True,
        )
        return self._read_result(out_dir, p.returncode, (p.stdout or "") + (p.stderr or ""))


class DockerExecutor(Executor):
    """Runs the worker image with the same contract. Secrets passed as -e NAME (value from env, not argv)."""

    name = "docker"

    def __init__(self, image: str = "perfeng-worker:0.1.0"):
        self.image = image

    def execute(self, spec, out_dir, project_root, secret_env):
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "spec.json").write_text(spec.model_dump_json(indent=2), encoding="utf-8")
        cmd = ["docker", "run", "--rm", "--network", "host",
               "-v", f"{out_dir.resolve()}:/out", "-v", f"{project_root.resolve()}:/project:ro"]
        for k in secret_env:
            cmd += ["-e", k]  # value inherited from our env, never on the command line
        cmd += [self.image, "--spec", "/out/spec.json", "--out", "/out", "--project", "/project"]
        p = subprocess.run(cmd, env={**os.environ, **secret_env}, capture_output=True, text=True)
        return self._read_result(out_dir, p.returncode, (p.stdout or "") + (p.stderr or ""))


def select_executor(preference: str | None = None) -> Executor:
    pref = preference or os.environ.get("PERF_EXECUTOR", "auto")
    if pref == "local-process":
        return LocalProcessExecutor()
    if pref == "docker":
        return DockerExecutor()
    from perfeng.executors.docker import docker_available

    if pref == "auto" and docker_available() and os.environ.get("PERF_DOCKER_IMAGE_READY") == "1":
        return DockerExecutor()
    return LocalProcessExecutor()
