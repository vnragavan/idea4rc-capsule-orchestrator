"""`idea4rc-capsule vault bootstrap ...` — first-run / admin Vault operations."""

from __future__ import annotations

import argparse
import getpass
import json
import os
from importlib import resources
from pathlib import Path

from idea4rc_capsule.vault._common import (
    chmod_600,
    fatal,
    log,
    make_client,
    wait_reachable,
)


def _packaged_policy_path() -> Path:
    """Return path to the packaged Vault policy HCL file."""
    res = resources.files("idea4rc_capsule.data") / "policy-capsule-readonly.hcl"
    if not res.is_file():  # pragma: no cover
        fatal(f"packaged policy file missing: {res}")
    return Path(str(res))


def resolve_token(args: argparse.Namespace) -> str:
    if getattr(args, "token", None):
        return args.token
    env_tok = os.environ.get("VAULT_TOKEN")
    if env_tok:
        return env_tok
    if getattr(args, "from_init_output", None):
        data = json.loads(Path(args.from_init_output).read_text())
        tok = data.get("root_token")
        if tok:
            return tok
    log("Token not supplied via --token, VAULT_TOKEN, or --from-init-output.")
    return getpass.getpass("Vault token (hidden): ").strip()


# --------------------------------------------------------------------- subcmds
def cmd_status(args: argparse.Namespace) -> int:
    client = make_client(args.addr)
    wait_reachable(client)
    s = client.sys.read_seal_status()
    print(json.dumps({
        "initialized": s.get("initialized"),
        "sealed": s.get("sealed"),
        "version": s.get("version"),
    }, indent=2))
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    client = make_client(args.addr)
    wait_reachable(client)
    if client.sys.is_initialized():
        log("Vault already initialized. Skipping.")
        return 0

    log(f"Initializing Vault: shares={args.key_shares} threshold={args.key_threshold}")
    result = client.sys.initialize(
        secret_shares=args.key_shares,
        secret_threshold=args.key_threshold,
    )
    payload = {
        "unseal_keys_b64": result["keys_base64"],
        "unseal_keys_hex": result["keys"],
        "root_token": result["root_token"],
    }

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2))
        chmod_600(out)
        log(f"Init output written to {out} (chmod 600).")
        log("MOVE THIS FILE TO SECURE STORAGE — it contains all unseal keys + root token.")
    else:
        log("================== VAULT INIT (one-shot output) ==================")
        print(json.dumps(payload, indent=2))
        log("===================================================================")
        log("Copy the JSON above into your password manager NOW.")
    return 0


def cmd_unseal(args: argparse.Namespace) -> int:
    client = make_client(args.addr)
    wait_reachable(client)
    s = client.sys.read_seal_status()
    if not s.get("sealed"):
        log("Vault already unsealed.")
        return 0
    threshold = int(s.get("t") or 3)

    keys: list[str] = []
    if args.from_init_output:
        data = json.loads(Path(args.from_init_output).read_text())
        keys = list(data.get("unseal_keys_b64") or [])[:threshold]
        if len(keys) < threshold:
            fatal(f"Init output has {len(keys)} keys; need {threshold}.")
        log(f"Auto-unsealing using {threshold} keys from {args.from_init_output}")
    else:
        log(f"Vault is sealed. Please paste {threshold} unseal key(s) (input hidden).")
        for i in range(1, threshold + 1):
            k = getpass.getpass(f"Unseal key {i}/{threshold}: ")
            if not k:
                fatal("Empty unseal key.")
            keys.append(k)

    for k in keys:
        client.sys.submit_unseal_key(k)

    if client.sys.read_seal_status().get("sealed"):
        fatal("Vault is still sealed after submitting keys.")
    log("Vault unsealed.")
    return 0


def cmd_configure(args: argparse.Namespace) -> int:
    token = resolve_token(args)
    if not token:
        fatal("Empty token.")
    client = make_client(args.addr, token=token)
    wait_reachable(client)
    if not client.is_authenticated():
        fatal("Token rejected by Vault.")

    mounts = client.sys.list_mounted_secrets_engines()["data"]
    mount_key = f"{args.secret_mount}/"
    if mount_key in mounts:
        cur = mounts[mount_key]
        if cur.get("type") != "kv":
            fatal(f"Mount {mount_key} exists with type={cur.get('type')}, expected kv.")
        log(f"KV mount {mount_key} already enabled (type=kv).")
    else:
        log(f"Enabling KV v2 at {mount_key}")
        client.sys.enable_secrets_engine(
            backend_type="kv",
            path=args.secret_mount,
            options={"version": "2"},
        )

    auths = client.sys.list_auth_methods()["data"]
    if "approle/" in auths:
        log("AppRole auth already enabled.")
    else:
        log("Enabling AppRole auth")
        client.sys.enable_auth_method("approle")

    policy_path = Path(args.policy_file) if args.policy_file else _packaged_policy_path()
    if not policy_path.is_file():
        fatal(f"Policy file not found: {policy_path}")
    log(f"Writing policy '{args.policy_name}' from {policy_path}")
    client.sys.create_or_update_policy(
        name=args.policy_name,
        policy=policy_path.read_text(),
    )

    log(f"Creating/updating AppRole '{args.role_name}'")
    client.auth.approle.create_or_update_approle(
        role_name=args.role_name,
        token_policies=[args.policy_name],
        token_ttl=args.token_ttl,
        token_max_ttl=args.token_max_ttl,
        secret_id_ttl="0",
        secret_id_num_uses=0,
        bind_secret_id=True,
    )

    base = args.kv_base.strip("/")
    for p in ("capsule", "vantage6", "keycloak", "kafka", "certs/query-executor"):
        try:
            client.secrets.kv.v2.create_or_update_secret(
                mount_point=args.secret_mount,
                path=f"{base}/{p}",
                secret={"placeholder": True},
            )
        except Exception as exc:  # noqa: BLE001
            log(f"WARN: could not pre-create {base}/{p}: {exc!r}")

    role_id = client.auth.approle.read_role_id(role_name=args.role_name)["data"]["role_id"]
    sid = client.auth.approle.generate_secret_id(role_name=args.role_name)["data"]["secret_id"]
    log("================== AppRole credentials ==================")
    if args.output_approle:
        out = Path(args.output_approle)
        out.parent.mkdir(parents=True, exist_ok=True)
        prev_umask = os.umask(0o077)
        try:
            out.write_text(
                f"VAULT_ROLE_ID={role_id}\n"
                f"VAULT_SECRET_ID={sid}\n"
            )
        finally:
            os.umask(prev_umask)
        chmod_600(out)
        log(f"Wrote AppRole credentials to {out} (chmod 600).")
        log(f"Verify with:  idea4rc-capsule vault fetch ping --approle-file {out}")
    else:
        log("Save the following two lines to ~/.vault-approle (chmod 600):")
        log("")
        print(f"VAULT_ROLE_ID={role_id}")
        print(f"VAULT_SECRET_ID={sid}")
        log("")
        log("Or re-run with --output-approle ~/.vault-approle to write them directly.")
    log("=========================================================")
    log("VAULT_SECRET_ID is shown ONCE. Rotate later via 'vault bootstrap rotate-secret-id'.")
    return 0


def cmd_rotate_secret_id(args: argparse.Namespace) -> int:
    token = resolve_token(args)
    client = make_client(args.addr, token=token)
    wait_reachable(client)
    if not client.is_authenticated():
        fatal("Token rejected by Vault.")
    role_id = client.auth.approle.read_role_id(role_name=args.role_name)["data"]["role_id"]
    sid = client.auth.approle.generate_secret_id(role_name=args.role_name)["data"]["secret_id"]
    log("New AppRole SECRET_ID generated.")
    if args.output_approle:
        out = Path(args.output_approle)
        out.parent.mkdir(parents=True, exist_ok=True)
        prev_umask = os.umask(0o077)
        try:
            out.write_text(
                f"VAULT_ROLE_ID={role_id}\n"
                f"VAULT_SECRET_ID={sid}\n"
            )
        finally:
            os.umask(prev_umask)
        chmod_600(out)
        log(f"Updated {out} (chmod 600).")
        log(f"Verify with:  idea4rc-capsule vault fetch ping --approle-file {out}")
    else:
        log("Update ~/.vault-approle:")
        log("")
        print(f"VAULT_SECRET_ID={sid}")
        log("")
        log("Replace the existing VAULT_SECRET_ID line, e.g.:")
        log("  sed -i 's|^VAULT_SECRET_ID=.*|VAULT_SECRET_ID=<paste above>|' ~/.vault-approle")
        log("Then verify:  idea4rc-capsule vault fetch ping --approle-file ~/.vault-approle")
        log("Or re-run with --output-approle ~/.vault-approle to overwrite the file directly.")
    return 0


def cmd_all(args: argparse.Namespace) -> int:
    rc = cmd_init(args)
    if rc != 0:
        return rc
    rc = cmd_unseal(args)
    if rc != 0:
        return rc
    return cmd_configure(args)


# ----------------------------------------------------------------------- CLI
def add_subcommands(parent: argparse._SubParsersAction) -> None:
    p = parent.add_parser("bootstrap",
                          help="Init/unseal/configure Vault (admin operations)")
    p.add_argument("--addr", default=os.environ.get("VAULT_ADDR", "http://127.0.0.1:8200"),
                   help="Vault server URL (default: $VAULT_ADDR or http://127.0.0.1:8200)")
    sub = p.add_subparsers(dest="subcmd", required=True)

    sp = sub.add_parser("status", help="Show seal status (no secrets)")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("init", help="Initialize Vault (one-shot output)")
    sp.add_argument("--key-shares", type=int, default=5)
    sp.add_argument("--key-threshold", type=int, default=3)
    sp.add_argument("--output", help="Write init JSON to this file (chmod 600)")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("unseal", help="Submit unseal keys (interactive)")
    sp.add_argument("--from-init-output",
                    help="Auto-unseal using keys from init JSON file (dev only)")
    sp.set_defaults(func=cmd_unseal)

    sp = sub.add_parser("configure",
                        help="Enable KV+AppRole, write policy, create role, print creds")
    sp.add_argument("--secret-mount", default="secret")
    sp.add_argument("--kv-base", default="idea4rc-capsule")
    sp.add_argument("--policy-name", default="capsule-readonly")
    sp.add_argument("--role-name", default="capsule-installer")
    sp.add_argument("--policy-file",
                    help="Path to policy HCL (default: packaged policy-capsule-readonly.hcl)")
    sp.add_argument("--token-ttl", default="30m")
    sp.add_argument("--token-max-ttl", default="1h")
    sp.add_argument("--token", help="Vault token (else $VAULT_TOKEN or --from-init-output)")
    sp.add_argument("--from-init-output", help="Read root_token from init JSON file")
    sp.add_argument("--output-approle",
                    help="Write VAULT_ROLE_ID/VAULT_SECRET_ID to this file (chmod 600) "
                         "instead of printing to stdout. Recommended.")
    sp.set_defaults(func=cmd_configure)

    sp = sub.add_parser("rotate-secret-id", help="Generate a new secret_id for the AppRole")
    sp.add_argument("--role-name", default="capsule-installer")
    sp.add_argument("--token")
    sp.add_argument("--from-init-output")
    sp.add_argument("--output-approle",
                    help="Overwrite this file with the new VAULT_ROLE_ID/VAULT_SECRET_ID "
                         "(chmod 600). Same path you pass to --approle-file at runtime.")
    sp.set_defaults(func=cmd_rotate_secret_id)

    sp = sub.add_parser("all", help="init + unseal + configure (interactive end-to-end)")
    sp.add_argument("--key-shares", type=int, default=5)
    sp.add_argument("--key-threshold", type=int, default=3)
    sp.add_argument("--output", help="Write init JSON to this file (chmod 600)")
    sp.add_argument("--from-init-output",
                    help="Read keys+root_token from init JSON file (instead of prompting)")
    sp.add_argument("--secret-mount", default="secret")
    sp.add_argument("--kv-base", default="idea4rc-capsule")
    sp.add_argument("--policy-name", default="capsule-readonly")
    sp.add_argument("--role-name", default="capsule-installer")
    sp.add_argument("--policy-file",
                    help="Path to policy HCL (default: packaged policy-capsule-readonly.hcl)")
    sp.add_argument("--token-ttl", default="30m")
    sp.add_argument("--token-max-ttl", default="1h")
    sp.add_argument("--token")
    sp.add_argument("--output-approle",
                    help="Write VAULT_ROLE_ID/VAULT_SECRET_ID to this file (chmod 600).")
    sp.set_defaults(func=cmd_all)
