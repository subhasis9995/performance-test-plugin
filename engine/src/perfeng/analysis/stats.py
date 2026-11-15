"""Deterministic statistics. No LLM involvement."""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
from scipy import stats as sps

PERCENTILES = (50, 90, 95, 99)


def percentiles(samples: Sequence[float], ps: Sequence[float] = PERCENTILES) -> dict[str, float | None]:
    if len(samples) == 0:
        return {f"p{int(p)}": None for p in ps}
    arr = np.asarray(samples, dtype=float)
    vals = np.percentile(arr, ps, method="linear")
    return {f"p{int(p)}": float(v) for p, v in zip(ps, vals)}


def summarize(durations_ms: Sequence[float], failures: int, total: int, window_s: float) -> dict[str, float | None]:
    out: dict[str, float | None] = dict(percentiles(durations_ms))
    arr = np.asarray(durations_ms, dtype=float) if len(durations_ms) else None
    out["mean"] = float(arr.mean()) if arr is not None else None
    out["max"] = float(arr.max()) if arr is not None else None
    out["count"] = float(total)
    out["error_rate"] = (failures / total) if total else None
    out["rps"] = (total / window_s) if window_s > 0 else None
    return out


def cv(values: Sequence[float]) -> float | None:
    """Coefficient of variation (sample std / mean)."""
    vals = [v for v in values if v is not None and not math.isnan(v)]
    if len(vals) < 2:
        return None
    m = float(np.mean(vals))
    if m == 0:
        return 0.0
    return float(np.std(vals, ddof=1) / m)


def pct_delta(current: float | None, reference: float | None) -> float | None:
    if current is None or reference is None or reference == 0:
        return None
    return (current - reference) / reference * 100.0


def mann_whitney_greater(current: Sequence[float], reference: Sequence[float]) -> float | None:
    """One-sided p-value that `current` is stochastically greater than `reference`."""
    c = [x for x in current if x is not None]
    r = [x for x in reference if x is not None]
    if len(c) < 3 or len(r) < 3:
        return None
    if len(set(c) | set(r)) == 1:
        return 1.0
    return float(sps.mannwhitneyu(c, r, alternative="greater").pvalue)


def min_achievable_p(n: int, m: int) -> float:
    """Smallest one-sided exact Mann-Whitney p-value possible with sample sizes n, m."""
    return 1.0 / math.comb(n + m, n) if n and m else 1.0


def bootstrap_ratio_ci(
    current: Sequence[float], reference: Sequence[float], n: int = 4000, alpha: float = 0.05, seed: int = 7
) -> tuple[float, float] | None:
    """CI (in %) of median(current)/median(reference)-1 via bootstrap. Deterministic seed."""
    c = np.asarray([x for x in current if x is not None], dtype=float)
    r = np.asarray([x for x in reference if x is not None], dtype=float)
    if len(c) < 2 or len(r) < 2:
        return None
    rng = np.random.default_rng(seed)
    cs = rng.choice(c, size=(n, len(c)), replace=True)
    rs = rng.choice(r, size=(n, len(r)), replace=True)
    rm = np.median(rs, axis=1)
    rm[rm == 0] = np.nan
    ratios = (np.median(cs, axis=1) / rm - 1.0) * 100.0
    ratios = ratios[~np.isnan(ratios)]
    lo, hi = np.percentile(ratios, [alpha / 2 * 100, (1 - alpha / 2) * 100])
    return float(lo), float(hi)


def linear_trend(values: Sequence[float]) -> dict[str, float | None]:
    """Slope per observation (least squares) and robust drift % (median of last 3 vs first 3)."""
    vals = [v for v in values if v is not None]
    if len(vals) < 3:
        return {"slope": None, "drift_pct": None, "n": float(len(vals))}
    x = np.arange(len(vals), dtype=float)
    slope, _ = np.polyfit(x, np.asarray(vals, dtype=float), 1)
    k = min(3, len(vals) // 2) or 1
    first = float(np.median(vals[:k]))
    last = float(np.median(vals[-k:]))
    drift = (last - first) / first * 100 if first else None
    return {"slope": float(slope), "drift_pct": drift, "n": float(len(vals))}
