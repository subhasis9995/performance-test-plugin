# ADR-0003 Provenance on every planned value
Status: accepted

Every threshold, workload number and budget in a PerformancePlan carries `source ∈ {NFR, BASELINE, POLICY, ASSUMPTION}` plus a reference. The system never invents NFRs. ASSUMPTION values block execution until a human confirms them, and reports list them.
