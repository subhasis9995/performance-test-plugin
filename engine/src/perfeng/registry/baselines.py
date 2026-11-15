"""Approvals (human gates) and the Baseline Manager.

Baselines are created ONLY here, and only from a stable, approved BenchmarkSeries.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select

from perfeng.analysis.verdict import aggregate, evaluate_stability, series_samples
from perfeng.domain.models import GateKind
from perfeng.registry.db import ApprovalRow, BaselineRow, Registry, SeriesRow, new_id


class GateError(Exception):
    pass


def content_hash(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def record_approval(
    reg: Registry, gate: GateKind, subject_id: str, subject_hash: str, approver: str, reason: str | None = None
) -> ApprovalRow:
    if not approver or approver.strip().lower() in {"claude", "assistant", "ai", "model", "agent"}:
        raise GateError("approver must be a named human")
    row = ApprovalRow(
        id=new_id("apr"), gate=gate.value, subject_id=subject_id, subject_hash=subject_hash, approver=approver, reason=reason
    )
    reg.add(row)
    reg.audit(approver, f"approve:{gate.value}", subject_id, {"hash": subject_hash, "reason": reason})
    return row


def series_hash(reg: Registry, series_id: str) -> str:
    runs = reg.runs_in_series(series_id)
    return content_hash([(r.id, r.status, r.spec_hash) for r in runs])


def promotion_check(reg: Registry, series_id: str, policy: dict) -> dict[str, Any]:
    series = reg.get(SeriesRow, series_id)
    if series is None:
        raise GateError(f"series {series_id} not found")
    runs = [r for r in reg.runs_in_series(series_id) if r.status == "completed"]
    metrics = [r.metrics or {} for r in runs]
    if series.workload_key.startswith("frontend@"):
        from perfeng.analysis.frontend import journey_samples
        from perfeng.analysis.stats import cv

        js = journey_samples(metrics, series.target)
        n = len(js.get("journey_ms", []))
        cvs = {k: cv(js.get(k, [])) for k in ("lcp_ms", "journey_ms", "tbt_ms") if js.get(k)}
        lim = max(policy["stability"]["max_cv_p95"] * 2, 0.15)
        reasons = []
        if n < int(policy["frontend"]["repeats"]):
            reasons.append(f"only {n} journey runs, need {policy['frontend']['repeats']}")
        reasons += [f"{k} CV {v:.3f} > {lim}" for k, v in cvs.items() if v is None or v > lim]
        st_dict = {"stable": not reasons, "runs": n, "cv": cvs, "reasons": reasons}
        if series.kind != "benchmark":
            reasons.append(f"series kind '{series.kind}' cannot be promoted")
        return {"series_id": series_id, "target": series.target, "workload_key": series.workload_key,
                "eligible": not reasons, "stability": st_dict, "reasons": reasons, "series_hash": series_hash(reg, series_id)}
    required = int(policy["benchmark"]["repeats"])
    st = evaluate_stability(metrics, series.target, policy, required)
    eligible = st.stable and series.kind == "benchmark"
    reasons = list(st.reasons)
    if series.kind != "benchmark":
        reasons.append(f"series kind '{series.kind}' cannot be promoted; only controlled benchmark series can")
    return {
        "series_id": series_id,
        "target": series.target,
        "workload_key": series.workload_key,
        "eligible": eligible,
        "stability": st.to_dict(),
        "reasons": reasons,
        "series_hash": series_hash(reg, series_id),
    }


def promote_baseline(reg: Registry, series_id: str, policy: dict, approver: str, reason: str) -> BaselineRow:
    chk = promotion_check(reg, series_id, policy)
    if not chk["eligible"]:
        raise GateError("series not eligible for baseline promotion: " + "; ".join(chk["reasons"]))
    approval = reg.has_approval(series_id, chk["series_hash"], GateKind.BASELINE_PROMOTION.value)
    if approval is None:
        raise GateError("baseline promotion requires a human approval (gate G5) bound to this series")
    series = reg.get(SeriesRow, series_id)
    runs = [r for r in reg.runs_in_series(series_id) if r.status == "completed"]
    if series.workload_key.startswith("frontend@"):
        from perfeng.analysis.frontend import journey_samples

        samples = journey_samples([r.metrics or {} for r in runs], series.target)
    else:
        samples = series_samples([r.metrics or {} for r in runs], series.target)
    prev = reg.query(
        select(BaselineRow).where(
            BaselineRow.application_id == series.application_id,
            BaselineRow.environment == series.environment,
            BaselineRow.target == series.target,
            BaselineRow.workload_key == series.workload_key,
        )
    )
    version = max((b.version for b in prev), default=0) + 1
    for b in prev:
        if b.status == "active":
            reg.update(BaselineRow, b.id, status="retired")
    row = BaselineRow(
        id=new_id("bl"),
        application_id=series.application_id,
        environment=series.environment,
        target=series.target,
        workload_key=series.workload_key,
        version=version,
        series_id=series_id,
        summary=aggregate(samples),
        samples=samples,
        approval_id=approval.id,
        status="active",
    )
    reg.add(row)
    reg.audit(approver, "baseline:promote", row.id, {"series": series_id, "version": version, "reason": reason})
    return row
