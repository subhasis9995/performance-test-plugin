"""Shared fixtures: native Juice Shop (session) and a fault proxy subprocess (module)."""
from __future__ import annotations

import importlib.util
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

SUT = Path(__file__).resolve().parents[1]
JS_DIR = SUT / "juice-shop"
INSTALL = JS_DIR / "install.py"
FAULT_PROXY = JS_DIR / "fault_proxy.py"
FAULTS_DIR = JS_DIR / "faults"
JS_PORT = 3000
JS_URL = f"http://127.0.0.1:{JS_PORT}"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


installer = load_module("js_install", INSTALL)
fault_proxy = load_module("fault_proxy", FAULT_PROXY)


def run_install(*args: str, timeout: float = 900) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(INSTALL), *args], capture_output=True, text=True, timeout=timeout)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def healthy(url: str, path: str = "/rest/admin/application-version") -> bool:
    try:
        return httpx.get(url + path, timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


@pytest.fixture(scope="session")
def juice_shop():
    """Ensure Juice Shop runs on 127.0.0.1:3000; stop it afterwards only if this session started it."""
    started = False
    if not healthy(JS_URL):
        r = run_install("--start", "--timeout", "240")
        assert r.returncode == 0, f"juice shop failed to start:\n{r.stderr[-3000:]}"
        started = True
    yield JS_URL
    if started:
        run_install("--stop")


class ProxyHandle:
    def __init__(self, port: int, proc: subprocess.Popen):
        self.port = port
        self.url = f"http://127.0.0.1:{port}"
        self.proc = proc
        self.client = httpx.Client(base_url=self.url, timeout=60)

    def set_faults_file(self, name: str) -> dict:
        body = (FAULTS_DIR / name).read_text(encoding="utf-8")
        r = self.client.put("/__faults", content=body, headers={"Content-Type": "application/yaml"})
        r.raise_for_status()
        return r.json()

    def set_faults(self, faults: list[dict]) -> dict:
        r = self.client.put("/__faults", json={"faults": faults})
        r.raise_for_status()
        return r.json()

    def clear(self) -> None:
        self.client.delete("/__faults").raise_for_status()


@pytest.fixture(scope="module")
def proxy(juice_shop):
    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, str(FAULT_PROXY), "--listen", f"127.0.0.1:{port}", "--upstream", juice_shop,
         "--seed", "1234", "--log-level", "WARNING"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and not healthy(url, "/__health"):
        if proc.poll() is not None:
            raise RuntimeError(proc.stdout.read().decode(errors="replace"))  # type: ignore[union-attr]
        time.sleep(0.2)
    handle = ProxyHandle(port, proc)
    yield handle
    handle.client.close()
    proc.terminate()
    try:
        proc.wait(10)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture
def clean_proxy(proxy):
    proxy.clear()
    yield proxy
    proxy.clear()


def timed_get(client: httpx.Client, path: str, n: int) -> list[float]:
    out = []
    for _ in range(n):
        t = time.perf_counter()
        r = client.get(path)
        r.read()
        out.append((time.perf_counter() - t) * 1000)
    return out


def median(xs: list[float]) -> float:
    s = sorted(xs)
    return s[len(s) // 2]


def pct(xs: list[float], q: float) -> float:
    s = sorted(xs)
    return s[min(len(s) - 1, int(q * len(s)))]


def status_json(*extra: str) -> dict:
    r = run_install("--status", *extra)
    return json.loads(r.stdout)
