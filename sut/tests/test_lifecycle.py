"""install.py --start / --health / --status / --stop, plus loopback-only binding."""
from __future__ import annotations

import json
import socket

import pytest

from conftest import FAULTS_DIR, JS_PORT, JS_URL, free_port, healthy, installer, run_install, status_json

pytestmark = pytest.mark.live


def lan_ip() -> str | None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("10.255.255.255", 1))
            ip = s.getsockname()[0]
        except OSError:
            return None
    return None if ip.startswith("127.") else ip


def test_install_is_pinned_and_verified():
    assert installer.is_installed() or run_install("--install").returncode == 0
    meta = json.loads((installer.install_dir() / ".install-complete").read_text())
    assert meta["version"] == installer.JUICE_SHOP_VERSION
    assert meta["md5"] == installer.PINNED_MD5.get(meta["asset"], meta["md5"])


def test_start_health_status_stop(juice_shop):
    pid = installer.read_pid("juice-shop", JS_PORT)
    if pid is None:
        pytest.skip("Juice Shop on :3000 was not started by install.py; not touching it")

    # running + healthy
    r = run_install("--health", "--timeout", "30")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["healthy"] is True
    # packaged release reports e.g. "20.4.0-SNAPSHOT"
    assert json.loads(r.stdout)["version"].startswith(installer.JUICE_SHOP_VERSION)
    st = status_json()
    assert st["juice_shop"]["running"] and st["juice_shop"]["healthy"]

    # loopback only
    ip = lan_ip()
    if ip:
        with socket.socket() as s:
            s.settimeout(1)
            assert s.connect_ex((ip, JS_PORT)) != 0, f"Juice Shop reachable on {ip}:{JS_PORT}"

    # stop
    r = run_install("--stop")
    assert r.returncode == 0
    assert not installer.pid_alive(pid)
    assert not installer.pid_file("juice-shop", JS_PORT).exists()
    assert not healthy(JS_URL)
    st = status_json()
    assert not st["juice_shop"]["running"] and not st["juice_shop"]["healthy"]
    assert run_install("--health", "--timeout", "2").returncode == 1

    # start again (restores state for other tests; also proves start after stop)
    r = run_install("--start", "--timeout", "240")
    assert r.returncode == 0, r.stderr[-3000:]
    assert healthy(JS_URL)
    new_pid = installer.read_pid("juice-shop", JS_PORT)
    assert new_pid and new_pid != pid and installer.pid_alive(new_pid)

    # second --start is a no-op
    r = run_install("--start", "--timeout", "30")
    assert r.returncode == 0 and installer.read_pid("juice-shop", JS_PORT) == new_pid


def test_proxy_start_stop_via_installer(juice_shop):
    port = free_port()
    r = run_install("--start-proxy", "--proxy-port", str(port), "--faults", str(FAULTS_DIR / "search-latency-200ms.yaml"))
    assert r.returncode == 0, r.stderr
    try:
        assert healthy(f"http://127.0.0.1:{port}", "/__faults")
        st = status_json("--proxy-port", str(port))
        assert st["fault_proxy"]["running"] and st["fault_proxy"]["healthy"]
        assert healthy(f"http://127.0.0.1:{port}")  # forwards to Juice Shop health route
    finally:
        assert run_install("--stop-proxy", "--proxy-port", str(port)).returncode == 0
    assert not healthy(f"http://127.0.0.1:{port}", "/__faults")
