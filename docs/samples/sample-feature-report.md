# Performance Test Report: juice-shop / perf-qa

**Feature performance test: KEY-22**

| Item | Value |
|---|---|
| Report generated | 2026-11-15 15:28 UTC |
| Plan | `plan_20261115103720_ea67c5` (v1, executed) |
| Application / environment | juice-shop / perf-qa |
| Feature / work item | KEY-22 |
| Overall verdict | ❌ **FAIL_REGRESSION** |
| Environment label | local-lab (load generator, browser and possibly the app share one machine) |

## 1. Executive summary

| Item | Value |
|---|---|
| Runs executed | 7 (7 completed, 0 aborted by safety stop, 0 failed/rejected) |
| Total API requests generated | 250 |
| Test window | 2026-11-15 10:37:30 → 2026-11-15 10:39:15 |
| Targets tested | GET /rest/products/search, Search |


**Findings**

- ❌ **GET /rest/products/search**: FAIL_REGRESSION. p95 +2055.7% vs baseline (fail >= 25.0%); p99 +1583.3% vs baseline (fail >= 25.0%); p50 +2502.7% vs baseline (fail >= 25.0%)
- ✅ **Search**: PASS. 

## 2. Objectives and scope

Determine the performance impact of KEY-22 by comparing against the approved baseline workload and any approved SLOs, then explain regressions.

**Feature:** KEY-22: Rank product search results by relevance

**Feature Impact Model**

| Area | Kind | Item | Confidence | Evidence |
|---|---|---|---|---|
| Backend | endpoint | `GET /rest/products/search` | 0.90 | routes/search.ts matches service-map 'product-search' |
| Backend | service | `SearchService` | 0.90 | routes/search.ts matches service-map 'product-search' |
| Frontend | component | `SearchResultComponent` | 0.90 | frontend/src/app/search-result/search-result.component.ts matches service-map 'search-ui' |
| Frontend | journey | `Search` | 0.85 | frontend/src/app/search-result/search-result.component.ts matches service-map 'search-ui'; mentioned in work item |
| Dependencies | dependency | `sqlite:Products` | 0.70 | routes/search.ts matches service-map 'product-search' |

**Changed files:** `frontend/src/app/search-result/search-result.component.ts`, `routes/search.ts`


**Measurements collected:** http_req_duration p50/p90/p95/p99; throughput (req/s); business transaction rate; error rate; dropped iterations; bytes sent/received; load-generator CPU; server CPU/memory (when telemetry configured); LCP; CLS; INP (when interactions exist); TBT (lab); TTFB; FCP; Speed Index; long tasks; JS execution time; resource count/size; network waterfall (HAR); failed requests; console errors; journey duration

## 3. Test inputs

### 3.1 Target and access

| Item | Value |
|---|---|
| Base URL | http://127.0.0.1:3100 |
| Frontend URL | http://127.0.0.1:3100 |
| Allowed hosts (allowlist) | 127.0.0.1 |
| Ownership / permission to load test | ✅ confirmed |
| Production target | no |
| Authentication | login_flow (login `/rest/user/login`) |
| Secret variables (names only) | `JUICE_SHOP_USER_EMAIL` (set), `JUICE_SHOP_USER_PASSWORD` (set) |
| Telemetry sources | `proxy`: scrape:http://127.0.0.1:3100/__metrics, `app`: scrape:http://127.0.0.1:3000/metrics, `app_process`: process:port=3000 |


### 3.2 Specifications, data and requirements

| Item | Value |
|---|---|
| API specifications | none provided (endpoints discovered by probe/crawl) |
| Test data | `test-data/catalog.json`: 8 rows; columns: q, id |
| NFR/SLO documents | none exist (declared by user) |
| Approved NFRs imported | 0 |
| Journeys | Search (Home → Search 'apple' → Results), ProductDetails (Home → Open product) |
| Targets requested | `GET /rest/products/search`, `GET /api/Products/{id}` |
| Request overrides | `GET /rest/products/search` → `/rest/products/search?q=${data.q}`, `GET /api/Products/{id}` → `/api/Products/${data.id}` |
| Repository / service map | C:\Users\subha\AppData\Local\Temp\jsproj\app-repo / .perf/service-map.yaml |


### 3.3 Workload settings (effective policy)

| Item | Value |
|---|---|
| Repeats per workload | 3 benchmark / 3 feature / 3 frontend |
| Characterization rates | [10] req/s, 8 s each |
| Hard caps | 100 req/s, 200 VUs, 1800 s |
| Safety aborts | error rate ≥ 20% or p95 ≥ 5000 ms; generator CPU saturation 85% |
| Stability rule | p95 CV ≤ 0.3, ≥ 50 samples/run |
| Regression thresholds | warn ≥ +10.0%, fail ≥ +25.0% (rank test α = 0.05) |
| Frontend lab budgets (POLICY) | lcp_ms 2500, cls 0.1, tbt_ms 200, inp_ms 200 |


### 3.4 Where the plan's numbers come from

| Source | Count | Meaning |
|---|---|---|
| POLICY | 7 | platform default |
| BASELINE | 2 | approved baseline workload |


### 3.5 Approvals (human gates)

| Gate | Approver | When | Content hash | Reason |
|---|---|---|---|---|
| G2_plan | eval-human | 2026-11-15 10:37:25 | `706687640a` | - |


## 4. Test environment and tooling

| Item | Value |
|---|---|
| Executor | local-process |
| Load generator OS | Windows-11-10.0.26300-SP0 |
| CPU cores / memory | 16 / 33.7 GB |
| k6 | k6.exe v2.3.0 (commit/e088784614, go1.26.8, windows/amd64) |
| Node.js | v24.14.0 |
| Playwright | 1.65.1 |
| Lighthouse | 13.6.0 |
| Chromium | 159.0.8154.6 |
| web-vitals | 6.2.3 |
| Load generator CPU (p95 of runs, whole machine) | 0.1 % |
| Load generator CPU max | 0.5 % |
| Load generator memory max | 36 MB |


## 5. Test plan and execution

| Test | Type | Workload | Repeats | Completed/started | Wall time |
|---|---|---|---|---|---|
| smoke:GET /rest/products/search | smoke | 2 req/s × 3s | 1 | 1/1 | 8 s |
| load:GET /rest/products/search@10 | load | 10 req/s × 8s | 3 | 3/3 | 36 s |
| frontend:Search | frontend | browser journey | 3 | 3/3 | 61 s |


### Run log

| Run | Type | Test | # | Status | Started | Duration | Note |
|---|---|---|---|---|---|---|---|
| `20261115103730_786ae2` | smoke | smoke:GET /rest/products/search | 1 | ✅ completed | 2026-11-15 10:37:30 | 7.7 s | - |
| `20261115103738_34de75` | load | load:GET /rest/products/search@10 | 1 | ✅ completed | 2026-11-15 10:37:38 | 12.0 s | - |
| `20261115103750_d0ec18` | load | load:GET /rest/products/search@10 | 2 | ✅ completed | 2026-11-15 10:37:50 | 12.0 s | - |
| `20261115103802_09c1db` | load | load:GET /rest/products/search@10 | 3 | ✅ completed | 2026-11-15 10:38:02 | 12.0 s | - |
| `20261115103814_3ba3ce` | frontend | frontend:Search | 1 | ✅ completed | 2026-11-15 10:38:14 | 20.3 s | - |
| `20261115103834_ad6b97` | frontend | frontend:Search | 2 | ✅ completed | 2026-11-15 10:38:34 | 20.4 s | - |
| `20261115103854_8ee00d` | frontend | frontend:Search | 3 | ✅ completed | 2026-11-15 10:38:54 | 20.2 s | - |


## 6. Results: API

### 6.1 Summary per workload (median across repeats)

| Target | Workload | Kind | Runs | Requests/run | p50 ms | p90 ms | p95 ms | p99 ms | max ms | Error rate | Req/s | Dropped iter. |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| GET /rest/products/search | smoke@2rps/3s | smoke | 1 | 7 | 211.6 | 213.9 | 214.0 | 214.1 | 214.1 | 0.00% | 2.3 | 0 |
| GET /rest/products/search | load@10rps/8s | feature | 3 | 81 | 210.0 | 220.3 | 222.7 | 223.8 | 224.3 | 0.00% | 10.1 | 0 |


### 6.2 Throughput, data and concurrency

| Target | Workload | Planned req/s | Achieved req/s | Achieved % | Max VUs | Data sent/run | Data received/run |
|---|---|---|---|---|---|---|---|
| GET /rest/products/search | smoke@2rps/3s | 2.0 | 2.3 | 117 % | 2 | 6 KB | 11 KB |
| GET /rest/products/search | load@10rps/8s | 10.0 | 10.1 | 101 % | 10 | 71 KB | 104 KB |


### 6.3 Per-repeat detail

**GET /rest/products/search** (smoke@2rps/3s, smoke)

| # | Status | Requests | p50 | p95 | p99 | Errors | Req/s | Dropped | Gen CPU p95 |
|---|---|---|---|---|---|---|---|---|---|
| 1 | completed | 7 | 211.6 | 214.0 | 214.1 | 0.00% | 2.3 | 0 | 0.1% |


**GET /rest/products/search** (load@10rps/8s, feature)

| # | Status | Requests | p50 | p95 | p99 | Errors | Req/s | Dropped | Gen CPU p95 |
|---|---|---|---|---|---|---|---|---|---|
| 1 | completed | 81 | 210.0 | 223.5 | 224.8 | 0.00% | 10.1 | 0 | 0.2% |
| 2 | completed | 81 | 210.6 | 222.2 | 223.6 | 0.00% | 10.1 | 0 | 0.2% |
| 3 | completed | 81 | 208.9 | 222.7 | 223.8 | 0.00% | 10.1 | 0 | 0.1% |



## 8. Results: frontend journeys (real browser, lab)

LCP loading · CLS visual stability · INP responsiveness (n/a without a qualifying interaction) · TBT lab proxy for blocking · FID is obsolete and not used. Lab results on one machine: use them for before/after comparison.

### Search (feature, 3 runs)

| Metric | Median | Min | Max | Lab budget (POLICY) | Within |
|---|---|---|---|---|---|
| LCP ms | 528 | 524 | 544 | 2,500 | ✅ |
| CLS | 0.058 | 0.058 | 0.058 | 0.1 | ✅ |
| INP ms | 40 | 16 | 40 | 200 | ✅ |
| TBT ms | 90 | 86 | 96 | 200 | ✅ |
| TTFB ms | 6.5 | 6 | 6.6 | - |  |
| FCP ms | 264 | 252 | 272 | - |  |
| Speed Index | 2,342.3 | 2,269.7 | 2,500.7 | - |  |
| Journey ms | 5,047 | 5,023 | 5,049 | - |  |
| Long tasks | 1 | 1 | 1 | - |  |
| JS exec ms | 139.0 | 135.0 | 143.7 | - |  |
| Resources | 47 | 47 | 47 | - |  |
| Transfer KB | 855.1 | 855.1 | 855.1 | - |  |
| Failed req. | 0 | 0 | 0 | - |  |
| Console err. | 0 | 0 | 0 | - |  |
| Lighthouse | 0.440 | 0.4 | 0.5 | - |  |

Slowest resources (first run):

| Resource | Type | Duration ms | Transfer KB |
|---|---|---|---|
| `http://127.0.0.1:3100/api/Challenges/?name=Score%20Board` | fetch | 411 | 0 |
| `http://127.0.0.1:3100/api/Challenges/?name=Score%20Board` | fetch | 327 | 1 |
| `http://127.0.0.1:3100/rest/products/search?q=` | fetch | 299 | 5 |
| `http://127.0.0.1:3100/api/Quantitys/` | fetch | 297 | 1 |
| `http://127.0.0.1:3100/rest/products/search?q=` | fetch | 219 | 0 |



## 9. Success and failure analysis

### 9.1 Requests by outcome

| Target | Workload | Requests | Successful | Failed | Success rate | HTTP status counts |
|---|---|---|---|---|---|---|
| GET /rest/products/search | smoke@2rps/3s | 7 | 7 | 0 | 100.00% | 200: 7 |
| GET /rest/products/search | load@10rps/8s | 243 | 243 | 0 | 100.00% | 200: 243 |


### 9.2 Run outcomes

| Status | Runs |
|---|---|
| completed | 7 |


### 9.3 Verdict per target

| Target | Workload | Verdict | Reasons |
|---|---|---|---|
| GET /rest/products/search | load@10rps/8s | ❌ FAIL_REGRESSION | p95 +2055.7% vs baseline (fail >= 25.0%); p99 +1583.3% vs baseline (fail >= 25.0%); p50 +2502.7% vs baseline (fail >= 25.0%) |
| Search | frontend@Search | ✅ PASS |  |


## 10. SLO, baseline and trend analysis

SLO (approved requirement), baseline (approved reference) and trend (history) are evaluated **separately**.

### GET /rest/products/search: ❌ FAIL_REGRESSION

Baseline: `bl_20261115103238_5491d8` v1, created 2026-11-15 10:32:38, workload `load@10rps/8s`.

**SLO:** none approved for this target, so no SLO verdict is possible.

**Baseline comparison**

| Metric | Baseline | Current | Delta | p-value (rank) | 95% CI of delta | Level |
|---|---|---|---|---|---|---|
| p95 | 10.3 | 222.7 | 2,055.7% | 0.050 | 1905% … 2265% | ❌ fail |
| p99 | 13.3 | 223.8 | 1,583.3% | 0.050 | 1456% … 1646% | ❌ fail |
| p50 | 8.1 | 210.0 | 2,502.7% | 0.050 | 2400% … 2590% | ❌ fail |
| error_rate | 0.0 | 0.0 | 0.0% | - | - | ✅ none |

**Trend (informational):** p95 drift 1,995.8% over 6 series; recent medians 11.3, 10.6, 10.3, 223.7, 9.9, 222.7. The baseline is never moved by trend.

**Measurement validity:** stable = True, CV p95 = 0.003

### Search: ✅ PASS

Baseline: `bl_20261115103242_a265b5` v1, created 2026-11-15 10:32:42, workload `frontend@Search`.

**SLO:** none approved for this target, so no SLO verdict is possible.

**Baseline comparison**

| Metric | Baseline | Current | Delta | p-value (rank) | 95% CI of delta | Level |
|---|---|---|---|---|---|---|
| lcp_ms | 544.0 | 528.0 | -2.9% | 0.939 | - | ✅ none |
| tbt_ms | 90.0 | 90.0 | 0.0% | 0.588 | - | ✅ none |
| journey_ms | 4,865.0 | 5,047.0 | 3.7% | 0.050 | - | ✅ none |
| inp_ms | 24.0 | 40.0 | 66.7% | 0.589 | - | ✅ none |
| ttfb_ms | 6.3 | 6.5 | 3.2% | 0.588 | - | ✅ none |
| fcp_ms | 268.0 | 264.0 | -1.5% | 0.750 | - | ✅ none |
| cls | 0.058 | 0.058 | 0.0% | 1.000 | - | ✅ none |

**Lab budgets (POLICY, not NFRs)**

| Metric | Budget | Observed | Within |
|---|---|---|---|
| lcp_ms | 2500 | 528.0 | ✅ |
| cls | 0.1 | 0.058 | ✅ |
| tbt_ms | 200 | 90.0 | ✅ |
| inp_ms | 200 | 40.0 | ✅ |

**Measurement validity:** stable = True


## 11. Server-side telemetry

| Series | Workload | Metric (median across repeats) | Value |
|---|---|---|---|
| GET /rest/products/search | smoke@2rps/3s | `proxy: sut_proxy_request_duration_seconds{method="GET",route="/rest/products/search"}:mean_ms` | 211.20 |
| GET /rest/products/search | smoke@2rps/3s | `proxy: sut_proxy_injected_delay_seconds{fault="search-latency",method="GET",route="/rest/products` | 200.00 |
| GET /rest/products/search | smoke@2rps/3s | `proxy: sut_proxy_request_duration_seconds{method="POST",route="/rest/user/login"}:mean_ms` | 13.77 |
| GET /rest/products/search | smoke@2rps/3s | `proxy: sut_proxy_upstream_latency_seconds{method="POST",route="/rest/user/login"}:mean_ms` | 13.56 |
| GET /rest/products/search | smoke@2rps/3s | `proxy: sut_proxy_upstream_latency_seconds{method="GET",route="/rest/products/search"}:mean_ms` | 8.45 |
| GET /rest/products/search | smoke@2rps/3s | `app_process: cpu_pct_p95` | 0.29 |
| GET /rest/products/search | smoke@2rps/3s | `app_process: rss_mb_max` | 283.91 |
| GET /rest/products/search | smoke@2rps/3s | `__host__: cpu_pct_p95` | 8.30 |
| GET /rest/products/search | load@10rps/8s | `proxy: sut_proxy_request_duration_seconds{method="GET",route="/rest/products/search"}:mean_ms` | 212.35 |
| GET /rest/products/search | load@10rps/8s | `proxy: sut_proxy_injected_delay_seconds{fault="search-latency",method="GET",route="/rest/products` | 200.00 |
| GET /rest/products/search | load@10rps/8s | `proxy: sut_proxy_request_duration_seconds{method="POST",route="/rest/user/login"}:mean_ms` | 12.05 |
| GET /rest/products/search | load@10rps/8s | `proxy: sut_proxy_upstream_latency_seconds{method="POST",route="/rest/user/login"}:mean_ms` | 11.85 |
| GET /rest/products/search | load@10rps/8s | `proxy: sut_proxy_upstream_latency_seconds{method="GET",route="/rest/products/search"}:mean_ms` | 7.61 |
| GET /rest/products/search | load@10rps/8s | `app_process: cpu_pct_p95` | 1.93 |
| GET /rest/products/search | load@10rps/8s | `app_process: rss_mb_max` | 275.37 |
| GET /rest/products/search | load@10rps/8s | `__host__: cpu_pct_p95` | 8.90 |
| Search | frontend@Search | `proxy: sut_proxy_request_duration_seconds{method="GET",route="/rest/products/search"}:mean_ms` | 258.64 |
| Search | frontend@Search | `proxy: sut_proxy_injected_delay_seconds{fault="search-latency",method="GET",route="/rest/products` | 200.00 |
| Search | frontend@Search | `proxy: sut_proxy_request_duration_seconds{method="GET",route="/api/Challenges"}:mean_ms` | 192.82 |
| Search | frontend@Search | `proxy: sut_proxy_upstream_latency_seconds{method="GET",route="/api/Challenges"}:mean_ms` | 192.63 |
| Search | frontend@Search | `proxy: sut_proxy_request_duration_seconds{method="GET",route="/api/Quantitys"}:mean_ms` | 191.55 |
| Search | frontend@Search | `proxy: sut_proxy_upstream_latency_seconds{method="GET",route="/api/Quantitys"}:mean_ms` | 191.33 |
| Search | frontend@Search | `proxy: sut_proxy_request_duration_seconds{method="GET",route="/rest/languages"}:mean_ms` | 144.96 |
| Search | frontend@Search | `proxy: sut_proxy_upstream_latency_seconds{method="GET",route="/rest/languages"}:mean_ms` | 144.65 |


## 12. Diagnosis (iteration 2)

- ✅ **[supported, high]** Latency on GET /rest/products/search is load-independent: a fixed per-request delay, not queueing or saturation
  - evidence (diagnostic-run): p95 223.2 ms @ 5 req/s -> 222.7 ms @ 20 req/s
- ✅ **[supported, high]** Added latency on GET /rest/products/search is introduced in front of the application (gateway/proxy/network), not by app processing
  - evidence (telemetry): proxy:sut_proxy_injected_delay_seconds{fault="search-latency",method="GET",route="/rest/products/search"}:mean_ms median 200.0 ms per request
  - evidence (analysis): client p95 increased by 212.4 ms
  - next test: Step load at 0.5x/1x/2x: a constant added delay is load-independent (not queueing)
- ➖ **[refuted, medium]** Application/server-side processing on /rest/products/search is the cause (server-side time is unchanged)
  - evidence (telemetry): proxy:sut_proxy_upstream_latency_seconds{method="GET",route="/rest/products/search"}:mean_ms: baseline 7.7 ms vs current 7.6 ms
- ➖ **[refuted, medium]** Application CPU saturation is the cause
  - evidence (telemetry): app_process:cpu_pct_p95: baseline 1.6% vs current 1.9%

## 13. Observations and recommendations

### Observations

- No approved NFRs/SLOs exist: no requirement-based pass/fail is possible. Results are baseline-relative only.
- GET /rest/products/search (smoke@2rps/3s): only 7 samples in the smallest run; statistics are weak below 50.
- GET /rest/products/search: material regression vs approved baseline (p95 +2055.7% vs baseline (fail >= 25.0%); p99 +1583.3% vs baseline (fail >= 25.0%)).
- Results come from a local-lab environment (load generator, browser and app on one machine): suitable for before/after comparison, not for production capacity claims.

### Recommendations

1. Agree and import NFRs (`perf import-nfrs`) so SLO verdicts become possible.
2. Do not release KEY-22 for GET /rest/products/search before the cause is understood (`/perf:diagnose plan_20261115103720_ea67c5`).
3. Investigate: Latency on GET /rest/products/search is load-independent: a fixed per-request delay, not queueing or saturation.
4. Investigate: Added latency on GET /rest/products/search is introduced in front of the application (gateway/proxy/network), not by app processing.

## 14. Assumptions, limitations and caveats

- ASSUMPTION values in the plan: none (all values come from NFR, BASELINE or POLICY).
- Statistical power is limited with few repeats; the report states when a delta rests on a bootstrap CI instead of a rank test.
- Percentiles are recomputed from raw k6 samples; frontend metrics are lab measurements (single machine, desktop profile).
- Load is generated only against allowlisted hosts under the caps listed in 3.3; Kubernetes/cloud execution is not part of this run.
- plan note: No approved NFRs/SLOs found: verdicts will be baseline-only; no SLO thresholds were invented.

## Appendix

### A. Evidence and artifacts

| Run | Type | Spec hash | Artifact folder (script.js, spec.json, raw.ndjson, trace.zip, network.har, lighthouse-*.json) |
|---|---|---|---|
| `20261115103730_786ae2` | smoke | `0a7bfb9b0a` | `.perf\artifacts\run_20261115103730_786ae2` |
| `20261115103738_34de75` | load | `30236b5a88` | `.perf\artifacts\run_20261115103738_34de75` |
| `20261115103750_d0ec18` | load | `30236b5a88` | `.perf\artifacts\run_20261115103750_d0ec18` |
| `20261115103802_09c1db` | load | `30236b5a88` | `.perf\artifacts\run_20261115103802_09c1db` |
| `20261115103814_3ba3ce` | frontend | `814f66836c` | `.perf\artifacts\run_20261115103814_3ba3ce` |
| `20261115103834_ad6b97` | frontend | `814f66836c` | `.perf\artifacts\run_20261115103834_ad6b97` |
| `20261115103854_8ee00d` | frontend | `814f66836c` | `.perf\artifacts\run_20261115103854_8ee00d` |


### B. Reproduce

```bash
uv run perf status plan_20261115103720_ea67c5
uv run perf export plan_20261115103720_ea67c5 --out ./perf-export     # k6 scripts + data + run scripts for manual execution
uv run perf run plan_20261115103720_ea67c5                            # re-run (plan must be approved)
```

### C. Audit trail (this plan)

| Time | Actor | Action | Subject |
|---|---|---|---|
| 2026-11-15 10:37:20 | engine | plan:create | `plan_20261115103720_ea67c5` |
| 2026-11-15 10:37:25 | eval-human | approve:G2_plan | `plan_20261115103720_ea67c5` |
| 2026-11-15 10:37:30 | engine | run:start | `run_20261115103730_786ae2` |
| 2026-11-15 10:37:38 | engine | run:completed | `run_20261115103730_786ae2` |
| 2026-11-15 10:37:38 | engine | run:start | `run_20261115103738_34de75` |
| 2026-11-15 10:37:50 | engine | run:completed | `run_20261115103738_34de75` |
| 2026-11-15 10:37:50 | engine | run:start | `run_20261115103750_d0ec18` |
| 2026-11-15 10:38:02 | engine | run:completed | `run_20261115103750_d0ec18` |
| 2026-11-15 10:38:02 | engine | run:start | `run_20261115103802_09c1db` |
| 2026-11-15 10:38:14 | engine | run:completed | `run_20261115103802_09c1db` |
| 2026-11-15 10:38:14 | engine | run:start | `run_20261115103814_3ba3ce` |
| 2026-11-15 10:38:34 | engine | run:completed | `run_20261115103814_3ba3ce` |
| 2026-11-15 10:38:34 | engine | run:start | `run_20261115103834_ad6b97` |
| 2026-11-15 10:38:54 | engine | run:completed | `run_20261115103834_ad6b97` |
| 2026-11-15 10:38:55 | engine | run:start | `run_20261115103854_8ee00d` |
| 2026-11-15 10:39:15 | engine | run:completed | `run_20261115103854_8ee00d` |

