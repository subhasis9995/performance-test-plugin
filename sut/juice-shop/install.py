#!/usr/bin/env python3
"""Install and run a pinned OWASP Juice Shop natively (no Docker).

Commands (combine as needed):
    --install           download the packaged release for this OS/Node, verify md5, unpack
    --start             start Juice Shop on 127.0.0.1:<port> (default 3000), write pid file
    --stop              stop Juice Shop (and the fault proxy, if this script started it)
    --health            poll /rest/admin/application-version until healthy (exit 1 on timeout)
    --status            print install/run state as JSON
    --start-proxy       start fault_proxy.py on 127.0.0.1:3100 in front of Juice Shop
    --stop-proxy        stop the fault proxy only

Layout:
    sut/.cache/juice-shop/<version>/        unpacked release
    sut/.cache/juice-shop/run/              pid files and logs

Stdlib only, so it runs with any Python 3.10+.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

JUICE_SHOP_VERSION = "20.4.0"
SUPPORTED_NODE_MAJORS = (22, 24, 26)  # package.json engines: "22 - 26"; release ships builds for each
RELEASE_BASE = f"https://github.com/juice-shop/juice-shop/releases/download/v{JUICE_SHOP_VERSION}"

# md5 values copied from the release's published *.md5 assets. Verified against the
# published file at install time too; a mismatch with either aborts the install.
PINNED_MD5 = {
    "juice-shop-20.4.0_node24_win32_x64.zip": "970d27e21515eb610d84d9709edf6f74",
}

HERE = Path(__file__).resolve().parent
SUT_DIR = HERE.parent
CACHE_ROOT = SUT_DIR / ".cache" / "juice-shop"
RUN_DIR = CACHE_ROOT / "run"
PRELOAD = HERE / "bind_localhost.cjs"
FAULT_PROXY = HERE / "fault_proxy.py"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 3000
DEFAULT_PROXY_PORT = 3100
HEALTH_PATH = "/rest/admin/application-version"

IS_WINDOWS = os.name == "nt"


# --------------------------------------------------------------------------- helpers

def log(msg: str) -> None:
    print(f"[juice-shop] {msg}", file=sys.stderr, flush=True)


def node_major() -> int:
    node = shutil.which("node")
    if not node:
        raise SystemExit("node not found on PATH; install Node 22, 24 or 26")
    out = subprocess.run([node, "--version"], capture_output=True, text=True, check=True).stdout.strip()
    return int(out.lstrip("v").split(".")[0])


def platform_tag() -> tuple[str, str, str]:
    """Return (os, arch, archive extension) matching Juice Shop release asset names."""
    sysname = sys.platform
    if sysname.startswith("win"):
        os_tag, ext = "win32", "zip"
    elif sysname == "darwin":
        os_tag, ext = "darwin", "zip"
    elif sysname.startswith("linux"):
        os_tag, ext = "linux", "tgz"
    else:
        raise SystemExit(f"unsupported platform {sysname}")
    machine = platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "x64"
    return os_tag, arch, ext


def asset_name(major: int) -> str:
    os_tag, arch, ext = platform_tag()
    return f"juice-shop-{JUICE_SHOP_VERSION}_node{major}_{os_tag}_{arch}.{ext}"


def install_dir(version: str = JUICE_SHOP_VERSION) -> Path:
    return CACHE_ROOT / version


def is_installed(version: str = JUICE_SHOP_VERSION) -> bool:
    d = install_dir(version)
    return (d / "build" / "app.js").is_file() and (d / ".install-complete").is_file()


def http_get(url: str, timeout: float = 5.0) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": "perf-sut-installer"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read() if e.fp else b""


def download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "perf-sut-installer"})
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(req, timeout=60) as resp, open(tmp, "wb") as fh:
        total = int(resp.headers.get("Content-Length") or 0)
        done, last = 0, 0.0
        while chunk := resp.read(1 << 20):
            fh.write(chunk)
            done += len(chunk)
            if time.monotonic() - last > 5:
                pct = f"{100 * done / total:.0f}%" if total else f"{done >> 20} MiB"
                log(f"downloading {dest.name}: {pct}")
                last = time.monotonic()
    tmp.replace(dest)


def md5_of(path: Path) -> str:
    h = hashlib.md5()  # noqa: S324 - md5 is what the upstream release publishes
    with open(path, "rb") as fh:
        while chunk := fh.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def lp(path: Path) -> str:
    """Long-path form for Windows (archive has paths beyond MAX_PATH=260)."""
    s = str(Path(path).resolve())
    prefix = "\\\\?\\"
    return prefix + s if IS_WINDOWS and not s.startswith(prefix) else s


def _safe_member(base: Path, name: str) -> Path:
    target = (base / name).resolve()
    if not str(target).startswith(str(base.resolve())):
        raise SystemExit(f"archive member escapes target dir: {name}")
    return target


def extract(archive: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as zf:
            for m in zf.infolist():
                _safe_member(dest, m.filename)
            zf.extractall(lp(dest))
    else:
        with tarfile.open(archive, "r:gz") as tf:
            for m in tf.getmembers():
                _safe_member(dest, m.name)
                if m.issym() or m.islnk():
                    _safe_member(dest, os.path.join(os.path.dirname(m.name), m.linkname))
            tf.extractall(dest)


# --------------------------------------------------------------------------- install

def cmd_install(force: bool = False) -> Path:
    target = install_dir()
    if is_installed() and not force:
        log(f"already installed at {target}")
        return target
    major = node_major()
    if major not in SUPPORTED_NODE_MAJORS:
        raise SystemExit(f"Node {major} not supported by Juice Shop {JUICE_SHOP_VERSION} (needs one of {SUPPORTED_NODE_MAJORS})")
    name = asset_name(major)
    downloads = CACHE_ROOT / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    archive = downloads / name

    status, body = http_get(f"{RELEASE_BASE}/{name}.md5", timeout=30)
    published = body.decode().split()[0].strip().lower() if status == 200 and body else None
    pinned = PINNED_MD5.get(name)
    expected = pinned or published
    if pinned and published and pinned != published:
        raise SystemExit(f"published md5 {published} differs from pinned md5 {pinned} for {name}")
    if not expected:
        raise SystemExit(f"no checksum available for {name}; refusing to install unverified archive")

    if not archive.exists() or md5_of(archive) != expected:
        log(f"downloading {RELEASE_BASE}/{name}")
        download(f"{RELEASE_BASE}/{name}", archive)
    actual = md5_of(archive)
    if actual != expected:
        archive.unlink(missing_ok=True)
        raise SystemExit(f"md5 mismatch for {name}: expected {expected}, got {actual}")
    log(f"md5 ok ({actual}, source={'pinned' if pinned else 'published'})")

    staging = CACHE_ROOT / ".stg"
    if staging.exists():
        shutil.rmtree(lp(staging))
    log(f"extracting to {staging}")
    extract(archive, staging)
    # The archive holds one top-level folder (juice-shop_<version>); find the dir with build/app.js.
    roots = [p.parent.parent for p in staging.glob("*/build/app.js")] or (
        [staging] if (staging / "build" / "app.js").is_file() else []
    )
    if not roots:
        raise SystemExit("extracted archive has no build/app.js")
    if target.exists():
        shutil.rmtree(lp(target))
    os.replace(lp(roots[0]), lp(target))  # same volume: atomic rename, no long-path copy
    shutil.rmtree(lp(staging), ignore_errors=True)
    (target / ".install-complete").write_text(
        json.dumps({"version": JUICE_SHOP_VERSION, "asset": name, "md5": actual, "node_major": major}, indent=2)
    )
    log(f"installed Juice Shop {JUICE_SHOP_VERSION} at {target}")
    return target


# --------------------------------------------------------------------------- processes

def pid_file(kind: str, port: int) -> Path:
    return RUN_DIR / f"{kind}-{port}.pid"


def log_file(kind: str, port: int) -> Path:
    return RUN_DIR / f"{kind}-{port}.log"


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if IS_WINDOWS:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"], capture_output=True, text=True
        ).stdout
        return f'"{pid}"' in out
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def read_pid(kind: str, port: int) -> int | None:
    p = pid_file(kind, port)
    try:
        return int(json.loads(p.read_text())["pid"])
    except (OSError, ValueError, KeyError):
        return None


def port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def spawn(kind: str, port: int, argv: list[str], cwd: Path, env: dict[str, str]) -> int:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    existing = read_pid(kind, port)
    if existing and pid_alive(existing):
        log(f"{kind} already running (pid {existing}) on port {port}")
        return existing
    if port_open(DEFAULT_HOST, port):
        raise SystemExit(f"port {port} already in use by another process; not starting {kind}")
    logf = open(log_file(kind, port), "ab")
    kwargs: dict = {"cwd": str(cwd), "env": env, "stdout": logf, "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL}
    if IS_WINDOWS:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(argv, **kwargs)
    logf.close()
    pid_file(kind, port).write_text(json.dumps({"pid": proc.pid, "port": port, "argv": argv, "started": time.time()}))
    log(f"started {kind} pid {proc.pid} on {DEFAULT_HOST}:{port} (log {log_file(kind, port)})")
    return proc.pid


def kill_tree(pid: int, timeout: float = 15.0) -> None:
    if IS_WINDOWS:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    else:
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and pid_alive(pid):
        time.sleep(0.2)
    if pid_alive(pid) and not IS_WINDOWS:
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def stop(kind: str, port: int) -> bool:
    pid = read_pid(kind, port)
    if pid is None:
        log(f"{kind} on port {port}: no pid file")
        return False
    if pid_alive(pid):
        kill_tree(pid)
        log(f"stopped {kind} pid {pid}")
    else:
        log(f"{kind} pid {pid} was not running")
    pid_file(kind, port).unlink(missing_ok=True)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and port_open(DEFAULT_HOST, port):
        time.sleep(0.2)
    return True


def cmd_start(port: int) -> int:
    if not is_installed():
        cmd_install()
    node = shutil.which("node")
    assert node
    env = os.environ.copy()
    env["PORT"] = str(port)
    env["JUICE_SHOP_BIND_HOST"] = DEFAULT_HOST
    env.setdefault("NODE_ENV", "default")
    return spawn("juice-shop", port, [node, "-r", str(PRELOAD), "build/app.js"], install_dir(), env)


def cmd_start_proxy(port: int, upstream_port: int, faults: str | None) -> int:
    # Use this interpreter if it has the proxy deps (e.g. `uv run --project sut`), else let uv provide them.
    has_deps = subprocess.run([sys.executable, "-c", "import aiohttp, yaml, prometheus_client"], capture_output=True).returncode == 0
    uv = shutil.which("uv")
    if has_deps:
        launcher = [sys.executable]
    elif uv:
        launcher = [uv, "run", "--quiet", "--project", str(SUT_DIR), "python"]
    else:
        raise SystemExit("fault proxy needs aiohttp, pyyaml, prometheus-client (or uv on PATH)")
    argv = launcher + [str(FAULT_PROXY), "--listen", f"{DEFAULT_HOST}:{port}", "--upstream", f"http://{DEFAULT_HOST}:{upstream_port}"]
    if faults:
        argv += ["--faults", str(Path(faults).resolve())]
    return spawn("fault-proxy", port, argv, HERE, os.environ.copy())


def wait_healthy(port: int, timeout: float, path: str = HEALTH_PATH) -> dict | None:
    url = f"http://{DEFAULT_HOST}:{port}{path}"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            status, body = http_get(url, timeout=3)
            if status == 200:
                return json.loads(body or b"{}")
        except (urllib.error.URLError, OSError, ValueError):
            pass
        time.sleep(0.5)
    return None


def cmd_status(port: int, proxy_port: int) -> dict:
    info: dict = {
        "version": JUICE_SHOP_VERSION,
        "installed": is_installed(),
        "install_dir": str(install_dir()),
        "juice_shop": {"port": port, "pid": read_pid("juice-shop", port)},
        "fault_proxy": {"port": proxy_port, "pid": read_pid("fault-proxy", proxy_port)},
    }
    for key, kind, p, path in (("juice_shop", "juice-shop", port, HEALTH_PATH), ("fault_proxy", "fault-proxy", proxy_port, "/__faults")):
        pid = info[key]["pid"]
        info[key]["running"] = bool(pid and pid_alive(pid))
        info[key]["healthy"] = wait_healthy(p, 0.1, path) is not None if port_open(DEFAULT_HOST, p) else False
    return info


# --------------------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--install", action="store_true")
    ap.add_argument("--force", action="store_true", help="reinstall even if present")
    ap.add_argument("--start", action="store_true")
    ap.add_argument("--stop", action="store_true")
    ap.add_argument("--health", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--start-proxy", action="store_true")
    ap.add_argument("--stop-proxy", action="store_true")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--proxy-port", type=int, default=DEFAULT_PROXY_PORT)
    ap.add_argument("--faults", help="fault YAML for --start-proxy")
    ap.add_argument("--timeout", type=float, default=180.0, help="health wait seconds")
    args = ap.parse_args(argv)

    if not any((args.install, args.start, args.stop, args.health, args.status, args.start_proxy, args.stop_proxy)):
        ap.print_help()
        return 2

    rc = 0
    if args.stop:
        stop("fault-proxy", args.proxy_port)
        stop("juice-shop", args.port)
    if args.stop_proxy and not args.stop:
        stop("fault-proxy", args.proxy_port)
    if args.install:
        cmd_install(force=args.force)
    if args.start:
        cmd_start(args.port)
    if args.start_proxy:
        cmd_start_proxy(args.proxy_port, args.port, args.faults)
    if args.health or args.start:
        info = wait_healthy(args.port, args.timeout)
        if info is None:
            log(f"Juice Shop not healthy on port {args.port} after {args.timeout:.0f}s (see {log_file('juice-shop', args.port)})")
            rc = 1
        else:
            log(f"healthy: {info}")
            if args.health:
                print(json.dumps({"healthy": True, **info}))
    if args.start_proxy and wait_healthy(args.proxy_port, 20, "/__faults") is None:
        log("fault proxy did not come up")
        rc = 1
    if args.status:
        print(json.dumps(cmd_status(args.port, args.proxy_port), indent=2))
    return rc


if __name__ == "__main__":
    sys.exit(main())
