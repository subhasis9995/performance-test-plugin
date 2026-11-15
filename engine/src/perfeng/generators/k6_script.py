"""Compile a validated TestRunSpec into a deterministic k6 script.

Secrets never appear in the script: they reach k6 only as process env vars (``__ENV``) that the
engine resolves from ``env:NAME`` references at execution time.
"""

from __future__ import annotations

import json
import re

from perfeng.spec.testrunspec_v1 import Request, Scenario, TestRunSpec

_PLACEHOLDER = re.compile(r"\$\{data\.([A-Za-z0-9_]+)\}")


def _js(obj) -> str:
    return json.dumps(obj, separators=(",", ":"))


def _scenario_options(s: Scenario) -> dict:
    exec_name = f"sc_{_ident(s.name)}"
    if s.executor == "per-vu-iterations":
        return {"executor": "per-vu-iterations", "vus": 1, "iterations": s.iterations or 1, "exec": exec_name,
                "maxDuration": f"{max(60, sum(st.duration_s for st in s.stages) or 300)}s", "tags": {"scenario_name": s.name}}
    if len(s.stages) == 1:
        st = s.stages[0]
        return {"executor": "constant-arrival-rate", "rate": st.rate, "timeUnit": "1s", "duration": f"{st.duration_s}s",
                "preAllocatedVUs": s.pre_allocated_vus, "maxVUs": s.max_vus, "exec": exec_name,
                "tags": {"scenario_name": s.name}}
    return {"executor": "ramping-arrival-rate", "startRate": s.stages[0].rate, "timeUnit": "1s",
            "preAllocatedVUs": s.pre_allocated_vus, "maxVUs": s.max_vus, "exec": exec_name,
            "stages": [{"target": st.rate, "duration": f"{st.duration_s}s"} for st in s.stages],
            "tags": {"scenario_name": s.name}}


def _ident(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", name)


def _template(s: str) -> str:
    """Turn '/x/${data.id}?q=${data.q}' into a JS template literal using the current data row."""
    s = s.replace("`", "\\`")
    return "`" + _PLACEHOLDER.sub(lambda m: "${row." + m.group(1) + "}", s) + "`"


def _body_expr(r: Request) -> str:
    if r.graphql is not None:
        return f"subst(JSON.stringify({_js(r.graphql)}), row)"
    if r.body is None:
        return "null"
    if isinstance(r.body, (dict, list)):
        return f"subst(JSON.stringify({_js(r.body)}), row)"
    return f"subst({_js(str(r.body))}, row)"


def _auth_js(spec: TestRunSpec) -> tuple[str, str]:
    """Returns (setup body, header-building expression)."""
    a = spec.auth

    def env(ref):
        return f"__ENV[{_js(ref.ref.split(':', 1)[1])}]" if ref else "''"

    if a.type == "none":
        return "return {};", "{}"
    if a.type == "bearer":
        return "return {};", f"{{Authorization: 'Bearer ' + {env(a.token)}}}"
    if a.type == "api_key":
        return "return {};", f"{{[{_js(a.header or 'X-API-Key')}]: {env(a.token)}}}"
    if a.type == "cookie":
        return "return {};", f"{{Cookie: {env(a.token)}}}"
    if a.type == "basic":
        return "return {};", f"{{Authorization: 'Basic ' + encoding.b64encode({env(a.username)} + ':' + {env(a.password)})}}"
    if a.type == "oauth2_client_credentials":
        setup = (
            f"const r = http.post({_js(a.token_url)}, {{grant_type: 'client_credentials', client_id: {env(a.client_id)}, "
            f"client_secret: {env(a.client_secret)}}}, {{tags: {{name: '__auth'}}}});"
            "if (r.status !== 200) { exec.test.abort('oauth2 token request failed: ' + r.status); }"
            "return {token: r.json('access_token')};"
        )
        return setup, "{Authorization: 'Bearer ' + data.token}"
    if a.type == "login_flow":
        lr = a.login_request or {}
        body = json.dumps(lr.get("body_template", {}))
        setup = (
            f"const body = {_js(body)}.replace('${{username}}', {env(a.username)}).replace('${{password}}', {env(a.password)});"
            f"const r = http.request({_js(lr.get('method', 'POST'))}, BASE + {_js(lr.get('path', '/login'))}, body, "
            "{headers: {'Content-Type': 'application/json'}, tags: {name: '__auth'}});"
            "if (r.status < 200 || r.status >= 300) { exec.test.abort('login failed: ' + r.status); }"
            f"return {{token: r.json({_js(lr.get('token_json_path', 'token'))})}};"
        )
        scheme = lr.get("token_scheme", "Bearer")
        return setup, f"{{Authorization: {_js(scheme + ' ')} + data.token}}"
    raise ValueError(f"unsupported auth type {a.type}")


def generate(spec: TestRunSpec, data_files: dict[str, str] | None = None) -> str:
    """data_files: logical name -> local path of a JSON array file available to the k6 process."""
    scenarios = {f"sc_{_ident(s.name)}": _scenario_options(s) for s in spec.scenarios if s.kind == "api"}
    th = spec.thresholds
    thresholds: dict[str, list] = {
        "http_req_failed{phase:main}": [{"threshold": f"rate<{th.abort_error_rate}", "abortOnFail": True, "delayAbortEval": "10s"}],
    }
    if th.abort_p95_ms:
        thresholds["http_req_duration{phase:main}"] = [
            {"threshold": f"p(95)<{th.abort_p95_ms}", "abortOnFail": True, "delayAbortEval": "15s"}
        ]
    options = {
        "scenarios": scenarios,
        "thresholds": thresholds,
        "discardResponseBodies": False,
        "summaryTrendStats": ["avg", "min", "med", "max", "p(90)", "p(95)", "p(99)"],
        "setupTimeout": "60s",
        "userAgent": f"perfeng/{spec.run_id}",
        "hosts": {},
    }
    setup_body, header_expr = _auth_js(spec)

    data_js = []
    first_data = "null"
    for i, (name, path) in enumerate((data_files or {}).items()):
        var = f"DATA{i}"
        loader = f"JSON.parse(open({_js(path)}))"  # CSV is converted to JSON by the runner
        data_js.append(f"const {var} = new SharedArray({_js(name)}, function () {{ return {loader}; }});")
        if i == 0:
            first_data = var

    fns = []
    for s in spec.scenarios:
        if s.kind != "api":
            continue
        lines = [f"export function sc_{_ident(s.name)}(data) {{",
                 f"  const row = {first_data} ? {first_data}[(exec.scenario.iterationInTest) % {first_data}.length] : {{}};",
                 f"  const auth = {header_expr};",
                 "  const t0 = Date.now(); let ok = true;"]
        for r in s.requests:
            url = r.path if "://" in r.path else None
            url_expr = _template(url) if url else f"BASE + {_template(r.path)}"
            hdrs = dict(r.headers)
            if r.graphql is not None or isinstance(r.body, (dict, list)):
                hdrs.setdefault("Content-Type", "application/json")
            lines.append(
                f"  {{ const res = http.request({_js(r.method)}, {url_expr}, {_body_expr(r)}, "
                f"{{headers: Object.assign({{}}, auth, {_js(hdrs)}), tags: {{name: {_js(r.name)}, phase: 'main'}}, "
                f"responseCallback: http.expectedStatuses(...{_js(r.expect_status)})}});"
            )
            lines.append(
                f"    const c = check(res, {{{_js(r.name + ' status')}: (x) => {_js(r.expect_status)}.includes(x.status)}}); ok = ok && c; }}"
            )
        lines.append(f"  TX.add(Date.now() - t0, {{tx: {_js(s.name)}}}); TXOK.add(ok, {{tx: {_js(s.name)}}});")
        lines.append("}")
        fns.append("\n".join(lines))

    return "\n".join([
        "// Generated by perfeng from TestRunSpec " + spec.run_id + " — do not edit; regenerate from the plan.",
        "import http from 'k6/http';",
        "import exec from 'k6/execution';",
        "import encoding from 'k6/encoding';",
        "import { check } from 'k6';",
        "import { Trend, Rate } from 'k6/metrics';",
        "import { SharedArray } from 'k6/data';",
        f"const BASE = {_js(spec.base_url.rstrip('/'))};",
        "const TX = new Trend('tx_duration', true);",
        "const TXOK = new Rate('tx_success');",
        "function subst(s, row) { return s.replace(/\\$\\{data\\.([A-Za-z0-9_]+)\\}/g, (_, k) => (row && row[k] !== undefined) ? row[k] : ''); }",
        *data_js,
        f"export const options = {_js(options)};",
        f"export function setup() {{ {setup_body} }}",
        *fns,
    ])
