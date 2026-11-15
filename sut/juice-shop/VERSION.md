# Juice Shop version pin

| Item | Value |
|---|---|
| Version | **OWASP Juice Shop v20.4.0** (released 2026-11-08, latest stable at time of pinning, 2026-11-15) |
| Node requirement | `engines.node: "22 - 26"` (package.json). Release ships prebuilt archives for Node 22, 24 and 26. |
| Node used here | v24.14.0 (Windows 11, x64) |
| Asset (this machine) | `juice-shop-20.4.0_node24_win32_x64.zip` (134,942,660 bytes) |
| Checksum | MD5 `970d27e21515eb610d84d9709edf6f74`, pinned in `install.py` and cross-checked against the release's published `.md5` asset at install time |
| Install dir | `sut/.cache/juice-shop/20.4.0/` (gitignored) |
| Reported version | `GET /rest/admin/application-version` returns `{"version":"20.4.0-SNAPSHOT"}` for the packaged build |

## How the install works

`install.py --install` picks the packaged release archive that matches the local OS
(`win32` / `linux` / `darwin`), arch and Node major, so no `npm install` or native
compilation is needed. Upstream publishes MD5 only (no SHA-256). Assets for other
platforms are verified against the published `.md5`; only the Windows/Node 24 MD5 is
pinned. Add more entries to `PINNED_MD5` when you pin another platform.

The zip has paths longer than Windows MAX_PATH (260), so extraction uses `\\?\` long paths.

## Binding

Juice Shop calls `server.listen(port)` with no host, which binds every interface.
`install.py --start` runs `node -r sut/juice-shop/bind_localhost.cjs build/app.js`. That
preload script patches `net.Server.prototype.listen` so the server binds `127.0.0.1` only
(override with `JUICE_SHOP_BIND_HOST`). `test_lifecycle.py` checks the port is closed on the LAN address.

## Upgrading

1. Change `JUICE_SHOP_VERSION` in `install.py`, and replace `PINNED_MD5` with the new
   asset's value from `https://github.com/juice-shop/juice-shop/releases/download/v<ver>/<asset>.md5`.
2. `python sut/juice-shop/install.py --install`, then `uv run --project sut pytest sut/tests`.
3. Update this file and `ROUTES.md`. Route list source: `server.ts` at the tag.
