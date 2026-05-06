"""`idea4rc-capsule vault write-secrets` — interactive paste-and-write tool.

Values are entered through hidden prompts and exist only in this process's
memory and in Vault. Nothing secret is written to disk locally; the script
itself contains no values and does not need to be deleted afterwards.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Optional

import hvac

from idea4rc_capsule.vault._common import fatal, log


# Each entry: (kv_subpath, [(field, prompt, hidden_input?)])
PROMPTS = [
    ("capsule", [
        ("pubIp",         "CAPSULE_PUB_IP (public IP/FQDN)",                        False),
    ]),
    ("vantage6", [
        ("apiKey",        "V6NODE_NODE_APIKEY (Vantage 6 API key)",                 True),
        ("nodeName",      "V6NODE_NODE_NAME (Vantage 6 node name)",                 False),
        ("k8sNodeName",   "V6NODE_NODE_K8S_NODENAME (microk8s.kubectl get node)",   False),
    ]),
    ("keycloak", [
        ("clientId",      "FCBEXEC_KEYCLOAK_CLIENTID (Keycloak client id)",         False),
        ("clientSecret",  "FCBEXEC_KEYCLOAK_CLIENTSECRET (Keycloak client secret)", True),
        ("host",          "FCBEXEC_KEYCLOAK_HOST (Keycloak URL)",                   False),
    ]),
    ("kafka", [
        ("clientId",      "FCBEXEC_KAFKA_CLIENTID (Kafka client id)",               False),
        ("consumerId",    "FCBEXEC_KAFKA_CONSUMERID (Kafka consumer id)",           False),
    ]),
]

CERT_FIELDS = {
    "ca.pem":          "ca.pem.b64",
    "client.cert.pem": "client.cert.pem.b64",
    "client.key.pem":  "client.key.pem.b64",
}


def _prompt(label: str, hidden: bool, default: Optional[str] = None) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        val = (getpass.getpass(f"{label}{suffix}: ") if hidden
               else input(f"{label}{suffix}: "))
        if not val and default is not None:
            return default
        if val:
            return val
        log("Empty value not allowed. Try again.")


def _resolve_token(args: argparse.Namespace) -> str:
    if args.token:
        return args.token
    env_tok = os.environ.get("VAULT_TOKEN")
    if env_tok:
        return env_tok
    if args.from_init_output:
        data = json.loads(Path(args.from_init_output).read_text())
        tok = data.get("root_token")
        if tok:
            return tok
    return getpass.getpass("Vault token (hidden): ").strip()


def _make_client(addr: str, token: str) -> hvac.Client:
    c = hvac.Client(url=addr, token=token, timeout=15)
    if not c.is_authenticated():
        fatal("Token not accepted by Vault.")
    return c


def _gather_values() -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    log("Paste each value when prompted. Hidden inputs do not echo to the screen.")
    log("Press Ctrl-C to abort without writing anything.")
    print("", file=sys.stderr)
    for sub, fields in PROMPTS:
        print(f"--- {sub} ---", file=sys.stderr)
        out.setdefault(sub, {})
        for field, prompt, hidden in fields:
            out[sub][field] = _prompt(prompt, hidden)
        print("", file=sys.stderr)
    return out


def _gather_certs(certs_dir: Optional[Path]) -> Optional[dict[str, str]]:
    if certs_dir is None:
        return None
    if not certs_dir.is_dir():
        fatal(f"--certs-dir not found: {certs_dir}")
    payload: dict[str, str] = {}
    for fname, vault_field in CERT_FIELDS.items():
        path = certs_dir / fname
        if not path.is_file():
            fatal(f"missing cert file: {path}")
        data = path.read_bytes()
        if not data.strip():
            fatal(f"empty cert file: {path}")
        payload[vault_field] = base64.b64encode(data).decode("ascii")
        log(f"loaded {path} ({len(data)} bytes)")
    return payload


def _shred_dir(d: Path) -> None:
    """Securely remove only the known cert files from `d`.

    Earlier versions iterated over every file in the directory, which was
    surprising when --certs-dir pointed at a shared dir (e.g. the helm
    chart's utils/ folder also containing the create-secret script).
    We now shred exactly the three files we read in `_gather_certs()`
    and only rmdir() the parent if it is then empty.
    """
    if not d.is_dir():
        return
    shred_bin = shutil.which("shred")
    for fname in CERT_FIELDS:
        f = d / fname
        if not f.is_file():
            continue
        try:
            if shred_bin:
                os.system(f"{shred_bin} -u {f.as_posix()!s} >/dev/null 2>&1")  # noqa: S605
            else:
                f.unlink()
        except Exception as exc:  # noqa: BLE001
            log(f"WARN: could not remove {f}: {exc!r}")
    try:
        d.rmdir()  # only succeeds if empty (other files were preserved)
    except OSError:
        pass


def _write_kv(client: hvac.Client, mount: str, base: str, sub: str, payload: dict) -> None:
    full = f"{base.strip('/')}/{sub.strip('/')}"
    log(f"writing {mount}/{full} ({len(payload)} fields)")
    client.secrets.kv.v2.create_or_update_secret(
        mount_point=mount, path=full, secret=payload,
    )


def cmd_write_secrets(args: argparse.Namespace) -> int:
    token = _resolve_token(args)
    if not token:
        fatal("No token provided.")
    client = _make_client(args.vault_addr, token)

    values = _gather_values()
    cert_payload = _gather_certs(args.certs_dir)

    if args.dry_run:
        log("Dry-run: would write the following paths (no values shown):")
        for sub, fields in values.items():
            log(f"  {args.secret_mount}/{args.kv_base}/{sub}: keys={sorted(fields.keys())}")
        if cert_payload is not None:
            log(f"  {args.secret_mount}/{args.kv_base}/certs/query-executor: "
                f"keys={sorted(cert_payload.keys())}")
        log("Dry-run complete. Nothing was written.")
        return 0

    log("Writing values to Vault...")
    for sub, fields in values.items():
        _write_kv(client, args.secret_mount, args.kv_base, sub, fields)
    if cert_payload is not None:
        _write_kv(client, args.secret_mount, args.kv_base, "certs/query-executor", cert_payload)

    log("All values written successfully.")

    if args.shred_certs and args.certs_dir is not None:
        log(f"Shredding {args.certs_dir}")
        _shred_dir(args.certs_dir)

    log(f"Done. Verify with: vault kv get {args.secret_mount}/{args.kv_base}/capsule")
    return 0


# ----------------------------------------------------------------------- CLI
def add_subcommands(parent: argparse._SubParsersAction) -> None:
    p = parent.add_parser("write-secrets",
                          help="Interactively push capsule secrets into Vault")
    p.add_argument("--vault-addr",
                   default=os.environ.get("VAULT_ADDR", "http://127.0.0.1:8200"))
    p.add_argument("--secret-mount", default="secret")
    p.add_argument("--kv-base", default="idea4rc-capsule")
    p.add_argument("--token", help="Vault token (else $VAULT_TOKEN or --from-init-output)")
    p.add_argument("--from-init-output",
                   help="JSON file from `idea4rc-capsule vault bootstrap init`")
    p.add_argument("--certs-dir", type=Path,
                   help="Directory with ca.pem / client.cert.pem / client.key.pem")
    p.add_argument("--shred-certs", action="store_true",
                   help="After successful upload, shred the cert files in --certs-dir")
    p.add_argument("--dry-run", action="store_true",
                   help="Prompt for everything but do not write to Vault")
    p.set_defaults(func=cmd_write_secrets)
