import pytest

from perfeng.domain.models import GateKind
from perfeng.registry.baselines import GateError, promote_baseline, promotion_check, record_approval


def _approve(reg, sid, policy, who="dana"):
    chk = promotion_check(reg, sid, policy)
    record_approval(reg, GateKind.BASELINE_PROMOTION, sid, chk["series_hash"], who, "stable")


def test_single_run_cannot_become_baseline(reg, policy, series_factory):
    sid = series_factory(p95s=(100,))
    chk = promotion_check(reg, sid, policy)
    assert not chk["eligible"]
    _approve(reg, sid, policy)
    with pytest.raises(GateError, match="not eligible"):
        promote_baseline(reg, sid, policy, "dana", "x")


def test_unstable_series_refused(reg, policy, series_factory):
    sid = series_factory(p95s=(100, 170, 80, 140, 90))
    with pytest.raises(GateError, match="CV"):
        promote_baseline(reg, sid, policy, "dana", "x")


def test_stable_series_needs_human_approval(reg, policy, series_factory):
    sid = series_factory()
    with pytest.raises(GateError, match="G5"):
        promote_baseline(reg, sid, policy, "dana", "x")
    _approve(reg, sid, policy)
    bl = promote_baseline(reg, sid, policy, "dana", "first")
    assert bl.version == 1 and bl.status == "active"
    assert bl.summary["p95"] == pytest.approx(100, rel=0.05)


def test_model_cannot_self_approve(reg, policy, series_factory):
    sid = series_factory()
    with pytest.raises(GateError, match="human"):
        record_approval(reg, GateKind.BASELINE_PROMOTION, sid, "h", "claude")


def test_feature_series_never_promotable(reg, policy, series_factory):
    sid = series_factory(kind="feature")
    assert not promotion_check(reg, sid, policy)["eligible"]


def test_versioning_keeps_history(reg, policy, series_factory):
    s1, s2 = series_factory(), series_factory(p95s=(110, 111, 109, 110, 112))
    _approve(reg, s1, policy)
    b1 = promote_baseline(reg, s1, policy, "dana", "v1")
    _approve(reg, s2, policy)
    b2 = promote_baseline(reg, s2, policy, "dana", "v2")
    assert b2.version == 2
    hist = reg.baselines("app")
    assert [b.version for b in hist] == [1, 2]
    assert [b.status for b in hist] == ["retired", "active"]
    assert reg.active_baseline("app", "perf-qa", "GET /x").id == b2.id
    assert b1.id != b2.id


def test_approval_bound_to_series_content(reg, policy, series_factory):
    sid = series_factory()
    record_approval(reg, GateKind.BASELINE_PROMOTION, sid, "stale-hash", "dana")
    with pytest.raises(GateError, match="G5"):
        promote_baseline(reg, sid, policy, "dana", "x")
