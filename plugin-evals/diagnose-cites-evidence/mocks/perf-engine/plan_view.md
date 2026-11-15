{
 "plan_id": "plan_diag_v2",
 "version": 2,
 "status": "draft",
 "workflow": "diagnostic",
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
   "name": "diag-step:GET /rest/products/search@12.5",
   "type": "load",
   "targets": [
    "GET /rest/products/search"
   ],
   "workload": [
    "12.5 req/s for 60s"
   ],
   "repeats": 2,
   "rationale": "focused step load: separates constant added latency from load-dependent queueing"
  },
  {
   "name": "diag-step:GET /rest/products/search@25",
   "type": "load",
   "targets": [
    "GET /rest/products/search"
   ],
   "workload": [
    "25 req/s for 60s"
   ],
   "repeats": 2,
   "rationale": "focused step load: separates constant added latency from load-dependent queueing"
  },
  {
   "name": "diag-step:GET /rest/products/search@50",
   "type": "load",
   "targets": [
    "GET /rest/products/search"
   ],
   "workload": [
    "50 req/s for 60s"
   ],
   "repeats": 2,
   "rationale": "focused step load: separates constant added latency from load-dependent queueing"
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
  "POLICY": [
   "diag-step rates = 0.5x/1x/2x of baseline bl_search_v1 (policy:diagnostics.step_factors)",
   "repeats = 2 (policy:diagnostics.repeats)"
  ]
 },
 "unconfirmed_assumptions": [],
 "high_risk_tests_needing_approval": [],
 "baseline_refs": {
  "GET /rest/products/search": "bl_search_v1",
  "Search": "bl_searchjourney_v1"
 },
 "estimated_load_seconds": 360,
 "notes": [
  "Diagnostic Plan v2 for plan_bad; hypotheses: edge_or_network_latency"
 ],
 "plan_hash": "abc123"
}
