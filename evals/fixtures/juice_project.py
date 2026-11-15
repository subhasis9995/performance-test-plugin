"""Build a realistic `perf` project around the natively running Juice Shop (SUT) for evals.

    uv run python evals/fixtures/juice_project.py <dest-dir> [--fast]

Creates:
  .perf/project.yaml       app juice-shop, env perf-qa -> fault proxy 127.0.0.1:3100, telemetry, auth (env var NAMES)
  .perf/service-map.yaml   file globs -> endpoints / journeys (how the feature model maps code to tests)
  .perf/workitems/KEY-22.yaml
  .perf/journeys/*.spec.ts persisted deterministic Playwright journeys
  .perf/policy.yaml        (--fast) short durations for quick evals; default = platform policy
  .env                     test user credentials copied from sut/fixtures/.env.eval (gitignored)
  test-data/catalog.json   search terms + product ids
  app-repo/                git repo mimicking Juice Shop layout with a KEY-22 commit touching search
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
ENV_EVAL = REPO / "sut" / "fixtures" / ".env.eval"
JOURNEYS = Path(__file__).resolve().parent / "journeys"
SEARCH = "GET /rest/products/search"
PRODUCT = "GET /api/Products/{id}"

FAST_POLICY = {
    "benchmark": {"repeats": 3, "characterization_rates": [10], "step_duration_s": 8, "smoke_duration_s": 3,
                  "smoke_rate": 2, "capacity_ladder": [10, 25, 50, 100], "capacity_step_duration_s": 6},
    "feature": {"repeats": 3, "load_rate": 10, "load_duration_s": 8},
    "stability": {"min_samples_per_run": 50, "max_cv_p95": 0.30},
    "frontend": {"repeats": 3, "lighthouse_runs": 1},
}


def _git(repo: Path, *a: str) -> None:
    subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)


def build_feature_repo(dest: Path) -> Path:
    repo = dest / "app-repo"
    if repo.exists():
        shutil.rmtree(repo)
    (repo / "routes").mkdir(parents=True)
    (repo / "frontend/src/app/search-result").mkdir(parents=True)
    (repo / "frontend/src/app/basket").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "dev@example.test")
    _git(repo, "config", "user.name", "Dev")
    (repo / "routes/search.ts").write_text(
        "export function searchProducts () {\n  return (req, res) => {\n"
        "    const criteria = req.query.q\n    // GET /rest/products/search\n"
        "    models.sequelize.query(`SELECT * FROM Products WHERE name LIKE '%${criteria}%'`)\n  }\n}\n")
    (repo / "routes/basket.ts").write_text("// GET /rest/basket/:id\nexport function retrieveBasket () {}\n")
    (repo / "frontend/src/app/search-result/search-result.component.ts").write_text("export class SearchResultComponent {}\n")
    (repo / "frontend/src/app/basket/basket.component.ts").write_text("export class BasketComponent {}\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "initial import")
    _git(repo, "checkout", "-qb", "feature/KEY-22-search-ranking")
    (repo / "routes/search.ts").write_text(
        "export function searchProducts () {\n  return (req, res) => {\n"
        "    const criteria = req.query.q\n    // GET /rest/products/search  (KEY-22: rank by relevance)\n"
        "    models.sequelize.query(`SELECT * FROM Products WHERE name LIKE '%${criteria}%' OR description LIKE '%${criteria}%'`)\n"
        "  }\n}\n")
    (repo / "frontend/src/app/search-result/search-result.component.ts").write_text(
        "export class SearchResultComponent { /* KEY-22 relevance badge */ }\n")
    _git(repo, "commit", "-qam", "KEY-22: rank search results by relevance")
    return repo


def build(dest: Path, fast: bool = False, with_auth: bool = True) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    perf = dest / ".perf"
    (perf / "workitems").mkdir(parents=True, exist_ok=True)
    (perf / "journeys").mkdir(parents=True, exist_ok=True)
    (dest / "test-data").mkdir(exist_ok=True)
    (dest / "test-data/catalog.json").write_text(json.dumps(
        [{"q": q, "id": i} for q, i in [("apple", 1), ("juice", 6), ("banana", 2), ("lemon", 3), ("orange", 4),
                                         ("water", 7), ("egg", 8), ("melon", 5)]]))
    repo = build_feature_repo(dest)
    env = {
        "base_url": "http://127.0.0.1:3100",
        "frontend_url": "http://127.0.0.1:3100",
        "allowed_hosts": ["127.0.0.1"],
        "ownership_confirmed": True,
        "telemetry": {
            "proxy": "scrape:http://127.0.0.1:3100/__metrics",
            "app": "scrape:http://127.0.0.1:3000/metrics",
            "app_process": "process:port=3000",
        },
        "auth": {"type": "none"},
    }
    if with_auth:
        env["auth"] = {
            "type": "login_flow",
            "env": {"username": "JUICE_SHOP_USER_EMAIL", "password": "JUICE_SHOP_USER_PASSWORD"},
            "login_request": {"method": "POST", "path": "/rest/user/login",
                              "body_template": {"email": "${username}", "password": "${password}"},
                              "token_json_path": "authentication.token"},
        }
    cfg = {
        "application_id": "juice-shop",
        "environments": {"perf-qa": env},
        "api_specs": [],
        "api_spec_declared_none": True,
        "test_data": ["test-data/catalog.json"],
        "nfr_files": [],
        "nfrs_declared_none": True,
        "auth_declared": True,
        "journeys": [
            {"name": "Search", "steps": ["Home", "Search 'apple'", "Results"], "spec": ".perf/journeys/search.spec.ts"},
            {"name": "ProductDetails", "steps": ["Home", "Open product"], "spec": ".perf/journeys/product.spec.ts"},
        ],
        "repository": str(repo),
        "service_map": ".perf/service-map.yaml",
        "targets": [SEARCH, PRODUCT],
        "request_overrides": {
            SEARCH: {"path": "/rest/products/search?q=${data.q}"},
            PRODUCT: {"path": "/api/Products/${data.id}"},
        },
    }
    (perf / "project.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    (perf / "service-map.yaml").write_text(yaml.safe_dump({"mappings": [
        {"name": "product-search", "paths": ["routes/search.ts"], "endpoints": [SEARCH],
         "services": ["SearchService"], "dependencies": ["sqlite:Products"]},
        {"name": "search-ui", "paths": ["frontend/src/app/search-result/**"], "components": ["SearchResultComponent"],
         "journeys": ["Search"]},
        {"name": "basket", "paths": ["routes/basket.ts", "frontend/src/app/basket/**"], "endpoints": ["GET /rest/basket/{id}"],
         "journeys": ["Checkout"]},
    ]}, sort_keys=False))
    (perf / "workitems/KEY-22.yaml").write_text(yaml.safe_dump({
        "id": "KEY-22", "title": "Rank product search results by relevance",
        "description": "Search should also match product descriptions and show a relevance badge.",
        "acceptance_criteria": ["Search returns products matching description", "Relevance badge shown"],
        "branch": "feature/KEY-22-search-ranking", "base": "main",
    }, sort_keys=False))
    for f in JOURNEYS.glob("*.spec.ts"):
        shutil.copy2(f, perf / "journeys" / f.name)
    if fast:
        (perf / "policy.yaml").write_text(yaml.safe_dump(FAST_POLICY))
    if ENV_EVAL.exists():
        lines = [ln for ln in ENV_EVAL.read_text().splitlines() if ln.startswith("JUICE_SHOP_USER_")]
        (dest / ".env").write_text("\n".join(lines) + "\n")
    (dest / ".gitignore").write_text(".env\n.env.*\n!.env.example\n.perf/state/\n.perf/artifacts/\n")
    return dest


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("dest", type=Path)
    ap.add_argument("--fast", action="store_true")
    ap.add_argument("--no-auth", action="store_true")
    a = ap.parse_args()
    print(build(a.dest, fast=a.fast, with_auth=not a.no_auth))
