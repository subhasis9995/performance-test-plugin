---
name: nfr-provenance
description: Rules for performance requirements - how NFR/SLO, baseline and policy defaults differ, how to label every number's source, and how to hand evidence to BMAD TEA nfr-assess or release gates. Use when discussing SLOs, thresholds, "is 500 ms our requirement?", release gate evidence, or when a plan contains ASSUMPTION values.
---

# NFR provenance

| Source | Meaning | May be presented as a requirement? |
|---|---|---|
| NFR | approved NFR/SLO document, imported with `set_project_inputs(nfr_file=...)` | yes |
| BASELINE | an approved baseline version (measured reference) | no: it is a reference, not a requirement |
| POLICY | platform default (repeats, rates, regression %, lab budgets) | no |
| ASSUMPTION | anything proposed by an agent or user without an approved source | no: needs explicit confirmation (gate G3) |

- Never invent NFRs. With none present, say "no approved SLO: verdict is baseline-only".
- SLO and baseline are different. For example, SLO p95 < 500 ms, baseline p95 = 408 ms, current = 470 ms gives SLO PASS, +15.2% vs baseline, so a regression warning.
- NFR file format (`nfrs.yaml`): `nfrs: [{id, scope: "GET /path" | journey | "*", metric: http_req_duration.p95 | error_rate | lcp_ms | throughput_rps, comparator: "<", threshold, unit, source_doc, approved_by}]`.
- BMAD TEA: when a team uses `bmad-testarch-nfr` (nfr-assess) or a trace/release gate, give it the report from `write_report` plus the analysis JSON as evidence. TEA does the PASS/CONCERNS/FAIL reasoning. It does not run tests.
