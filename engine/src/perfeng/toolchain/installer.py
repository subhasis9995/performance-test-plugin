"""Pinned toolchain installer. No Docker required.

Installs into the tools dir (``PERF_TOOLS_DIR`` > ``CLAUDE_PLUGIN_DATA``/tools > ~/.perfeng/tools):
  k6/<version>/k6[.exe]          — official release archive, SHA-256 verified
  browser/                       — npm project with pinned Playwright, Lighthouse, web-vitals
  browser/ms-playwright/         — Chromium downloaded by Playwright (PLAYWRIGHT_BROWSERS_PATH)
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import shutil
import subprocess
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

import httpx

K6_VERSION = "v2.3.0"
K6_SHA256 = {
    "windows-amd64.zip": "112276d495e5741c968e2bc09ea6196099c1275bd6db9ee0875d173c7148ce43",
    "linux-amd64.tar.gz": "39c3117b6af817592dcd0ce4242105c0a7af10948c2a425306f0be8f7a8a8ab1",
    "linux-arm64.tar.gz": "5ca3433e8201da72a284aaa241a1bb5fb47f4abb4e384d39410ddd8062f49b90",
    "macos-amd64.zip": "c83bb16f54f0676afa4ea235fd757f3892d307dbd1bd201450de83af31986654",
    "macos-arm64.zip": "b2417a3038edc5fe81dc178a889237724b595c5c9cfed875822008e46e862c7d",
}
BROWSER_PKG_DIR = Path(__file__).resolve().parents[1] / "workers" / "browser"


def tools_dir() -> Path:
    if os.environ.get("PERF_TOOLS_DIR") or os.environ.get("EVAL_PERF_TOOLS_DIR"):
        d = Path(os.environ.get("PERF_TOOLS_DIR") or os.environ["EVAL_PERF_TOOLS_DIR"])
    elif os.environ.get("CLAUDE_PLUGIN_DATA"):
        d = Path(os.environ["CLAUDE_PLUGIN_DATA"]) / "tools"
    else:
        d = Path.home() / ".perfeng" / "tools"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _k6_asset() -> str:
    sysname = platform.system().lower()
    arch = platform.machine().lower()
    arch = {"x86_64": "amd64", "amd64": "amd64", "arm64": "arm64", "aarch64": "arm64"}.get(arch, arch)
    if sysname == "windows":
        return f"windows-{arch}.zip"
    if sysname == "darwin":
        return f"macos-{arch}.zip"
    return f"linux-{arch}.tar.gz"


def k6_path() -> Path:
    exe = "k6.exe" if platform.system() == "Windows" else "k6"
    return tools_dir() / "k6" / K6_VERSION / exe


def install_k6(force: bool = False) -> Path:
    target = k6_path()
    if target.exists() and not force:
        return target
    asset = _k6_asset()
    if asset not in K6_SHA256:
        raise RuntimeError(f"no pinned k6 build for {asset}")
    name = f"k6-{K6_VERSION}-{asset}"
    url = f"https://github.com/grafana/k6/releases/download/{K6_VERSION}/{name}"
    data = httpx.get(url, follow_redirects=True, timeout=300).raise_for_status().content
    digest = hashlib.sha256(data).hexdigest()
    if digest != K6_SHA256[asset]:
        raise RuntimeError(f"k6 checksum mismatch for {name}: {digest}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if asset.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            member = next(m for m in z.namelist() if m.endswith(target.name))
            target.write_bytes(z.read(member))
    else:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as t:
            member = next(m for m in t.getmembers() if m.name.endswith("/k6"))
            target.write_bytes(t.extractfile(member).read())
    target.chmod(0o755)
    return target


def browser_dir() -> Path:
    return tools_dir() / "browser"


def _npm() -> str:
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if not npm:
        raise RuntimeError("npm not found; install Node.js >= 20 to enable frontend tests")
    return npm


def install_browser(force: bool = False) -> Path:
    d = browser_dir()
    d.mkdir(parents=True, exist_ok=True)
    for f in ("package.json", "package-lock.json"):
        src = BROWSER_PKG_DIR / f
        if src.exists():
            shutil.copy2(src, d / f)
    for f in BROWSER_PKG_DIR.glob("*.mjs"):
        shutil.copy2(f, d / f.name)
    env = {**os.environ, "PLAYWRIGHT_BROWSERS_PATH": str(d / "ms-playwright")}
    if force or not (d / "node_modules").exists():
        cmd = "ci" if (d / "package-lock.json").exists() else "install"
        subprocess.run([_npm(), cmd, "--no-audit", "--no-fund"], cwd=d, check=True, env=env, shell=os.name == "nt")
    npx = str(Path(_npm()).with_name("npx.cmd" if os.name == "nt" else "npx"))
    subprocess.run([npx, "playwright", "install", "chromium"], cwd=d, check=True, env=env, shell=os.name == "nt")
    return d


def chromium_path() -> Path | None:
    root = browser_dir() / "ms-playwright"
    if not root.exists():
        return None
    pats = ["chromium-*/chrome-win*/chrome.exe", "chromium-*/chrome-linux*/chrome",
            "chromium-*/chrome-mac*/Chromium.app/Contents/MacOS/Chromium",
            "chromium-*/chrome-mac*/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"]
    for p in pats:
        hits = sorted(root.glob(p))
        if hits:
            return hits[-1]
    return None


@dataclass
class ToolStatus:
    name: str
    ok: bool
    version: str | None
    path: str | None
    detail: str | None = None


def tool_versions() -> dict[str, str | None]:
    out: dict[str, str | None] = {"k6": None, "playwright": None, "lighthouse": None, "node": None}
    k6 = k6_path()
    if k6.exists():
        r = subprocess.run([str(k6), "version"], capture_output=True, text=True)
        out["k6"] = (r.stdout or r.stderr).strip().splitlines()[0] if r.returncode == 0 else None
    lock = browser_dir() / "node_modules"
    for pkg, key in (("playwright", "playwright"), ("lighthouse", "lighthouse")):
        pj = lock / pkg / "package.json"
        if pj.exists():
            out[key] = json.loads(pj.read_text(encoding="utf-8"))["version"]
    node = shutil.which("node")
    if node:
        out["node"] = subprocess.run([node, "--version"], capture_output=True, text=True).stdout.strip()
    return out


def doctor() -> list[ToolStatus]:
    v = tool_versions()
    res = [
        ToolStatus("k6", v["k6"] is not None, v["k6"], str(k6_path()) if k6_path().exists() else None,
                   None if v["k6"] else "run `perf setup`"),
        ToolStatus("node", v["node"] is not None, v["node"], shutil.which("node"),
                   None if v["node"] else "install Node.js >= 20 (frontend tests only)"),
        ToolStatus("playwright", v["playwright"] is not None, v["playwright"], str(browser_dir()),
                   None if v["playwright"] else "run `perf setup --browser`"),
        ToolStatus("chromium", chromium_path() is not None, None, str(chromium_path() or ""),
                   None if chromium_path() else "run `perf setup --browser`"),
        ToolStatus("lighthouse", v["lighthouse"] is not None, v["lighthouse"], None,
                   None if v["lighthouse"] else "run `perf setup --browser`"),
    ]
    from perfeng.executors.docker import docker_available

    res.append(ToolStatus("docker (optional)", True, "available" if docker_available() else "not available", None,
                          "native LocalProcess executor in use" if not docker_available() else None))
    return res
