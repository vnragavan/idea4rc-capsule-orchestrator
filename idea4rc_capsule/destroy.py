"""Capsule teardown: helm uninstall + namespace delete + PV cleanup."""

from __future__ import annotations

import argparse
from pathlib import Path

from idea4rc_capsule.config import Config, load_config
from idea4rc_capsule.helm import Helm
from idea4rc_capsule.kube import Kube
from idea4rc_capsule.logging import log


def destroy(cfg: Config, *, dry_run: bool = False) -> None:
    helm = Helm(cfg, dry_run=dry_run)
    kube = Kube(cfg, dry_run=dry_run)

    helm.uninstall()
    kube.delete_namespace(cfg.k8s.namespace)
    for ns in cfg.k8s.extra_namespaces:
        if ns == cfg.k8s.namespace:
            continue
        kube.delete_namespace(ns)

    if cfg.k8s.pvs_to_delete_before_destroy:
        for pv in cfg.k8s.pvs_to_delete_before_destroy:
            kube.delete_pv(pv, wait=False)


def recreate_namespaces(cfg: Config, *, dry_run: bool = False) -> None:
    kube = Kube(cfg, dry_run=dry_run)
    kube.ensure_namespace(cfg.k8s.namespace)
    for ns in cfg.k8s.extra_namespaces:
        if ns == cfg.k8s.namespace:
            continue
        kube.ensure_namespace(ns)
    log("Namespace recreate complete.")


def cmd_destroy(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.config))
    if not args.yes:
        prompt = (f"This will destroy capsule release '{cfg.k8s.release_name}' "
                  f"in namespace '{cfg.k8s.namespace}' and remove data volumes.\n"
                  f"Type 'yes' to continue: ")
        ans = input(prompt).strip()
        if ans != "yes":
            log("Aborted by user.")
            return 1
    destroy(cfg, dry_run=args.dry_run)
    log("Destroy complete.")
    return 0


def add_subcommands(parent: "argparse._SubParsersAction") -> None:
    p = parent.add_parser(
        "destroy",
        help="Uninstall release, delete namespace + PVs",
    )
    p.add_argument("--config", required=True, help="Path to capsule.toml")
    p.add_argument("--yes", action="store_true",
                   help="Skip the destructive-action confirmation prompt")
    p.add_argument("--dry-run", action="store_true",
                   help="Print actions without executing them")
    p.set_defaults(func=cmd_destroy)
