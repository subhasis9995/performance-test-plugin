---
name: results-explainer
description: How to explain deterministic performance results to humans - percentiles, error rate, throughput, dropped iterations, stability (CV), significance, local-lab caveats, Core Web Vitals (LCP, CLS, INP, TBT lab, TTFB, FCP, Speed Index). Use when summarizing perf analysis, explaining a verdict, or answering "is this good?".
---

# Explaining results

- All numbers come from `get_analysis`. Never estimate or recompute them in prose.
- Lead with the verdict. Then give SLO, baseline delta and trend, kept separate.
- Percentiles: p95 is what 95% of requests beat. Prefer p95/p99 over averages.
- Significance: with few repeats, say "low statistical power". A delta inside the noise is not a regression.
- Dropped iterations or high load-generator CPU means the measurement may be limited by the client, so the verdict is INCONCLUSIVE.
- Local runs share one machine between the load generator, the browser and possibly the app. Call them "local-lab" results. They are good for before/after comparison, not for production capacity claims.
- Frontend: LCP (loading), CLS (visual stability), INP (responsiveness; null when there was no interaction), TBT (lab proxy for main-thread blocking), TTFB, FCP, Speed Index. **FID is obsolete. Do not use it.**
- Lighthouse budgets are POLICY lab budgets, not NFRs.
