"""Deterministic orchestrator (ADR-0001).

    intake -> discover -> plan -> [human approval] -> compile TestRunSpecs -> policy gate
           -> execute (executor/worker) -> normalize -> analyze -> diagnose -> (plan vN ...)

All state lives in the registry; every step is idempotent / re-runnable by id.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select

from perfeng.analysis import stats
from perfeng.analysis.verdict import decide, evaluate_stability, overall, series_samples
from perfeng.config import (
    EnvironmentConfig,
    ProjectConfig,
    load_policy,
    load_project,
    perf_dir,
    project_root,
    save_project,
)
from perfeng.domain.models import (
    APPROVAL_REQUIRED_TYPES,
    Endpoint,
    GateKind,
    NFR,
    PerformancePlan,
    TestType,
    Verdict,
)
from perfeng.executors.base import Executor, select_executor
from perfeng.planning import planner
from perfeng.policy.gate import validate_spec
from perfeng.registry.baselines import GateError, content_hash, promote_baseline, promotion_check, record_approval
from perfeng.registry.db import (
    AnalysisRow,
    BaselineRow,
    NFRRow,
    PlanRow,
    Registry,
    RunRow,
    SeriesRow,
    new_id,
    row_to_dict,
)
from perfeng.secrets import MissingSecret, presence, resolve
from perfeng.spec import testrunspec_v1 as trs

SAFE_METHODS = {"GET", "HEAD"}


class EngineError(Exception):
    pass


class Engine:
    def __init__(self, root: Path | None = None, registry: Registry | None = None, policy: dict | None = None,
                 executor: Executor | None = None):
        self.root = (root or project_root()).resolve()
        self.policy = policy or load_policy(self.root / ".perf" / "policy.yaml")
        self.reg = registry or Registry(_db_url(self.root))
        self.executor = executor or select_executor()

    # ================================================================ configuration / intake
    @property
    def cfg(self) -> ProjectConfig | None:
        return load_project(self.root)

    def require_cfg(self) -> ProjectConfig:
        c = self.cfg
        if c is None:
            raise EngineError("project not configured: run intake (/perf:benchmark) or `perf init` first")
        return c

    def env(self, name: str) -> EnvironmentConfig:
        c = self.require_cfg()
        if name not in c.environments:
            raise EngineError(f"unknown environment '{name}'. Known: {', '.join(c.environments) or 'none'}")
        return c.environments[name]

    def init_project(self, application_id: str, environment: str, base_url: str, *, ownership_confirmed: bool,
                     allowed_hosts: list[str] | None = None, frontend_url: str | None = None,
                     actor: str = "user") -> Path:
        from urllib.parse import urlparse

        cfg = self.cfg or ProjectConfig(application_id=application_id)
        host = urlparse(base_url).hostname
        hosts = sorted(set((allowed_hosts or []) + ([host] if host else [])))
        existing = cfg.environments.get(environment)
        if existing and (existing.base_url != base_url or set(existing.allowed_hosts) != set(hosts)):
            self.reg.audit(actor, "target:change", environment, {"from": existing.base_url, "to": base_url})
        cfg.environments[environment] = EnvironmentConfig(
            base_url=base_url, frontend_url=frontend_url, allowed_hosts=hosts, ownership_confirmed=ownership_confirmed,
            **({"limits": existing.limits, "telemetry": existing.telemetry, "auth": existing.auth} if existing else {}),
        )
        cfg.application_id = application_id
        f = save_project(cfg, self.root)
        _ensure_env_hygiene(self.root)
        self.reg.audit(actor, "project:init", application_id, {"environment": environment, "base_url": base_url})
        return f

    def update_project(self, actor: str = "user", **fields) -> ProjectConfig:
        cfg = self.require_cfg()
        d = cfg.model_dump()
        for k, v in fields.items():
            if k.startswith("env."):
                _, env_name, attr = k.split(".", 2)
                d["environments"][env_name][attr] = v
            else:
                d[k] = v
        new = ProjectConfig.model_validate(d)
        save_project(new, self.root)
        self.reg.audit(actor, "project:update", cfg.application_id, {"fields": sorted(fields)})
        return new

    def intake_status(self, workflow: str = "benchmark", environment: str | None = None) -> dict[str, Any]:
        """What the plugin still needs to ask the user. Never returns secret values."""
        c = self.cfg
        missing: list[dict[str, str]] = []
        known: dict[str, Any] = {}
        if c is None:
            missing.append({"item": "target", "ask": "Application name, environment name, base URL, and confirmation you own / may load-test it"})
            return {"ready": False, "known": known, "missing": missing + _optional_asks(workflow), "secrets_rule": SECRETS_RULE}
        known["application_id"] = c.application_id
        known["environments"] = {k: {"base_url": v.base_url, "ownership_confirmed": v.ownership_confirmed}
                                 for k, v in c.environments.items()}
        envs = [environment] if environment else list(c.environments)
        for e in envs:
            ec = c.environments.get(e)
            if ec is None:
                missing.append({"item": "target", "ask": f"Base URL for environment '{e}' and ownership confirmation"})
                continue
            if not ec.ownership_confirmed:
                missing.append({"item": "ownership", "ask": f"Confirm you own or have permission to load test {ec.base_url}"})
            if ec.auth.type != "none":
                pres = presence(list(ec.auth.env.values()), self.root)
                known[f"auth.{e}"] = {"type": ec.auth.type, "env_vars_present": pres}
                for var, ok in pres.items():
                    if not ok:
                        missing.append({"item": "secret", "ask": f"Add {var} to the project .env file (do not paste it in chat)"})
        if not c.api_specs and not c.api_spec_declared_none:
            missing.append({"item": "api_spec", "ask": "Do you have an OpenAPI/Swagger file or URL, Postman collection, GraphQL schema, HAR capture or .proto? (or 'none')"})
        else:
            known["api_specs"] = [s.model_dump() for s in c.api_specs]
        if not c.test_data and not c.test_data_declared_none:
            missing.append({"item": "test_data", "ask": "Any test data files (CSV/JSON: ids, users, search terms, payloads)? (or 'none')"})
        if not c.nfr_files and not c.nfrs_declared_none and not self.reg.nfrs(c.application_id):
            missing.append({"item": "nfr", "ask": "Do approved NFRs/SLOs exist (document/path)? If none, verdicts will be baseline-only — nothing will be invented."})
        if not c.auth_declared:
            missing.append({"item": "auth", "ask": "Do the endpoints need authentication? Type (none/basic/bearer/api_key/oauth2/login flow/cookie) and the ENV VAR NAMES only."})
        if workflow == "feature":
            if not c.repository:
                missing.append({"item": "repository", "ask": "Repository path, branch or PR for the feature"})
        if not c.journeys and not c.journeys_declared_none:
            missing.append({"item": "frontend", "ask": "Frontend URL and important user journeys (e.g. login, search, checkout)? (or 'none')"})
        if environment and environment in c.environments and not c.environments[environment].telemetry:
            missing.append({"item": "telemetry", "ask": "Optional: Prometheus / metrics endpoint for CPU, memory and downstream diagnosis"})
        blocking = {"target", "ownership", "secret"}
        return {"ready": not any(m["item"] in blocking for m in missing), "known": known, "missing": missing,
                "secrets_rule": SECRETS_RULE}

    # ================================================================ NFRs
    def import_nfrs(self, path: str, actor: str = "user") -> list[NFR]:
        c = self.require_cfg()
        data = yaml.safe_load((self.root / path).read_text(encoding="utf-8")) or {}
        out = []
        for n in data.get("nfrs", []):
            nfr = NFR(application_id=c.application_id, **n)
            if self.reg.get(NFRRow, nfr.id):
                self.reg.update(NFRRow, nfr.id, **nfr.model_dump(exclude={"id"}))
            else:
                self.reg.add(NFRRow(**nfr.model_dump()))
            out.append(nfr)
        if path not in c.nfr_files:
            self.update_project(actor, nfr_files=[*c.nfr_files, path])
        self.reg.audit(actor, "nfr:import", path, {"count": len(out)})
        return out

    def _nfrs(self, app: str) -> list[dict]:
        return [{k: getattr(n, k) for k in ("id", "scope", "metric", "comparator", "threshold", "unit")} for n in self.reg.nfrs(app)]

    # ================================================================ discovery
    def discover(self, environment: str, crawl: bool = True) -> dict[str, Any]:
        from perfeng.discovery import specs as ds

        c = self.require_cfg()
        ec = self.env(environment)
        endpoints: dict[str, Endpoint] = {}
        sources: list[str] = []
        for s in c.api_specs:
            if s.kind == "none" or not s.location:
                continue
            try:
                for ep in ds.load_spec(s.kind, s.location, self.root, ec.base_url):
                    endpoints.setdefault(ep.key, ep)
                sources.append(f"{s.kind}:{s.location}")
            except NotImplementedError as e:
                sources.append(f"{s.kind}:{s.location} (skipped: {e})")
        routes: list[str] = []
        if not endpoints:
            spec_url = ds.probe_spec_urls(ec.base_url)
            if spec_url:
                for ep in ds.parse_openapi(ds._load_doc(spec_url, self.root)):
                    endpoints.setdefault(ep.key, ep.model_copy(update={"confidence": 0.9}))
                sources.append(f"openapi(probed):{spec_url}")
        if crawl:
            cr = ds.crawl(ec.frontend_url or ec.base_url)
            routes = cr["routes"]
            for ep in cr["endpoints"]:
                endpoints.setdefault(ep.key, ep)
            sources.append(f"crawl:{cr['pages_fetched']} pages")
        existing = _scan_existing_tests(self.root)
        result = {
            "environment": environment,
            "endpoints": [e.model_dump() for e in endpoints.values()],
            "frontend_routes": routes,
            "existing_tests": existing,
            "nfrs": self._nfrs(c.application_id),
            "sources": sources,
            "discovered_at": datetime.now(UTC).isoformat(),
        }
        d = perf_dir(self.root)
        d.mkdir(exist_ok=True)
        (d / "discovery.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        self.reg.audit("engine", "discover", environment, {"endpoints": len(endpoints), "sources": sources})
        return result

    def _discovery(self) -> dict[str, Any]:
        f = perf_dir(self.root) / "discovery.json"
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {"endpoints": []}

    def _endpoint_index(self) -> dict[str, dict[str, Any]]:
        idx = {f"{e['method']} {e['path']}": e for e in self._discovery().get("endpoints", [])}
        c = self.cfg
        for k, ov in (c.request_overrides if c else {}).items():
            m, p = k.split(" ", 1)
            idx.setdefault(k, {"method": m, "path": p, "source": "manual", "confidence": 1.0})
        return idx

    # ================================================================ planning
    def plan_benchmark(self, environment: str, targets: list[str] | None = None, include_capacity: bool = False,
                       journeys: list[str] | None = None) -> PerformancePlan:
        c = self.require_cfg()
        self.env(environment)
        idx = self._endpoint_index()
        chosen = targets or c.targets or [k for k, e in idx.items() if e["method"] in SAFE_METHODS]
        unknown = [t for t in chosen if t not in idx]
        if unknown:
            raise EngineError(f"targets not discovered or configured: {unknown}. Run discover or add request_overrides.")
        unsafe = [t for t in chosen if idx[t]["method"] not in SAFE_METHODS and t not in c.request_overrides]
        if unsafe:
            raise EngineError(f"non-GET targets need an explicit request override with test data first: {unsafe}")
        tmeta = [{"key": t, "source": idx[t].get("source"), "confidence": idx[t].get("confidence", 1.0)} for t in chosen]
        js = journeys if journeys is not None else [j["name"] for j in c.journeys]
        plan = planner.plan_benchmark(c.application_id, environment, tmeta, self.policy, self._nfrs(c.application_id),
                                      include_capacity=include_capacity, journeys=js)
        return self._save_plan(plan)

    def plan_feature(self, feature_id: str, environment: str, repo: str | None = None, base: str | None = None,
                     head: str | None = None, include_peak: bool = False) -> PerformancePlan:
        from perfeng.context.impact import build_impact

        c = self.require_cfg()
        self.env(environment)
        impact = build_impact(self.root, c, feature_id, self._endpoint_index(), repo=repo, base=base, head=head)
        idx = self._endpoint_index()
        baselines: dict[str, dict[str, Any]] = {}
        for t in impact.endpoints() + impact.journeys():
            bl = self.reg.active_baseline(c.application_id, environment, t)
            if bl:
                m = re.match(r"(\w+)@([\d.]+)rps/(\d+)s", bl.workload_key)
                baselines[t] = {"id": bl.id, "workload_key": bl.workload_key,
                                "rate": float(m.group(2)) if m else None, "duration_s": int(m.group(3)) if m else None}
        tmeta = {k: {"key": k, "source": v.get("source"), "confidence": v.get("confidence", 1.0)} for k, v in idx.items()}
        plan = planner.plan_feature(c.application_id, environment, impact, tmeta, baselines, self.policy,
                                    self._nfrs(c.application_id), include_peak=include_peak)
        return self._save_plan(plan)

    def _save_plan(self, plan: PerformancePlan, parent: str | None = None) -> PerformancePlan:
        content = json.loads(plan.model_dump_json())
        self.reg.add(PlanRow(id=plan.id, application_id=plan.application_id, environment=plan.environment,
                             feature_id=plan.feature_id, workflow=plan.workflow, version=plan.version,
                             parent_plan_id=parent, content=content, content_hash=content_hash(content), status="draft"))
        self.reg.audit("engine", "plan:create", plan.id, {"workflow": plan.workflow, "feature": plan.feature_id})
        return plan

    def get_plan(self, plan_id: str) -> PerformancePlan:
        row = self.reg.get(PlanRow, plan_id)
        if row is None:
            raise EngineError(f"plan {plan_id} not found")
        return PerformancePlan.model_validate(row.content)

    def revise_plan(self, plan_id: str, overrides: dict[str, Any], rationale: str) -> PerformancePlan:
        new = planner.apply_overrides(self.get_plan(plan_id), overrides, rationale)
        new = new.model_copy(update={"id": new_id("plan")})
        self.reg.update(PlanRow, plan_id, status="superseded")
        return self._save_plan(new, parent=plan_id)

    def plan_view(self, plan_id: str) -> dict[str, Any]:
        """Human-readable plan for the approval gate: provenance and assumptions up front."""
        p = self.get_plan(plan_id)
        row = self.reg.get(PlanRow, plan_id)
        by_source: dict[str, list[str]] = {}
        for k, v in p.all_values():
            by_source.setdefault(v.source.value, []).append(f"{k} = {v.value}{(' ' + v.unit) if v.unit else ''} ({v.ref})")
        high_risk = [t.name for t in p.tests if t.type in APPROVAL_REQUIRED_TYPES]
        est = sum(sum(int(s.duration_s.value) for s in t.workload) * int(t.repeats.value) for t in p.tests)
        return {
            "plan_id": p.id, "version": p.version, "status": row.status, "workflow": p.workflow,
            "feature_id": p.feature_id, "environment": p.environment,
            "impact": p.impact.model_dump() if p.impact else None,
            "tests": [{"name": t.name, "type": t.type.value, "targets": t.targets,
                       "workload": [f"{s.rate.value:g} req/s for {s.duration_s.value}s" for s in t.workload],
                       "repeats": t.repeats.value, "rationale": t.rationale} for t in p.tests],
            "measurements": p.measurements,
            "values_by_source": by_source,
            "unconfirmed_assumptions": p.unconfirmed_assumptions(),
            "high_risk_tests_needing_approval": high_risk,
            "baseline_refs": p.baseline_refs,
            "estimated_load_seconds": est,
            "notes": p.notes,
            "plan_hash": row.content_hash,
        }

    # ================================================================ human gates
    def approve_plan(self, plan_id: str, approver: str, confirm_assumptions: bool = False,
                     approve_high_risk: bool = False, reason: str | None = None) -> dict[str, Any]:
        p = self.get_plan(plan_id)
        row = self.reg.get(PlanRow, plan_id)
        if p.unconfirmed_assumptions():
            if not confirm_assumptions:
                raise GateError("plan has unconfirmed ASSUMPTION values (gate G3): " + ", ".join(p.unconfirmed_assumptions()))
            p = planner.confirm_assumptions(p)
            content = json.loads(p.model_dump_json())
            self.reg.update(PlanRow, plan_id, content=content, content_hash=content_hash(content))
            row = self.reg.get(PlanRow, plan_id)
            record_approval(self.reg, GateKind.ASSUMPTIONS, plan_id, row.content_hash, approver, reason)
        high = [t for t in p.tests if t.type in APPROVAL_REQUIRED_TYPES]
        if high and not approve_high_risk:
            raise GateError("plan contains high-risk tests needing explicit approval (gate G4): " + ", ".join(t.name for t in high))
        if high:
            record_approval(self.reg, GateKind.HIGH_RISK_TEST, plan_id, row.content_hash, approver, reason)
        if p.workflow == "diagnostic":
            record_approval(self.reg, GateKind.DIAGNOSTIC_RUN, plan_id, row.content_hash, approver, reason)
        a = record_approval(self.reg, GateKind.PLAN, plan_id, row.content_hash, approver, reason)
        self.reg.update(PlanRow, plan_id, status="approved")
        return {"plan_id": plan_id, "approval_id": a.id, "plan_hash": row.content_hash, "approver": approver}

    def _plan_approval(self, plan_id: str) -> Any:
        row = self.reg.get(PlanRow, plan_id)
        return self.reg.has_approval(plan_id, row.content_hash, GateKind.PLAN.value)

    # ================================================================ compile specs
    def _request_for(self, key: str) -> trs.Request:
        c = self.require_cfg()
        idx = self._endpoint_index()
        ep = idx.get(key)
        if ep is None:
            raise EngineError(f"unknown target {key}")
        ov = c.request_overrides.get(key, {})
        path = ov.get("path") or re.sub(r"\{([A-Za-z0-9_]+)\}", r"${data.\1}", ep["path"])
        body = ov.get("body", (ep.get("request_example") or {}).get("body"))
        return trs.Request(name=key, method=ep["method"], path=path, body=body, headers=ov.get("headers", {}),
                           expect_status=ov.get("expect_status", [200]), graphql=ov.get("graphql"))

    def _auth(self, ec: EnvironmentConfig) -> tuple[trs.AuthConfig, list[trs.SecretRef]]:
        a = ec.auth
        refs = {k: trs.SecretRef(ref=f"env:{v}") for k, v in a.env.items()}
        cfg = trs.AuthConfig(type=a.type, header=a.header, token_url=a.token_url, login_request=a.login_request,
                             **{k: refs[k] for k in ("username", "password", "token", "client_id", "client_secret") if k in refs})
        return cfg, list(refs.values())

    def compile_spec(self, plan: PerformancePlan, test, step_index: int | None, run_id: str) -> trs.TestRunSpec:
        c = self.require_cfg()
        ec = self.env(plan.environment)
        lim = {**self.policy["limits"], **ec.limits}
        auth, refs = self._auth(ec)
        approval = self._plan_approval(plan.id)
        nfr_slo = {m: float(v.value) for m, v in test.thresholds.items() if v.source.value == "NFR"}
        common = dict(
            run_id=run_id, application_id=c.application_id, feature_id=plan.feature_id, plan_id=plan.id,
            repository=c.repository, environment=plan.environment, test_type=test.type.value,
            base_url=ec.base_url, approved_hosts=ec.allowed_hosts, auth=auth, secret_refs=refs,
            limits=trs.Limits(max_duration_s=int(lim["max_duration_s"]), max_vus=int(lim["max_vus"]),
                              max_arrival_rate=float(lim["max_arrival_rate"])),
            thresholds=trs.Thresholds(abort_error_rate=self.policy["safety"]["abort_error_rate"],
                                      abort_p95_ms=self.policy["safety"]["abort_p95_ms"], slo=nfr_slo),
            baseline_ref=plan.baseline_refs.get(test.targets[0]),
            test_data_refs=list(c.test_data),
            telemetry_queries=dict(ec.telemetry),
            approval=trs.Approval(approval_id=approval.id, approver=approval.approver) if approval else None,
            labels={"plan_version": str(plan.version), "test": test.name},
        )
        if test.type == TestType.FRONTEND:
            j = next((j for j in c.journeys if j["name"] == test.targets[0]), {"name": test.targets[0]})
            common["labels"] = {**common["labels"], "lighthouse_runs": str(self.policy["frontend"]["lighthouse_runs"])}
            return trs.TestRunSpec(**{**common, "base_url": ec.frontend_url or ec.base_url}, scenarios=[
                trs.Scenario(name=j["name"], kind="browser", journey=j["name"], journey_spec_path=j.get("spec"),
                             executor="per-vu-iterations", iterations=1, stages=[trs.Stage(rate=1, duration_s=300)],
                             pre_allocated_vus=1, max_vus=1)],
                browser_profiles=[trs.BrowserProfile()])
        steps = test.workload if step_index is None else [test.workload[step_index]]
        rate_max = max(float(s.rate.value) for s in steps)
        vus = int(min(lim["max_vus"], max(10, rate_max * 2)))
        scenario = trs.Scenario(
            name=test.name.split(":", 1)[0] + "_" + re.sub(r"\W", "_", test.targets[0])[:40], kind="api",
            requests=[self._request_for(t) for t in test.targets],
            stages=[trs.Stage(rate=float(s.rate.value), duration_s=int(s.duration_s.value)) for s in steps],
            pre_allocated_vus=min(vus, max(2, int(rate_max))), max_vus=vus,
        )
        return trs.TestRunSpec(**common, scenarios=[scenario])

    # ================================================================ execution
    def execute_plan(self, plan_id: str, only: list[str] | None = None, actor: str = "engine",
                     progress=None) -> dict[str, Any]:
        plan = self.get_plan(plan_id)
        if self._plan_approval(plan_id) is None:
            raise GateError("plan is not approved (gate G2): show the plan to the user and get approval first")
        ec = self.env(plan.environment)
        kind = {"benchmark": "benchmark", "feature": "feature", "diagnostic": "diagnostic"}[plan.workflow]
        results: list[dict[str, Any]] = []
        smoke_failed: set[str] = set()
        for test in plan.tests:
            if only and test.name not in only:
                continue
            if test.targets[0] in smoke_failed:
                results.append({"test": test.name, "skipped": "smoke test failed for this target"})
                continue
            steps = [None] if test.type in (TestType.FRONTEND,) else (
                list(range(len(test.workload))) if test.type == TestType.CAPACITY else [None])
            if test.type == TestType.CAPACITY:
                results.append(self._run_capacity(plan, test, ec, kind, actor, progress))
                continue
            wk = (f"frontend@{test.targets[0]}" if test.type == TestType.FRONTEND else
                  planner.workload_key(test.type.value, float(test.workload[0].rate.value), int(test.workload[0].duration_s.value)))
            series_id = new_id("ser")
            self.reg.add(SeriesRow(id=series_id, application_id=plan.application_id, environment=plan.environment,
                                   target=test.targets[0], workload_key=wk, kind=kind if test.type != TestType.SMOKE else "smoke",
                                   feature_id=plan.feature_id, plan_id=plan.id))
            runs = []
            for i in range(int(test.repeats.value)):
                for si in steps:
                    r = self._execute_one(plan, test, si, series_id, i, ec, kind, actor)
                    runs.append(r)
                    if progress:
                        progress(f"{test.name} repeat {i + 1}/{int(test.repeats.value)}: {r['status']}")
                    if r["status"] in ("aborted", "rejected"):
                        break
                if runs and runs[-1]["status"] in ("aborted", "rejected"):
                    break
            if test.type == TestType.SMOKE:
                bad = [r for r in runs if r["status"] != "completed" or (r.get("error_rate") or 0) > 0.05]
                if bad:
                    smoke_failed.add(test.targets[0])
            results.append({"test": test.name, "series_id": series_id, "runs": runs})
        self.reg.update(PlanRow, plan_id, status="executed")
        analysis = self.analyze_plan(plan_id)
        return {"plan_id": plan_id, "results": results, "analysis": analysis}

    def _execute_one(self, plan, test, step_index, series_id, repeat, ec, kind, actor) -> dict[str, Any]:
        run_id = new_id("run")
        spec = self.compile_spec(plan, test, step_index, run_id)
        gate = validate_spec(spec, ec, self.policy, approval_valid=spec.approval is not None)
        out_dir = perf_dir(self.root) / "artifacts" / run_id
        base = dict(id=run_id, series_id=series_id, plan_id=plan.id, application_id=plan.application_id,
                    environment=plan.environment, feature_id=plan.feature_id, kind=kind, test_type=test.type.value,
                    repeat_index=repeat, spec=json.loads(spec.model_dump_json()), spec_hash=spec.content_hash())
        if not gate.ok:
            self.reg.add(RunRow(**base, status="rejected", error="; ".join(gate.violations)))
            self.reg.audit(actor, "run:rejected", run_id, {"violations": gate.violations})
            return {"run_id": run_id, "status": "rejected", "violations": gate.violations}
        try:
            secret_env = resolve(spec, self.root)
        except MissingSecret as e:
            self.reg.add(RunRow(**base, status="rejected", error=str(e)))
            return {"run_id": run_id, "status": "rejected", "violations": [str(e)]}
        self.reg.add(RunRow(**base, status="running", started_at=datetime.now(UTC)))
        self.reg.audit(actor, "run:start", run_id, {"test": test.name, "executor": self.executor.name})
        from perfeng.telemetry.collector import TelemetryCollector

        tele = TelemetryCollector(ec.telemetry)
        tele.start()
        try:
            res = self.executor.execute(spec, out_dir, self.root, secret_env)
        finally:
            telemetry = tele.stop()
        metrics = res.get("metrics") or {}
        metrics["telemetry"] = telemetry
        self.reg.update(RunRow, run_id, status=res["status"], metrics=metrics, tool_versions=res.get("tool_versions"),
                        artifacts={"dir": str(out_dir.relative_to(self.root)), **(res.get("artifacts") or {})},
                        environment_info=_environment_info(self.executor.name), error=res.get("error"),
                        finished_at=datetime.now(UTC))
        self.reg.audit("engine", f"run:{res['status']}", run_id, {"aborted_reason": metrics.get("aborted_reason")})
        ov = (metrics.get("per_target") or {}).get(test.targets[0]) or {}
        return {"run_id": run_id, "status": res["status"], "p95": ov.get("p95"), "error_rate": ov.get("error_rate"),
                "aborted_reason": metrics.get("aborted_reason"), "error": (res.get("error") or "")[:300] or None}

    def _run_capacity(self, plan, test, ec, kind, actor, progress) -> dict[str, Any]:
        series_id = new_id("ser")
        self.reg.add(SeriesRow(id=series_id, application_id=plan.application_id, environment=plan.environment,
                               target=test.targets[0], workload_key=f"capacity@{test.targets[0]}", kind="capacity",
                               feature_id=plan.feature_id, plan_id=plan.id))
        steps, knee, stop = [], None, None
        s = self.policy["safety"]
        for si, st in enumerate(test.workload):
            r = self._execute_one(plan, test, si, series_id, si, ec, kind, actor)
            r["rate"] = float(st.rate.value)
            steps.append(r)
            if progress:
                progress(f"capacity step {r['rate']:g} req/s: {r['status']} p95={r.get('p95')}")
            row = self.reg.get(RunRow, r["run_id"])
            m = row.metrics or {}
            reasons = []
            if r["status"] != "completed":
                reasons.append(r.get("aborted_reason") or r["status"])
            if (r.get("error_rate") or 0) >= s["abort_error_rate"]:
                reasons.append(f"error rate {r['error_rate']:.2%}")
            if (r.get("p95") or 0) >= s["abort_p95_ms"]:
                reasons.append(f"p95 {r['p95']:.0f}ms")
            if (m.get("dropped_iterations") or 0) > 0:
                reasons.append(f"dropped iterations {m['dropped_iterations']} (target or generator cannot sustain rate)")
            if reasons:
                stop = {"rate": r["rate"], "reasons": reasons}
                break
            knee = r["rate"]
        self.reg.update(SeriesRow, series_id, stability={"capacity": {"last_good_rate": knee, "stopped_at": stop}})
        return {"test": test.name, "series_id": series_id, "steps": steps, "last_good_rate": knee, "stopped_at": stop}

    # ================================================================ analysis
    def analyze_plan(self, plan_id: str) -> dict[str, Any]:
        plan = self.get_plan(plan_id)
        series = self.reg.query(select(SeriesRow).where(SeriesRow.plan_id == plan_id))
        nfrs = self._nfrs(plan.application_id)
        verdicts = []
        policy = json.loads(json.dumps(self.policy))
        policy["regression"]["warn_pct"] = float(plan.regression_warn_pct.value)
        policy["regression"]["fail_pct"] = float(plan.regression_fail_pct.value)
        characterization = []
        for s in series:
            if s.kind in ("smoke", "capacity"):
                continue
            runs = [r for r in self.reg.runs_in_series(s.id) if r.status in ("completed", "aborted")]
            metrics = [r.metrics or {} for r in runs]
            target = s.target
            if s.workload_key.startswith("frontend@"):
                from perfeng.analysis.frontend import frontend_verdict

                fv = frontend_verdict(self, plan, s, runs, nfrs, policy)
                verdicts.append(fv)
                if s.kind == "benchmark":
                    characterization.append({"target": target, "workload": s.workload_key, "frontend": True,
                                             "stable": fv["stability"]["stable"], **fv["current"]})
                continue
            st = evaluate_stability(metrics, target, policy, int(policy["benchmark"]["repeats"]))
            self.reg.update(SeriesRow, s.id, stability=st.to_dict())
            cur = series_samples(metrics, target)
            bl = None
            if s.kind != "benchmark":
                bl = self.reg.active_baseline(plan.application_id, plan.environment, target, s.workload_key)
            trend = self._trend(plan.application_id, plan.environment, target, s.workload_key)
            v = decide(target, cur, bl.samples if bl else None, nfrs, st, policy, bl.id if bl else None, trend)
            d = v.to_dict()
            d.update({"series_id": s.id, "workload_key": s.workload_key, "kind": s.kind,
                      "promotion_eligible": s.kind == "benchmark" and st.stable})
            if bl is None and s.kind != "benchmark":
                d["reasons"].append("no approved baseline for this workload")
            verdicts.append(d)
            if s.kind == "benchmark":
                characterization.append({"target": target, "workload": s.workload_key, "stable": st.stable,
                                         "cv_p95": st.cv_p95, **{k: d["current"].get(k) for k in ("p50", "p90", "p95", "p99", "error_rate", "rps")}})
        ov = overall_from_dicts([v for v in verdicts if v.get("kind") != "benchmark"]) if plan.workflow != "benchmark" else None
        result = {"plan_id": plan_id, "workflow": plan.workflow, "feature_id": plan.feature_id,
                  "overall_verdict": ov, "targets": verdicts, "characterization": characterization,
                  "notes": plan.notes, "analyzed_at": datetime.now(UTC).isoformat()}
        aid = new_id("ana")
        self.reg.add(AnalysisRow(id=aid, subject_id=plan_id, content=json.loads(json.dumps(result, default=str)),
                                 verdict=ov or "CHARACTERIZED"))
        result["analysis_id"] = aid
        return result

    def _trend(self, app, env, target, workload_key) -> dict[str, Any]:
        hist = [s for s in reversed(self.reg.series_history(app, env, target, 20)) if s.workload_key == workload_key]
        meds = []
        for s in hist:
            runs = [r for r in self.reg.runs_in_series(s.id) if r.status == "completed"]
            ps = series_samples([r.metrics or {} for r in runs], target).get("p95", [])
            if ps:
                meds.append(sorted(ps)[len(ps) // 2])
        t = stats.linear_trend(meds)
        t["recent_p95_medians"] = meds[-10:]
        return t

    def latest_analysis(self, subject_id: str) -> dict[str, Any] | None:
        rows = self.reg.query(select(AnalysisRow).where(AnalysisRow.subject_id == subject_id).order_by(AnalysisRow.created_at.desc()))
        return rows[0].content if rows else None

    # ================================================================ baselines
    def baseline_candidates(self, plan_id: str | None = None) -> list[dict[str, Any]]:
        stmt = select(SeriesRow).where(SeriesRow.kind == "benchmark")
        if plan_id:
            stmt = stmt.where(SeriesRow.plan_id == plan_id)
        return [promotion_check(self.reg, s.id, self.policy) for s in self.reg.query(stmt)]

    def approve_baseline(self, series_id: str, approver: str, reason: str) -> dict[str, Any]:
        chk = promotion_check(self.reg, series_id, self.policy)
        if not chk["eligible"]:
            raise GateError("not eligible: " + "; ".join(chk["reasons"]))
        record_approval(self.reg, GateKind.BASELINE_PROMOTION, series_id, chk["series_hash"], approver, reason)
        bl = promote_baseline(self.reg, series_id, self.policy, approver, reason)
        return {"baseline_id": bl.id, "target": bl.target, "workload_key": bl.workload_key, "version": bl.version,
                "summary": bl.summary}

    def list_baselines(self) -> list[dict[str, Any]]:
        c = self.require_cfg()
        return [{k: v for k, v in row_to_dict(b).items() if k != "samples"} for b in self.reg.baselines(c.application_id)]

    # ================================================================ status / compare / history
    def status(self, ident: str) -> dict[str, Any]:
        for cls in (RunRow, SeriesRow, PlanRow, BaselineRow, AnalysisRow):
            row = self.reg.get(cls, ident)
            if row is not None:
                d = row_to_dict(row)
                if cls is RunRow:
                    d.pop("spec", None)
                if cls is SeriesRow:
                    d["runs"] = [{"id": r.id, "status": r.status, "p95": ((r.metrics or {}).get("per_target") or {}).get(row.target, {}).get("p95")}
                                 for r in self.reg.runs_in_series(ident)]
                if cls is PlanRow:
                    d["analysis"] = self.latest_analysis(ident)
                    d.pop("content", None)
                    d["view"] = self.plan_view(ident)
                return {"type": cls.__tablename__, **d}
        raise EngineError(f"no run/series/plan/baseline/analysis with id {ident}")

    def compare(self, a: str, b: str) -> dict[str, Any]:
        sa, sb = self._samples_for(a), self._samples_for(b)
        targets = sorted(set(sa) & set(sb))
        out = {}
        for t in targets:
            row = {}
            for k in ("p50", "p90", "p95", "p99", "error_rate", "rps"):
                va, vb = sa[t].get(k), sb[t].get(k)
                ma = sorted(va)[len(va) // 2] if va else None
                mb = sorted(vb)[len(vb) // 2] if vb else None
                row[k] = {"a": ma, "b": mb, "delta_pct": stats.pct_delta(mb, ma)}
            row["p95_p_value_b_gt_a"] = stats.mann_whitney_greater(sb[t].get("p95", []), sa[t].get("p95", []))
            out[t] = row
        return {"a": a, "b": b, "targets": out, "only_in_a": sorted(set(sa) - set(sb)), "only_in_b": sorted(set(sb) - set(sa))}

    def _samples_for(self, ident: str) -> dict[str, dict[str, list[float]]]:
        run = self.reg.get(RunRow, ident)
        if run:
            m = run.metrics or {}
            return {t: series_samples([m], t) for t in (m.get("per_target") or {})}
        s = self.reg.get(SeriesRow, ident)
        if s:
            return {s.target: series_samples([r.metrics or {} for r in self.reg.runs_in_series(ident) if r.status == "completed"], s.target)}
        bl = self.reg.get(BaselineRow, ident)
        if bl:
            return {bl.target: bl.samples}
        raise EngineError(f"{ident} is not a run, series or baseline id")

    def history(self, target: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        c = self.require_cfg()
        stmt = select(SeriesRow).where(SeriesRow.application_id == c.application_id)
        if target:
            stmt = stmt.where(SeriesRow.target == target)
        out = []
        for s in self.reg.query(stmt.order_by(SeriesRow.created_at.desc()).limit(limit)):
            runs = [r for r in self.reg.runs_in_series(s.id)]
            ps = series_samples([r.metrics or {} for r in runs if r.status == "completed"], s.target).get("p95", [])
            out.append({"series_id": s.id, "target": s.target, "workload": s.workload_key, "kind": s.kind,
                        "feature_id": s.feature_id, "runs": len(runs), "p95_median": sorted(ps)[len(ps) // 2] if ps else None,
                        "created_at": s.created_at.isoformat()})
        return out


# ---------------------------------------------------------------- helpers

SECRETS_RULE = ("Never paste passwords, tokens or API keys in chat. Put them in the project .env file "
                "(it is gitignored) and tell me only the variable name, e.g. PERF_API_TOKEN.")


def _optional_asks(workflow: str) -> list[dict[str, str]]:
    asks = [
        {"item": "api_spec", "ask": "Do you have an OpenAPI/Swagger file or URL, Postman collection, GraphQL schema, HAR capture or .proto? (or 'none')"},
        {"item": "test_data", "ask": "Any test data files (CSV/JSON)? (or 'none')"},
        {"item": "auth", "ask": "Authentication type and ENV VAR NAMES only (values go in .env)"},
        {"item": "nfr", "ask": "Do approved NFRs/SLOs exist? (or 'none')"},
        {"item": "frontend", "ask": "Frontend URL and key user journeys? (or 'none')"},
    ]
    if workflow == "feature":
        asks.append({"item": "repository", "ask": "Repository path and branch/PR for the feature"})
    return asks


def overall_from_dicts(vs: list[dict[str, Any]]) -> str | None:
    if not vs:
        return None
    order = [Verdict.PASS, Verdict.PASS_WITH_REGRESSION_WARNING, Verdict.INCONCLUSIVE, Verdict.FAIL_REGRESSION, Verdict.FAIL_SLO]
    return max((Verdict(v["verdict"]) for v in vs), key=order.index).value


def _db_url(root: Path) -> str:
    import os

    if os.environ.get("PERF_DB_URL"):
        return os.environ["PERF_DB_URL"]
    d = root / ".perf" / "state"
    d.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{(d / 'registry.db').as_posix()}"


def _ensure_env_hygiene(root: Path) -> None:
    gi = root / ".gitignore"
    lines = gi.read_text(encoding="utf-8").splitlines() if gi.exists() else []
    want = [".env", ".env.*", "!.env.example", ".perf/state/", ".perf/artifacts/"]
    add = [w for w in want if w not in lines]
    if add:
        gi.write_text("\n".join(lines + ["# perf plugin"] + add) + "\n", encoding="utf-8")
    ex = root / ".env.example"
    if not ex.exists():
        ex.write_text("# Copy to .env and fill in. Never commit .env or paste secrets in chat.\n"
                      "# PERF_API_TOKEN=\n# PERF_USERNAME=\n# PERF_PASSWORD=\n", encoding="utf-8")


def _environment_info(executor: str) -> dict[str, Any]:
    import platform

    import psutil

    return {"executor": executor, "os": platform.platform(), "python": platform.python_version(),
            "cpu_count": psutil.cpu_count(), "mem_gb": round(psutil.virtual_memory().total / 1e9, 1),
            "label": "local-lab" if executor == "local-process" else executor}


def _scan_existing_tests(root: Path) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {"k6": [], "playwright": [], "lighthouse": []}
    skip = {"node_modules", ".git", ".venv", ".perf"}
    for p in root.rglob("*"):
        if any(part in skip for part in p.parts) or not p.is_file():
            continue
        n = p.name
        if n.endswith((".js", ".ts")) and p.stat().st_size < 500_000:
            try:
                head = p.read_text(encoding="utf-8", errors="ignore")[:4000]
            except OSError:
                continue
            if "from 'k6" in head or 'from "k6' in head:
                out["k6"].append(str(p.relative_to(root)))
            elif "@playwright/test" in head:
                out["playwright"].append(str(p.relative_to(root)))
        if n in ("lighthouserc.json", "lighthouserc.js", ".lighthouserc.json"):
            out["lighthouse"].append(str(p.relative_to(root)))
        if sum(len(v) for v in out.values()) > 200:
            break
    return out
