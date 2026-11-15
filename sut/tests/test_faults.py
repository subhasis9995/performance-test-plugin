"""Each fault type measurably changes traffic through the proxy in front of real Juice Shop."""
from __future__ import annotations

import statistics
import time
from pathlib import Path

import pytest

from conftest import FAULTS_DIR, fault_proxy, installer, median, pct, timed_get

pytestmark = pytest.mark.live

SEARCH = "/rest/products/search?q=apple"
PRODUCT = "/api/Products/1"


# ---------------------------------------------------------------- fault files + control API

@pytest.mark.parametrize("path", sorted(FAULTS_DIR.glob("*.yaml")), ids=lambda p: p.stem)
def test_sample_fault_files_parse(path: Path):
    faults = fault_proxy.load_fault_file(path)
    assert path.stem == "none" or faults, f"{path.name} has no faults"


def test_sample_files_present():
    names = {p.stem for p in FAULTS_DIR.glob("*.yaml")}
    assert {"none", "search-latency-200ms", "errors-10pct", "jitter-unstable", "slow-assets"} <= names


def test_control_api_roundtrip(clean_proxy):
    c = clean_proxy.client
    assert c.get("/__faults").json() == {"faults": []}
    clean_proxy.set_faults_file("search-latency-200ms.yaml")
    rules = c.get("/__faults").json()["faults"]
    assert [r["name"] for r in rules] == ["search-latency"]
    assert rules[0]["latency_ms"] == 200 and rules[0]["match"] == {"method": "GET", "path": "^/rest/products/search"}

    r = c.put("/__faults/extra", json={"match": {"path": "^/api/"}, "error_rate": 0.5})
    assert r.status_code == 200 and r.json()["name"] == "extra"
    assert c.get("/__faults/extra").json()["error_rate"] == 0.5
    assert len(c.get("/__faults").json()["faults"]) == 2
    assert c.delete("/__faults/extra").status_code == 200
    assert c.get("/__faults/extra").status_code == 404

    assert c.put("/__faults", json={"faults": [{"name": "bad", "path": "("}]}).status_code == 400
    assert c.put("/__faults", json={"faults": [{"name": "bad", "error_rate": 2}]}).status_code == 400
    assert c.put("/__faults", json={"faults": [{"name": "bad", "typo_ms": 1}]}).status_code == 400
    assert len(c.get("/__faults").json()["faults"]) == 1, "rejected PUT must not change rules"

    assert c.delete("/__faults").json() == {"faults": []}


def test_proxy_passthrough(clean_proxy, juice_shop):
    r = clean_proxy.client.get(SEARCH)
    assert r.status_code == 200
    assert r.json()["status"] == "success" and r.json()["data"]
    assert "X-Fault-Injected" not in r.headers
    # gzip passthrough intact
    r = clean_proxy.client.get("/", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200 and "<html" in r.text.lower()


# ---------------------------------------------------------------- latency

def test_fixed_latency(clean_proxy):
    c = clean_proxy.client
    timed_get(c, SEARCH, 3)  # warm up
    base_search = median(timed_get(c, SEARCH, 10))
    base_product = median(timed_get(c, PRODUCT, 10))

    clean_proxy.set_faults_file("search-latency-200ms.yaml")
    slow_search = timed_get(c, SEARCH, 10)
    product = median(timed_get(c, PRODUCT, 10))

    assert min(slow_search) >= 200, slow_search
    assert median(slow_search) - base_search >= 180, (base_search, slow_search)
    assert median(slow_search) - base_search <= 320, (base_search, slow_search)
    assert product - base_product < 100, "fault must only hit matching route"


def test_method_filter(clean_proxy):
    c = clean_proxy.client
    clean_proxy.set_faults([{"name": "post-only", "match": {"method": "POST", "path": "^/rest/products/search"}, "latency_ms": 300}])
    assert max(timed_get(c, SEARCH, 5)) < 250, "GET must not match a POST-only fault"


# ---------------------------------------------------------------- jitter

def test_jitter(clean_proxy):
    c = clean_proxy.client
    base = timed_get(c, SEARCH, 20)
    clean_proxy.set_faults_file("jitter-unstable.yaml")  # 20 ms + U(0, 300) ms
    xs = timed_get(c, SEARCH, 60)
    assert min(xs) >= 20
    assert statistics.pstdev(xs) > 50, f"jitter should spread latencies: stdev={statistics.pstdev(xs):.1f}"
    assert pct(xs, 0.9) - pct(xs, 0.1) > 150
    assert median(xs) - median(base) > 100   # mean added delay ~170 ms
    assert max(xs) < 320 + max(base) + 200


# ---------------------------------------------------------------- errors

def test_error_rate(clean_proxy):
    c = clean_proxy.client
    clean_proxy.set_faults_file("errors-10pct.yaml")
    n = 500
    codes = [c.get(PRODUCT).status_code for _ in range(n)]
    errors = codes.count(503)
    assert set(codes) <= {200, 503}
    assert 0.06 <= errors / n <= 0.14, f"error rate {errors / n:.3f}"

    r = None
    for _ in range(200):
        r = c.get(PRODUCT)
        if r.status_code == 503:
            break
    assert r is not None and r.status_code == 503
    assert r.headers["X-Fault-Injected"] == "api-errors" and r.json()["fault"] == "api-errors"

    root = [c.get("/").status_code for _ in range(50)]
    assert root.count(200) == 50, "non-API routes must not get errors"


def test_error_status_and_full_rate(clean_proxy):
    clean_proxy.set_faults([{"name": "down", "match": {"path": "^/api/Products"}, "error_rate": 1.0, "error_status": 500}])
    assert all(clean_proxy.client.get(PRODUCT).status_code == 500 for _ in range(10))


# ---------------------------------------------------------------- bandwidth

def _static_asset(min_kb: int, max_kb: int) -> tuple[str, int]:
    """Pick a frontend file between min_kb and max_kb and return (url path, size)."""
    root = installer.install_dir() / "frontend" / "dist" / "frontend"
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.suffix in (".js", ".css", ".png", ".jpg", ".svg") and min_kb * 1024 <= p.stat().st_size <= max_kb * 1024:
            return "/" + p.relative_to(root).as_posix(), p.stat().st_size
    pytest.skip("no suitable static asset found")


def _timed_download(client, path: str) -> tuple[float, int, dict]:
    t = time.perf_counter()
    r = client.get(path, headers={"Accept-Encoding": "identity"})
    body = r.content
    return time.perf_counter() - t, len(body), dict(r.headers)


def test_bandwidth_limit(clean_proxy):
    c = clean_proxy.client
    path, size = _static_asset(150, 600)
    base_s, base_len, _ = _timed_download(c, path)
    assert base_len == size

    rate_kbps = 100
    clean_proxy.set_faults([{"name": "bw", "match": {"method": "GET", "path": "^" + path.replace(".", r"\.") + "$"}, "bandwidth_kbps": rate_kbps}])
    slow_s, slow_len, headers = _timed_download(c, path)
    assert slow_len == size, "throttled body must be complete"
    assert headers.get("x-fault-throttled") == "bw"
    expected = size / (rate_kbps * 1024)
    assert slow_s >= expected * 0.85, (slow_s, expected)
    assert slow_s <= expected * 1.5 + base_s + 0.5, (slow_s, expected)


def test_slow_assets_profile(clean_proxy):
    c = clean_proxy.client
    path, size = _static_asset(20, 120)
    clean_proxy.set_faults_file("slow-assets.yaml")  # 50 ms + 100 KB/s on static files only
    s, n, headers = _timed_download(c, path)
    assert n == size and headers.get("x-fault-throttled") == "slow-assets"
    assert s >= 0.05 + size / (100 * 1024) * 0.85
    api = median(timed_get(c, SEARCH, 5))
    assert api < 150, "API routes must not be throttled by slow-assets"
