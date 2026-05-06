"""Shared helpers for the vault subcommand group.

Logging goes to stderr so stdout can carry structured output (e.g. env-file
content). No secret values are ever logged; helpers go to lengths to keep
them in memory only.
"""

from __future__ import annotations

import os
import re
import stat
import sys
import time
from pathlib import Path
from typing import Optional

try:
    import hvac
except ImportError as exc:  # pragma: no cover - import guard
    raise SystemExit(
        "ERROR: hvac is not installed in this environment. "
        "Reinstall idea4rc-capsule from the package checkout (e.g. `pipx install . --force`)."
    ) from exc


__all__ = [
    "log",
    "fatal",
    "make_client",
    "wait_reachable",
    "chmod_600",
    "shell_quote",
    "parse_approle_file",
    "login_approle",
    "revoke_self",
]


# ---------------------------------------------------------------------- logging
_PREFIX = os.environ.get("IDEA4RC_VAULT_LOG_PREFIX", "idea4rc-capsule:vault")


def log(msg: str) -> None:
    print(f"[{_PREFIX}] {msg}", file=sys.stderr, flush=True)


def fatal(msg: str, code: int = 1) -> None:
    print(f"[{_PREFIX}] FATAL: {msg}", file=sys.stderr, flush=True)
    sys.exit(code)


# ---------------------------------------------------------------------- vault
def make_client(addr: str, token: Optional[str] = None) -> "hvac.Client":
    return hvac.Client(url=addr, token=token, timeout=15)


def wait_reachable(client: "hvac.Client", timeout: int = 60) -> None:
    """Poll Vault until /sys/seal-status responds. Reachable means initialized
    or not, sealed or not — anything except network/HTTP failure."""
    start = time.time()
    last_err: Optional[Exception] = None
    while time.time() - start < timeout:
        try:
            client.sys.read_seal_status()
            return
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            time.sleep(2)
    fatal(f"Vault not reachable at {client.url} within {timeout}s ({last_err!r})")


def login_approle(addr: str, role_id: str, secret_id: str) -> "hvac.Client":
    client = make_client(addr)
    if not client.sys.is_initialized():
        fatal(f"Vault at {addr} is not initialized")
    if client.sys.read_seal_status().get("sealed"):
        fatal(f"Vault at {addr} is sealed; unseal it first")
    try:
        resp = client.auth.approle.login(role_id=role_id, secret_id=secret_id)
    except Exception as exc:  # noqa: BLE001
        fatal(f"AppRole login failed: {exc!r}")
    client.token = resp["auth"]["client_token"]
    if not client.is_authenticated():
        fatal("AppRole login returned a token but it failed to authenticate")
    return client


def revoke_self(client: "hvac.Client") -> None:
    if not client.token:
        return
    try:
        client.auth.token.revoke_self()
    except Exception as exc:  # noqa: BLE001
        log(f"WARN: token revoke-self failed: {exc!r}")


# ---------------------------------------------------------------------- files
def chmod_600(path: Path) -> None:
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def shell_quote(s: str) -> str:
    """Single-quote a string for safe POSIX shell `source`."""
    return "'" + s.replace("'", "'\\''") + "'"


_APPROLE_LINE_RE = re.compile(
    r"^\s*(VAULT_ROLE_ID|VAULT_SECRET_ID)\s*=\s*(.+?)\s*$"
)


def parse_approle_file(path: Path) -> tuple[str, str]:
    """Read role_id / secret_id from a chmod-600 file with shell KEY=VALUE lines.

    Accepts both bare (`KEY=value`) and quoted (`KEY="value"` / `KEY='value'`)
    forms. Permissions must be 0600 or 0400 — anything more permissive is a
    warning.
    """
    if not path.is_file():
        fatal(f"approle file not found: {path}")
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode not in (0o600, 0o400):
        log(f"WARN: {path} mode is {oct(mode)}; recommend 600")

    role_id: Optional[str] = None
    secret_id: Optional[str] = None
    for raw in path.read_text().splitlines():
        if not raw or raw.lstrip().startswith("#"):
            continue
        m = _APPROLE_LINE_RE.match(raw)
        if not m:
            continue
        key, val = m.group(1), m.group(2)
        if (val.startswith('"') and val.endswith('"')) or \
           (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
        if key == "VAULT_ROLE_ID":
            role_id = val
        elif key == "VAULT_SECRET_ID":
            secret_id = val

    if not role_id or not secret_id:
        fatal(f"{path} must define both VAULT_ROLE_ID and VAULT_SECRET_ID")
    return role_id, secret_id
