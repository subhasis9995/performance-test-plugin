# Juice Shop v20.4.0: routes and telemetry for perf testing

Verified 2026-11-15 against the running packaged build (Node 24, Windows), both direct on
`http://127.0.0.1:3000` and through the fault proxy on `http://127.0.0.1:3100` (env `perf-qa`).
Route source: `server.ts` at tag v20.4.0. Timings are single warm requests on a dev laptop,
to give a sense of scale only. They are not a baseline.

## Telemetry

### Juice Shop `GET /metrics`: yes, Prometheus text (prom-client), no auth

Useful for diagnostics:
- `http_requests_count{status_code="2XX|4XX|5XX"}`: counter grouped by status **class** only. No route label and **no latency histogram**. Use the proxy's metrics for per-route latency.
- `process_cpu_seconds_total`, `process_resident_memory_bytes`
- `nodejs_eventloop_lag_seconds`, `nodejs_eventloop_lag_p50/p90/p99_seconds`, `nodejs_gc_duration_seconds`
- `nodejs_heap_size_used_bytes`, `nodejs_heap_size_total_bytes`, `nodejs_active_handles_total`
- `juiceshop_startup_duration_seconds{task}`, `juiceshop_version_info`
- Business counters: `juiceshop_orders_placed_total`, `juiceshop_users_registered_total`

### Fault proxy `GET /__metrics` (Prometheus text, prometheus_client)

| Metric | Type | Labels |
|---|---|---|
| `sut_proxy_requests_total` | counter | method, route, status |
| `sut_proxy_upstream_latency_seconds` | histogram | method, route. Time to upstream response headers, excluding injected delay. Injected errors never reach upstream. |
| `sut_proxy_request_duration_seconds` | histogram | method, route. Full time in proxy, including faults and throttled body. |
| `sut_proxy_injected_delay_seconds` | histogram | method, route, fault |
| `sut_proxy_injected_errors_total` | counter | method, route, fault, status |
| `sut_proxy_throttled_bytes_total` | counter | method, route, fault |
| `sut_proxy_upstream_errors_total` | counter | method, route (upstream connect failure, answered 502) |
| `sut_proxy_active_faults` | gauge | none |

`route` is the path with numeric/uuid segments replaced by `{id}` (for example `/api/Products/{id}`,
`/rest/basket/{id}/checkout`), query string dropped, capped at 4 segments. Static files collapse to
`/<first-segment>/*.<ext>` or `/*.<ext>`. Up to 200 distinct routes, then `other`.
Buckets: 1 ms to 30 s.

### Health
- `GET /rest/admin/application-version` → `{"version":"20.4.0-SNAPSHOT"}` (used by `install.py --health`)
- `GET /__health` (proxy only, does not touch upstream)

## Routes for perf scenarios

Auth = `Authorization: Bearer <token>` from login. IDs in the third column are where the value comes from.

| Flow | Method + path | Auth | Notes | ~ms |
|---|---|---|---|---|
| Browse | `GET /rest/products/search?q=<term>` | no | `q=` empty returns all products. Raw SQL `LIKE` (the SQLi challenge), so it is the natural latency regression target. | 24 |
| Browse | `GET /api/Products` | no | finale-rest list | 44 |
| Browse | `GET /api/Products/{id}` | no | ids 1..~46 | 22 |
| Browse | `GET /rest/products/{id}/reviews` | no | MarsDB (in-memory) | 9 |
| Register | `GET /api/SecurityQuestions` | no | pick `data[0]` | 41 |
| Register | `POST /api/Users` | no | `{email,password,passwordRepeat,securityQuestion:{id,...},securityAnswer}` returns 201 `data.id` | 44 |
| Register | `POST /api/SecurityAnswers` | no | `{UserId,answer,SecurityQuestionId}` → 201 | 26 |
| Login | `POST /rest/user/login` | no | `{email,password}` → `authentication.{token,bid,umail}`; `bid` = basket id | 20 |
| Login | `GET /rest/user/whoami` | cookie/bearer | | 4 |
| Users | `GET /api/Users` | **yes** (401 without) | admin-ish listing; avoid in load | |
| Basket | `GET /rest/basket/{bid}` | yes | `data.Products[].BasketItem.id` | 55 |
| Basket | `POST /api/BasketItems` | yes | `{ProductId, BasketId:"<bid>", quantity}`; the same product twice → **500** (unique constraint; use PUT). Exclude it from error-rate SLOs or avoid it in scripts. | 10 |
| Basket | `PUT /api/BasketItems/{id}` | yes | `{quantity}` | 29 |
| Basket | `DELETE /api/BasketItems/{id}` | yes | | |
| Checkout | `POST /api/Addresss` (sic, 3 s) | yes | `{country,fullName,mobileNum,zipCode,streetAddress,city,state}` → `data.id` | 26 |
| Checkout | `GET /api/Addresss` | yes | | 2 |
| Checkout | `GET /api/Deliverys` | yes | ids 1..3 | 2 |
| Checkout | `POST /api/Cards` | yes | `{fullName,cardNum(16 digits),expMonth,expYear}` → `data.id` (fake app, test numbers only) | 26 |
| Checkout | `GET /api/Cards` | yes | | 3 |
| Checkout | `GET /rest/wallet/balance` | yes | pay with `paymentId:"wallet"` needs balance | 21 |
| Checkout | `PUT /rest/basket/{bid}/coupon/{code}` | yes | optional | |
| Checkout | `POST /rest/basket/{bid}/checkout` | yes | `{orderDetails:{paymentId,addressId,deliveryMethodId}}` → `orderConfirmation`. Generates an order PDF. **First call ~1.5 s (cold), then ~60 ms.** Warm up before measuring. Empties the basket. | 60 |
| Orders | `GET /rest/order-history` | yes | | 8 |
| Orders | `GET /rest/track-order/{orderConfirmation}` | no | | 7 |
| Front end | `GET /`, `/main-*.js`, `/chunk-*.js`, `/styles-*.css`, `/assets/public/images/...` | no | Angular build, hashed file names; gzip served | |

## Gotchas

- **DB resets on every start.** Juice Shop recreates SQLite + MarsDB data at boot. Re-run
  `sut/fixtures/create_test_user.py` after each `install.py --start` or restart.
- Users from `create_test_user.py` are created **directly on :3000**, so proxy faults cannot break setup.
- Rate limits exist on `/rest/user/reset-password` (and 2FA). Avoid them in load scenarios.
- `/api/Products` POST/PUT/DELETE are access-controlled or challenge traps. Treat products as read-only.
- Socket.IO (`/socket.io/`) carries challenge notifications. The proxy passes through WebSocket upgrades and long-polling.
