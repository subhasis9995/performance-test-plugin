from __future__ import annotations

import random

import pytest

from perfeng.config import EnvironmentConfig, load_policy
from perfeng.registry.db import Registry, RunRow, SeriesRow, new_id
from perfeng.spec import testrunspec_v1 as trs


@pytest.fixture
def policy():
    return load_policy()


@pytest.fixture
def env():
    return EnvironmentConfig(
        base_url="http://127.0.0.1:3100", allowed_hosts=["127.0.0.1", "localhost"], ownership_confirmed=True
    )


@pytest.fixture
def reg(tmp_path):
    return Registry(f"sqlite:///{(tmp_path / 'r.db').as_posix()}")


def make_spec(**over) -> trs.TestRunSpec:
    base = dict(
        run_id="run_x",
        application_id="juice-shop",
        environment="perf-qa",
        test_type="load",
        base_url="http://127.0.0.1:3100",
        approved_hosts=["127.0.0.1"],
        scenarios=[
            trs.Scenario(
                name="search",
                kind="api",
                requests=[trs.Request(name="GET /rest/products/search", method="GET", path="/rest/products/search?q=apple")],
                stages=[trs.Stage(rate=25, duration_s=30)],
            )
        ],
        limits=trs.Limits(max_duration_s=600, max_vus=50, max_arrival_rate=50),
    )
    base.update(over)
    return trs.TestRunSpec(**base)


@pytest.fixture
def spec_factory():
    return make_spec


def run_metrics(target: str, p95: float, err: float = 0.0, count: int = 1500, dropped: int = 0, jitter: float = 0.0):
    r = random.Random(int(p95 * 1000) + count)
    p95v = p95 * (1 + r.uniform(-jitter, jitter))
    return {
        "dropped_iterations": dropped,
        "per_target": {
            target: {"target": target, "count": count, "p50": p95v * 0.5, "p90": p95v * 0.85, "p95": p95v,
                     "p99": p95v * 1.3, "error_rate": err, "rps": 25.0, "extra": {}}
        },
        "generator": {"cpu_pct_p95": 30.0},
    }


@pytest.fixture
def series_factory(reg):
    def _make(target="GET /x", p95s=(100, 101, 99, 100, 102), kind="benchmark", err=0.0, dropped=0):
        sid = new_id("ser")
        reg.add(SeriesRow(id=sid, application_id="app", environment="perf-qa", target=target,
                          workload_key="load@25rps/60s", kind=kind))
        for i, p in enumerate(p95s):
            reg.add(RunRow(id=new_id("run"), series_id=sid, application_id="app", environment="perf-qa", kind=kind,
                           test_type="load", repeat_index=i, spec={}, spec_hash="h", status="completed",
                           metrics=run_metrics(target, p, err=err, dropped=dropped)))
        return sid

    return _make
