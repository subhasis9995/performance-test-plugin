{
 "plan_id": "plan_bad",
 "iteration": 1,
 "overall_verdict": "FAIL_REGRESSION",
 "hypotheses": [
  {
   "id": "h1",
   "category": "edge_or_network_latency",
   "status": "supported",
   "confidence": "high",
   "claim": "Added latency on GET /rest/products/search is introduced in front of the application (gateway/proxy/network), not by app processing",
   "evidence": [
    {
     "source": "telemetry",
     "detail": "proxy:sut_proxy_injected_delay_seconds{route=\"/rest/products/search\"}:mean_ms median 200.0 ms per request"
    },
    {
     "source": "analysis",
     "detail": "client p95 increased by 212.4 ms (10.3 -> 222.7 ms)"
    }
   ],
   "next_test": "Step load at 0.5x/1x/2x"
  },
  {
   "id": "h2",
   "category": "app_server_latency",
   "status": "refuted",
   "confidence": "medium",
   "claim": "Application/server-side processing on /rest/products/search is the cause (server-side time is unchanged)",
   "evidence": [
    {
     "source": "telemetry",
     "detail": "upstream latency baseline 7.7 ms vs current 7.6 ms"
    }
   ]
  },
  {
   "id": "h3",
   "category": "cpu_saturation",
   "status": "refuted",
   "confidence": "medium",
   "claim": "Application CPU saturation is the cause",
   "evidence": [
    {
     "source": "telemetry",
     "detail": "app_process:cpu_pct_p95 baseline 1.6% vs current 1.9%"
    }
   ]
  },
  {
   "id": "h4",
   "category": "unknown",
   "status": "insufficient_evidence",
   "confidence": "low",
   "claim": "Database query time on SearchService",
   "evidence": [],
   "next_test": "Add database metrics (query duration) to telemetry"
  }
 ],
 "diagnostic_plan_id": "plan_diag_v2",
 "diagnostic_plan_gate": "G6_diagnostic_run",
 "notes": []
}
