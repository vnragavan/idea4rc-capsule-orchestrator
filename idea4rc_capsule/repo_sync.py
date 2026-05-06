"""`idea4rc-capsule repo-sync` — clone / pull / switch the chart repo.

Thin CLI shim around ``idea4rc_capsule.git_sync.run_repo_sync`` so users
can refresh the chart checkout without doing a full deploy.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from idea4rc_capsule.config import load_config
from idea4rc_capsule.git_sync import run_repo_sync


def cmd_repo_sync(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.config))
    if not cfg.repo_sync.enabled:
        # Allow running ad-hoc with --branch override even if disabled in config.
        if args.url or args.branch:
            cfg.repo_sync.enabled = True
            if args.url:
                cfg.repo_sync.url = args.url
            if args.branch:
                cfg.repo_sync.branch = args.branch
        else:
            from idea4rc_capsule.logging import log
            log("[repo_sync].enabled = false; nothing to do. Pass --url/--branch "
                "to override, or set repo_sync.enabled in the config.")
            return 0
    if args.reset:
        cfg.repo_sync.reset = True
    run_repo_sync(cfg, dry_run=args.dry_run)
    return 0


def add_subcommands(parent: "argparse._SubParsersAction") -> None:
    p = parent.add_parser(
        "repo-sync",
        help="Clone / pull / switch the chart repo (idempotent)",
    )
    p.add_argument("--config", required=True, help="Path to capsule.toml")
    p.add_argument("--url", help="Override repo_sync.url")
    p.add_argument("--branch", help="Override repo_sync.branch")
    p.add_argument("--reset", action="store_true",
                   help="Hard-reset to origin/<branch> (drops local changes)")
    p.add_argument("--dry-run", action="store_true",
                   help="Print git operations without executing them")
    p.set_defaults(func=cmd_repo_sync)
