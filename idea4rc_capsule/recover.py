"""`idea4rc-capsule recover-aerospike` — clean up after a wedged ingest.

When a previous ingest crashed mid-flight (or the upload-status flag in
Aerospike got stuck on "data conversion in progress..."), the way to
return to a clean state without wiping the OMOP CDM is:

  1. confirm no real ingest is still running (row counts stable),
  2. truncate the upload-buffer + error sets in Aerospike,
  3. rollout-restart the etl-idea deployment so it forgets in-memory
     state and re-reads the (now empty) buffer.

This is exactly the recovery the legacy `fresh_ingest.sh` told the
operator to run by hand (lines 1060/1161). Packaging it as one
subcommand makes it idempotent and safe.

Read-only by default: prints what it would do and asks for confirmation
unless ``--yes`` is given.
"""

from __future__ import annotations

import argparse
import re
import time
from pathlib import Path
from typing import Iterable

from idea4rc_capsule.config import Config, load_config
from idea4rc_capsule.kube import Kube
from idea4rc_capsule.logging import fatal, log, warn


# Default sets to truncate. ExcelRecord = upload buffer; EtlProcessError =
# per-row error markers etl-idea writes during ingestion.
DEFAULT_SETS = ("ExcelRecord", "EtlProcessError")


# --------------------------------------------------------------------- helpers
def _find_aerospike_pod(kube: Kube, cfg: Config) -> str:
    """Resolve the Aerospike pod name. Honours [aerospike_drain].deployment."""
    label = f"app={cfg.aerospike_drain.deployment}"
    return kube.get_running_pod_for_selector(label,
                                             ns=cfg.k8s.namespace,
                                             appear_timeout=60)


_OBJECTS_RE = re.compile(r"objects=(\d+)")


def _aerospike_count(kube: Kube, cfg: Config, pod: str, set_name: str) -> int:
    """Parse `asinfo -v sets/<ns>/<set>` for the `objects=` field."""
    cmd_str = f"sets/{cfg.aerospike_drain.as_namespace}/{set_name}"
    try:
        out = kube.exec_capture(pod, "asinfo", "-v", cmd_str,
                                ns=cfg.k8s.namespace, check=False)
    except SystemExit:
        return 0
    m = _OBJECTS_RE.search(out or "")
    return int(m.group(1)) if m else 0


def _stable_count(kube: Kube, cfg: Config, pod: str, set_name: str,
                  *, samples: int = 3, interval: int = 5) -> int:
    """Sample the set count `samples` times; return the count if stable.

    If the count keeps changing, raises FatalError -- a real ingest is
    likely still running and should not be interrupted.
    """
    counts: list[int] = []
    for i in range(samples):
        n = _aerospike_count(kube, cfg, pod, set_name)
        counts.append(n)
        log(f"  {set_name}: sample {i+1}/{samples} = {n}")
        if i < samples - 1:
            time.sleep(interval)
    if len(set(counts)) == 1:
        return counts[0]
    fatal(
        f"{set_name} row count is changing across samples ({counts}); "
        "an ingest may still be running. Refusing to truncate. "
        "Wait for it to finish or pass --force to override."
    )
    return -1  # unreachable


def _truncate_set(kube: Kube, cfg: Config, pod: str, set_name: str,
                  *, dry_run: bool) -> None:
    """Truncate a single Aerospike set via `asinfo truncate`."""
    cmd_str = (f"truncate-namespace:namespace={cfg.aerospike_drain.as_namespace};"
               f"set={set_name}")
    if dry_run:
        log(f"  [dry-run] asinfo -v '{cmd_str}'")
        return
    out = kube.exec_capture(pod, "asinfo", "-v", cmd_str,
                            ns=cfg.k8s.namespace, check=False)
    log(f"  asinfo response for {set_name}: {(out or '').strip() or '<empty>'}")


def _restart_etl_idea(kube: Kube, cfg: Config, *, dry_run: bool) -> None:
    deployment = cfg.k8s.ready_label_selector.split("=", 1)[-1] or "etl-idea"
    timeout = cfg.runtime_overrides.rollout_timeout
    log(f"Rollout-restarting deploy/{deployment} (timeout {timeout})")
    if dry_run:
        log(f"  [dry-run] kubectl rollout restart deploy/{deployment}")
        log(f"  [dry-run] kubectl rollout status  deploy/{deployment} --timeout={timeout}")
        return
    kube.run("rollout", "restart", f"deploy/{deployment}",
             ns=cfg.k8s.namespace)
    kube.run("rollout", "status", f"deploy/{deployment}",
             f"--timeout={timeout}",
             ns=cfg.k8s.namespace)


# --------------------------------------------------------------------- subcmd
def cmd_recover_aerospike(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.config))
    kube = Kube(cfg, dry_run=args.dry_run)

    log("=== aerospike recovery ===")
    log(f"  namespace      = {cfg.k8s.namespace}")
    log(f"  as_namespace   = {cfg.aerospike_drain.as_namespace}")
    log(f"  sets to clear  = {list(args.sets)}")

    pod = _find_aerospike_pod(kube, cfg)
    log(f"  aerospike pod  = {pod}")

    log("")
    log("--- current row counts ---")
    counts: dict[str, int] = {}
    for s in args.sets:
        if args.force:
            counts[s] = _aerospike_count(kube, cfg, pod, s)
            log(f"  {s}: {counts[s]} (no stability check, --force)")
        else:
            counts[s] = _stable_count(kube, cfg, pod, s,
                                      samples=args.stability_samples,
                                      interval=args.stability_interval)
            log(f"  {s}: {counts[s]} (stable across "
                f"{args.stability_samples} samples)")

    total = sum(counts.values())
    if total == 0:
        log("All target sets are empty; nothing to truncate. Will still "
            "rollout-restart etl-idea unless --no-restart is given.")
    else:
        log(f"Total rows to be discarded: {total}")
        if not args.yes:
            ans = input(
                f"This will truncate {sum(1 for c in counts.values() if c > 0)} "
                f"non-empty Aerospike set(s) in '{cfg.aerospike_drain.as_namespace}' "
                f"and rollout-restart etl-idea.\nType 'yes' to continue: "
            ).strip()
            if ans != "yes":
                log("Aborted by user.")
                return 1

    log("")
    log("--- truncating sets ---")
    for s in args.sets:
        if counts[s] == 0:
            log(f"  {s}: already empty, skipping truncate")
            continue
        _truncate_set(kube, cfg, pod, s, dry_run=args.dry_run)

    if args.no_restart:
        log("Skipping etl-idea restart per --no-restart.")
    else:
        log("")
        _restart_etl_idea(kube, cfg, dry_run=args.dry_run)

    log("")
    log("--- post-recovery row counts ---")
    if args.dry_run:
        log("(dry-run: skipping verification)")
    else:
        time.sleep(2)
        for s in args.sets:
            log(f"  {s}: {_aerospike_count(kube, cfg, pod, s)}")

    log("Aerospike recovery complete.")
    return 0


# ----------------------------------------------------------------------- CLI
def add_subcommands(parent: "argparse._SubParsersAction") -> None:
    p = parent.add_parser(
        "recover-aerospike",
        help="Clear stuck Aerospike upload-buffer and restart etl-idea "
             "(post-failure recovery)",
    )
    p.add_argument("--config", required=True, help="Path to capsule.toml")
    p.add_argument("--sets", nargs="+", default=list(DEFAULT_SETS),
                   help=f"Aerospike sets to truncate (default: {' '.join(DEFAULT_SETS)})")
    p.add_argument("--yes", action="store_true",
                   help="Skip the confirmation prompt")
    p.add_argument("--dry-run", action="store_true",
                   help="Print what would be done without changing anything")
    p.add_argument("--force", action="store_true",
                   help="Skip the row-count stability check (dangerous if a "
                        "real ingest is in progress)")
    p.add_argument("--no-restart", action="store_true",
                   help="Do not rollout-restart etl-idea after truncating")
    p.add_argument("--stability-samples", type=int, default=3,
                   help="How many row-count samples to take for stability check")
    p.add_argument("--stability-interval", type=int, default=5,
                   help="Seconds between stability samples")
    p.set_defaults(func=cmd_recover_aerospike)
