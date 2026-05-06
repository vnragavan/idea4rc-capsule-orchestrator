"""`idea4rc-capsule vault install` — install the HashiCorp Vault server.

This is the only piece of the Vault flow that needs root: it adds
HashiCorp's apt repo, runs ``apt install -y vault``, writes
``/etc/vault.d/vault.hcl`` with a single-node file backend, and
enables/starts the ``vault.service`` systemd unit. After this command
the daemon is reachable but uninitialized and sealed.

The follow-up steps live in ``idea4rc-capsule vault bootstrap …`` and
``idea4rc-capsule vault write-secrets …`` — those don't need root and
operate against the running server over HTTP.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path

from idea4rc_capsule.logging import fatal, log
from idea4rc_capsule.shell import require_tool, run, run_check_output


def _is_root() -> bool:
    return os.geteuid() == 0


def _vault_installed() -> bool:
    return shutil.which("vault") is not None


def _ensure_hashicorp_apt_repo() -> None:
    keyring = Path("/usr/share/keyrings/hashicorp-archive-keyring.gpg")
    sources = Path("/etc/apt/sources.list.d/hashicorp.list")
    if keyring.is_file() and sources.is_file():
        return
    if not _is_root():
        fatal("Need root to add HashiCorp apt repo. Re-run with sudo.")
    require_tool("curl")
    require_tool("gpg")
    log("Adding HashiCorp apt repo + signing key")
    keyring.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["curl", "-fsSL", "https://apt.releases.hashicorp.com/gpg"],
        capture_output=True, check=True,
    )
    dearmored = subprocess.run(
        ["gpg", "--dearmor"], input=proc.stdout, capture_output=True, check=True,
    )
    keyring.write_bytes(dearmored.stdout)
    keyring.chmod(0o644)
    codename = run_check_output(["lsb_release", "-cs"], check=True, log_cmd=False)
    sources.write_text(
        f"deb [signed-by={keyring}] https://apt.releases.hashicorp.com {codename} main\n"
    )


def _apt_install_vault() -> None:
    if not _is_root():
        fatal("Need root to apt-install vault. Re-run with sudo.")
    log("apt-get install -y vault")
    run(["apt-get", "update", "-y"], check=True)
    run(["apt-get", "install", "-y", "vault"], check=True)


def _write_vault_hcl(addr: str, data_dir: str, config_dir: str) -> None:
    if not _is_root():
        fatal("Need root to write /etc/vault.d/vault.hcl. Re-run with sudo.")
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    Path(config_dir).mkdir(parents=True, exist_ok=True)

    listener_addr = addr.split("://", 1)[-1].split(":", 1)[0] or "127.0.0.1"
    listener_port = "8200"
    if ":" in addr.split("://", 1)[-1]:
        tail = addr.rsplit(":", 1)[-1].split("/", 1)[0]
        if tail.isdigit():
            listener_port = tail

    hcl = (
        "ui = true\n"
        "\n"
        "storage \"file\" {\n"
        f"  path = \"{data_dir}\"\n"
        "}\n"
        "\n"
        "listener \"tcp\" {\n"
        f"  address     = \"{listener_addr}:{listener_port}\"\n"
        "  tls_disable = \"true\"\n"
        "}\n"
        "\n"
        f"api_addr      = \"{addr}\"\n"
        "disable_mlock = true\n"
    )
    target = Path(config_dir) / "vault.hcl"
    log(f"Writing {target}")
    target.write_text(hcl)
    for d in (data_dir, config_dir):
        try:
            shutil.chown(d, user="vault", group="vault")
        except (PermissionError, LookupError):
            pass


def _start_vault_service() -> None:
    log("Enabling + starting vault.service")
    run(["systemctl", "enable", "vault.service"], check=False)
    run(["systemctl", "restart", "vault.service"], check=True)


def cmd_vault_install(args: argparse.Namespace) -> int:
    log("=== Vault server install ===")
    if _vault_installed():
        log(f"vault already installed: {shutil.which('vault')}")
    else:
        if not args.auto:
            log("vault binary missing. Re-run with --auto to install via apt "
                "(needs sudo).")
            return 1
        _ensure_hashicorp_apt_repo()
        _apt_install_vault()

    if args.skip_service:
        log("--skip-service: not writing vault.hcl or touching systemd.")
    else:
        _write_vault_hcl(args.addr, args.data_dir, args.config_dir)
        _start_vault_service()

    log("Done. Next:")
    log("  idea4rc-capsule vault bootstrap all --output ~/.vault-init.json")
    return 0


def add_subcommands(parent: argparse._SubParsersAction) -> None:
    p = parent.add_parser(
        "install",
        help="Install the Vault server (apt + systemd unit + vault.hcl)",
    )
    p.add_argument("--auto", action="store_true",
                   help="Run apt-get install if vault is missing (needs sudo)")
    p.add_argument("--addr", default="http://127.0.0.1:8200",
                   help="api_addr / listener address (default: http://127.0.0.1:8200)")
    p.add_argument("--data-dir", default="/var/lib/vault",
                   help="storage \"file\" path (default: /var/lib/vault)")
    p.add_argument("--config-dir", default="/etc/vault.d",
                   help="Directory to write vault.hcl into (default: /etc/vault.d)")
    p.add_argument("--skip-service", action="store_true",
                   help="Don't write vault.hcl and don't (re)start vault.service")
    p.set_defaults(func=cmd_vault_install)
