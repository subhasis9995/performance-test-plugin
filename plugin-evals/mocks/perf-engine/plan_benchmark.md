{
 "plan_id": "plan_bench_v1",
 "version": 1,
 "status": "draft",
 "workflow": "benchmark",
 "feature_id": null,
 "environment": "perf-qa",
 "impact": null,
 "tests": [
  {
   "name": "smoke:GET /rest/products/search",
   "type": "smoke",
   "targets": [
    "GET /rest/products/search"
   ],
   "workload": [
    "2 req/s for 20s"
   ],
   "repeats": 1,
   "rationale": "changed endpoint still works"
  },
  {
   "name": "load:GET /rest/products/search@25",
   "type": "load",
   "targets": [
    "GET /rest/products/search"
   ],
   "workload": [
    "25 req/s for 60s"
   ],
   "repeats": 5,
   "rationale": "normal load, same workload as baseline"
  },
  {
   "name": "frontend:Search",
   "type": "frontend",
   "targets": [
    "Search"
   ],
   "workload": [],
   "repeats": 5,
   "rationale": "affected frontend journey"
  }
 ],
 "measurements": [
  "http_req_duration p50/p90/p95/p99",
  "error rate",
  "LCP",
  "CLS",
  "INP",
  "TBT"
 ],
 "values_by_source": {
  "BASELINE": [
   "load:GET /rest/products/search@25.step0.rate = 25 req/s (bl_search_v1)"
  ],
  "POLICY": [
   "regression_warn_pct = 10 % (policy:regression.warn_pct)",
   "load.repeats = 5 (policy:feature.repeats)"
  ]
 },
 "unconfirmed_assumptions": [],
 "high_risk_tests_needing_approval": [],
 "baseline_refs": {
  "GET /rest/products/search": "bl_search_v1",
  "Search": "bl_searchjourney_v1"
 },
 "estimated_load_seconds": 340,
 "notes": [
  "No approved NFRs/SLOs found: verdicts will be baseline-only; no SLO thresholds were invented."
 ],
 "plan_hash": "abc123"
}
