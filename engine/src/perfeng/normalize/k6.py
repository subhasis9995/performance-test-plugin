"""Normalize raw k6 NDJSON (``--out json=``) into canonical RunMetrics.

Percentiles are recomputed from raw samples (not k6's summary) so analysis is
independent of summary trend-stat configuration. Setup/auth requests (tag name "__auth")
and requests without phase=main are excluded from target metrics.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from perfeng.analysis.stats import percentiles


def _ts(s: str) -> float:
    # k6 emits RFC3339 with up to 9 fractional digits; Python handles 6
    if "." in s:
        head, rest = s.split(".", 1)
        frac = "".join(ch for ch in rest if ch.isdigit())
        tz = rest[len(frac):]
        s = f"{head}.{frac[:6]}{tz}"
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def parse_ndjson(path: str | Path) -> dict[str, Any]:
    durations: dict[str, list[float]] = defaultdict(list)
    waiting: dict[str, list[float]] = defaultdict(list)
    failed: dict[str, int] = defaultdict(int)
    total: dict[str, int] = defaultdict(int)
    status_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    tx: dict[str, list[float]] = defaultdict(list)
    tx_ok: dict[str, list[float]] = defaultdict(list)
    counters: dict[str, float] = defaultdict(float)
    vus_max = 0
    t_first: float | None = None
    t_last: float | None = None
    all_d: list[float] = []
    all_failed = 0
    all_total = 0

    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.startswith('{"metric"'):
                continue
            rec = json.loads(line)
            if rec.get("type") != "Point":
                continue
            m = rec["metric"]
            d = rec["data"]
            tags = d.get("tags") or {}
            v = d["value"]
            if m in ("vus_max",):
                vus_max = max(vus_max, int(v))
                continue
            if m in ("data_sent", "data_received", "iterations", "dropped_iterations"):
                counters[m] += v
                continue
            if m == "tx_duration":
                tx[tags.get("tx", "?")].append(v)
                continue
            if m == "tx_success":
                tx_ok[tags.get("tx", "?")].append(v)
                continue
            name = tags.get("name", "?")
            if tags.get("phase") != "main" or name == "__auth":
                continue
            t = _ts(d["time"])
            t_first = t if t_first is None else min(t_first, t)
            t_last = t if t_last is None else max(t_last, t)
            if m == "http_req_duration":
                durations[name].append(v)
                all_d.append(v)
                status_counts[name][tags.get("status", "0")] += 1
            elif m == "http_req_waiting":
                waiting[name].append(v)
            elif m == "http_req_failed":
                total[name] += 1
                all_total += 1
                if v:
                    failed[name] += 1
                    all_failed += 1

    window = (t_last - t_first) if t_first is not None and t_last is not None else 0.0
    window = max(window, 1e-9)

    def summary(target: str, ds: list[float], fl: int, tot: int, wt: list[float] | None = None) -> dict[str, Any]:
        p = percentiles(ds)
        extra: dict[str, float | None] = {}
        if wt:
            extra["ttfb_p95"] = percentiles(wt, (95,))["p95"]
        return {"target": target, "count": tot, **p,
                "mean": (sum(ds) / len(ds)) if ds else None, "max": max(ds) if ds else None,
                "error_rate": (fl / tot) if tot else None, "rps": tot / window if window > 1e-6 else None,
                "extra": extra}

    per_target = {n: summary(n, durations[n], failed[n], total[n], waiting.get(n)) for n in durations}
    for n, codes in status_counts.items():
        per_target[n]["extra"].update({f"status_{c}": float(k) for c, k in codes.items()})
    tx_summary = {}
    for name, vals in tx.items():
        ok = tx_ok.get(name, [])
        tx_summary[name] = {**percentiles(vals), "count": len(vals), "success_rate": (sum(ok) / len(ok)) if ok else None,
                            "rate_per_s": len(vals) / window if window > 1e-6 else None}
    return {
        "duration_s": window,
        "iterations": int(counters["iterations"]),
        "dropped_iterations": int(counters["dropped_iterations"]),
        "vus_max": vus_max,
        "data_sent_bytes": int(counters["data_sent"]),
        "data_received_bytes": int(counters["data_received"]),
        "http_reqs": all_total,
        "overall": summary("__overall__", all_d, all_failed, all_total),
        "per_target": per_target,
        "transactions": tx_summary,
    }
