# ADR-0002 Benchmark ≠ Baseline
Status: accepted

A Benchmark is one measurement run. A BenchmarkSeries is N repeated controlled runs (default 5). A Baseline is an *approved*, versioned reference derived from a stable series. Promotion requires: series size ≥ policy minimum, stability (CV of p95 and error-rate spread within policy, no generator saturation), and an explicit human approval record. Feature runs never auto-promote, preventing normalisation of gradual degradation. SLO/NFR, baseline, and recent trend are stored and evaluated separately.
