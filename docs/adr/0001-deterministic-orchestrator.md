# ADR-0001 Deterministic orchestrator, LLM proposes only
Status: accepted

The durable workflow (intake → plan → approve → validate → execute → normalize → analyze → diagnose) is a persisted Python state machine. LLM agents (Claude Code skills/subagents) may only *propose* artifacts — impact refinements, plan drafts, hypothesis ranking, prose — via MCP tools that validate against schemas and policy. All numbers (percentiles, deltas, variance, verdicts) are computed by deterministic code. Reason: reproducibility, auditability, safety; an unconstrained LLM loop cannot be a release gate.
