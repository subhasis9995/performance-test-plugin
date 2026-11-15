"""Shared fixtures for the plugin evaluation suites (L1 deterministic E2E, L3 headless plugin journeys).

The system under test is OWASP Juice Shop running natively on 127.0.0.1:3000 behind the fault proxy on
127.0.0.1:3100 (env `perf-qa`). Regressions are injected deterministically through the proxy control API.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "evals" / "fixtures"))
INSTALL = REPO / "sut" / "juice-shop" / "install.py"
FAULTS = REPO / "sut" / "juice-shop" / "faults"
PROXY = "http://127.0.0.1:3100"
SEARCH = "GET /rest/products/search"
PRODUCT = "GET /api/Products/{id}"


def _healthy() -> bool:
    try:
        return httpx.get(PROXY + "/rest/admin/application-version", timeout=3).status_code == 200
    except httpx.HTTPError:
        return False


@pytest.fixture(scope="session")
def sut():
    """Ensure Juice Shop + fault proxy are running (native, no Docker) and a fresh test user exists."""
    started = False
    if not _healthy():
        subprocess.run([sys.executable, str(INSTALL), "--install"], check=True)
        subprocess.run([sys.executable, str(INSTALL), "--start", "--start-proxy"], check=True)
        started = True
    subprocess.run([sys.executable, str(INSTALL), "--health"], check=True)
    subprocess.run(["uv", "run", "--quiet", "--project", str(REPO / "sut"), "python",
                    str(REPO / "sut" / "fixtures" / "create_test_user.py")], check=True, capture_output=True)
    clear_faults()
    yield PROXY
    clear_faults()
    if started and os.environ.get("EVAL_KEEP_SUT") != "1":
        subprocess.run([sys.executable, str(INSTALL), "--stop"], check=False)


def set_faults(name: str) -> None:
    body = (FAULTS / f"{name}.yaml").read_bytes()
    httpx.put(PROXY + "/__faults", content=body, headers={"Content-Type": "application/yaml"}, timeout=5).raise_for_status()


def clear_faults() -> None:
    try:
        httpx.delete(PROXY + "/__faults", timeout=5)
    except httpx.HTTPError:
        pass


@pytest.fixture
def faults():
    class F:
        set = staticmethod(set_faults)
        clear = staticmethod(clear_faults)

    yield F
    clear_faults()


def perf_cli(project: Path, *args: str, check: bool = True, timeout: int = 1800) -> subprocess.CompletedProcess:
    env = {**os.environ, "PERF_PROJECT_DIR": str(project), "PYTHONIOENCODING": "utf-8"}
    p = subprocess.run(["uv", "run", "--quiet", "--project", str(REPO), "perf", *args], env=env,
                       capture_output=True, text=True, encoding="utf-8", timeout=timeout)
    if check and p.returncode != 0:
        raise AssertionError(f"perf {' '.join(args)} failed ({p.returncode}):\n{p.stdout[-3000:]}\n{p.stderr[-3000:]}")
    return p


def perf_json(project: Path, *args: str, **kw):
    return json.loads(perf_cli(project, *args, "--json", **kw).stdout)


@pytest.fixture(scope="module")
def project(tmp_path_factory, sut):
    from juice_project import build

    d = tmp_path_factory.mktemp("juice-project")
    build(d, fast=os.environ.get("EVAL_FULL_POLICY") != "1")
    return d


class Evidence:
    """Collects per-scenario evidence for the scorecard."""

    path = REPO / "evals" / "reports" / "latest" / "l1-evidence.jsonl"  # set per suite in _fresh_evidence

    @classmethod
    def record(_k, scenario: str, **data) -> None:
        _k.path.parent.mkdir(parents=True, exist_ok=True)
        with open(_k.path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"scenario": scenario, "at": time.time(), **data}, default=str) + "\n")


@pytest.fixture(scope="session", autouse=True)
def _fresh_evidence(request):
    layer = "l3" if any("headless" in str(a) for a in request.config.args) else "l1"
    Evidence.path = REPO / "evals" / "reports" / "latest" / f"{layer}-evidence.jsonl"
    if Evidence.path.exists():
        Evidence.path.unlink()
    yield


@pytest.fixture
def evidence():
    return Evidence


def have_claude() -> bool:
    return shutil.which("claude") is not None
