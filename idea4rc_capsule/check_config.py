"""`idea4rc-capsule check-config` — validate a capsule.toml without running.

Loads the config exactly the way the orchestrator does (so any fatal()
that would fail the run will fail here too), then prints a one-line
summary of the resolved values that matter most.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from idea4rc_capsule.config import load_config
from idea4rc_capsule.logging import log


def cmd_check_config(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.config))
    log("Configuration valid.")
    log(f"  k8s.namespace        = {cfg.k8s.namespace}")
    log(f"  k8s.release_name     = {cfg.k8s.release_name}")
    log(f"  k8s.chart_path       = {cfg.k8s.chart_path}")
    log(f"  k8s.kubectl_bin      = {cfg.k8s.kubectl_bin}")
    log(f"  k8s.helm_bin         = {cfg.k8s.helm_bin}")
    log(f"  vault.enabled        = {cfg.vault.enabled}")
    if cfg.vault.enabled:
        log(f"  vault.addr           = {cfg.vault.addr}")
        log(f"  vault.kv_base        = {cfg.vault.kv_base}")
        log(f"  vault.approle_file   = {cfg.vault.approle_file}")
    else:
        log(f"  capsule_install.public_ip = {cfg.capsule_install.public_ip}")
    log(f"  repo_sync.enabled    = {cfg.repo_sync.enabled}")
    if cfg.repo_sync.enabled:
        log(f"  repo_sync.url        = {cfg.repo_sync.url}")
        log(f"  repo_sync.branch     = {cfg.repo_sync.branch}")
    log(f"  ingest.mode          = {cfg.ingest.mode}")
    log(f"  paths.log_dir        = {cfg.paths.log_dir}")
    log(f"  safety.auto_confirm_destruction = "
        f"{cfg.safety.auto_confirm_destruction}"
        + ("  (destructive prompts skipped)"
           if cfg.safety.auto_confirm_destruction else ""))
    return 0


def add_subcommands(parent: "argparse._SubParsersAction") -> None:
    p = parent.add_parser(
        "check-config",
        help="Validate a capsule.toml without running anything",
    )
    p.add_argument("--config", required=True,
                   help="Path to capsule.toml")
    p.set_defaults(func=cmd_check_config)
