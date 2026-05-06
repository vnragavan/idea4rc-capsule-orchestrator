"""`idea4rc-capsule vault verify` — confirm Vault has every secret deploy needs.

Uses the same AppRole credentials and KV layout as `vault fetch ...`, so a
green run here is a strong guarantee that `idea4rc-capsule deploy` will be
able to read every secret it requires.

Output is a structured per-path/per-field table. Values are never printed;
only presence, length (string fields) or decoded byte size (cert fields)
are reported.

Exit codes:
    0  every expected path and field is present and well-formed
    1  one or more fields missing / empty / unreadable
    2  cert payloads present but failed --deep openssl validation
"""

from __future__ import annotations

import argparse
import base64
import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import hvac

from idea4rc_capsule.vault._common import (
    fatal,
    log,
    login_approle,
    parse_approle_file,
    revoke_self,
)
from idea4rc_capsule.vault.fetch import (
    CERT_FIELDS,
    CERTS_PATH,
    SECRET_MAP,
)


# Re-derive (sub, [field, ...]) groupings so we can show one row per Vault path.
_STRING_GROUPS: dict[str, list[tuple[str, str]]] = {}
for env_var, (sub, field) in SECRET_MAP.items():
    _STRING_GROUPS.setdefault(sub, []).append((env_var, field))


# --------------------------------------------------------------------- helpers
def _read_kv(client: hvac.Client, mount: str, base: str, sub: str) -> Optional[dict]:
    full = f"{base.strip('/')}/{sub.strip('/')}"
    try:
        return client.secrets.kv.v2.read_secret_version(
            mount_point=mount, path=full, raise_on_deleted_version=True,
        )["data"]["data"]
    except hvac.exceptions.InvalidPath:
        return None
    except Exception as exc:  # noqa: BLE001
        log(f"WARN: read failed for {mount}/{full}: {exc!r}")
        return None


def _check_pem_payload(b64: str) -> tuple[bool, int, str]:
    """Return (ok, decoded_size, reason). Cheap structural check."""
    if not b64:
        return False, 0, "empty"
    try:
        raw = base64.b64decode(b64, validate=True)
    except Exception as exc:  # noqa: BLE001
        return False, 0, f"invalid base64: {exc!r}"
    if not raw.strip():
        return False, 0, "decoded payload is empty"
    if b"-----BEGIN" not in raw or b"-----END" not in raw:
        return False, len(raw), "no PEM BEGIN/END markers"
    return True, len(raw), "ok"


def _deep_check_certs(payloads: dict[str, bytes]) -> tuple[bool, list[str]]:
    """Run openssl validations on already-decoded PEM bytes.

    Verifies (best-effort):
      * each PEM parses
      * client.cert.pem / client.key.pem moduli match
      * client.cert.pem is signed by ca.pem (chain check)
      * report subjects + expiry
    Writes PEMs to a tmpfs scratch dir and shells out to openssl.
    """
    if not shutil.which("openssl"):
        return False, ["openssl binary not found; cannot do --deep validation"]

    msgs: list[str] = []
    ok = True
    scratch = Path(tempfile.mkdtemp(prefix="vault-verify-",
                                    dir=os.environ.get("XDG_RUNTIME_DIR", "/tmp")))
    try:
        os.chmod(scratch, 0o700)
        files: dict[str, Path] = {}
        for fname, raw in payloads.items():
            p = scratch / fname
            prev_umask = os.umask(0o077)
            try:
                p.write_bytes(raw)
            finally:
                os.umask(prev_umask)
            os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)
            files[fname] = p

        for name in ("ca.pem", "client.cert.pem"):
            r = subprocess.run(
                ["openssl", "x509", "-in", str(files[name]),
                 "-noout", "-subject", "-issuer", "-enddate"],
                capture_output=True, text=True, check=False,
            )
            if r.returncode != 0:
                ok = False
                msgs.append(f"{name}: openssl x509 failed ({r.stderr.strip()})")
            else:
                for line in r.stdout.strip().splitlines():
                    msgs.append(f"  {name}: {line}")

        r = subprocess.run(
            ["openssl", "rsa", "-in", str(files["client.key.pem"]),
             "-check", "-noout"],
            capture_output=True, text=True, check=False,
        )
        if r.returncode != 0:
            ok = False
            msgs.append(f"client.key.pem: rsa -check failed ({r.stderr.strip()})")

        def _modulus_sha(args: list[str]) -> Optional[str]:
            r1 = subprocess.run(args, capture_output=True, text=True, check=False)
            if r1.returncode != 0:
                return None
            mod_line = next(
                (ln for ln in r1.stdout.splitlines() if ln.startswith("Modulus=")),
                None,
            )
            if not mod_line:
                return None
            import hashlib
            return hashlib.sha256(mod_line.encode("ascii")).hexdigest()

        cert_mod = _modulus_sha(["openssl", "x509", "-noout", "-modulus",
                                 "-in", str(files["client.cert.pem"])])
        key_mod = _modulus_sha(["openssl", "rsa", "-noout", "-modulus",
                                "-in", str(files["client.key.pem"])])
        if cert_mod and key_mod and cert_mod == key_mod:
            msgs.append("  client.cert.pem ↔ client.key.pem: modulus match (sha256)")
        else:
            ok = False
            msgs.append("  client.cert.pem ↔ client.key.pem: MODULUS MISMATCH")

        r = subprocess.run(
            ["openssl", "verify", "-CAfile", str(files["ca.pem"]),
             str(files["client.cert.pem"])],
            capture_output=True, text=True, check=False,
        )
        if r.returncode == 0 and "OK" in r.stdout:
            msgs.append("  client.cert.pem: signed by ca.pem (chain OK)")
        else:
            ok = False
            msgs.append(
                f"  client.cert.pem: chain verify FAILED "
                f"(stdout={r.stdout.strip()!r}, stderr={r.stderr.strip()!r})"
            )
    finally:
        for f in scratch.iterdir():
            try:
                if shutil.which("shred"):
                    os.system(f"shred -u {f.as_posix()!s} >/dev/null 2>&1")  # noqa: S605
                else:
                    f.unlink()
            except Exception:  # noqa: BLE001
                pass
        try:
            scratch.rmdir()
        except OSError:
            pass

    return ok, msgs


# --------------------------------------------------------------------- subcmd
def cmd_verify(args: argparse.Namespace) -> int:
    role_id, secret_id = parse_approle_file(Path(args.approle_file))
    client = login_approle(args.vault_addr, role_id, secret_id)

    string_missing: list[str] = []
    cert_missing: list[str] = []
    deep_failed = False
    decoded_certs: dict[str, bytes] = {}

    try:
        log("=== Vault secret inventory ===")
        log(f"  vault    = {args.vault_addr}")
        log(f"  mount    = {args.secret_mount}")
        log(f"  kv_base  = {args.kv_base}")
        log("")

        for sub, fields in _STRING_GROUPS.items():
            full = f"{args.kv_base.strip('/')}/{sub}"
            data = _read_kv(client, args.secret_mount, args.kv_base, sub)
            if data is None:
                log(f"  {args.secret_mount}/{full}: PATH MISSING")
                for env_var, field in fields:
                    string_missing.append(f"{full}.{field}  (env: {env_var})")
                continue
            log(f"  {args.secret_mount}/{full}:")
            for env_var, field in fields:
                val = data.get(field)
                if val is None or val == "":
                    log(f"    {field:<20s} MISSING")
                    string_missing.append(f"{full}.{field}  (env: {env_var})")
                else:
                    log(f"    {field:<20s} ok  (len={len(str(val))}, env: {env_var})")
            log("")

        full = f"{args.kv_base.strip('/')}/{CERTS_PATH}"
        data = _read_kv(client, args.secret_mount, args.kv_base, CERTS_PATH)
        if data is None:
            log(f"  {args.secret_mount}/{full}: PATH MISSING")
            for fname in CERT_FIELDS:
                cert_missing.append(f"{full}.{CERT_FIELDS[fname]}  (file: {fname})")
        else:
            log(f"  {args.secret_mount}/{full}:")
            for fname, vault_field in CERT_FIELDS.items():
                b64 = data.get(vault_field) or ""
                ok, size, reason = _check_pem_payload(b64)
                if ok:
                    log(f"    {vault_field:<22s} ok  "
                        f"(b64_len={len(b64)}, decoded={size} bytes, file: {fname})")
                    decoded_certs[fname] = base64.b64decode(b64, validate=True)
                else:
                    log(f"    {vault_field:<22s} BAD ({reason}, file: {fname})")
                    cert_missing.append(f"{full}.{vault_field}  ({reason})")
            log("")

        if args.deep and len(decoded_certs) == 3 and not cert_missing:
            log("--- deep cert validation (openssl) ---")
            ok, lines = _deep_check_certs(decoded_certs)
            for line in lines:
                log(line)
            if not ok:
                deep_failed = True
            log("")
    finally:
        revoke_self(client)

    expected_string_fields = sum(len(v) for v in _STRING_GROUPS.values())
    expected_cert_fields = len(CERT_FIELDS)
    string_ok = expected_string_fields - len(string_missing)
    cert_ok = expected_cert_fields - len(cert_missing)
    log(f"=== Result: {string_ok}/{expected_string_fields} strings, "
        f"{cert_ok}/{expected_cert_fields} cert fields present ===")

    if string_missing or cert_missing:
        log("Missing / invalid fields:")
        for m in string_missing + cert_missing:
            log(f"  - {m}")
        log("Fix with:  idea4rc-capsule vault write-secrets ...  (or `vault kv patch`)")
        return 1
    if deep_failed:
        log("Deep cert validation reported errors above. "
            "Re-issue / re-upload via `vault write-secrets --certs-dir ...`.")
        return 2
    log("All expected secrets are present and well-formed. Deploy is safe.")
    return 0


# ----------------------------------------------------------------------- CLI
def add_subcommands(parent: argparse._SubParsersAction) -> None:
    p = parent.add_parser(
        "verify",
        help="Confirm every secret deploy needs is present in Vault (no values shown)",
    )
    p.add_argument("--vault-addr",
                   default=os.environ.get("VAULT_ADDR", "http://127.0.0.1:8200"),
                   help="Vault server URL (default: $VAULT_ADDR or http://127.0.0.1:8200)")
    p.add_argument("--approle-file", default=os.path.expanduser("~/.vault-approle"),
                   help="Path to chmod-600 file with VAULT_ROLE_ID / VAULT_SECRET_ID "
                        "(default: ~/.vault-approle)")
    p.add_argument("--secret-mount", default="secret",
                   help="KV v2 mount path (default: secret)")
    p.add_argument("--kv-base", default="idea4rc-capsule",
                   help="KV path prefix under the mount (default: idea4rc-capsule)")
    p.add_argument("--deep", action="store_true",
                   help="Also run openssl validation on the cert payloads "
                        "(chain, modulus match, dates).")
    p.set_defaults(func=cmd_verify)
