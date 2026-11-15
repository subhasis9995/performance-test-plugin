#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["aiohttp>=3.10", "pyyaml>=6.0", "prometheus-client>=0.20"]
# ///
"""Fault-injecting reverse proxy for the system under test.

    client -> 127.0.0.1:3100 (this proxy) -> 127.0.0.1:3000 (Juice Shop)

Faults are rules matched by HTTP method + path regex. Each matching rule applies:
    latency_ms      fixed added delay before the request is forwarded
    jitter_ms       extra uniform random delay in [0, jitter_ms]
    error_rate      probability (0..1) of answering with error_status instead of forwarding
    error_status    status code for injected errors (default 503)
    bandwidth_kbps  response body throttled to this many KB/s (1 KB = 1024 bytes)
When several rules match, delays add up, the first rule whose error roll hits wins,
and the lowest bandwidth limit applies.

Control API (loopback only, never forwarded):
    GET    /__faults           current rules as JSON
    PUT    /__faults           replace all rules (JSON or YAML body: {"faults": [...]})
    DELETE /__faults           remove all rules
    GET    /__faults/{name}    one rule
    PUT    /__faults/{name}    add or replace one rule
    DELETE /__faults/{name}    remove one rule
    GET    /__metrics          Prometheus text format
    GET    /__health           proxy liveness (does not touch upstream)

Fault file YAML:
    faults:
      - name: search-latency
        match: {method: GET, path: '^/rest/products/search'}
        latency_ms: 200
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import aiohttp
import yaml
from aiohttp import web
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest
from prometheus_client.exposition import CONTENT_TYPE_LATEST

log = logging.getLogger("fault_proxy")

HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
    "transfer-encoding", "upgrade", "host", "content-length",
}
LATENCY_BUCKETS = (0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.15, 0.2, 0.3, 0.5, 0.75, 1, 1.5, 2, 3, 5, 10, 30)
MAX_ROUTES = 200
STATIC_EXT = re.compile(r"\.(js|mjs|css|map|png|jpe?g|gif|svg|ico|webp|woff2?|ttf|eot|json|txt|html?)$", re.I)
ID_SEGMENT = re.compile(r"^(\d+|[0-9a-fA-F-]{16,}|[0-9a-fA-F]{24})$")


# --------------------------------------------------------------------------- fault model

class FaultError(ValueError):
    pass


@dataclass
class Fault:
    name: str
    path: str = ".*"
    method: str = "*"
    latency_ms: float = 0.0
    jitter_ms: float = 0.0
    error_rate: float = 0.0
    error_status: int = 503
    bandwidth_kbps: float | None = None
    enabled: bool = True
    _regex: re.Pattern = field(default=None, repr=False, compare=False)  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if not self.name or not isinstance(self.name, str):
            raise FaultError("fault needs a non-empty string name")
        try:
            self._regex = re.compile(self.path)
        except re.error as e:
            raise FaultError(f"fault {self.name}: bad path regex {self.path!r}: {e}") from e
        self.method = (self.method or "*").upper()
        for attr in ("latency_ms", "jitter_ms"):
            v = float(getattr(self, attr))
            if v < 0:
                raise FaultError(f"fault {self.name}: {attr} must be >= 0")
            setattr(self, attr, v)
        self.error_rate = float(self.error_rate)
        if not 0.0 <= self.error_rate <= 1.0:
            raise FaultError(f"fault {self.name}: error_rate must be within 0..1")
        self.error_status = int(self.error_status)
        if not 100 <= self.error_status <= 599:
            raise FaultError(f"fault {self.name}: error_status must be an HTTP status")
        if self.bandwidth_kbps is not None:
            self.bandwidth_kbps = float(self.bandwidth_kbps)
            if self.bandwidth_kbps <= 0:
                raise FaultError(f"fault {self.name}: bandwidth_kbps must be > 0")

    @classmethod
    def from_dict(cls, d: dict[str, Any], default_name: str | None = None) -> "Fault":
        if not isinstance(d, dict):
            raise FaultError("each fault must be a mapping")
        d = dict(d)
        match = d.pop("match", None) or {}
        if not isinstance(match, dict):
            raise FaultError("match must be a mapping with method/path")
        d.setdefault("path", match.get("path", ".*"))
        d.setdefault("method", match.get("method", "*"))
        if "name" not in d and default_name:
            d["name"] = default_name
        known = {"name", "path", "method", "latency_ms", "jitter_ms", "error_rate", "error_status", "bandwidth_kbps", "enabled"}
        unknown = set(d) - known
        if unknown:
            raise FaultError(f"fault {d.get('name')}: unknown keys {sorted(unknown)}")
        return cls(**d)

    def to_dict(self) -> dict[str, Any]:
        d = {k: v for k, v in asdict(self).items() if not k.startswith("_")}
        d["match"] = {"method": d.pop("method"), "path": d.pop("path")}
        return d

    def matches(self, method: str, path: str) -> bool:
        return self.enabled and (self.method in ("*", method)) and self._regex.search(path) is not None


def parse_faults(doc: Any) -> list[Fault]:
    if doc is None:
        return []
    if isinstance(doc, dict):
        doc = doc.get("faults") or []
    if not isinstance(doc, list):
        raise FaultError("expected {faults: [...]} or a list of faults")
    faults = [Fault.from_dict(item) for item in doc]
    names = [f.name for f in faults]
    if len(names) != len(set(names)):
        raise FaultError("fault names must be unique")
    return faults


def load_fault_file(path: str | Path) -> list[Fault]:
    return parse_faults(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


def parse_body(raw: bytes, content_type: str) -> Any:
    text = raw.decode("utf-8") if raw else ""
    if not text.strip():
        return None
    if "json" in content_type:
        return json.loads(text)
    return yaml.safe_load(text)  # YAML is a superset of JSON


# --------------------------------------------------------------------------- metrics

class Metrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        r = self.registry
        self.requests = Counter("sut_proxy_requests", "Requests handled by the fault proxy", ["method", "route", "status"], registry=r)
        self.upstream_latency = Histogram(
            "sut_proxy_upstream_latency_seconds", "Upstream time to response headers (excludes injected delay)",
            ["method", "route"], buckets=LATENCY_BUCKETS, registry=r)
        self.request_duration = Histogram(
            "sut_proxy_request_duration_seconds", "Total time spent in the proxy per request, including injected faults",
            ["method", "route"], buckets=LATENCY_BUCKETS, registry=r)
        self.injected_delay = Histogram(
            "sut_proxy_injected_delay_seconds", "Delay injected by faults (latency + jitter)",
            ["method", "route", "fault"], buckets=LATENCY_BUCKETS, registry=r)
        self.injected_errors = Counter(
            "sut_proxy_injected_errors", "Error responses injected by faults", ["method", "route", "fault", "status"], registry=r)
        self.throttled_bytes = Counter(
            "sut_proxy_throttled_bytes", "Response bytes sent under a bandwidth limit", ["method", "route", "fault"], registry=r)
        self.upstream_errors = Counter(
            "sut_proxy_upstream_errors", "Upstream connection failures", ["method", "route"], registry=r)
        self.active_faults = Gauge("sut_proxy_active_faults", "Number of enabled fault rules", registry=r)
        self._routes: set[str] = set()

    def route(self, path: str) -> str:
        """Collapse a request path to a low-cardinality route label."""
        segs = [s for s in path.split("/") if s]
        if not segs:
            return "/"
        if segs[0] not in ("rest", "api", "socket.io", "metrics") and STATIC_EXT.search(segs[-1]):
            ext = STATIC_EXT.search(segs[-1]).group(1).lower()  # type: ignore[union-attr]
            return f"/{segs[0]}/*.{ext}" if len(segs) > 1 else f"/*.{ext}"
        templ = "/" + "/".join("{id}" if ID_SEGMENT.match(s) else s for s in segs[:4])
        if templ in self._routes:
            return templ
        if len(self._routes) >= MAX_ROUTES:
            return "other"
        self._routes.add(templ)
        return templ


# --------------------------------------------------------------------------- proxy

class FaultProxy:
    def __init__(self, upstream: str, faults: list[Fault] | None = None, seed: int | None = None) -> None:
        self.upstream = upstream.rstrip("/")
        self.faults: list[Fault] = list(faults or [])
        self.rng = random.Random(seed)
        self.metrics = Metrics()
        self.metrics.active_faults.set(sum(f.enabled for f in self.faults))
        self.session: aiohttp.ClientSession | None = None

    # -- lifecycle
    async def on_startup(self, app: web.Application) -> None:
        self.session = aiohttp.ClientSession(
            auto_decompress=False,
            timeout=aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=300),
            connector=aiohttp.TCPConnector(limit=0, limit_per_host=0, force_close=False),
        )

    async def on_cleanup(self, app: web.Application) -> None:
        if self.session:
            await self.session.close()

    def app(self) -> web.Application:
        app = web.Application(client_max_size=64 * 1024 * 1024)
        app.on_startup.append(self.on_startup)
        app.on_cleanup.append(self.on_cleanup)
        app.router.add_route("GET", "/__faults", self.get_faults)
        app.router.add_route("PUT", "/__faults", self.put_faults)
        app.router.add_route("DELETE", "/__faults", self.delete_faults)
        app.router.add_route("GET", "/__faults/{name}", self.get_fault)
        app.router.add_route("PUT", "/__faults/{name}", self.put_fault)
        app.router.add_route("DELETE", "/__faults/{name}", self.delete_fault)
        app.router.add_route("GET", "/__metrics", self.get_metrics)
        app.router.add_route("GET", "/__health", self.get_health)
        app.router.add_route("*", "/{tail:.*}", self.proxy)
        return app

    def _set_faults(self, faults: list[Fault]) -> None:
        self.faults = faults
        self.metrics.active_faults.set(sum(f.enabled for f in faults))

    # -- control API
    def _faults_json(self) -> web.Response:
        return web.json_response({"faults": [f.to_dict() for f in self.faults]})

    async def get_faults(self, request: web.Request) -> web.Response:
        return self._faults_json()

    async def put_faults(self, request: web.Request) -> web.Response:
        try:
            faults = parse_faults(parse_body(await request.read(), request.content_type))
        except (FaultError, ValueError, TypeError, yaml.YAMLError) as e:
            return web.json_response({"error": str(e)}, status=400)
        self._set_faults(faults)
        log.info("faults replaced: %s", [f.name for f in faults])
        return self._faults_json()

    async def delete_faults(self, request: web.Request) -> web.Response:
        self._set_faults([])
        log.info("faults cleared")
        return self._faults_json()

    async def get_fault(self, request: web.Request) -> web.Response:
        name = request.match_info["name"]
        for f in self.faults:
            if f.name == name:
                return web.json_response(f.to_dict())
        return web.json_response({"error": f"no fault {name}"}, status=404)

    async def put_fault(self, request: web.Request) -> web.Response:
        name = request.match_info["name"]
        try:
            doc = parse_body(await request.read(), request.content_type) or {}
            if isinstance(doc, dict):
                doc = {**doc, "name": name}
            fault = Fault.from_dict(doc)
        except (FaultError, ValueError, TypeError, yaml.YAMLError) as e:
            return web.json_response({"error": str(e)}, status=400)
        self._set_faults([f for f in self.faults if f.name != name] + [fault])
        log.info("fault set: %s", name)
        return web.json_response(fault.to_dict())

    async def delete_fault(self, request: web.Request) -> web.Response:
        name = request.match_info["name"]
        remaining = [f for f in self.faults if f.name != name]
        if len(remaining) == len(self.faults):
            return web.json_response({"error": f"no fault {name}"}, status=404)
        self._set_faults(remaining)
        return self._faults_json()

    async def get_metrics(self, request: web.Request) -> web.Response:
        body = generate_latest(self.metrics.registry)
        return web.Response(body=body, headers={"Content-Type": CONTENT_TYPE_LATEST})

    async def get_health(self, request: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "upstream": self.upstream, "faults": len(self.faults)})

    # -- data path
    async def proxy(self, request: web.Request) -> web.StreamResponse:
        t0 = time.perf_counter()
        method, path = request.method, request.path
        route = self.metrics.route(path)
        m = self.metrics
        matched = [f for f in self.faults if f.matches(method, path)]

        # 1. delay
        for f in matched:
            delay = f.latency_ms + (self.rng.uniform(0, f.jitter_ms) if f.jitter_ms else 0.0)
            if delay > 0:
                await asyncio.sleep(delay / 1000.0)
                m.injected_delay.labels(method, route, f.name).observe(delay / 1000.0)

        # 2. error
        for f in matched:
            if f.error_rate > 0 and self.rng.random() < f.error_rate:
                m.injected_errors.labels(method, route, f.name, str(f.error_status)).inc()
                m.requests.labels(method, route, str(f.error_status)).inc()
                m.request_duration.labels(method, route).observe(time.perf_counter() - t0)
                return web.json_response(
                    {"error": "injected fault", "fault": f.name, "status": f.error_status},
                    status=f.error_status, headers={"X-Fault-Injected": f.name})

        limits = [f for f in matched if f.bandwidth_kbps]
        throttle = min(limits, key=lambda f: f.bandwidth_kbps) if limits else None  # type: ignore[arg-type,return-value]

        if request.headers.get("Upgrade", "").lower() == "websocket":
            return await self._proxy_ws(request, route, t0)

        # 3. forward
        assert self.session is not None
        url = self.upstream + request.rel_url.path_qs
        headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP}
        peer = request.remote or ""
        headers["X-Forwarded-For"] = ", ".join(x for x in (request.headers.get("X-Forwarded-For"), peer) if x)
        headers["X-Forwarded-Host"] = request.host
        headers["X-Forwarded-Proto"] = request.scheme
        body = await request.read() if request.body_exists else None
        t_up = time.perf_counter()
        try:
            async with self.session.request(method, url, headers=headers, data=body, allow_redirects=False) as up:
                m.upstream_latency.labels(method, route).observe(time.perf_counter() - t_up)
                resp = web.StreamResponse(status=up.status, reason=up.reason)
                for k, v in up.headers.items():
                    if k.lower() not in HOP_BY_HOP:
                        resp.headers.add(k, v)
                if throttle is not None:
                    resp.headers["X-Fault-Throttled"] = throttle.name
                length = up.headers.get("Content-Length")
                if length is not None and method != "HEAD":
                    resp.content_length = int(length)
                await resp.prepare(request)
                if method != "HEAD":
                    if throttle is None:
                        async for chunk in up.content.iter_chunked(64 * 1024):
                            await resp.write(chunk)
                    else:
                        await self._throttled_copy(up, resp, throttle, method, route)
                await resp.write_eof()
                m.requests.labels(method, route, str(up.status)).inc()
                m.request_duration.labels(method, route).observe(time.perf_counter() - t0)
                return resp
        except (aiohttp.ClientConnectorError, aiohttp.ServerDisconnectedError, asyncio.TimeoutError) as e:
            m.upstream_errors.labels(method, route).inc()
            m.requests.labels(method, route, "502").inc()
            m.request_duration.labels(method, route).observe(time.perf_counter() - t0)
            return web.json_response({"error": "upstream unavailable", "detail": type(e).__name__}, status=502)
        except ConnectionResetError:
            # client went away mid-stream
            m.requests.labels(method, route, "499").inc()
            raise

    async def _throttled_copy(self, up: aiohttp.ClientResponse, resp: web.StreamResponse, f: Fault, method: str, route: str) -> None:
        rate = f.bandwidth_kbps * 1024.0  # bytes per second
        chunk_size = max(256, min(64 * 1024, int(rate / 20)))  # ~20 writes per second
        start = time.perf_counter()
        sent = 0
        async for chunk in up.content.iter_chunked(chunk_size):
            await resp.write(chunk)
            sent += len(chunk)
            ahead = sent / rate - (time.perf_counter() - start)
            if ahead > 0:
                await asyncio.sleep(ahead)
        self.metrics.throttled_bytes.labels(method, route, f.name).inc(sent)

    async def _proxy_ws(self, request: web.Request, route: str, t0: float) -> web.StreamResponse:
        assert self.session is not None
        ws_url = self.upstream.replace("http", "ws", 1) + request.rel_url.path_qs
        headers = {k: v for k, v in request.headers.items()
                   if k.lower() not in HOP_BY_HOP and not k.lower().startswith("sec-websocket")}
        try:
            upstream_ws = await self.session.ws_connect(ws_url, headers=headers, autoping=False)
        except aiohttp.ClientError:
            self.metrics.upstream_errors.labels(request.method, route).inc()
            return web.json_response({"error": "upstream websocket unavailable"}, status=502)
        client_ws = web.WebSocketResponse(autoping=False)
        await client_ws.prepare(request)
        self.metrics.requests.labels(request.method, route, "101").inc()

        async def pump(src, dst) -> None:
            async for msg in src:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    await dst.send_str(msg.data)
                elif msg.type == aiohttp.WSMsgType.BINARY:
                    await dst.send_bytes(msg.data)
                elif msg.type == aiohttp.WSMsgType.PING:
                    await dst.ping(msg.data)
                elif msg.type == aiohttp.WSMsgType.PONG:
                    await dst.pong(msg.data)
                else:
                    break
            await dst.close()

        await asyncio.gather(pump(client_ws, upstream_ws), pump(upstream_ws, client_ws), return_exceptions=True)
        return client_ws


# --------------------------------------------------------------------------- main

def parse_listen(value: str) -> tuple[str, int]:
    host, _, port = value.rpartition(":")
    return host or "127.0.0.1", int(port)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Fault-injecting reverse proxy (see module docstring)")
    ap.add_argument("--listen", default="127.0.0.1:3100", help="host:port to bind (default 127.0.0.1:3100)")
    ap.add_argument("--upstream", default="http://127.0.0.1:3000")
    ap.add_argument("--faults", help="YAML fault file loaded at startup")
    ap.add_argument("--seed", type=int, help="seed for jitter/error randomness")
    ap.add_argument("--allow-remote", action="store_true", help="permit binding a non-loopback address")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    host, port = parse_listen(args.listen)
    if host not in ("127.0.0.1", "localhost", "::1") and not args.allow_remote:
        ap.error("refusing to bind a non-loopback address without --allow-remote (control API is unauthenticated)")
    faults = load_fault_file(args.faults) if args.faults else []
    proxy = FaultProxy(args.upstream, faults, seed=args.seed)
    log.info("fault proxy %s:%d -> %s with faults %s", host, port, args.upstream, [f.name for f in faults])
    web.run_app(proxy.app(), host=host, port=port, access_log=None, print=None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
