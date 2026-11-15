"""Performance / Baseline Registry (SQLAlchemy; SQLite by default, any SQLAlchemy URL works).

Tables keep the Benchmark ≠ Baseline separation explicit:
  runs        — every measurement run (benchmark, feature, diagnostic)
  series      — a group of repeated runs under identical workload (BenchmarkSeries)
  baselines   — approved, versioned references created ONLY by promote_baseline()
  nfrs        — approved NFR/SLO thresholds (separate from baselines)
  approvals   — human gate records bound to content hashes
  audit       — append-only audit log
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

SCHEMA_VERSION = 1


def _now() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}_{datetime.now(UTC):%Y%m%d%H%M%S}_{uuid.uuid4().hex[:6]}"


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class Meta(Base):
    __tablename__ = "meta"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(String)


class PlanRow(Base):
    __tablename__ = "plans"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    application_id: Mapped[str] = mapped_column(String, index=True)
    environment: Mapped[str] = mapped_column(String)
    feature_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    workflow: Mapped[str] = mapped_column(String)
    version: Mapped[int] = mapped_column(Integer, default=1)
    parent_plan_id: Mapped[str | None] = mapped_column(String, nullable=True)
    content: Mapped[dict[str, Any]] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="draft")  # draft|approved|executed|superseded
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class SeriesRow(Base):
    __tablename__ = "series"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    application_id: Mapped[str] = mapped_column(String, index=True)
    environment: Mapped[str] = mapped_column(String)
    target: Mapped[str] = mapped_column(String, index=True)  # endpoint key / journey / scenario name
    workload_key: Mapped[str] = mapped_column(String)  # e.g. "load@25rps/60s"
    kind: Mapped[str] = mapped_column(String)  # benchmark|feature|diagnostic
    feature_id: Mapped[str | None] = mapped_column(String, nullable=True)
    plan_id: Mapped[str | None] = mapped_column(String, nullable=True)
    stability: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class RunRow(Base):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    series_id: Mapped[str | None] = mapped_column(ForeignKey("series.id"), nullable=True, index=True)
    plan_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    application_id: Mapped[str] = mapped_column(String, index=True)
    environment: Mapped[str] = mapped_column(String)
    feature_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String)  # benchmark|feature|diagnostic
    test_type: Mapped[str] = mapped_column(String)
    repeat_index: Mapped[int] = mapped_column(Integer, default=0)
    spec: Mapped[dict[str, Any]] = mapped_column(JSON)
    spec_hash: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    tool_versions: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    environment_info: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    artifacts: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class BaselineRow(Base):
    __tablename__ = "baselines"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    application_id: Mapped[str] = mapped_column(String, index=True)
    environment: Mapped[str] = mapped_column(String)
    target: Mapped[str] = mapped_column(String, index=True)
    workload_key: Mapped[str] = mapped_column(String)
    version: Mapped[int] = mapped_column(Integer)
    series_id: Mapped[str] = mapped_column(ForeignKey("series.id"))
    summary: Mapped[dict[str, Any]] = mapped_column(JSON)  # aggregated metrics of the series
    samples: Mapped[dict[str, Any]] = mapped_column(JSON)  # per-run p95 etc. for significance tests
    approval_id: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="active")  # active|retired
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class NFRRow(Base):
    __tablename__ = "nfrs"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    application_id: Mapped[str] = mapped_column(String, index=True)
    scope: Mapped[str] = mapped_column(String)
    metric: Mapped[str] = mapped_column(String)
    comparator: Mapped[str] = mapped_column(String)
    threshold: Mapped[float] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String, nullable=True)
    source_doc: Mapped[str | None] = mapped_column(String, nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String, nullable=True)


class ApprovalRow(Base):
    __tablename__ = "approvals"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    gate: Mapped[str] = mapped_column(String)
    subject_id: Mapped[str] = mapped_column(String, index=True)
    subject_hash: Mapped[str] = mapped_column(String)
    approver: Mapped[str] = mapped_column(String)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AnalysisRow(Base):
    __tablename__ = "analyses"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    subject_id: Mapped[str] = mapped_column(String, index=True)  # series id or plan id
    content: Mapped[dict[str, Any]] = mapped_column(JSON)
    verdict: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AuditRow(Base):
    __tablename__ = "audit"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    actor: Mapped[str] = mapped_column(String)
    action: Mapped[str] = mapped_column(String)
    subject: Mapped[str | None] = mapped_column(String, nullable=True)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class Registry:
    def __init__(self, url: str | None = None):
        if url is None:
            url = os.environ.get("PERF_DB_URL")
        if url is None:
            from perfeng.config import perf_dir

            d = perf_dir() / "state"
            d.mkdir(parents=True, exist_ok=True)
            url = f"sqlite:///{(d / 'registry.db').as_posix()}"
        self.url = url
        self.engine = create_engine(url, future=True)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(self.engine, expire_on_commit=False)
        with self.Session.begin() as s:
            row = s.get(Meta, "schema_version")
            if row is None:
                s.add(Meta(key="schema_version", value=str(SCHEMA_VERSION)))
            elif int(row.value) > SCHEMA_VERSION:
                raise RuntimeError(f"registry schema {row.value} newer than engine supports ({SCHEMA_VERSION})")

    # ------------------------------------------------------------ helpers
    def session(self) -> Session:
        return self.Session()

    def audit(self, actor: str, action: str, subject: str | None = None, detail: dict | None = None) -> None:
        with self.Session.begin() as s:
            s.add(AuditRow(actor=actor, action=action, subject=subject, detail=_jsonable(detail)))

    def add(self, row: Base) -> Base:
        with self.Session.begin() as s:
            s.add(row)
        return row

    def get(self, cls, id_: str):
        with self.Session() as s:
            return s.get(cls, id_)

    def update(self, cls, id_: str, **fields) -> Any:
        with self.Session.begin() as s:
            row = s.get(cls, id_)
            if row is None:
                raise KeyError(f"{cls.__tablename__} {id_} not found")
            for k, v in fields.items():
                setattr(row, k, _jsonable(v) if isinstance(v, (dict, list)) else v)
            return row

    def query(self, stmt):
        with self.Session() as s:
            return list(s.scalars(stmt))

    # ------------------------------------------------------------ domain queries
    def runs_in_series(self, series_id: str) -> list[RunRow]:
        return self.query(select(RunRow).where(RunRow.series_id == series_id).order_by(RunRow.repeat_index))

    def active_baseline(self, app: str, env: str, target: str, workload_key: str | None = None) -> BaselineRow | None:
        stmt = select(BaselineRow).where(
            BaselineRow.application_id == app,
            BaselineRow.environment == env,
            BaselineRow.target == target,
            BaselineRow.status == "active",
        )
        if workload_key:
            stmt = stmt.where(BaselineRow.workload_key == workload_key)
        rows = self.query(stmt.order_by(BaselineRow.version.desc()))
        return rows[0] if rows else None

    def baselines(self, app: str | None = None) -> list[BaselineRow]:
        stmt = select(BaselineRow)
        if app:
            stmt = stmt.where(BaselineRow.application_id == app)
        return self.query(stmt.order_by(BaselineRow.target, BaselineRow.version))

    def nfrs(self, app: str) -> list[NFRRow]:
        return self.query(select(NFRRow).where(NFRRow.application_id == app))

    def approvals_for(self, subject_id: str) -> list[ApprovalRow]:
        return self.query(select(ApprovalRow).where(ApprovalRow.subject_id == subject_id))

    def has_approval(self, subject_id: str, subject_hash: str, gate: str | None = None) -> ApprovalRow | None:
        for a in self.approvals_for(subject_id):
            if a.subject_hash == subject_hash and (gate is None or a.gate == gate):
                return a
        return None

    def series_history(self, app: str, env: str, target: str, limit: int = 20) -> list[SeriesRow]:
        stmt = (
            select(SeriesRow)
            .where(SeriesRow.application_id == app, SeriesRow.environment == env, SeriesRow.target == target)
            .order_by(SeriesRow.created_at.desc())
            .limit(limit)
        )
        return self.query(stmt)

    def recent_runs(self, limit: int = 20) -> list[RunRow]:
        return self.query(select(RunRow).order_by(RunRow.created_at.desc()).limit(limit))

    def audit_log(self, limit: int = 100) -> list[AuditRow]:
        return self.query(select(AuditRow).order_by(AuditRow.id.desc()).limit(limit))


def _jsonable(obj):
    if obj is None:
        return None
    return json.loads(json.dumps(obj, default=str))


def row_to_dict(row) -> dict[str, Any]:
    return {c.name: _jsonable(getattr(row, c.name)) if not isinstance(getattr(row, c.name), (str, int, float, type(None))) else getattr(row, c.name) for c in row.__table__.columns}
