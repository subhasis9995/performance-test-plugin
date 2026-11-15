{
 "baselines": [
  {
   "id": "bl_search_v1",
   "target": "GET /rest/products/search",
   "workload_key": "load@25rps/60s",
   "version": 1,
   "status": "active",
   "approval_id": "apr_1"
  }
 ],
 "candidates": [
  {
   "series_id": "ser_feature_key22",
   "target": "GET /rest/products/search",
   "workload_key": "load@25rps/60s",
   "eligible": false,
   "reasons": [
    "series kind 'feature' cannot be promoted; only controlled benchmark series can"
   ],
   "stability": {
    "stable": true
   }
  }
 ]
}
