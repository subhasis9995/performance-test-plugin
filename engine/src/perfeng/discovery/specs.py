"""API discovery from user-provided specs (OpenAPI 2/3, Postman v2.x, GraphQL SDL/introspection, HAR)
and a bounded, read-only probe/crawl fallback when no spec exists.

Discovery never invents requirements; it only lists what exists, with source + confidence.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
import yaml

from perfeng.domain.models import Endpoint

_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}


def _load_doc(location: str, root: Path) -> Any:
    if location.startswith(("http://", "https://")):
        r = httpx.get(location, timeout=20, follow_redirects=True)
        r.raise_for_status()
        text = r.text
    else:
        p = (root / location).resolve()
        text = p.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            return yaml.safe_load(text)
        except yaml.YAMLError:
            return text


def _example_from_schema(schema: dict | None, doc: dict, depth: int = 0) -> Any:
    if not schema or depth > 4:
        return None
    if "$ref" in schema:
        ref = schema["$ref"].lstrip("#/").split("/")
        node: Any = doc
        for part in ref:
            node = node.get(part, {}) if isinstance(node, dict) else {}
        return _example_from_schema(node, doc, depth + 1)
    if "example" in schema:
        return schema["example"]
    t = schema.get("type")
    if t == "object" or "properties" in schema:
        return {k: _example_from_schema(v, doc, depth + 1) for k, v in (schema.get("properties") or {}).items()}
    if t == "array":
        return [_example_from_schema(schema.get("items"), doc, depth + 1)]
    return {"integer": 1, "number": 1.0, "boolean": True, "string": "string"}.get(t)


def parse_openapi(doc: dict) -> list[Endpoint]:
    out: list[Endpoint] = []
    base_path = doc.get("basePath", "") if "swagger" in doc else ""
    if "servers" in doc and doc["servers"]:
        base_path = urlparse(doc["servers"][0].get("url", "")).path.rstrip("/")
    global_sec = bool(doc.get("security"))
    for path, item in (doc.get("paths") or {}).items():
        for method, op in (item or {}).items():
            if method.lower() not in _METHODS or not isinstance(op, dict):
                continue
            body = None
            rb = op.get("requestBody", {}).get("content", {}) if isinstance(op.get("requestBody"), dict) else {}
            if "application/json" in rb:
                media = rb["application/json"]
                body = media.get("example") or _example_from_schema(media.get("schema"), doc)
            for prm in op.get("parameters", []) or []:
                if isinstance(prm, dict) and prm.get("in") == "body":
                    body = _example_from_schema(prm.get("schema"), doc)
            out.append(Endpoint(
                method=method.upper(), path=(base_path + path) or "/", operation_id=op.get("operationId"),
                tags=op.get("tags", []), source="openapi", confidence=1.0,
                request_example={"body": body} if body is not None else None,
                requires_auth=bool(op.get("security", [None]) if "security" in op else global_sec),
            ))
    return out


def parse_postman(doc: dict) -> list[Endpoint]:
    out: list[Endpoint] = []

    def walk(items):
        for it in items or []:
            if "item" in it:
                walk(it["item"])
                continue
            req = it.get("request") or {}
            url = req.get("url")
            raw = url.get("raw") if isinstance(url, dict) else url
            if not raw:
                continue
            path = "/" + "/".join(url.get("path", [])) if isinstance(url, dict) and url.get("path") else urlparse(
                re.sub(r"\{\{[^}]+\}\}", "", raw)).path
            path = re.sub(r"/:([A-Za-z0-9_]+)", r"/{\1}", path)
            body = None
            if (req.get("body") or {}).get("mode") == "raw":
                try:
                    body = json.loads(req["body"].get("raw") or "null")
                except json.JSONDecodeError:
                    body = req["body"].get("raw")
            out.append(Endpoint(method=req.get("method", "GET").upper(), path=path or "/", operation_id=it.get("name"),
                                source="postman", confidence=0.95,
                                request_example={"body": body} if body is not None else None,
                                requires_auth=bool(req.get("auth"))))

    walk(doc.get("item"))
    return out


_GQL_FIELD = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(\([^)]*\))?\s*:", re.M)


def parse_graphql(doc: Any, endpoint_path: str = "/graphql") -> list[Endpoint]:
    ops: list[tuple[str, str]] = []
    if isinstance(doc, dict):  # introspection result
        schema = doc.get("data", doc).get("__schema", {})
        types = {t["name"]: t for t in schema.get("types", [])}
        for kind, key in (("query", "queryType"), ("mutation", "mutationType")):
            tn = (schema.get(key) or {}).get("name")
            for f in (types.get(tn, {}).get("fields") or []):
                ops.append((kind, f["name"]))
    else:  # SDL
        for kind in ("Query", "Mutation"):
            m = re.search(r"type\s+" + kind + r"\s*\{([^}]*)\}", str(doc))
            if m:
                ops += [(kind.lower(), f.group(1)) for f in _GQL_FIELD.finditer(m.group(1))]
    return [Endpoint(method="POST", path=endpoint_path, operation_id=f"{k}.{n}", tags=["graphql", k], source="graphql",
                     confidence=1.0, request_example={"graphql": {"operationName": n, "kind": k}}) for k, n in ops]


def parse_har(doc: dict, base_url: str) -> list[Endpoint]:
    host = urlparse(base_url).netloc
    seen: dict[str, Endpoint] = {}
    for e in (doc.get("log") or {}).get("entries", []):
        req = e.get("request", {})
        u = urlparse(req.get("url", ""))
        if u.netloc != host:
            continue
        mime = (e.get("response", {}).get("content", {}) or {}).get("mimeType", "")
        if not any(x in mime for x in ("json", "xml", "text/plain")) and req.get("method") == "GET":
            continue
        path = templatize(u.path)
        ep = Endpoint(method=req.get("method", "GET"), path=path, source="har", confidence=0.8)
        seen.setdefault(ep.key, ep)
    return list(seen.values())


_ID_SEG = re.compile(r"^(\d+|[0-9a-f]{8}-[0-9a-f-]{27,}|[0-9a-f]{24})$", re.I)


def templatize(path: str) -> str:
    return "/".join("{id}" if _ID_SEG.match(seg) else seg for seg in path.split("/"))


def load_spec(kind: str, location: str, root: Path, base_url: str) -> list[Endpoint]:
    doc = _load_doc(location, root)
    if kind == "openapi":
        return parse_openapi(doc)
    if kind == "postman":
        return parse_postman(doc)
    if kind == "graphql":
        return parse_graphql(doc)
    if kind == "har":
        return parse_har(doc, base_url)
    if kind == "proto":
        raise NotImplementedError("gRPC (.proto) is an extension point; not executed in v1")
    raise ValueError(kind)


# ---------------------------------------------------------------- probe / crawl fallback

COMMON_SPEC_PATHS = ["/openapi.json", "/swagger.json", "/v3/api-docs", "/api-docs", "/swagger/v1/swagger.json",
                     "/api/openapi.json", "/api-docs/swagger.json"]


def probe_spec_urls(base_url: str, client: httpx.Client | None = None) -> str | None:
    """Look for a published OpenAPI document (GET only)."""
    c = client or httpx.Client(timeout=5, follow_redirects=True)
    for p in COMMON_SPEC_PATHS:
        try:
            r = c.get(urljoin(base_url.rstrip("/") + "/", p.lstrip("/")))
        except httpx.HTTPError:
            continue
        if r.status_code == 200 and ("openapi" in r.text[:200] or "swagger" in r.text[:200]):
            return str(r.url)
    return None


_LINK = re.compile(r"""(?:href|src)=["']([^"'#]+)["']""", re.I)
_API_IN_JS = re.compile(r"""["'`](/(?:api|rest|graphql)(?:/[A-Za-z0-9_\-{}.]+)*)(?=["'`?$/])""")


def crawl(base_url: str, max_pages: int = 20, client: httpx.Client | None = None) -> dict[str, list]:
    """Bounded same-origin GET crawl: frontend routes + API paths referenced in HTML/JS bundles."""
    c = client or httpx.Client(timeout=8, follow_redirects=True)
    origin = urlparse(base_url).netloc
    queue = [base_url]
    seen: set[str] = set()
    routes: set[str] = set()
    api_paths: set[str] = set()
    while queue and len(seen) < max_pages:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        try:
            r = c.get(url)
        except httpx.HTTPError:
            continue
        ctype = r.headers.get("content-type", "")
        if "html" in ctype or "javascript" in ctype:
            for m in _API_IN_JS.finditer(r.text):
                api_paths.add(templatize(m.group(1)))
        if "html" in ctype:
            routes.add(urlparse(url).path or "/")
            for link in _LINK.findall(r.text):
                nxt = urljoin(url, link)
                if urlparse(nxt).netloc == origin and nxt not in seen:
                    if nxt.endswith((".js", ".html", "/")) or "." not in urlparse(nxt).path.rsplit("/", 1)[-1]:
                        queue.append(nxt)
    eps = [Endpoint(method="GET", path=p, source="crawl", confidence=0.4) for p in sorted(api_paths)]
    return {"endpoints": eps, "routes": sorted(routes), "pages_fetched": len(seen)}
