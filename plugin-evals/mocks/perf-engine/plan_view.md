{
 "plan_id": "plan_key22_v1",
 "version": 1,
 "status": "draft",
 "workflow": "feature",
 "feature_id": "KEY-22",
 "environment": "perf-qa",
 "impact": {
  "feature_id": "KEY-22",
  "summary": "Rank product search results by relevance",
  "changed_files": [
   "routes/search.ts",
   "frontend/src/app/search-result/search-result.component.ts"
  ],
  "backend": [
   {
    "kind": "endpoint",
    "name": "GET /rest/products/search",
    "evidence": [
     "routes/search.ts matches service-map 'product-search'"
    ],
    "confidence": 0.9
   },
   {
    "kind": "service",
    "name": "SearchService",
    "evidence": [
     "routes/search.ts matches service-map 'product-search'"
    ],
    "confidence": 0.9
   }
  ],
  "frontend": [
   {
    "kind": "journey",
    "name": "Search",
    "evidence": [
     "search-result.component.ts matches service-map 'search-ui'"
    ],
    "confidence": 0.85
   }
  ],
  "dependencies": [
   {
    "kind": "dependency",
    "name": "sqlite:Products",
    "evidence": [
     "service-map"
    ],
    "confidence": 0.7
   }
  ],
  "unknowns": []
 },
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
