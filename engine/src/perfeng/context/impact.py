"""Feature understanding: work item + git diff + service map -> FeatureImpactModel.

Deterministic first: changed files are mapped to endpoints/components/journeys through
  1. the project's service map (.perf/service-map.yaml: glob -> endpoints/journeys/dependencies),
  2. route literals found in changed hunks matched against discovered endpoints,
  3. work-item text mentioning endpoints/journeys.
An agent may refine the result (propose_impact), but every item keeps its evidence.
"""

from __future__ import annotations

import fnmatch
import re
import subprocess
from pathlib import Path
from typing import Any

import yaml

from perfeng.config import ProjectConfig
from perfeng.domain.models import FeatureImpactModel, ImpactItem

_ROUTE_LIT = re.compile(r"""["'`](/(?:api|rest|graphql)[A-Za-z0-9_\-/:{}.]*)["'`]""")


def _git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout


def load_workitem(root: Path, cfg: ProjectConfig, feature_id: str) -> dict[str, Any]:
    d = root / cfg.workitems_dir
    for ext in (".yaml", ".yml", ".md", ".json"):
        f = d / f"{feature_id}{ext}"
        if f.exists():
            text = f.read_text(encoding="utf-8")
            if ext in (".yaml", ".yml", ".json"):
                data = yaml.safe_load(text) or {}
                data.setdefault("id", feature_id)
                return data
            return {"id": feature_id, "description": text}
    return {"id": feature_id}


def find_feature_commits(repo: Path, feature_id: str, base: str | None, head: str | None) -> list[str]:
    rng = f"{base}..{head or 'HEAD'}" if base else (head or "HEAD")
    out = _git(repo, "log", "--format=%H", f"--grep={re.escape(feature_id)}", "-i", rng)
    return [c for c in out.split() if c]


def changed_files(repo: Path, feature_id: str, base: str | None, head: str | None) -> tuple[list[str], str, list[str]]:
    """Returns (files, unified diff text, commits). Uses feature-tagged commits; falls back to base..head."""
    commits = find_feature_commits(repo, feature_id, base, head)
    files: set[str] = set()
    diff = ""
    if commits:
        for c in commits:
            files.update(f for f in _git(repo, "show", "--name-only", "--format=", c).split("\n") if f)
            diff += _git(repo, "show", "--format=", "-U0", c)
    elif base:
        files.update(f for f in _git(repo, "diff", "--name-only", f"{base}...{head or 'HEAD'}").split("\n") if f)
        diff = _git(repo, "diff", "-U0", f"{base}...{head or 'HEAD'}")
    return sorted(files), diff, commits


def _match_endpoint(literal: str, endpoints: dict[str, dict]) -> list[str]:
    lit = re.sub(r":([A-Za-z0-9_]+)", r"{\1}", literal.split("?")[0]).rstrip("/")
    hits = []
    for key, e in endpoints.items():
        p = e["path"].rstrip("/")
        pat = "^" + re.sub(r"\\\{[^}]+\\\}", "[^/]+", re.escape(p)) + "$"
        if p == lit or re.match(pat, lit) or re.match("^" + re.sub(r"\\\{[^}]+\\\}", "[^/]+", re.escape(lit)) + "$", p):
            hits.append(key)
    return hits


def build_impact(root: Path, cfg: ProjectConfig, feature_id: str, endpoints: dict[str, dict],
                 repo: str | None = None, base: str | None = None, head: str | None = None) -> FeatureImpactModel:
    wi = load_workitem(root, cfg, feature_id)
    repo_path = Path(repo or cfg.repository or root)
    if not repo_path.is_absolute():
        repo_path = (root / repo_path).resolve()
    unknowns: list[str] = []
    try:
        files, diff, commits = changed_files(repo_path, feature_id, base or wi.get("base"), head or wi.get("branch"))
    except RuntimeError as e:
        files, diff, commits = [], "", []
        unknowns.append(f"git diff unavailable: {e}")
    if not files:
        unknowns.append("no changed files found (no commits mention the feature id and no base..head range)")

    smap_path = root / (cfg.service_map or ".perf/service-map.yaml")
    smap = yaml.safe_load(smap_path.read_text(encoding="utf-8")) if smap_path.exists() else {"mappings": []}
    backend: dict[str, ImpactItem] = {}
    frontend: dict[str, ImpactItem] = {}
    deps: dict[str, ImpactItem] = {}

    def add(bucket: dict, kind: str, name: str, ev: str, conf: float):
        it = bucket.get(name)
        if it is None:
            bucket[name] = ImpactItem(kind=kind, name=name, evidence=[ev], confidence=conf)
        else:
            if ev not in it.evidence:
                it.evidence.append(ev)
            it.confidence = max(it.confidence, conf)

    for f in files:
        for m in smap.get("mappings", []):
            if any(fnmatch.fnmatch(f, g) for g in m.get("paths", [])):
                ev = f"{f} matches service-map '{m.get('name', m['paths'][0])}'"
                for ep in m.get("endpoints", []):
                    add(backend, "endpoint", ep, ev, 0.9)
                for s in m.get("services", []):
                    add(backend, "service", s, ev, 0.9)
                for c in m.get("components", []):
                    add(frontend, "component", c, ev, 0.9)
                for j in m.get("journeys", []):
                    add(frontend, "journey", j, ev, 0.85)
                for d in m.get("dependencies", []):
                    add(deps, "dependency", d, ev, 0.7)
    for lit in set(_ROUTE_LIT.findall(diff)):
        for key in _match_endpoint(lit, endpoints):
            add(backend, "endpoint", key, f"route literal '{lit}' in changed code", 0.8)
    text = " ".join(str(wi.get(k, "")) for k in ("title", "description", "acceptance_criteria"))
    for key, e in endpoints.items():
        if e["path"] in text or key in text:
            add(backend, "endpoint", key, "mentioned in work item", 0.6)
    for j in cfg.journeys:
        if j["name"].lower() in text.lower():
            add(frontend, "journey", j["name"], "mentioned in work item", 0.6)
    for ep in wi.get("endpoints", []) or []:
        add(backend, "endpoint", ep, "listed in work item", 0.9)
    for j in wi.get("journeys", []) or []:
        add(frontend, "journey", j, "listed in work item", 0.9)
    unknown_eps = [k for k in backend if backend[k].kind == "endpoint" and k not in endpoints]
    for k in unknown_eps:
        unknowns.append(f"endpoint {k} referenced but not discovered/configured — add to API spec or request_overrides")
    known_journeys = {j["name"] for j in cfg.journeys}
    for j in [n for n, it in frontend.items() if it.kind == "journey" and n not in known_journeys]:
        unknowns.append(f"journey '{j}' not defined in project journeys")
    return FeatureImpactModel(
        feature_id=feature_id, summary=wi.get("title") or wi.get("description", "")[:200] or None,
        changed_files=files,
        backend=[it for k, it in backend.items() if not (it.kind == "endpoint" and k in unknown_eps)],
        frontend=[it for n, it in frontend.items() if not (it.kind == "journey" and n not in known_journeys)],
        dependencies=list(deps.values()), unknowns=unknowns,
    )
