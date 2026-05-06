"""`idea4rc-capsule vault fetch ...` — runtime helper for the deploy/ingest pipeline.

Each subcommand opens a short-lived AppRole session, fetches what's needed,
writes outputs (chmod 600 file or chmod 700 dir), revokes the token, and
exits. The token never touches disk.

The orchestrator typically calls these primitives in-process via
`fetch_install_secrets()` in `idea4rc_capsule.vault_cmd`, but the CLI
form is preserved so partners and ad-hoc operators can invoke them
directly.
"""

from __future__ import annotations

import argparse
import base64
import os
import stat
from pathlib import Path

import hvac

from idea4rc_capsule.vault._common import (
    chmod_600,
    fatal,
    log,
    login_approle,
    parse_approle_file,
    revoke_self,
    shell_quote,
)


# Capsule-specific schema (Vault KV paths and field names).
# All paths are relative to <secret_mount>/<kv_base>/.
SECRET_MAP = {
    "CAPSULE_PUB_IP":                ("capsule",  "pubIp"),
    "V6NODE_NODE_APIKEY":            ("vantage6", "apiKey"),
    "V6NODE_NODE_NAME":              ("vantage6", "nodeName"),
    "V6NODE_NODE_K8S_NODENAME":      ("vantage6", "k8sNodeName"),
    "FCBEXEC_KEYCLOAK_CLIENTID":     ("keycloak", "clientId"),
    "FCBEXEC_KEYCLOAK_CLIENTSECRET": ("keycloak", "clientSecret"),
    "FCBEXEC_KEYCLOAK_HOST":         ("keycloak", "host"),
    "FCBEXEC_KAFKA_CLIENTID":        ("kafka",    "clientId"),
    "FCBEXEC_KAFKA_CONSUMERID":      ("kafka",    "consumerId"),
}

CERTS_PATH = "certs/query-executor"
CERT_FIELDS = {
    "ca.pem":          "ca.pem.b64",
    "client.cert.pem": "client.cert.pem.b64",
    "client.key.pem":  "client.key.pem.b64",
}


# ---------------------------------------------------------------------- helpers
def _kv_field(client: hvac.Client, mount: str, base: str, sub: str, field: str) -> str:
    full_path = f"{base.strip('/')}/{sub.strip('/')}"
    try:
        data = client.secrets.kv.v2.read_secret_version(
            mount_point=mount, path=full_path, raise_on_deleted_version=True,
        )["data"]["data"]
    except hvac.exceptions.InvalidPath:
        fatal(f"KV path not found: {mount}/{full_path}")
    except Exception as exc:  # noqa: BLE001
        fatal(f"Failed to read {mount}/{full_path}: {exc!r}")
    val = data.get(field)
    if val is None or val == "":
        fatal(f"Missing field '{field}' at {mount}/{full_path}")
    return str(val)


# ---------------------------------------------------------------------- subcmds
def cmd_ping(args: argparse.Namespace) -> int:
    role_id, secret_id = parse_approle_file(Path(args.approle_file))
    client = login_approle(args.vault_addr, role_id, secret_id)
    try:
        info = client.auth.token.lookup_self()["data"]
        log(f"Login OK. token TTL={info.get('ttl')}s policies={info.get('policies')}")
    finally:
        revoke_self(client)
    return 0


def cmd_secrets(args: argparse.Namespace) -> int:
    role_id, secret_id = parse_approle_file(Path(args.approle_file))
    client = login_approle(args.vault_addr, role_id, secret_id)
    try:
        log("Fetching capsule install secrets")
        env: dict[str, str] = {}
        for var, (sub, field) in SECRET_MAP.items():
            env[var] = _kv_field(client, args.secret_mount, args.kv_base, sub, field)

        out = Path(args.out_env)
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(out.suffix + ".tmp")
        prev_umask = os.umask(0o077)
        try:
            with tmp.open("w") as fh:
                fh.write("# idea4rc-capsule output — chmod 600 — delete after sourcing\n")
                for k, v in env.items():
                    fh.write(f"{k}={shell_quote(v)}\n")
        finally:
            os.umask(prev_umask)
        os.replace(tmp, out)
        chmod_600(out)
        log(f"Wrote env file to {out} ({len(env)} variables, values not logged)")
    finally:
        revoke_self(client)
    return 0


def cmd_certs(args: argparse.Namespace) -> int:
    role_id, secret_id = parse_approle_file(Path(args.approle_file))
    client = login_approle(args.vault_addr, role_id, secret_id)
    try:
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(out_dir, 0o700)
        log(f"Fetching Query Executor certs into {out_dir}")
        for fname, field in CERT_FIELDS.items():
            b64 = _kv_field(client, args.secret_mount, args.kv_base, CERTS_PATH, field)
            try:
                pem = base64.b64decode(b64, validate=True)
            except Exception as exc:  # noqa: BLE001
                fatal(f"{field}: invalid base64 ({exc!r})")
            if not pem.strip():
                fatal(f"{field}: decoded content is empty")
            target = out_dir / fname
            prev_umask = os.umask(0o077)
            try:
                target.write_bytes(pem)
            finally:
                os.umask(prev_umask)
            os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)
        log(f"Cert files staged ({list(CERT_FIELDS.keys())})")
    finally:
        revoke_self(client)
    return 0


# ----------------------------------------------------------------------- CLI
def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--vault-addr",
                   default=os.environ.get("VAULT_ADDR", "http://127.0.0.1:8200"),
                   help="Vault server URL (default: $VAULT_ADDR)")
    p.add_argument("--approle-file", required=True,
                   help="Path to chmod-600 file with VAULT_ROLE_ID / VAULT_SECRET_ID")
    p.add_argument("--secret-mount", default="secret",
                   help="KV v2 mount path (default: secret)")
    p.add_argument("--kv-base", default="idea4rc-capsule",
                   help="KV path prefix under the mount (default: idea4rc-capsule)")


def add_subcommands(parent: argparse._SubParsersAction) -> None:
    p = parent.add_parser("fetch",
                          help="Fetch capsule secrets / certs from Vault (runtime helper)")
    sub = p.add_subparsers(dest="subcmd", required=True)

    sp = sub.add_parser("ping", help="Verify Vault is reachable + AppRole works")
    _add_common(sp)
    sp.set_defaults(func=cmd_ping)

    sp = sub.add_parser("secrets", help="Write capsule install secrets to an env file")
    _add_common(sp)
    sp.add_argument("--out-env", required=True,
                    help="Path to write env file (chmod 600). Bash sources it.")
    sp.set_defaults(func=cmd_secrets)

    sp = sub.add_parser("certs", help="Write Query Executor PEM certs to a directory")
    _add_common(sp)
    sp.add_argument("--out-dir", required=True,
                    help="Directory (preferably tmpfs) to write cert files into")
    sp.set_defaults(func=cmd_certs)
