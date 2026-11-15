"""Tiny threaded HTTP system-under-test for fast engine integration tests.

Endpoints: /api/items, /api/items/{id}, /rest/search?q=, /login (POST), /metrics, /openapi.json
Latency/error faults controlled via the `faults` dict (path prefix -> {delay_ms, error_rate}).
"""

from __future__ import annotations

import json
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

OPENAPI = {
    "openapi": "3.0.3",
    "info": {"title": "mini", "version": "1"},
    "paths": {
        "/api/items": {"get": {"operationId": "listItems"}},
        "/api/items/{id}": {"get": {"operationId": "getItem", "parameters": [{"name": "id", "in": "path"}]}},
        "/rest/search": {"get": {"operationId": "search"}},
        "/login": {"post": {"operationId": "login", "requestBody": {"content": {"application/json": {
            "schema": {"type": "object", "properties": {"email": {"type": "string"}, "password": {"type": "string"}}}}}}}},
    },
}


class SUT:
    def __init__(self, port: int = 0):
        self.faults: dict[str, dict] = {}
        self.counts: dict[str, int] = {}
        self.secs: dict[str, float] = {}
        self.auth_seen: list[str] = []
        sut = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body, ctype="application/json"):
                data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _handle(self):
                t0 = time.perf_counter()
                path = urlparse(self.path).path
                if path == "/metrics":
                    lines = []
                    for k, v in sut.counts.items():
                        lines.append(f'mini_requests_total{{route="{k}"}} {v}')
                        lines.append(f'mini_request_duration_seconds_sum{{route="{k}"}} {sut.secs[k]}')
                        lines.append(f'mini_request_duration_seconds_count{{route="{k}"}} {v}')
                    return self._send(200, "\n".join(lines) + "\n", "text/plain")
                if path == "/openapi.json":
                    return self._send(200, OPENAPI)
                if self.headers.get("Authorization"):
                    sut.auth_seen.append(self.headers["Authorization"])
                for prefix, f in sut.faults.items():
                    if path.startswith(prefix):
                        time.sleep(f.get("delay_ms", 0) / 1000 + random.uniform(0, f.get("jitter_ms", 0)) / 1000)
                        if random.random() < f.get("error_rate", 0):
                            return self._send(503, {"error": "injected"})
                time.sleep(0.004 + random.uniform(0, 0.002))
                route = path if not path.startswith("/api/items/") else "/api/items/{id}"
                if path == "/" or path == "/index.html":
                    resp = (200, '<html><body><a href="/about">about</a><script src="/app.js"></script></body></html>', "text/html")
                elif path == "/app.js":
                    resp = (200, 'fetch("/api/items"); fetch("/rest/search?q=x")', "application/javascript")
                elif path == "/about":
                    resp = (200, "<html>about</html>", "text/html")
                elif path == "/api/items":
                    resp = (200, [{"id": i} for i in range(10)], "application/json")
                elif path.startswith("/api/items/"):
                    resp = (200, {"id": path.rsplit("/", 1)[-1]}, "application/json")
                elif path == "/rest/search":
                    resp = (200, {"results": []}, "application/json")
                elif path == "/login" and self.command == "POST":
                    n = int(self.headers.get("Content-Length") or 0)
                    body = json.loads(self.rfile.read(n) or b"{}")
                    if body.get("password") == "pw-correct-horse":
                        resp = (200, {"authentication": {"token": "tok-abcdef123456"}}, "application/json")
                    else:
                        resp = (401, {"error": "bad creds"}, "application/json")
                else:
                    resp = (404, {"error": "nf"}, "application/json")
                sut.counts[route] = sut.counts.get(route, 0) + 1
                sut.secs[route] = sut.secs.get(route, 0.0) + (time.perf_counter() - t0)
                self._send(*resp)

            do_GET = _handle
            do_POST = _handle

        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), H)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *a):
        self.httpd.shutdown()
        self.httpd.server_close()
