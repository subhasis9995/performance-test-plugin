"""Telemetry collection around a run, for correlation and diagnostics.

Environment config ``telemetry`` maps a name to a source:
  scrape:<url>                 Prometheus exposition endpoint, scraped before/after (counter & histogram deltas)
  prom:<base_url>|<promql>     PromQL range query against a Prometheus server for the run window
  process:port=<n>             CPU / RSS of the local process listening on TCP port n (psutil)
"""

from __future__ import annotations

import re
import threading
import time
from collections import defaultdict
from typing import Any

import httpx
import psutil

_LINE = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{[^}]*\})?\s+([-+0-9.eE]+|NaN|\+Inf|-Inf)")


def parse_exposition(text: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for ln in text.splitlines():
        if not ln or ln.startswith("#"):
            continue
        m = _LINE.match(ln)
        if not m:
            continue
        try:
            out[m.group(1) + (m.group(2) or "")] = float(m.group(3))
        except ValueError:
            continue
    return out


def _labels(series: str) -> str:
    i = series.find("{")
    return series[i:] if i >= 0 else ""


def exposition_delta(before: dict[str, float], after: dict[str, float]) -> dict[str, float]:
    """Histogram/summary means and counter increases over the run window."""
    out: dict[str, float] = {}
    sums: dict[str, float] = {}
    counts: dict[str, float] = {}
    for k, v in after.items():
        d = v - before.get(k, 0.0)
        name = k.split("{", 1)[0]
        lab = _labels(k)
        if name.endswith("_sum"):
            sums[name[:-4] + lab] = d
        elif name.endswith("_count"):
            counts[name[:-6] + lab] = d
        elif name.endswith("_total") and d > 0:
            out[f"{k}:increase"] = d
        elif name.endswith("_bucket"):
            continue
    for k, s in sums.items():
        c = counts.get(k, 0.0)
        if c > 0:
            mean = s / c
            unit = "ms" if "seconds" in k.split("{")[0] else ""
            out[f"{k}:mean{('_' + unit) if unit else ''}"] = mean * 1000 if unit == "ms" else mean
            out[f"{k}:count"] = c
    return dict(sorted(out.items(), key=lambda kv: -abs(kv[1]))[:300])


def _pid_on_port(port: int) -> int | None:
    try:
        for c in psutil.net_connections(kind="tcp"):
            if c.laddr and c.laddr.port == port and c.status == psutil.CONN_LISTEN and c.pid:
                return c.pid
    except (psutil.AccessDenied, PermissionError):
        return None
    return None


class TelemetryCollector:
    def __init__(self, sources: dict[str, str] | None):
        self.sources = sources or {}
        self._stop = threading.Event()
        self._proc_samples: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
        self._before: dict[str, dict[str, float]] = {}
        self._errors: dict[str, str] = {}
        self._thread: threading.Thread | None = None
        self.t0 = 0.0

    def _scrape(self, name: str, url: str) -> dict[str, float]:
        try:
            r = httpx.get(url, timeout=5)
            r.raise_for_status()
            return parse_exposition(r.text)
        except httpx.HTTPError as e:
            self._errors[name] = f"scrape failed: {e}"
            return {}

    def start(self) -> None:
        self.t0 = time.time()
        for name, src in self.sources.items():
            if src.startswith("scrape:"):
                self._before[name] = self._scrape(name, src[7:])
        procs = {n: s for n, s in self.sources.items() if s.startswith("process:")}
        if procs:
            self._thread = threading.Thread(target=self._sample, args=(procs,), daemon=True)
            self._thread.start()

    def _sample(self, procs: dict[str, str]) -> None:
        handles: dict[str, psutil.Process] = {}
        for name, src in procs.items():
            m = re.search(r"port=(\d+)", src)
            pid = _pid_on_port(int(m.group(1))) if m else None
            if pid is None:
                self._errors[name] = f"no process listening ({src})"
                continue
            try:
                p = psutil.Process(pid)
                p.cpu_percent(None)
                handles[name] = p
            except psutil.Error as e:
                self._errors[name] = str(e)
        ncpu = psutil.cpu_count() or 1
        while not self._stop.wait(1.0):
            for name, p in handles.items():
                try:
                    with p.oneshot():
                        self._proc_samples[name]["cpu_pct"].append(p.cpu_percent(None) / ncpu)
                        self._proc_samples[name]["rss_mb"].append(p.memory_info().rss / 1e6)
                        self._proc_samples[name]["threads"].append(float(p.num_threads()))
                except psutil.Error:
                    pass
            self._proc_samples["__host__"]["cpu_pct"].append(psutil.cpu_percent(None))

    def stop(self) -> dict[str, Any]:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
        t1 = time.time()
        out: dict[str, Any] = {"window_s": round(t1 - self.t0, 1), "sources": dict(self.sources)}
        for name, src in self.sources.items():
            if src.startswith("scrape:"):
                after = self._scrape(name, src[7:])
                if after:
                    out[name] = exposition_delta(self._before.get(name, {}), after)
            elif src.startswith("prom:"):
                base, _, q = src[5:].partition("|")
                try:
                    r = httpx.get(f"{base.rstrip('/')}/api/v1/query_range",
                                  params={"query": q, "start": self.t0, "end": t1, "step": "5s"}, timeout=10)
                    vals = [float(v[1]) for res in r.json()["data"]["result"] for v in res["values"]]
                    out[name] = {"max": max(vals) if vals else None, "mean": sum(vals) / len(vals) if vals else None}
                except (httpx.HTTPError, KeyError, ValueError) as e:
                    self._errors[name] = f"promql failed: {e}"
        for name, series in self._proc_samples.items():
            agg = {}
            for k, vals in series.items():
                if vals:
                    s = sorted(vals)
                    agg[f"{k}_p95"] = s[int(0.95 * (len(s) - 1))]
                    agg[f"{k}_max"] = s[-1]
                    agg[f"{k}_mean"] = sum(s) / len(s)
            out[name] = agg
        if self._errors:
            out["errors"] = dict(self._errors)
        return out
