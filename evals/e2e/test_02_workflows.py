"""Evals 2-17 — the real workflows against the natively running Juice Shop (SUT) through the perf CLI
(the same engine the plugin's MCP tools call). Tests run in order and share one project state.

Scenarios: setup/doctor, discovery, provenance (no invented NFRs), gates, characterization (repeats, metrics,
artifacts, tool versions), baseline promotion rules, capacity ladder safety stop, policy rejections, frontend
benchmark (Web Vitals, trace, HAR, Lighthouse), feature KEY-22 negative control, feature regression,
diagnostic loop (v2) incl. insufficient-evidence path, baseline protection, compare/status/history, secrets.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest
import yaml

from conftest import PRODUCT, SEARCH, perf_cli, perf_json

S: dict = {}  # shared state between ordered tests


def _db(project: Path):
    return sqlite3.connect(project / ".perf" / "state" / "registry.db")


def _approve(project, plan_id, *extra):
    # the eval harness acts as the human approver (Claude's own Bash is denied this command by the hook)
    return perf_cli(project, "approve", plan_id, "--approver", "eval-human", "--yes", *extra)


def test_02_setup_doctor_no_docker(project, evidence):
    rows = perf_json(project, "doctor")
    by = {r["name"]: r for r in rows}
    assert by["k6"]["ok"] and "v2.3.0" in by["k6"]["version"]
    assert by["playwright"]["ok"] and by["chromium"]["ok"] and by["lighthouse"]["ok"]
    evidence.record("E2.doctor", docker=by["docker (optional)"]["version"], k6=by["k6"]["version"])


def test_03_discovery_minimal_inputs(project, evidence):
    d = perf_json(project, "discover", "--env", "perf-qa")
    keys = {f"{e['method']} {e['path']}" for e in d["endpoints"]}
    assert len(keys) >= 10, keys  # Juice Shop bundle references many /api and /rest routes
    assert any(e["source"] == "crawl" and e["confidence"] < 0.6 for e in d["endpoints"])  # inferred = low confidence
    assert d["nfrs"] == []
    assert "/" in d["frontend_routes"]
    evidence.record("E3.discovery", endpoints=len(keys), sources=d["sources"])


def test_04_plan_provenance_no_invented_nfr(project, evidence):
    v = perf_json(project, "plan", "--benchmark", "--env", "perf-qa")
    S["bench_plan"] = v["plan_id"]
    assert "NFR" not in v["values_by_source"]
    assert v["values_by_source"]["POLICY"]
    assert any("No approved NFRs" in n for n in v["notes"])
    types = {t["type"] for t in v["tests"]}
    assert {"smoke", "load", "frontend"} <= types and "capacity" not in types
    evidence.record("E4.provenance", sources={k: len(x) for k, x in v["values_by_source"].items()})


def test_05_gate_unapproved_plan_does_not_run(project):
    p = perf_cli(project, "run", S["bench_plan"], check=False)
    assert p.returncode != 0 and "G2" in p.stderr
    p2 = perf_cli(project, "approve", S["bench_plan"], "--approver", "claude", "--yes", check=False)
    assert p2.returncode != 0 and "human" in p2.stderr
    with _db(project) as c:
        assert c.execute("select count(*) from runs where plan_id=?", (S["bench_plan"],)).fetchone()[0] == 0


def test_06_characterization_benchmark(project, evidence):
    _approve(project, S["bench_plan"])
    res = perf_json(project, "run", S["bench_plan"], timeout=2400)
    ch = {c["target"]: c for c in res["analysis"]["characterization"]}
    for t in (SEARCH, PRODUCT):
        c = ch[t]
        for k in ("p50", "p90", "p95", "p99", "error_rate", "rps"):
            assert c[k] is not None, (t, k)
        assert c["cv_p95"] is not None
    with _db(project) as c:
        rows = c.execute("select metrics, tool_versions, artifacts, environment_info from runs where plan_id=? and test_type='load'",
                         (S["bench_plan"],)).fetchall()
    assert len(rows) >= 2 * 3  # targets x repeats (fast policy: 3)
    m, tv, art, ei = (json.loads(x) for x in rows[0])
    for k in ("dropped_iterations", "data_sent_bytes", "data_received_bytes", "http_reqs", "iterations", "generator", "telemetry"):
        assert k in m, k
    assert m["telemetry"].get("proxy"), "server-side telemetry scraped"
    assert tv["k6"].startswith("k6") and ei["label"] == "local-lab"
    assert (project / art["dir"] / "raw.ndjson").exists() and (project / art["dir"] / "script.js").exists()
    S["bench_analysis"] = res["analysis"]
    evidence.record("E6.characterization", **{t: {k: ch[t][k] for k in ("p95", "cv_p95", "stable")} for t in ch if not ch[t].get("frontend")})


def test_07_frontend_benchmark_web_vitals(project, evidence):
    fe = {c["target"]: c for c in S["bench_analysis"]["characterization"] if c.get("frontend")}
    assert {"Search", "ProductDetails"} <= set(fe)
    s = fe["Search"]
    for k in ("lcp_ms", "cls", "tbt_ms", "ttfb_ms", "fcp_ms", "speed_index_ms", "journey_ms", "resource_count",
              "long_tasks", "js_exec_ms", "failed_requests", "console_errors", "lh_performance_score"):
        assert s.get(k) is not None, k
    assert "fid" not in json.dumps(fe).lower()
    with _db(project) as c:
        art = json.loads(c.execute("select artifacts from runs where plan_id=? and test_type='frontend' and status='completed' limit 1",
                                   (S["bench_plan"],)).fetchone()[0])
    d = project / art["dir"]
    for f in ("trace.zip", "network.har", "final.png", "journey-result.json"):
        assert (d / f).exists(), f
    assert list(d.glob("lighthouse-*.json"))
    vers = json.loads((d / "journey-result.json").read_text())["versions"]
    assert vers["playwright"] and vers["lighthouse"] and vers["chromium"]
    evidence.record("E7.frontend", **{k: s[k] for k in ("lcp_ms", "cls", "inp_ms", "tbt_ms", "speed_index_ms")})


def test_08_baseline_promotion_rules(project, evidence):
    cands = perf_json(project, "baseline", "candidates", "--plan-id", S["bench_plan"])
    elig = {c["target"]: c for c in cands if c["eligible"]}
    assert SEARCH in elig and "Search" in elig, cands
    # a smoke series (single run) is never a candidate; a promotion without approval is impossible via engine API
    with _db(project) as c:
        smoke = c.execute("select id from series where plan_id=? and kind='smoke' limit 1", (S["bench_plan"],)).fetchone()[0]
    p = perf_cli(project, "baseline", "promote", smoke, "--approver", "eval-human", "--reason", "x", "--yes", check=False)
    assert p.returncode != 0 and ("not eligible" in p.stderr or "not found" in p.stderr or "cannot be promoted" in p.stderr)
    for t in (SEARCH, "Search"):
        r = json.loads(perf_cli(project, "baseline", "promote", elig[t]["series_id"], "--approver", "eval-human",
                                "--reason", "E8", "--yes").stdout)
        assert r["version"] == 1
        S[f"bl:{t}"] = r["baseline_id"]
    evidence.record("E8.promotion", baselines=[S[f"bl:{SEARCH}"], S["bl:Search"]])


def test_09_unstable_series_refused(project, faults, evidence):
    """A noisy environment (latency toggling on/off at random intervals, like a noisy neighbour) must make the
    repeated series unstable, and an unstable series must not be promotable."""
    import random
    import threading
    import time as _t

    stop = threading.Event()

    def noisy():
        rnd = random.Random(42)
        while not stop.is_set():
            faults.set("search-latency-200ms") if rnd.random() < 0.5 else faults.clear()
            stop.wait(rnd.uniform(1.0, 6.0))

    v = perf_json(project, "plan", "--benchmark", "--env", "perf-qa", "--target", SEARCH)
    _approve(project, v["plan_id"])
    th = threading.Thread(target=noisy, daemon=True)
    th.start()
    try:
        perf_cli(project, "run", v["plan_id"], "--only", f"load:{SEARCH}@10", timeout=1200)
    finally:
        stop.set()
        th.join()
        faults.clear()
    cands = [c for c in perf_json(project, "baseline", "candidates", "--plan-id", v["plan_id"]) if c["target"] == SEARCH]
    assert cands and not cands[0]["eligible"], cands
    assert any("CV" in r for r in cands[0]["reasons"]), cands[0]["reasons"]
    evidence.record("E9.unstable_refused", reasons=cands[0]["reasons"], cv=cands[0]["stability"].get("cv_p95"))


def test_10_capacity_ladder_safety_stop(project, faults, evidence):
    v = perf_json(project, "plan", "--benchmark", "--env", "perf-qa", "--target", SEARCH, "--capacity")
    assert v["high_risk_tests_needing_approval"]
    p = perf_cli(project, "approve", v["plan_id"], "--approver", "eval-human", "--yes", check=False)
    assert p.returncode != 0 and "G4" in p.stderr
    _approve(project, v["plan_id"], "--approve-high-risk")
    faults.set("errors-10pct")
    # tighten abort so a 10% error rate breaches at the first steps (policy can only be lowered per environment)
    pol = project / ".perf" / "policy.yaml"
    d = yaml.safe_load(pol.read_text())
    d["safety"] = {"abort_error_rate": 0.05}
    pol.write_text(yaml.safe_dump(d))
    try:
        res = perf_json(project, "run", v["plan_id"], "--only", f"capacity:{SEARCH}", timeout=1200)
    finally:
        d.pop("safety")
        pol.write_text(yaml.safe_dump(d))
        faults.clear()
    cap = res["results"][0]
    assert cap["stopped_at"] is not None, cap
    ladder = [10, 25, 50, 100]
    assert len(cap["steps"]) < len(ladder)
    with _db(project) as c:
        specs = [json.loads(x[0]) for x in c.execute("select spec from runs where series_id=?", (cap["series_id"],))]
    assert all(s["scenarios"][0]["stages"][0]["rate"] <= s["limits"]["max_arrival_rate"] <= 100 for s in specs)
    evidence.record("E10.capacity_stop", steps=len(cap["steps"]), stopped_at=cap["stopped_at"])


def test_11_policy_gate_rejections(project, evidence):
    from perfeng.config import load_project
    from perfeng.policy.gate import validate_spec
    from perfeng.spec import testrunspec_v1 as trs

    cfg = load_project(project)
    env = cfg.environments["perf-qa"]
    from perfeng.config import load_policy

    pol = load_policy()
    base = dict(run_id="r", application_id="juice-shop", environment="perf-qa", test_type="load", base_url=env.base_url,
                approved_hosts=["127.0.0.1"], limits=trs.Limits(max_duration_s=60, max_vus=10, max_arrival_rate=10),
                scenarios=[trs.Scenario(name="s", kind="api", requests=[trs.Request(name="x", method="GET", path="/")],
                                        stages=[trs.Stage(rate=5, duration_s=5)])])
    cases = {
        "non_allowlisted": trs.TestRunSpec(**{**base, "base_url": "https://www.example.com"}),
        "over_cap": trs.TestRunSpec(**{**base, "scenarios": [base["scenarios"][0].model_copy(update={"stages": [trs.Stage(rate=5000, duration_s=5)]})]}),
        "stress_unapproved": trs.TestRunSpec(**{**base, "test_type": "stress"}),
    }
    out = {}
    for name, spec in cases.items():
        r = validate_spec(spec, env, pol, approval_valid=(name == "stress_unapproved"))
        out[name] = r.violations
        assert not r.ok, name
    with pytest.raises(Exception):
        trs.SecretRef(ref="Sup3rSecret")
    evidence.record("E11.policy", **{k: v[:2] for k, v in out.items()})


def test_12_feature_impact_and_negative_control(project, evidence):
    v = perf_json(project, "plan", "--feature", "KEY-22", "--env", "perf-qa")
    im = v["impact"]
    eps = [i["name"] for i in im["backend"] if i["kind"] == "endpoint"]
    js = [i["name"] for i in im["frontend"] if i["kind"] == "journey"]
    assert SEARCH in eps and "Search" in js and PRODUCT not in eps
    assert any("routes/search.ts" in f for f in im["changed_files"])
    assert v["baseline_refs"][SEARCH] == S[f"bl:{SEARCH}"]
    assert v["values_by_source"].get("BASELINE")  # workload reused from the approved baseline
    _approve(project, v["plan_id"])
    res = perf_json(project, "run", v["plan_id"], timeout=1800)
    verdicts = {t["target"]: t["verdict"] for t in res["analysis"]["targets"]}
    assert verdicts[SEARCH] == "PASS", res["analysis"]["targets"]
    assert res["analysis"]["overall_verdict"] in ("PASS",), verdicts
    S["feature_ok"] = v["plan_id"]
    evidence.record("E12.negative_control", verdicts=verdicts, impact_endpoints=eps, impact_journeys=js)


def test_13_feature_regression_detected(project, faults, evidence):
    faults.set("search-latency-200ms")
    try:
        v = perf_json(project, "plan", "--feature", "KEY-22", "--env", "perf-qa")
        _approve(project, v["plan_id"])
        res = perf_json(project, "run", v["plan_id"], timeout=1800)
    finally:
        faults.clear()
    t = next(x for x in res["analysis"]["targets"] if x["target"] == SEARCH)
    assert t["verdict"] == "FAIL_REGRESSION", t
    p95 = next(r for r in t["regression"] if r["metric"] == "p95")
    assert p95["delta_pct"] > 300 and p95["current"] - p95["baseline"] > 150
    S["feature_bad"] = v["plan_id"]
    S["feature_bad_series"] = t["series_id"]
    evidence.record("E13.regression", verdict=t["verdict"], p95=p95)


def test_14_slo_vs_baseline_kept_separate(project, faults, evidence):
    """SLO PASS + material baseline regression => PASS_WITH_REGRESSION_WARNING (spec example semantics)."""
    nf = project / "nfrs.yaml"
    nf.write_text(yaml.safe_dump({"nfrs": [{"id": "NFR-SEARCH-P95", "scope": SEARCH, "metric": "http_req_duration.p95",
                                            "comparator": "<", "threshold": 500, "unit": "ms", "approved_by": "PO"}]}))
    perf_cli(project, "import-nfrs", "nfrs.yaml")
    pol = project / ".perf" / "policy.yaml"
    d = yaml.safe_load(pol.read_text())
    d["regression"] = {"warn_pct": 10.0, "fail_pct": 100000.0}  # make the fail band unreachable for this check
    pol.write_text(yaml.safe_dump(d))
    faults.set("search-latency-200ms")
    try:
        v = perf_json(project, "plan", "--feature", "KEY-22", "--env", "perf-qa")
        assert v["values_by_source"].get("NFR"), v["values_by_source"]
        _approve(project, v["plan_id"])
        res = perf_json(project, "run", v["plan_id"], timeout=1800)
    finally:
        faults.clear()
        d.pop("regression")
        pol.write_text(yaml.safe_dump(d))
    t = next(x for x in res["analysis"]["targets"] if x["target"] == SEARCH)
    assert t["slo"][0]["passed"] is True
    assert t["verdict"] == "PASS_WITH_REGRESSION_WARNING", t
    evidence.record("E14.slo_vs_baseline", slo=t["slo"][0], verdict=t["verdict"])


def test_15_diagnostic_loop(project, faults, evidence):
    d1 = perf_json(project, "diagnose", S["feature_bad"])
    sup = [h for h in d1["hypotheses"] if h["status"] == "supported"]
    assert sup and all(h["evidence"] for h in sup)
    assert sup[0]["category"] == "edge_or_network_latency", sup[0]
    assert any(h["category"] == "app_server_latency" and h["status"] == "refuted" for h in d1["hypotheses"])
    dp = d1["diagnostic_plan_id"]
    assert dp
    p = perf_cli(project, "run", dp, check=False)
    assert p.returncode != 0 and "G2" in p.stderr  # diagnostic plan also needs a human gate
    _approve(project, dp)
    faults.set("search-latency-200ms")
    try:
        perf_cli(project, "run", dp, timeout=1800)
    finally:
        faults.clear()
    d2 = perf_json(project, "diagnose", dp)
    assert d2["iteration"] == 2
    cats = {h["category"]: h for h in d2["hypotheses"] if h["status"] == "supported"}
    assert "constant_added_latency" in cats, d2["hypotheses"]
    assert cats["constant_added_latency"]["evidence"][0]["points"]
    evidence.record("E15.diagnostic_loop", iteration1=[h["category"] for h in sup], iteration2=list(cats))


def test_16_insufficient_evidence_without_telemetry(project, faults, evidence):
    cfg = yaml.safe_load((project / ".perf" / "project.yaml").read_text())
    saved = cfg["environments"]["perf-qa"]["telemetry"]
    cfg["environments"]["perf-qa"]["telemetry"] = {}
    (project / ".perf" / "project.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    faults.set("search-latency-200ms")
    try:
        v = perf_json(project, "plan", "--feature", "KEY-22", "--env", "perf-qa")
        _approve(project, v["plan_id"])
        perf_cli(project, "run", v["plan_id"], "--only", f"load:{SEARCH}@10", timeout=1200)
        d = perf_json(project, "diagnose", v["plan_id"])
    finally:
        faults.clear()
        cfg["environments"]["perf-qa"]["telemetry"] = saved
        (project / ".perf" / "project.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    ins = [h for h in d["hypotheses"] if h["status"] == "insufficient_evidence"]
    assert ins and "telemetry" in ins[0]["next_test"].lower(), d["hypotheses"]
    assert not any(h["status"] == "supported" and h["category"] in ("edge_or_network_latency", "app_server_latency")
                   for h in d["hypotheses"])
    evidence.record("E16.insufficient_evidence", hypotheses=[(h["category"], h["status"]) for h in d["hypotheses"]])


def test_17_baseline_protection(project, evidence):
    bls = perf_json(project, "baseline", "list")
    active = {b["target"]: b for b in bls if b["status"] == "active"}
    assert active[SEARCH]["id"] == S[f"bl:{SEARCH}"] and active[SEARCH]["version"] == 1
    assert active["Search"]["id"] == S["bl:Search"]
    with _db(project) as c:
        assert c.execute("select count(*) from series where kind='feature'").fetchone()[0] >= 3
        audit = [r[0] for r in c.execute("select action from audit where action like 'baseline:%'")]
    assert audit.count("baseline:promote") == 2  # only the two explicit promotions
    hist = perf_json(project, "history", "--target", SEARCH)
    assert len(hist) >= 4
    evidence.record("E17.baseline_protection", active=SEARCH + " v1", feature_series=len(hist))


def test_18_compare_status_history(project, evidence):
    cmp_ = perf_json(project, "compare", S[f"bl:{SEARCH}"], S["feature_bad_series"])
    assert cmp_["targets"][SEARCH]["p95"]["delta_pct"] > 300
    st = perf_json(project, "status", S["feature_bad"])
    assert st["analysis"]["overall_verdict"] == "FAIL_REGRESSION"
    rep = perf_cli(project, "report", S["feature_bad"]).stdout
    assert "FAIL_REGRESSION" in rep and "Impact" in rep and "Evidence" in rep
    evidence.record("E18.compare", p95_delta=cmp_["targets"][SEARCH]["p95"]["delta_pct"])


def test_19_secrets_never_leak(project, evidence):
    env = dict(ln.split("=", 1) for ln in (project / ".env").read_text().splitlines() if "=" in ln)
    pw = env["JUICE_SHOP_USER_PASSWORD"]
    assert len(pw) >= 12
    leaks = []
    for f in (project / ".perf").rglob("*"):
        if f.is_file() and f.suffix not in (".png", ".zip"):
            try:
                if pw in f.read_text(encoding="utf-8", errors="ignore"):
                    leaks.append(str(f))
            except OSError:
                pass
    with _db(project) as c:
        for (blob,) in c.execute("select spec || coalesce(metrics,'') || coalesce(error,'') from runs"):
            if pw in (blob or ""):
                leaks.append("registry.runs")
    assert not leaks, leaks
    # the login flow actually used the secret (auth worked): setup request tagged __auth appears in raw output
    raw = next((project / ".perf" / "artifacts").rglob("raw.ndjson"))
    assert '"__auth"' in raw.read_text(encoding="utf-8")
    spec = json.loads(next((project / ".perf" / "artifacts").rglob("spec.json")).read_text())
    assert spec["auth"]["password"]["ref"] == "env:JUICE_SHOP_USER_PASSWORD"
    evidence.record("E19.secrets", leaks=0, auth_used=True)
