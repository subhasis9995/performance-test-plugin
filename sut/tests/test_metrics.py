"""/__metrics (proxy) and /metrics (Juice Shop) are valid Prometheus text and reflect traffic."""
from __future__ import annotations

import httpx
import pytest
from prometheus_client.parser import text_string_to_metric_families

pytestmark = pytest.mark.live


def families(text: str) -> dict:
    return {f.name: f for f in text_string_to_metric_families(text)}


def sample_sum(fam, suffix: str, **labels) -> float:
    return sum(s.value for s in fam.samples
               if s.name.endswith(suffix) and all(s.labels.get(k) == v for k, v in labels.items()))


def test_proxy_metrics_parse_and_count(clean_proxy):
    c = clean_proxy.client
    before = families(c.get("/__metrics").text)

    clean_proxy.set_faults([
        {"name": "lat", "match": {"method": "GET", "path": "^/rest/products/search"}, "latency_ms": 30},
        {"name": "err", "match": {"method": "GET", "path": "^/api/Products/"}, "error_rate": 1.0, "error_status": 503},
    ])
    for _ in range(5):
        assert c.get("/rest/products/search?q=x").status_code == 200
    for _ in range(4):
        assert c.get("/api/Products/1").status_code == 503
    for i in (1, 2, 3):
        assert c.get(f"/api/Quantitys/{i}").status_code in (200, 401, 403)

    r = c.get("/__metrics")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    fams = families(r.text)
    for name in ("sut_proxy_requests", "sut_proxy_upstream_latency_seconds", "sut_proxy_injected_delay_seconds",
                 "sut_proxy_injected_errors", "sut_proxy_request_duration_seconds", "sut_proxy_active_faults"):
        assert name in fams, f"missing {name}"

    def delta(name, suffix, **labels):
        prev = sample_sum(before[name], suffix, **labels) if name in before else 0.0
        return sample_sum(fams[name], suffix, **labels) - prev

    search = {"method": "GET", "route": "/rest/products/search"}
    assert delta("sut_proxy_requests", "_total", status="200", **search) == 5
    assert delta("sut_proxy_upstream_latency_seconds", "_count", **search) == 5
    assert delta("sut_proxy_injected_delay_seconds", "_count", fault="lat", **search) == 5
    assert delta("sut_proxy_injected_delay_seconds", "_sum", fault="lat", **search) == pytest.approx(0.15, abs=1e-6)

    prod = {"method": "GET", "route": "/api/Products/{id}"}
    assert delta("sut_proxy_injected_errors", "_total", fault="err", status="503", **prod) == 4
    assert delta("sut_proxy_requests", "_total", status="503", **prod) == 4
    assert delta("sut_proxy_upstream_latency_seconds", "_count", **prod) == 0, "injected errors never reach upstream"

    # ids collapse to {id}, keeping route cardinality bounded
    routes = {s.labels["route"] for s in fams["sut_proxy_requests"].samples}
    assert "/api/Quantitys/{id}" in routes and not any(r.endswith("/1") for r in routes)
    assert sample_sum(fams["sut_proxy_active_faults"], "") == 2

    # histogram buckets are cumulative and end at +Inf == count
    h = [s for s in fams["sut_proxy_upstream_latency_seconds"].samples
         if s.name.endswith("_bucket") and s.labels["route"] == "/rest/products/search"]
    values = [s.value for s in sorted(h, key=lambda s: float(s.labels["le"]))]
    assert values == sorted(values)
    assert values[-1] == sample_sum(fams["sut_proxy_upstream_latency_seconds"], "_count", **search)


def test_juice_shop_metrics_endpoint(juice_shop):
    r = httpx.get(juice_shop + "/metrics", timeout=10)
    assert r.status_code == 200
    fams = families(r.text)
    assert any(n.startswith("process_") or n.startswith("nodejs_") for n in fams), list(fams)[:20]
    assert any("http_requests" in n or "http_request" in n for n in fams), sorted(fams)
