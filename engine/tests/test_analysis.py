import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from perfeng.analysis import stats
from perfeng.analysis.verdict import decide, evaluate_stability, overall, series_samples
from perfeng.domain.models import Verdict

from conftest import run_metrics

T = "GET /rest/products/search"


@given(st.lists(st.floats(min_value=0.1, max_value=1e5, allow_nan=False), min_size=1, max_size=500))
@settings(max_examples=50, deadline=None)
def test_percentiles_match_numpy(xs):
    got = stats.percentiles(xs)
    for p in (50, 90, 95, 99):
        assert got[f"p{p}"] == pytest.approx(float(np.percentile(xs, p)))


def test_summarize():
    s = stats.summarize([10, 20, 30, 40], failures=1, total=4, window_s=2)
    assert s["error_rate"] == 0.25 and s["rps"] == 2 and s["p50"] == 25


def test_cv_and_delta():
    assert stats.cv([100, 100, 100]) == 0
    assert stats.cv([1]) is None
    assert stats.pct_delta(470, 408) == pytest.approx(15.196, rel=1e-3)


def test_mann_whitney_detects_shift():
    assert stats.mann_whitney_greater([150, 152, 149, 151, 150], [100, 101, 99, 100, 102]) < 0.05
    assert stats.mann_whitney_greater([100, 101, 99, 100, 102], [100, 101, 99, 100, 102]) > 0.05
    assert stats.mann_whitney_greater([1, 2], [1, 2]) is None


def test_trend_drift():
    t = stats.linear_trend([400, 420, 440, 460, 480])
    assert t["slope"] == pytest.approx(20) and t["drift_pct"] == pytest.approx(60 / 410 * 100)


def _samples(p95s, err=0.0, **kw):
    return series_samples([run_metrics(T, p, err=err, **kw) for p in p95s], T)


SLO = [{"id": "NFR-1", "metric": "http_req_duration.p95", "comparator": "<", "threshold": 500}]


def test_spec_example_slo_pass_with_regression_warning(policy):
    base = _samples([408, 410, 406, 409, 407])
    cur = _samples([470, 472, 468, 471, 469])
    v = decide(T, cur, base, SLO, None, policy)
    assert v.verdict == Verdict.PASS_WITH_REGRESSION_WARNING
    p95 = next(r for r in v.regression if r.metric == "p95")
    assert p95.delta_pct == pytest.approx(15.2, abs=0.3) and p95.significant
    assert v.slo[0].passed is True


def test_material_regression_fail(policy):
    v = decide(T, _samples([560, 562, 558, 561, 559]), _samples([408, 410, 406, 409, 407]), [], None, policy)
    assert v.verdict == Verdict.FAIL_REGRESSION


def test_slo_fail_dominates(policy):
    v = decide(T, _samples([620, 621, 619, 622, 618]), _samples([408, 410, 406, 409, 407]), SLO, None, policy)
    assert v.verdict == Verdict.FAIL_SLO


def test_no_regression_pass(policy):
    v = decide(T, _samples([410, 405, 409, 407, 411]), _samples([408, 410, 406, 409, 407]), SLO, None, policy)
    assert v.verdict == Verdict.PASS


def test_noise_not_significant_is_not_regression(policy):
    # +12% median but overlapping distribution -> not significant
    v = decide(T, _samples([300, 600, 320, 590, 310]), _samples([290, 580, 300, 600, 305]), [], None, policy)
    assert v.verdict in (Verdict.PASS,)


def test_error_rate_regression(policy):
    v = decide(T, _samples([408] * 5, err=0.08), _samples([408] * 5, err=0.0), [], None, policy)
    assert v.verdict == Verdict.FAIL_REGRESSION


def test_nothing_to_compare_is_inconclusive_characterization(policy):
    v = decide(T, _samples([400] * 5), None, [], None, policy)
    assert v.verdict == Verdict.INCONCLUSIVE and "characterization" in v.reasons[0]


def test_stability(policy):
    stable = evaluate_stability([run_metrics(T, p) for p in (100, 101, 99, 100, 102)], T, policy, 5)
    assert stable.stable
    unstable = evaluate_stability([run_metrics(T, p) for p in (100, 160, 80, 130, 95)], T, policy, 5)
    assert not unstable.stable and "CV" in unstable.reasons[0]
    few = evaluate_stability([run_metrics(T, 100)], T, policy, 5)
    assert not few.stable
    sat = evaluate_stability([run_metrics(T, 100, dropped=50) for _ in range(5)], T, policy, 5)
    assert not sat.stable and any("saturation" in r for r in sat.reasons)


def test_saturated_current_is_inconclusive(policy):
    cur_runs = [run_metrics(T, 900, dropped=40) for _ in range(5)]
    st_ = evaluate_stability(cur_runs, T, policy, 5)
    v = decide(T, series_samples(cur_runs, T), _samples([408] * 5), [], st_, policy)
    assert v.verdict == Verdict.INCONCLUSIVE


def test_overall_severity(policy):
    a = decide(T, _samples([410] * 5), _samples([408] * 5), SLO, None, policy)
    b = decide(T, _samples([620] * 5), _samples([408] * 5), SLO, None, policy)
    assert overall([a, b]) == Verdict.FAIL_SLO
