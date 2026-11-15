"""TestRunSpec v1 — the only thing workers execute.

Agents never hand workers free-form load instructions. The planner compiles a
PerformancePlan into TestRunSpecs; the policy gate validates them; workers run them.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SPEC_VERSION = "perfeng.dev/testrunspec/v1"
SECRET_REF_RE = re.compile(r"^env:[A-Z_][A-Z0-9_]*$")


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SecretRef(_M):
    """Reference to a secret. Value never stored in the spec."""

    ref: str = Field(..., description="env:VAR_NAME")

    @field_validator("ref")
    @classmethod
    def _check(cls, v: str) -> str:
        if not SECRET_REF_RE.match(v):
            raise ValueError("secret must be a reference of form env:UPPER_SNAKE_NAME, never a literal value")
        return v


class AuthConfig(_M):
    type: Literal["none", "basic", "bearer", "api_key", "oauth2_client_credentials", "login_flow", "cookie"] = "none"
    header: str | None = None  # api_key header name
    username: SecretRef | None = None
    password: SecretRef | None = None
    token: SecretRef | None = None
    client_id: SecretRef | None = None
    client_secret: SecretRef | None = None
    token_url: str | None = None
    login_request: dict[str, Any] | None = Field(
        None, description="login_flow: {method, path, body_template, token_json_path}; body uses ${username}/${password}"
    )


class Request(_M):
    name: str  # endpoint key / transaction name, used as k6 tag
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
    path: str  # may contain ${data.field} placeholders
    headers: dict[str, str] = {}
    body: Any | None = None
    graphql: dict[str, Any] | None = None  # {query, variables, operationName}
    expect_status: list[int] = [200]
    weight: int = 1
    protocol: Literal["http", "graphql", "grpc", "websocket"] = "http"

    @model_validator(mode="after")
    def _protocol(self) -> "Request":
        if self.protocol in ("grpc", "websocket"):
            raise ValueError(f"protocol {self.protocol} is an extension point, not implemented in v1")
        return self


class Stage(_M):
    rate: float = Field(..., gt=0, description="arrival rate (iterations/s)")
    duration_s: int = Field(..., gt=0)


class Scenario(_M):
    name: str
    kind: Literal["api", "browser"]
    requests: list[Request] = []
    journey: str | None = None  # browser: journey name
    journey_spec_path: str | None = None
    executor: Literal["constant-arrival-rate", "ramping-arrival-rate", "per-vu-iterations"] = "constant-arrival-rate"
    stages: list[Stage] = []
    iterations: int | None = None  # per-vu-iterations / browser repeats
    pre_allocated_vus: int = 10
    max_vus: int = 50


class Thresholds(_M):
    """Abort / pass thresholds. Abort thresholds are safety stops."""

    abort_error_rate: float = 0.2
    abort_p95_ms: float | None = None
    slo: dict[str, float] = Field(default_factory=dict, description="metric -> threshold (from NFR only)")


class Limits(_M):
    max_duration_s: int
    max_vus: int
    max_arrival_rate: float


class BrowserProfile(_M):
    name: str = "desktop"
    viewport: tuple[int, int] = (1350, 940)
    cpu_slowdown: float = 1.0
    network: Literal["none", "fast4g", "slow4g", "3g"] = "none"


class Approval(_M):
    approval_id: str
    approver: str


class TestRunSpec(_M):
    __test__: ClassVar[bool] = False  # not a pytest class
    apiVersion: Literal["perfeng.dev/testrunspec/v1"] = SPEC_VERSION
    run_id: str
    application_id: str
    feature_id: str | None = None
    plan_id: str | None = None
    repository: str | None = None
    commit: str | None = None
    environment: str
    test_type: str
    base_url: str
    approved_hosts: list[str]
    auth: AuthConfig = AuthConfig()
    scenarios: list[Scenario]
    limits: Limits
    thresholds: Thresholds = Thresholds()
    baseline_ref: str | None = None
    browser_profiles: list[BrowserProfile] = []
    test_data_refs: list[str] = []  # paths to CSV/JSON within project
    secret_refs: list[SecretRef] = []
    telemetry_queries: dict[str, str] = {}  # name -> PromQL or scrape spec "scrape:<url>#<metric>"
    artifact_retention_days: int = 30
    approval: Approval | None = None
    labels: dict[str, str] = {}

    def content_hash(self) -> str:
        """Hash of everything except run_id/approval, used to bind approvals to exact content."""
        d = self.model_dump(mode="json", exclude={"run_id", "approval"})
        return hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()

    def total_duration_s(self) -> int:
        total = 0
        for s in self.scenarios:
            total = max(total, sum(st.duration_s for st in s.stages))
        return total

    def peak_rate(self) -> float:
        return max((st.rate for s in self.scenarios for st in s.stages), default=0.0)

    def peak_vus(self) -> int:
        return max((s.max_vus for s in self.scenarios), default=0)


def export_json_schema() -> dict[str, Any]:
    return TestRunSpec.model_json_schema()
