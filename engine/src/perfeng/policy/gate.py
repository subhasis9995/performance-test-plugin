"""Policy / safety gate. Every TestRunSpec passes through `validate_spec` before a worker sees it."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from perfeng.config import EnvironmentConfig
from perfeng.domain.models import APPROVAL_REQUIRED_TYPES, TestType
from perfeng.spec.testrunspec_v1 import TestRunSpec

# Literal secrets smuggled into headers/bodies.
_SECRET_PATTERNS = [
    re.compile(r"(?i)\bbearer\s+[a-z0-9\-_\.=]{16,}"),
    re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),  # JWT
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)(password|passwd|secret|api[_-]?key|token)[\\\"']*\s*[:=]\s*[\\\"']*(?!\$\{)[^\s\"'\\${}]{6,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]


class PolicyViolation(Exception):
    def __init__(self, violations: list[str]):
        super().__init__("; ".join(violations))
        self.violations = violations


@dataclass
class GateResult:
    ok: bool
    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def effective_limits(policy: dict, env: EnvironmentConfig) -> dict[str, float]:
    lim = dict(policy["limits"])
    lim.update(env.limits)
    return lim


def host_allowed(url_or_host: str, allowed: list[str]) -> bool:
    host = urlparse(url_or_host).hostname if "://" in url_or_host else url_or_host.split(":")[0]
    if not host:
        return False
    host = host.lower()
    for a in allowed:
        a = a.lower()
        if a.startswith("*.") and host.endswith(a[1:]):
            return True
        if host == a:
            return True
    return False


def find_literal_secrets(obj) -> list[str]:
    text = json.dumps(obj, default=str)
    hits = []
    for p in _SECRET_PATTERNS:
        m = p.search(text)
        if m:
            hits.append(p.pattern[:40])
    return hits


def validate_spec(
    spec: TestRunSpec,
    env: EnvironmentConfig,
    policy: dict,
    approval_valid: bool,
) -> GateResult:
    v: list[str] = []
    w: list[str] = []
    lim = effective_limits(policy, env)

    if not env.ownership_confirmed:
        v.append("environment ownership/permission to load test not confirmed (gate G1)")
    if env.production:
        v.append("production environments are not allowed as load targets in this release")
    if not host_allowed(spec.base_url, env.allowed_hosts):
        v.append(f"base_url host not in environment allowlist: {urlparse(spec.base_url).hostname}")
    for h in spec.approved_hosts:
        if not host_allowed(h, env.allowed_hosts):
            v.append(f"approved_hosts entry outside allowlist: {h}")
    for s in spec.scenarios:
        for r in s.requests:
            if "://" in r.path and not host_allowed(r.path, env.allowed_hosts):
                v.append(f"request {r.name} targets non-allowlisted URL")
    if spec.peak_rate() > lim["max_arrival_rate"]:
        v.append(f"peak arrival rate {spec.peak_rate()} > cap {lim['max_arrival_rate']}")
    if spec.peak_vus() > lim["max_vus"]:
        v.append(f"max VUs {spec.peak_vus()} > cap {lim['max_vus']}")
    if spec.total_duration_s() > lim["max_duration_s"]:
        v.append(f"duration {spec.total_duration_s()}s > cap {lim['max_duration_s']}s")
    if spec.limits.max_arrival_rate > lim["max_arrival_rate"] or spec.limits.max_vus > lim["max_vus"]:
        v.append("spec limits exceed environment/policy caps")
    try:
        tt = TestType(spec.test_type)
    except ValueError:
        v.append(f"unknown test type {spec.test_type}")
        tt = None
    if not approval_valid:
        v.append("no valid human approval bound to this exact spec content (gate G2/G4)")
    elif tt in APPROVAL_REQUIRED_TYPES and spec.approval is None:
        v.append(f"{tt.value} test requires explicit approval (gate G4)")
    secrets = find_literal_secrets(
        {"auth": spec.auth.model_dump(), "scenarios": [s.model_dump() for s in spec.scenarios]}
    )
    if secrets:
        v.append("literal secret-like values found in spec; use env:NAME secret references")
    if spec.thresholds.abort_error_rate > policy["safety"]["abort_error_rate"]:
        w.append("abort_error_rate higher than policy default")
    return GateResult(ok=not v, violations=v, warnings=w)
