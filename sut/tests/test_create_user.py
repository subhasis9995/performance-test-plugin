"""create_test_user.py registers a working user, stores creds only in the env file, never prints the password."""
from __future__ import annotations

import subprocess
import sys

import httpx
import pytest

from conftest import SUT

pytestmark = pytest.mark.live

SCRIPT = SUT / "fixtures" / "create_test_user.py"


def read_env(path) -> dict[str, str]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            k, _, v = line.partition("=")
            out[k] = v
    return out


def test_create_user_writes_env_and_hides_password(juice_shop, tmp_path):
    env_file = tmp_path / ".env.eval"
    r = subprocess.run([sys.executable, str(SCRIPT), "--base-url", juice_shop, "--env-file", str(env_file)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    env = read_env(env_file)
    for key in ("JUICE_SHOP_BASE_URL", "JUICE_SHOP_USER_EMAIL", "JUICE_SHOP_USER_PASSWORD", "JUICE_SHOP_USER_ID", "JUICE_SHOP_BASKET_ID"):
        assert env.get(key), key
    pw = env["JUICE_SHOP_USER_PASSWORD"]
    assert len(pw) >= 16
    assert pw not in r.stdout and pw not in r.stderr

    login = httpx.post(juice_shop + "/rest/user/login", json={"email": env["JUICE_SHOP_USER_EMAIL"], "password": pw}, timeout=10)
    assert login.status_code == 200
    auth = login.json()["authentication"]
    assert str(auth["bid"]) == env["JUICE_SHOP_BASKET_ID"]
    basket = httpx.get(f"{juice_shop}/rest/basket/{auth['bid']}", headers={"Authorization": f"Bearer {auth['token']}"}, timeout=10)
    assert basket.status_code == 200


def test_default_env_file_is_gitignored():
    target = SUT / "fixtures" / ".env.eval"
    r = subprocess.run(["git", "check-ignore", "-q", str(target)], cwd=SUT)
    assert r.returncode == 0, "sut/fixtures/.env.eval must be gitignored"


def test_failure_does_not_leak(tmp_path):
    env_file = tmp_path / ".env.eval"
    r = subprocess.run([sys.executable, str(SCRIPT), "--base-url", "http://127.0.0.1:9", "--env-file", str(env_file)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 1 and not env_file.exists()
