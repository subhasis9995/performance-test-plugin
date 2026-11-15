# ADR-0005 Native-first execution, container-portable contract
Status: accepted

Users may not have Docker. Workers run as local processes using pinned toolchains installed into the plugin data directory. Docker is auto-detected and used when available. Both paths use one worker contract: `perfeng-worker --spec <TestRunSpec.json> --out <artifact dir/URI>`, exit code, `result.json`. Kubernetes and managed cloud executors (Phase 2) reuse the same contract.
