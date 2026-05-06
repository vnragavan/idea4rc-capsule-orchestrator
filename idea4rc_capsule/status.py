"""`idea4rc-capsule status` — print a brief deployment snapshot.

Pure-information command. Never modifies cluster state. Useful as a
sanity check after a run, or before deciding whether to redeploy.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from idea4rc_capsule.config import load_config
from idea4rc_capsule.helm import Helm
from idea4rc_capsule.kube import Kube
from idea4rc_capsule.logging import log
from idea4rc_capsule.shell import run_check_output


def cmd_status(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.config))
    helm = Helm(cfg)
    kube = Kube(cfg)

    log("=== Capsule status ===")
    log(f"  namespace = {cfg.k8s.namespace}")
    log(f"  release   = {cfg.k8s.release_name}")
    log(f"  chart     = {cfg.k8s.chart_path}")

    ns_present = kube.namespace_exists(cfg.k8s.namespace)
    log(f"  namespace exists: {ns_present}")

    helm_present = helm.status() if ns_present else False
    log(f"  helm release deployed: {helm_present}")

    if ns_present:
        log("--- Pods ---")
        try:
            pods = run_check_output(
                kube.cmd("get", "pod", "-o",
                         "custom-columns=NAME:.metadata.name,READY:.status.containerStatuses[*].ready,STATUS:.status.phase",
                         "--no-headers", ns=cfg.k8s.namespace),
                check=False, log_cmd=False,
            )
            if pods:
                for line in pods.splitlines():
                    log(f"    {line}")
            else:
                log("    (no pods)")
        except Exception as exc:  # noqa: BLE001
            log(f"    (failed to list pods: {exc})")

    log("--- Vault ---")
    log(f"  vault.enabled = {cfg.vault.enabled}")
    if cfg.vault.enabled:
        log(f"  vault.addr    = {cfg.vault.addr}")
        approle = Path(cfg.vault.approle_file)
        log(f"  approle file present: {approle.is_file()}")
    return 0


def add_subcommands(parent: "argparse._SubParsersAction") -> None:
    p = parent.add_parser(
        "status",
        help="Show cluster + Helm release + Vault state",
    )
    p.add_argument("--config", required=True, help="Path to capsule.toml")
    p.set_defaults(func=cmd_status)
