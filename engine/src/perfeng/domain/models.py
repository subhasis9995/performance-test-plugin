"""Core domain model.

Key distinctions enforced here (see docs/adr):
- Benchmark (one run) vs BenchmarkSeries (repeated runs) vs Baseline (approved, versioned reference).
- NFR/SLO vs baseline vs trend are separate inputs to analysis.
- Every planned value carries provenance; ASSUMPTION values need human confirmation.
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def utcnow() -> datetime:
    return datetime.now(UTC)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False)


class Source(str, enum.Enum):
    NFR = "NFR"  # from an approved NFR/SLO document
    BASELINE = "BASELINE"  # from an approved baseline
    POLICY = "POLICY"  # platform default policy
    ASSUMPTION = "ASSUMPTION"  # needs human confirmation


class ProvenancedValue(_Model):
    value: float | int | str | bool
    unit: str | None = None
    source: Source
    ref: str | None = Field(None, description="NFR id, baseline id, policy key, or assumption rationale")
    confirmed: bool = Field(False, description="ASSUMPTION values must be confirmed before execution")

    @property
    def needs_confirmation(self) -> bool:
        return self.source == Source.ASSUMPTION and not self.confirmed


class TestType(str, enum.Enum):
    SMOKE = "smoke"
    LOAD = "load"
    PEAK = "peak"
    STRESS = "stress"
    SPIKE = "spike"
    SOAK = "soak"
    CAPACITY = "capacity"  # stepped ladder, stops at first breach
    FRONTEND = "frontend"


# Test types that always require explicit human approval (gate G4).
APPROVAL_REQUIRED_TYPES = {TestType.STRESS, TestType.SPIKE, TestType.SOAK, TestType.CAPACITY}


class Verdict(str, enum.Enum):
    PASS = "PASS"
    PASS_WITH_REGRESSION_WARNING = "PASS_WITH_REGRESSION_WARNING"
    FAIL_SLO = "FAIL_SLO"
    FAIL_REGRESSION = "FAIL_REGRESSION"
    INCONCLUSIVE = "INCONCLUSIVE"


class RunStatus(str, enum.Enum):
    PLANNED = "planned"
    APPROVED = "approved"
    RUNNING = "running"
    COMPLETED = "completed"
    ABORTED = "aborted"  # safety stop
    FAILED = "failed"  # tooling failure
    REJECTED = "rejected"  # policy gate


class GateKind(str, enum.Enum):
    INTAKE = "G1_intake"
    PLAN = "G2_plan"
    ASSUMPTIONS = "G3_assumptions"
    HIGH_RISK_TEST = "G4_high_risk_test"
    BASELINE_PROMOTION = "G5_baseline_promotion"
    DIAGNOSTIC_RUN = "G6_diagnostic_run"
    TARGET_CHANGE = "G7_target_change"


# ---------------------------------------------------------------- registry entities


class NFR(_Model):
    id: str
    application_id: str
    scope: str = Field(..., description="endpoint key ('GET /rest/products/search'), journey name, or '*'")
    metric: str = Field(..., description="e.g. http_req_duration.p95, error_rate, lcp.p75")
    comparator: Literal["<", "<=", ">", ">="] = "<"
    threshold: float
    unit: str | None = None
    source_doc: str | None = None
    approved_by: str | None = None


class Endpoint(_Model):
    method: str
    path: str  # templated, e.g. /api/Products/{id}
    operation_id: str | None = None
    tags: list[str] = []
    source: Literal["openapi", "postman", "graphql", "har", "crawl", "probe", "manual", "repo"] = "manual"
    confidence: float = 1.0
    request_example: dict[str, Any] | None = None
    requires_auth: bool = False

    @property
    def key(self) -> str:
        return f"{self.method.upper()} {self.path}"


class Journey(_Model):
    name: str
    steps: list[str]
    spec_path: str | None = None  # persisted deterministic Playwright test
    source: Literal["user", "discovered", "repo"] = "user"


class Approval(_Model):
    id: str
    gate: GateKind
    subject_id: str  # plan id, series id, ...
    subject_hash: str  # hash of the exact content approved
    approver: str
    reason: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


# ---------------------------------------------------------------- feature understanding


class ImpactItem(_Model):
    kind: Literal["endpoint", "service", "repository", "component", "journey", "dependency"]
    name: str
    evidence: list[str] = Field(default_factory=list, description="changed files / diff hunks / mapping rules")
    confidence: float = 0.5


class FeatureImpactModel(_Model):
    feature_id: str
    summary: str | None = None
    changed_files: list[str] = []
    backend: list[ImpactItem] = []
    frontend: list[ImpactItem] = []
    dependencies: list[ImpactItem] = []
    relevant_baselines: list[str] = []
    unknowns: list[str] = []

    def endpoints(self) -> list[str]:
        return [i.name for i in self.backend if i.kind == "endpoint"]

    def journeys(self) -> list[str]:
        return [i.name for i in self.frontend if i.kind == "journey"]


# ---------------------------------------------------------------- planning


class WorkloadStep(_Model):
    rate: ProvenancedValue  # requests (iterations) per second
    duration_s: ProvenancedValue


class PlannedTest(_Model):
    name: str
    type: TestType
    targets: list[str]  # endpoint keys or journey names
    workload: list[WorkloadStep] = []
    repeats: ProvenancedValue
    thresholds: dict[str, ProvenancedValue] = {}  # metric -> value
    rationale: str


class PerformancePlan(_Model):
    id: str
    version: int = 1
    application_id: str
    environment: str
    feature_id: str | None = None
    workflow: Literal["benchmark", "feature", "diagnostic"]
    impact: FeatureImpactModel | None = None
    tests: list[PlannedTest]
    measurements: list[str]
    baseline_refs: dict[str, str] = {}  # target -> baseline id
    regression_warn_pct: ProvenancedValue
    regression_fail_pct: ProvenancedValue
    notes: list[str] = []
    created_at: datetime = Field(default_factory=utcnow)

    def all_values(self) -> list[tuple[str, ProvenancedValue]]:
        out: list[tuple[str, ProvenancedValue]] = [
            ("regression_warn_pct", self.regression_warn_pct),
            ("regression_fail_pct", self.regression_fail_pct),
        ]
        for t in self.tests:
            out.append((f"{t.name}.repeats", t.repeats))
            for i, s in enumerate(t.workload):
                out.append((f"{t.name}.step{i}.rate", s.rate))
                out.append((f"{t.name}.step{i}.duration_s", s.duration_s))
            for m, v in t.thresholds.items():
                out.append((f"{t.name}.threshold.{m}", v))
        return out

    def unconfirmed_assumptions(self) -> list[str]:
        return [k for k, v in self.all_values() if v.needs_confirmation]


# ---------------------------------------------------------------- results


class MetricSummary(_Model):
    """Canonical per-target metric summary (one run)."""

    target: str
    count: int = 0
    p50: float | None = None
    p90: float | None = None
    p95: float | None = None
    p99: float | None = None
    mean: float | None = None
    max: float | None = None
    error_rate: float | None = None
    rps: float | None = None
    extra: dict[str, float | None] = {}


class RunMetrics(_Model):
    duration_s: float = 0
    iterations: int = 0
    dropped_iterations: int = 0
    vus_max: int = 0
    data_sent_bytes: int = 0
    data_received_bytes: int = 0
    http_reqs: int = 0
    overall: MetricSummary | None = None
    per_target: dict[str, MetricSummary] = {}
    frontend: dict[str, dict[str, float | None]] = {}  # journey -> metric -> value
    telemetry: dict[str, float | None] = {}
    generator: dict[str, float | None] = {}  # load generator health (cpu %, mem)
    aborted_reason: str | None = None


class Hypothesis(_Model):
    id: str
    claim: str
    category: str  # downstream_latency, error_spike, cpu_saturation, ...
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"] = "low"
    next_test: str | None = None
    status: Literal["open", "supported", "refuted", "insufficient_evidence"] = "open"
