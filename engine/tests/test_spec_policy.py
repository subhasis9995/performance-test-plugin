import json

import pytest
from pydantic import ValidationError

from perfeng.policy.gate import find_literal_secrets, host_allowed, validate_spec
from perfeng.spec import testrunspec_v1 as trs


def test_schema_export_roundtrip(spec_factory):
    s = spec_factory()
    schema = trs.export_json_schema()
    assert schema["title"] == "TestRunSpec"
    again = trs.TestRunSpec.model_validate_json(s.model_dump_json())
    assert again.content_hash() == s.content_hash()


def test_content_hash_ignores_run_id_but_not_workload(spec_factory):
    a, b = spec_factory(run_id="a"), spec_factory(run_id="b")
    assert a.content_hash() == b.content_hash()
    c = spec_factory(limits=trs.Limits(max_duration_s=600, max_vus=50, max_arrival_rate=49))
    assert c.content_hash() != a.content_hash()


def test_literal_secret_rejected_by_schema():
    with pytest.raises(ValidationError):
        trs.SecretRef(ref="hunter2")
    assert trs.SecretRef(ref="env:PERF_PASSWORD").ref == "env:PERF_PASSWORD"


def test_grpc_is_extension_point_only():
    with pytest.raises(ValidationError):
        trs.Request(name="x", method="POST", path="/svc", protocol="grpc")


@pytest.mark.parametrize("host,ok", [("http://127.0.0.1:3100", True), ("http://evil.com", False),
                                     ("https://api.shop.test", True), ("shop.test", False)])
def test_host_allowlist(host, ok):
    assert host_allowed(host, ["127.0.0.1", "*.shop.test"]) is ok


def test_gate_passes_valid_spec(spec_factory, env, policy):
    r = validate_spec(spec_factory(), env, policy, approval_valid=True)
    assert r.ok, r.violations


def test_gate_requires_approval(spec_factory, env, policy):
    r = validate_spec(spec_factory(), env, policy, approval_valid=False)
    assert not r.ok and any("approval" in v for v in r.violations)


def test_gate_rejects_non_allowlisted(spec_factory, env, policy):
    r = validate_spec(spec_factory(base_url="https://example.org"), env, policy, approval_valid=True)
    assert any("allowlist" in v for v in r.violations)


def test_gate_rejects_caps(spec_factory, env, policy):
    sc = spec_factory().scenarios[0].model_copy(update={"stages": [trs.Stage(rate=500, duration_s=30)], "max_vus": 999})
    r = validate_spec(spec_factory(scenarios=[sc]), env, policy, approval_valid=True)
    assert any("arrival rate" in v for v in r.violations)
    assert any("VUs" in v for v in r.violations)


def test_gate_rejects_stress_without_explicit_approval(spec_factory, env, policy):
    r = validate_spec(spec_factory(test_type="capacity"), env, policy, approval_valid=True)
    assert any("G4" in v for v in r.violations)
    ok = validate_spec(spec_factory(test_type="capacity", approval=trs.Approval(approval_id="a", approver="sam")),
                       env, policy, approval_valid=True)
    assert ok.ok, ok.violations


def test_gate_rejects_unconfirmed_ownership_and_production(spec_factory, env, policy):
    e = env.model_copy(update={"ownership_confirmed": False, "production": True})
    r = validate_spec(spec_factory(), e, policy, approval_valid=True)
    assert any("ownership" in v for v in r.violations)
    assert any("production" in v for v in r.violations)


def test_gate_detects_literal_secret_in_headers(spec_factory, env, policy):
    sc = spec_factory().scenarios[0]
    req = sc.requests[0].model_copy(update={"headers": {"Authorization": "Bearer abcdefghijklmnopqrstuvwxyz123456"}})
    spec = spec_factory(scenarios=[sc.model_copy(update={"requests": [req]})])
    r = validate_spec(spec, env, policy, approval_valid=True)
    assert any("secret" in v for v in r.violations)


def test_secret_placeholders_are_fine():
    assert find_literal_secrets({"body": {"email": "${secrets.username}", "password": "${secrets.password}"}}) == []
    assert find_literal_secrets({"body": json.dumps({"password": "S3cretValue!"})})
