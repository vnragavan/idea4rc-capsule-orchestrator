"""Idempotent clone / fetch / checkout of the chart repo. Ports repo_sync.sh."""

from __future__ import annotations

from pathlib import Path

from idea4rc_capsule.config import Config
from idea4rc_capsule.logging import fatal, log
from idea4rc_capsule.shell import require_tool, run, run_check_output


def run_repo_sync(cfg: Config, *, dry_run: bool = False) -> None:
    rs = cfg.repo_sync
    if not rs.enabled:
        log(f"Repo auto-sync disabled; skipping.")
        return
    require_tool("git")

    target = Path(cfg.k8s.chart_path)
    log(f"Syncing chart repo ({rs.url} @ {rs.branch}) into {target}")

    if dry_run:
        log(f"[dry-run] git ops on {target}")
        return

    # 1. Clone if missing or empty.
    if not (target / ".git").is_dir():
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and any(target.iterdir()):
            fatal(f"{target} exists and is not a git checkout; refusing to clobber. "
                  f"Move it aside or set repo_sync.enabled = false.")
        log(f"Cloning {rs.url} (branch {rs.branch}) into {target}")
        run(["git", "clone", "--branch", rs.branch, rs.url, str(target)],
            check=True)
    else:
        # 2. Fetch + checkout existing clone.
        log(f"Fetching {rs.url} for {target}")
        # ensure origin URL matches (warn on mismatch).
        existing = run_check_output(
            ["git", "-C", str(target), "remote", "get-url", "origin"],
            check=False, log_cmd=False,
        )
        if existing and existing != rs.url:
            log(f"WARN: origin URL is '{existing}', expected '{rs.url}'. "
                f"Leaving as-is; update manually if intentional.")
        run(["git", "-C", str(target), "fetch", "origin", rs.branch,
             "--prune", "--tags"], check=True)
        # checkout
        run(["git", "-C", str(target), "checkout", rs.branch], check=True)
        if rs.reset:
            log(f"Hard-resetting to origin/{rs.branch}")
            run(["git", "-C", str(target), "reset", "--hard", f"origin/{rs.branch}"],
                check=True)
        else:
            run(["git", "-C", str(target), "pull", "--ff-only", "origin", rs.branch],
                check=True)

    if (target / "Chart.yaml").is_file():
        log("Helm chart present; helm dependency update will run before install.")
    head = run_check_output(["git", "-C", str(target), "rev-parse", "--short", "HEAD"],
                            check=False, log_cmd=False)
    log(f"Repo sync complete: {head} on {rs.branch}")
