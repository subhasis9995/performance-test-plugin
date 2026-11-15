"""Engine integration: real k6 against an in-process SUT. Proves the deterministic workflow end to end:
intake -> discover -> plan (provenance) -> human gate -> run -> analyze -> baseline promotion ->
feature test (no fault = PASS, fault = regression) -> baseline protection."""

from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path

import pytest
import yaml

from perfeng.config import load_policy
from perfeng.executors.base import LocalProcessExecutor
from perfeng.orchestrator.engine import Engine, EngineError
from perfeng.registry.baselines import GateError
from perfeng.toolchain.installer import k6_path

from mini_sut import OPENAPI, SUT

pytestmark = pytest.mark.skipif(not k6_path().exists(), reason="k6 not installed (run `perf setup`)")
SEARCH = "GET /rest/search"


def fast_policy():
    p = copy.deepcopy(load_policy())
    p["benchmark"].update(repeats=3, characterization_rates=[20], step_duration_s=4, smoke_duration_s=2, smoke_rate=2,
                          capacity_ladder=[10, 20, 40, 80], capacity_step_duration_s=3)
    p["feature"].update(repeats=3, load_rate=20, load_duration_s=4)
    p["stability"].update(min_samples_per_run=40, max_cv_p95=0.35)
    return p


@pytest.fixture
def sut():
    with SUT() as s:
        yield s


@pytest.fixture
def project(tmp_path, sut):
    (tmp_path / "openapi.json").write_text(json.dumps(OPENAPI))
    eng = Engine(tmp_path, policy=fast_policy(), executor=LocalProcessExecutor())
    eng.init_project("mini", "perf-qa", sut.url, ownership_confirmed=True)
    eng.update_project(api_specs=[{"kind": "openapi", "location": "openapi.json"}], nfrs_declared_none=True,
                       auth_declared=True, test_data_declared_none=True, journeys_declared_none=True,
                       request_overrides={"GET /api/items/{id}": {"path": "/api/items/7"}})
    return eng


def test_intake_reports_gaps_and_secret_rule(tmp_path):
    eng = Engine(tmp_path, policy=fast_policy())
    st = eng.intake_status()
    assert not st["ready"] and st["missing"][0]["item"] == "target"
    assert ".env" in st["secrets_rule"] and "Never paste" in st["secrets_rule"]
    items = {m["item"] for m in st["missing"]}
    assert {"api_spec", "test_data", "auth", "nfr"} <= items


def test_init_writes_env_hygiene(project):
    gi = (project.root / ".gitignore").read_text()
    assert ".env" in gi and (project.root / ".env.example").exists()


def test_discovery_from_openapi_and_crawl(project):
    d = project.discover("perf-qa")
    keys = {f"{e['method']} {e['path']}" for e in d["endpoints"]}
    assert {"GET /api/items", "GET /api/items/{id}", SEARCH, "POST /login"} <= keys
    assert d["nfrs"] == []
    assert any(s.startswith("openapi:") for s in d["sources"])


def test_plan_provenance_never_invents_nfr(project):
    project.discover("perf-qa")
    plan = project.plan_benchmark("perf-qa", targets=[SEARCH])
    view = project.plan_view(plan.id)
    assert "NFR" not in view["values_by_source"]
    assert "POLICY" in view["values_by_source"]
    assert any("No approved NFRs" in n for n in view["notes"])
    with pytest.raises(EngineError):
        project.plan_benchmark("perf-qa", targets=["POST /login"])  # unsafe without override + data


def test_unapproved_plan_cannot_run(project):
    project.discover("perf-qa")
    plan = project.plan_benchmark("perf-qa", targets=[SEARCH])
    with pytest.raises(GateError, match="G2"):
        project.execute_plan(plan.id)
    with pytest.raises(GateError, match="human"):
        project.approve_plan(plan.id, approver="claude")


def test_capacity_requires_high_risk_approval_and_stops_on_breach(project, sut):
    project.discover("perf-qa")
    plan = project.plan_benchmark("perf-qa", targets=[SEARCH], include_capacity=True)
    with pytest.raises(GateError, match="G4"):
        project.approve_plan(plan.id, approver="dana")
    project.approve_plan(plan.id, approver="dana", approve_high_risk=True)
    sut.faults["/rest/search"] = {"error_rate": 0.5}
    res = project.execute_plan(plan.id, only=[f"capacity:{SEARCH}"])
    cap = res["results"][0]
    assert cap["stopped_at"] is not None, cap
    assert len(cap["steps"]) < 4  # ladder aborted early
    for st in cap["steps"]:
        assert st["rate"] <= project.policy["limits"]["max_arrival_rate"]


def _git(repo: Path, *a):
    subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)


def _feature_repo(root: Path) -> Path:
    repo = root / "app-repo"
    (repo / "routes").mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "dev@example.test")
    _git(repo, "config", "user.name", "dev")
    (repo / "routes" / "search.ts").write_text("export const r = '/rest/search';\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "initial")
    (repo / "routes" / "search.ts").write_text("export const r = '/rest/search'; // KEY-22 faster ranking\nconst x = 1;\n")
    _git(repo, "commit", "-qam", "KEY-22: change search ranking")
    return repo


def test_full_benchmark_baseline_feature_cycle(project, sut):
    project.discover("perf-qa")
    # --- benchmark (characterization) + baseline promotion
    plan = project.plan_benchmark("perf-qa", targets=[SEARCH])
    project.approve_plan(plan.id, approver="dana")
    res = project.execute_plan(plan.id)
    ch = res["analysis"]["characterization"]
    assert ch and ch[0]["p95"] is not None and ch[0]["stable"], ch
    run = project.reg.get(__import__("perfeng.registry.db", fromlist=["RunRow"]).RunRow,
                          res["results"][1]["runs"][0]["run_id"])
    assert run.tool_versions["k6"].startswith("k6") and run.environment_info["label"] == "local-lab"
    assert (project.root / run.artifacts["dir"] / "raw.ndjson").exists()
    cands = [c for c in project.baseline_candidates(plan.id) if c["eligible"]]
    assert cands, project.baseline_candidates(plan.id)
    bl = project.approve_baseline(cands[0]["series_id"], approver="dana", reason="stable lab baseline")
    assert bl["version"] == 1

    # --- feature test, no fault -> PASS (negative control)
    repo = _feature_repo(project.root)
    project.update_project(repository=str(repo))
    (project.root / ".perf").mkdir(exist_ok=True)
    (project.root / ".perf" / "service-map.yaml").write_text(yaml.safe_dump({"mappings": [
        {"name": "search", "paths": ["routes/search.ts"], "endpoints": [SEARCH], "services": ["SearchService"]}]}))
    fp = project.plan_feature("KEY-22", "perf-qa")
    assert SEARCH in fp.impact.endpoints()
    assert fp.baseline_refs[SEARCH] == bl["baseline_id"]
    load = next(t for t in fp.tests if t.type.value == "load")
    assert load.workload[0].rate.source.value == "BASELINE"  # like-for-like workload
    project.approve_plan(fp.id, approver="dana")
    ok = project.execute_plan(fp.id)
    assert ok["analysis"]["overall_verdict"] == "PASS", json.dumps(ok["analysis"]["targets"], indent=1, default=str)[:3000]

    # --- feature test with injected latency -> regression
    sut.faults["/rest/search"] = {"delay_ms": 40}
    fp2 = project.plan_feature("KEY-22", "perf-qa")
    project.approve_plan(fp2.id, approver="dana")
    bad = project.execute_plan(fp2.id)
    v = bad["analysis"]["overall_verdict"]
    assert v in ("FAIL_REGRESSION", "PASS_WITH_REGRESSION_WARNING"), v
    t = next(x for x in bad["analysis"]["targets"] if x["target"] == SEARCH and x["kind"] == "feature")
    p95 = next(r for r in t["regression"] if r["metric"] == "p95")
    assert p95["delta_pct"] > 100

    # --- baseline protection: feature runs never moved the baseline
    bls = project.list_baselines()
    assert len(bls) == 1 and bls[0]["id"] == bl["baseline_id"] and bls[0]["status"] == "active"
    # --- compare / status / history
    cmp_ = project.compare(bl["baseline_id"], t["series_id"])
    assert cmp_["targets"][SEARCH]["p95"]["delta_pct"] > 100
    assert project.status(fp2.id)["analysis"]["overall_verdict"] == v
    assert len(project.history(SEARCH)) >= 3

    # --- professional report (Markdown + HTML) built from the registry
    from perfeng.report.full import build_report

    md = build_report(project, fp2.id)
    for h in ("# Performance Test Report", "## 1. Executive summary", "## 3. Test inputs", "### 3.1 Target and access",
              "## 4. Test environment and tooling", "## 5. Test plan and execution", "## 6. Results: API",
              "## 9. Success and failure analysis", "## 10. SLO, baseline and trend analysis", "## 13. Observations and recommendations",
              "## 14. Assumptions", "## Appendix"):
        assert h in md, h
    assert "FAIL_REGRESSION" in md or "PASS_WITH_REGRESSION_WARNING" in md
    assert "k6.exe v2" in md or "k6 v2" in md or "k6" in md
    assert "approved baseline" in md or "baseline" in md
    assert "Approvals (human gates)" in md and "G2_plan" in md
    html = build_report(project, fp2.id, html=True)
    assert html.startswith("<!doctype html>") and "<svg" in html and "<table>" in html

    # --- manual-run export: scripts regenerated with relative data paths, secrets as names only
    from perfeng.report.export import export_plan

    out = project.root / "bundle"
    r = export_plan(project, fp2.id, out)
    assert (out / "README.md").exists() and r["scripts"]
    sc = next(out.glob("k6/*load*/script.js"))
    text = sc.read_text(encoding="utf-8")
    assert str(project.root) not in text, "absolute paths must not leak into exported scripts"
    assert "bypass" in (out / "README.md").read_text(encoding="utf-8").lower()
    env_lines = [ln for ln in (out / ".env.example").read_text(encoding="utf-8").splitlines() if ln and not ln.startswith("#")]
    assert all(ln.endswith("=") for ln in env_lines), "exported .env.example must contain variable names only, never values"
    # the exported script really runs by hand with k6 from the bundle folder
    sut.faults.clear()  # mini SUT: remove injected latency
    p = subprocess.run([str(k6_path()), "run", "--quiet", "--summary-export", "summary.json", str(sc.relative_to(out))],
                       cwd=out, capture_output=True, text=True, timeout=120)
    assert p.returncode in (0, 99), p.stderr[-500:]
    assert (out / "summary.json").exists()
