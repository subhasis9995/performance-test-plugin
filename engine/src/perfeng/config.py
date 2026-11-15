"""Project configuration (.perf/project.yaml) and platform policy loading.

`.perf/project.yaml` never contains secret values — only env var names.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from perfeng.spec.testrunspec_v1 import SECRET_REF_RE

POLICY_DEFAULTS = Path(__file__).parent / "policy" / "defaults.yaml"


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AuthIntake(_M):
    type: Literal["none", "basic", "bearer", "api_key", "oauth2_client_credentials", "login_flow", "cookie"] = "none"
    header: str | None = None
    env: dict[str, str] = Field(default_factory=dict, description="role -> ENV VAR NAME, e.g. {username: PERF_USER}")
    token_url: str | None = None
    login_request: dict[str, Any] | None = None


class EnvironmentConfig(_M):
    base_url: str
    frontend_url: str | None = None
    allowed_hosts: list[str]
    ownership_confirmed: bool = False  # user confirmed they own / may load-test this target
    production: bool = False
    limits: dict[str, float] = {}  # overrides of policy.limits (may only lower unless approved)
    telemetry: dict[str, str] = {}  # name -> prometheus base url or scrape url
    auth: AuthIntake = AuthIntake()


class SpecSource(_M):
    kind: Literal["openapi", "postman", "graphql", "har", "proto", "none"]
    location: str | None = None  # workspace path or URL


class ProjectConfig(_M):
    application_id: str
    environments: dict[str, EnvironmentConfig] = {}
    api_specs: list[SpecSource] = []
    test_data: list[str] = []
    nfr_files: list[str] = []
    nfrs_declared_none: bool = False  # user said "no NFRs exist"
    auth_declared: bool = False  # user answered the auth question (type + env var names)
    test_data_declared_none: bool = False
    journeys_declared_none: bool = False
    api_spec_declared_none: bool = False
    journeys: list[dict[str, Any]] = []
    repository: str | None = None
    service_map: str | None = None  # path to .perf/service-map.yaml
    targets: list[str] = []  # endpoint keys the user wants benchmarked (empty = all discovered, safe methods)
    request_overrides: dict[str, dict[str, Any]] = {}  # endpoint key -> {path, body, headers, expect_status}
    frontend_journeys_dir: str = ".perf/journeys"
    workitems_dir: str = ".perf/workitems"


def project_root(start: Path | None = None) -> Path:
    env = os.environ.get("PERF_PROJECT_DIR")
    if env:
        return Path(env).resolve()
    p = (start or Path.cwd()).resolve()
    for cand in [p, *p.parents]:
        if (cand / ".perf" / "project.yaml").exists():
            return cand
    return p


def perf_dir(root: Path | None = None) -> Path:
    return (root or project_root()) / ".perf"


def load_project(root: Path | None = None) -> ProjectConfig | None:
    f = perf_dir(root) / "project.yaml"
    if not f.exists():
        return None
    return ProjectConfig.model_validate(yaml.safe_load(f.read_text(encoding="utf-8")) or {})


def save_project(cfg: ProjectConfig, root: Path | None = None) -> Path:
    d = perf_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    for name, env in cfg.environments.items():
        for role, var in env.auth.env.items():
            if not SECRET_REF_RE.match(f"env:{var}"):
                raise ValueError(f"auth.env.{role} must be an ENV VAR NAME (UPPER_SNAKE), not a value")
    f = d / "project.yaml"
    f.write_text(yaml.safe_dump(cfg.model_dump(mode="json", exclude_defaults=False), sort_keys=False), encoding="utf-8")
    return f


def load_policy(path: str | Path | None = None) -> dict[str, Any]:
    base = yaml.safe_load(POLICY_DEFAULTS.read_text(encoding="utf-8"))
    override = path or os.environ.get("PERF_POLICY_FILE")
    if override and Path(override).exists():
        _deep_merge(base, yaml.safe_load(Path(override).read_text(encoding="utf-8")) or {})
    return base


def _deep_merge(a: dict, b: dict) -> dict:
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(a.get(k), dict):
            _deep_merge(a[k], v)
        else:
            a[k] = v
    return a
