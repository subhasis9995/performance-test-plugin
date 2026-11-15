---
name: k6-scripting
description: How API load scenarios are expressed for the perf engine (endpoints, request overrides, test data placeholders, auth via env var names, GraphQL bodies) instead of hand-written k6 scripts. Use when a user wants a custom API scenario, a POST/PUT endpoint in a test, data-driven requests, or asks to "write a k6 script".
---

# API scenarios (k6, compiled by the engine)

The engine compiles every k6 script from a validated TestRunSpec, so caps, allowlists, abort thresholds, the watchdog and audit always apply. Do **not** write and run k6 scripts yourself. Raw `k6 run` is blocked by policy.

To add or customize an endpoint, use `set_project_inputs(request_overrides={...})`:
```json
{
  "POST /api/BasketItems": {"path": "/api/BasketItems", "body": {"ProductId": "${data.id}", "quantity": 1}, "expect_status": [200, 201]},
  "GET /rest/products/search": {"path": "/rest/products/search?q=${data.q}"},
  "POST /graphql": {"path": "/graphql", "graphql": {"query": "query($id: ID!){ product(id:$id){ name } }", "variables": {"id": "${data.id}"}}}
}
```
- `${data.<field>}` reads a row from the project's test data (CSV/JSON).
- Auth is project-level: `set_project_inputs(auth={type, env:{...ENV VAR NAMES...}, login_request:{method, path, body_template:{"email":"${username}","password":"${password}"}, token_json_path}})`.
- Write endpoints (POST/PUT/DELETE) need explicit overrides plus test data, and must be safe to repeat against the target.
- gRPC and WebSocket are extension points and are not executed in v1.
- Reference: Grafana's k6 skill (`grafana/skills`) is a good authoring guide but is not bundled, because it runs scripts directly (see docs/security-review.md).
