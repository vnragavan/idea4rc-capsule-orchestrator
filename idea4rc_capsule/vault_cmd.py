"""`idea4rc-capsule vault ...` — registers the embedded vault subcommands.

The vault tooling lives in ``idea4rc_capsule.vault`` (folded in from the
former ``idea4rc-vault`` package). This module wires those subcommands
into the top-level CLI and exposes high-level helpers used by the
deploy/ingest pipeline:

* :func:`fetch_install_secrets`: loads ``CAPSULE_PUB_IP`` / ``V6NODE_*`` /
  ``FCBEXEC_*`` from Vault into a Python dict (kept in memory only).
* :func:`fetch_query_executor_certs`: stages the Query Executor PEM files
  onto tmpfs.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from idea4rc_capsule.config import Config
from idea4rc_capsule.logging import fatal, log
from idea4rc_capsule.vault import bootstrap as _v_bootstrap
from idea4rc_capsule.vault import fetch as _v_fetch
from idea4rc_capsule.vault import install_server as _v_install
from idea4rc_capsule.vault import verify as _v_verify
from idea4rc_capsule.vault import write_secrets as _v_write


def add_subcommands(parent: "argparse._SubParsersAction") -> None:
    p = parent.add_parser(
        "vault",
        help="Vault operations (install / bootstrap / write-secrets / fetch / verify)",
    )
    sub = p.add_subparsers(dest="vault_subcmd", required=True)
    _v_install.add_subcommands(sub)
    _v_bootstrap.add_subcommands(sub)
    _v_write.add_subcommands(sub)
    _v_fetch.add_subcommands(sub)
    _v_verify.add_subcommands(sub)


# --------------------------------------------------------------------- helpers
def fetch_install_secrets(cfg: Config, *, dry_run: bool = False) -> dict[str, str]:
    """Return install-time secrets as a plain dict.

    When ``vault.enabled``, opens a short-lived AppRole session via the
    embedded fetch APIs (token revoked before return). Otherwise, returns
    the plaintext values from ``[fallback_secrets]`` + the capsule public
    IP from ``[capsule_install]``.
    """
    if dry_run:
        return {"CAPSULE_PUB_IP": cfg.capsule_install.public_ip or "dryrun.example",
                "V6NODE_NODE_APIKEY": "dryrun",
                "V6NODE_NODE_NAME": "dryrun",
                "V6NODE_NODE_K8S_NODENAME": "dryrun",
                "FCBEXEC_KEYCLOAK_CLIENTID": "dryrun",
                "FCBEXEC_KEYCLOAK_CLIENTSECRET": "dryrun",
                "FCBEXEC_KEYCLOAK_HOST": "dryrun",
                "FCBEXEC_KAFKA_CLIENTID": "dryrun",
                "FCBEXEC_KAFKA_CONSUMERID": "dryrun"}

    if not cfg.vault.enabled:
        fb = cfg.fallback_secrets
        return {
            "CAPSULE_PUB_IP":                 cfg.capsule_install.public_ip,
            "V6NODE_NODE_APIKEY":             fb.v6node_apikey,
            "V6NODE_NODE_NAME":               fb.v6node_name,
            "V6NODE_NODE_K8S_NODENAME":       fb.v6node_k8s_nodename,
            "FCBEXEC_KEYCLOAK_CLIENTID":      fb.fcbexec_keycloak_clientid,
            "FCBEXEC_KEYCLOAK_CLIENTSECRET":  fb.fcbexec_keycloak_clientsecret,
            "FCBEXEC_KEYCLOAK_HOST":          fb.fcbexec_keycloak_host,
            "FCBEXEC_KAFKA_CLIENTID":         fb.fcbexec_kafka_clientid,
            "FCBEXEC_KAFKA_CONSUMERID":       fb.fcbexec_kafka_consumerid,
        }

    log(f"Fetching capsule install secrets from Vault at {cfg.vault.addr}")
    role_id, secret_id = _v_fetch.parse_approle_file(Path(cfg.vault.approle_file))
    client = _v_fetch.login_approle(cfg.vault.addr, role_id, secret_id)
    try:
        out: dict[str, str] = {}
        for var, (sub, field) in _v_fetch.SECRET_MAP.items():
            out[var] = _v_fetch._kv_field(  # noqa: SLF001
                client, cfg.vault.secret_mount, cfg.vault.kv_base, sub, field,
            )
        log(f"Loaded {len(out)} secret values (values not logged).")
        return out
    finally:
        _v_fetch.revoke_self(client)


def fetch_query_executor_certs(cfg: Config, dst_dir: Path,
                               *, dry_run: bool = False) -> None:
    """Stage ca.pem / client.cert.pem / client.key.pem into ``dst_dir``."""
    if dry_run:
        log(f"[dry-run] fetch query-executor certs into {dst_dir}")
        return
    if not cfg.vault.enabled:
        return  # nothing to do; user supplied certs locally
    role_id, secret_id = _v_fetch.parse_approle_file(Path(cfg.vault.approle_file))
    client = _v_fetch.login_approle(cfg.vault.addr, role_id, secret_id)
    try:
        import base64, stat as _stat
        dst_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(dst_dir, 0o700)
        for fname, field in _v_fetch.CERT_FIELDS.items():
            b64 = _v_fetch._kv_field(  # noqa: SLF001
                client, cfg.vault.secret_mount, cfg.vault.kv_base,
                _v_fetch.CERTS_PATH, field,
            )
            data = base64.b64decode(b64, validate=True)
            if not data.strip():
                fatal(f"{field}: decoded content is empty")
            out = dst_dir / fname
            prev = os.umask(0o077)
            try:
                out.write_bytes(data)
            finally:
                os.umask(prev)
            os.chmod(out, _stat.S_IRUSR | _stat.S_IWUSR)
        log(f"Cert files staged in {dst_dir}")
    finally:
        _v_fetch.revoke_self(client)
