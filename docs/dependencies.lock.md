# Pinned external components (resolved 2026-11-15)

Pinning rule: choose a release at least ~2 weeks old unless a fix is required. Re-validate every quarter.

| Component | Pinned | Latest seen | Source | Use |
|---|---|---|---|---|
| k6 OSS | **v2.3.0** (2026-09-21) | v2.4.0 (2026-11-12) | github.com/grafana/k6/releases | API load engine, native binary installed by `perf setup` (SHA-256 checked) |
| grafana/mcp-k6 | v0.7.0 (2026-10-05) | v0.7.0 | github.com/grafana/mcp-k6 | Optional stdio MCP: script validation/docs |
| grafana/skills (k6 skill) | commit `8fb72a52` | — | github.com/grafana/skills | Vendored reference skill, see security-review.md |
| @playwright/test / playwright | 1.65.1 | 1.65.1 | npm | Deterministic browser journeys |
| @playwright/mcp | 0.0.85 | 0.0.85 | npm (Microsoft) | Journey discovery only, never in CI path |
| lighthouse | 13.6.0 | 13.6.0 | npm (GoogleChrome) | Synthetic audits |
| @lhci/cli | 0.16.1 | 0.16.1 | npm | Budgets / assertions |
| web-vitals | 6.2.3 | 6.2.3 | npm | LCP/CLS/INP/TTFB/FCP in-page |
| bmad-method | 6.12.4 | 6.12.4 | npm | Optional: TEA module skills (nfr / test-design) |
| mcp (Python SDK) | 2.3.x | 2.3.0 | PyPI | perf-engine MCP server (`MCPServer`, stdio) |
| OWASP Juice Shop | see sut/juice-shop/VERSION.md | — | github.com/juice-shop/juice-shop | Eval SUT (native, 127.0.0.1 only) |

## k6 v2.3.0 SHA-256
```
112276d495e5741c968e2bc09ea6196099c1275bd6db9ee0875d173c7148ce43  k6-v2.3.0-windows-amd64.zip
39c3117b6af817592dcd0ce4242105c0a7af10948c2a425306f0be8f7a8a8ab1  k6-v2.3.0-linux-amd64.tar.gz
5ca3433e8201da72a284aaa241a1bb5fb47f4abb4e384d39410ddd8062f49b90  k6-v2.3.0-linux-arm64.tar.gz
c83bb16f54f0676afa4ea235fd757f3892d307dbd1bd201450de83af31986654  k6-v2.3.0-macos-amd64.zip
b2417a3038edc5fe81dc178a889237724b595c5c9cfed875822008e46e862c7d  k6-v2.3.0-macos-arm64.zip
```

## Rejected / not on critical path
- Community Playwright+Lighthouse MCP servers: maintenance risk; Lighthouse runs directly.
- bmad-loop / bmad-mcp-server: not needed; orchestration is deterministic Python (ADR-0001).
- Kubernetes, k6-operator, ECS, Cloud Run, ACA executors: Phase 2 (same worker contract).
