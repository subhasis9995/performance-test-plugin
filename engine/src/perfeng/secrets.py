"""Secret resolution and redaction.

Secrets are referenced as ``env:NAME``. Values come from the process environment or the
project's ``.env`` file, are passed only to worker processes, and are never returned to the
model, written to specs, scripts, logs, or reports.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from dotenv import dotenv_values

from perfeng.spec.testrunspec_v1 import TestRunSpec


class MissingSecret(Exception):
    pass


def _dotenv(root: Path) -> dict[str, str]:
    vals: dict[str, str] = {}
    for name in (".env", ".env.local"):
        f = root / name
        if f.exists():
            vals.update({k: v for k, v in dotenv_values(f).items() if v is not None})
    return vals


def referenced_names(spec: TestRunSpec) -> list[str]:
    names = {r.ref.split(":", 1)[1] for r in spec.secret_refs}
    a = spec.auth
    for ref in (a.username, a.password, a.token, a.client_id, a.client_secret):
        if ref is not None:
            names.add(ref.ref.split(":", 1)[1])
    return sorted(names)


def resolve(spec: TestRunSpec, root: Path) -> dict[str, str]:
    file_vals = _dotenv(root)
    out: dict[str, str] = {}
    missing = []
    for n in referenced_names(spec):
        v = os.environ.get(n, file_vals.get(n))
        if v is None or v == "":
            missing.append(n)
        else:
            out[n] = v
    if missing:
        raise MissingSecret(
            "missing secret env vars: " + ", ".join(missing) + ". Add them to the project .env file (never paste them in chat)."
        )
    return out


def presence(names: list[str], root: Path) -> dict[str, bool]:
    """Which env var names are set — never their values."""
    file_vals = _dotenv(root)
    return {n: bool(os.environ.get(n) or file_vals.get(n)) for n in names}


_HDR = re.compile(r'(?i)("?(authorization|cookie|set-cookie|x-api-key|proxy-authorization)"?\s*[:=]\s*")([^"]*)(")')


def redact_text(text: str, secret_values: list[str]) -> str:
    for v in sorted(set(secret_values), key=len, reverse=True):
        if v and len(v) >= 4:
            text = text.replace(v, "[REDACTED]")
    return _HDR.sub(lambda m: m.group(1) + "[REDACTED]" + m.group(4), text)


def redact_file(path: Path, secret_values: list[str]) -> None:
    try:
        t = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return
    r = redact_text(t, secret_values)
    if r != t:
        path.write_text(r, encoding="utf-8")
