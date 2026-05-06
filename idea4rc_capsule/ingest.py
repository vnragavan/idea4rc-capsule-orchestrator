"""`idea4rc-capsule ingest` — full deploy + upload + drain + audit pipeline.

Mirrors fresh_ingest.sh's ``main()`` order:

  1. validate config + csv + timeout
  2. confirm destructive action (full path) or data-only confirmation
  3. prepare logs + acquire lock
  4. repo sync (skipped when ``--skip-deploy`` unless ``--with-repo-sync``)
  5. fetch install secrets (Vault or fallback)
  6. deploy phase (destroy + recreate + install + OMOP restore + grants),
     **skipped** when ``--skip-deploy`` (requires prior ``deploy``)
  7. start port-forward (when ingest.mode=port-forward) + curl upload
  8. wait for Aerospike upload buffer to drain
  9. wait for staging Postgres to populate
 10. restart omop-etl (only AFTER staging is populated)
 11. wait for OMOP CDM to populate
 12. run audit pipeline + render HTML summary
"""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

from idea4rc_capsule.audit import run_audit_command
from idea4rc_capsule.config import load_config
from idea4rc_capsule.deploy import deploy_phase, verify_release_for_data_only_ingest
from idea4rc_capsule.drains import wait_aerospike, wait_omop, wait_staging
from idea4rc_capsule.git_sync import run_repo_sync
from idea4rc_capsule.kube import Kube
from idea4rc_capsule.logging import fatal, log
from idea4rc_capsule.omop_db import resolve_omop_pod, restart_omop_etl
from idea4rc_capsule.port_forward import port_forward
from idea4rc_capsule.runtime import (
    RunContext,
    acquire_lock,
    install_signal_handlers,
    prepare_logs,
    tee_run_to_file,
    validate_csv_path,
)
from idea4rc_capsule.preflight import run_preflight
from idea4rc_capsule.shell import require_tool
from idea4rc_capsule.vault_cmd import fetch_install_secrets


def _build_ingest_command(cfg, csv_path: Path) -> str:
    """Substitute ${VAR}, __CSV_PATH__, __NAMESPACE__ in the active template."""
    from idea4rc_capsule.audit import _substitute_template  # noqa: SLF001
    if cfg.ingest.mode == "public":
        tpl, name = cfg.ingest.public_template, "ingest.public_template"
    else:
        tpl, name = cfg.ingest.port_forward_template, "ingest.port_forward_template"
    cmd = _substitute_template(tpl,
                               csv_path=csv_path,
                               namespace=cfg.k8s.namespace,
                               template_name=name)
    if not cmd:
        fatal(f"{name} resolved to an empty command")
    return cmd


def _run_ingest_command(ctx: RunContext, cfg, kube: Kube) -> None:
    csv = ctx.csv_path
    assert csv is not None
    cmd = _build_ingest_command(cfg, csv)

    log(f"Running ingestion command (mode={cfg.ingest.mode}).")

    with port_forward(ctx, kube, cfg):
        if ctx.dry_run:
            log(f"[dry-run] {cmd}")
            return
        proc = subprocess.run(["bash", "-o", "pipefail", "-c", cmd],
                              check=False)
        if proc.returncode != 0:
            fatal(f"Ingestion failed (rc={proc.returncode}, "
                  f"mode={cfg.ingest.mode}).")


def cmd_ingest(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.config))

    ctx = RunContext(log_dir=Path(cfg.paths.log_dir),
                     dry_run=args.dry_run)
    ctx.csv_path = validate_csv_path(args.csv)
    ctx.log_dir.mkdir(parents=True, exist_ok=True)
    log_file = ctx.log_dir / f"ingest-{ctx.run_id}.log"

    require_tool("bash")

    rc = 0
    with install_signal_handlers(ctx):
        try:
            with tee_run_to_file(ctx, log_file):
                log(f"=== idea4rc-capsule ingest run_id={ctx.run_id} ===")
                log(f"Full log: {log_file}")
                log(f"CSV: {ctx.csv_path}")

                if not args.skip_preflight:
                    run_preflight(cfg, deep=args.deep_preflight,
                                  fail_on_warnings=args.strict_preflight)
                else:
                    from idea4rc_capsule.logging import warn as _warn
                    _warn("--skip-preflight: skipping all sanity checks.")

                if args.check_only:
                    if args.skip_deploy:
                        verify_release_for_data_only_ingest(
                            cfg, dry_run=ctx.dry_run)
                    log("--check-only: preflight complete; not ingesting.")
                else:
                    if args.yes:
                        log("--yes: skipping confirmation prompt.")
                    elif cfg.safety.auto_confirm_destruction:
                        log("[safety].auto_confirm_destruction=true in "
                            "capsule.toml: skipping confirmation prompt.")
                    else:
                        if args.skip_deploy:
                            ans = input(
                                "This will upload the CSV and run drains + audit "
                                "WITHOUT redeploying (no helm uninstall, no PV "
                                "wipe, no OMOP dictionary restore).\n"
                                "Type 'yes' to continue: ").strip()
                        else:
                            ans = input(
                                f"This will destroy capsule release "
                                f"'{cfg.k8s.release_name}' in namespace "
                                f"'{cfg.k8s.namespace}' and remove data volumes.\n"
                                f"Type 'yes' to continue: ").strip()
                        if ans != "yes":
                            log("Aborted by user.")
                            rc = 1

                    if rc == 0:
                        prepare_logs(ctx, keep_logs=args.keep_logs)
                        acquire_lock(ctx)

                        if args.skip_deploy:
                            verify_release_for_data_only_ingest(
                                cfg, dry_run=ctx.dry_run)

                        # 1. Refresh chart repo (skip when --skip-deploy saves
                        #    time; override with --with-repo-sync).
                        if cfg.repo_sync.enabled:
                            if args.skip_deploy and not args.with_repo_sync:
                                log(
                                    "repo_sync skipped (--skip-deploy); "
                                    "pass --with-repo-sync to refresh the chart "
                                    "before the data path.",
                                )
                            else:
                                run_repo_sync(cfg, dry_run=ctx.dry_run)

                        # 2. Pull install-time secrets. Live in process memory
                        #    only.
                        secrets = fetch_install_secrets(cfg,
                                                        dry_run=ctx.dry_run)
                        os.environ["CAPSULE_PUB_IP"] = secrets.get(
                            "CAPSULE_PUB_IP", cfg.capsule_install.public_ip
                        )
                        if cfg.ingest.mode == "port-forward":
                            os.environ["INGEST_PORT_FORWARD_LOCAL_PORT"] = str(
                                cfg.ingest.port_forward_local_port
                            )

                        # 3. Deploy phase (optional).
                        if args.skip_deploy:
                            log("=== deploy phase skipped (--skip-deploy) ===")
                        else:
                            deploy_phase(ctx, cfg)

                        # 4. Upload (with port-forward when configured).
                        kube = Kube(cfg, dry_run=ctx.dry_run)
                        _run_ingest_command(ctx, cfg, kube)

                        # 5. Adaptive drains for the two upload-side stages.
                        log("Post-ingestion phase 1/2: waiting for Aerospike "
                            "upload buffer to drain.")
                        wait_aerospike(cfg, kube, dry_run=ctx.dry_run)
                        log("Post-ingestion phase 2/2: waiting for staging "
                            "Postgres to populate before kicking omop-etl.")
                        wait_staging(cfg, kube, dry_run=ctx.dry_run)

                        # 6. Trigger omop-etl now that staging is populated.
                        restart_omop_etl(kube, cfg)

                        # 7. Wait for OMOP CDM to populate, then audit.
                        pod = resolve_omop_pod(kube, cfg)
                        wait_omop(cfg, kube, pod, dry_run=ctx.dry_run)
                        run_audit_command(ctx, cfg)

                        log("Ingestion run completed successfully.")
                        log(f"=== Ingest complete. Full log: {log_file} ===")
        finally:
            ctx.cleanup()
    if rc == 0:
        log(f"Full log: {log_file}")
    return rc


def add_subcommands(parent: "argparse._SubParsersAction") -> None:
    p = parent.add_parser(
        "ingest",
        help="Ingest pipeline: deploy (optional) + upload + drains + audit",
    )
    p.add_argument("--config", required=True, help="Path to capsule.toml")
    p.add_argument("--csv", required=True,
                   help="Absolute path to the CSV input")
    p.add_argument("--yes", action="store_true",
                   help="Skip the destructive-action confirmation prompt")
    p.add_argument("--keep-logs", action="store_true",
                   help="Keep existing logs in paths.log_dir")
    p.add_argument("--dry-run", action="store_true",
                   help="Print actions without executing them")
    p.add_argument("--check-only", action="store_true",
                   help="Run preflight only; do not ingest. Exits 0 if all checks pass.")
    p.add_argument("--skip-preflight", action="store_true",
                   help="Skip preflight checks (NOT recommended).")
    p.add_argument("--deep-preflight", action=argparse.BooleanOptionalAction,
                   default=True,
                   help="Run openssl chain/modulus/expiry checks on cert "
                        "payloads (default: enabled; opt out with --no-deep-preflight).")
    p.add_argument("--strict-preflight", action="store_true",
                   help="Treat preflight warnings as errors.")
    p.add_argument(
        "--skip-deploy",
        action="store_true",
        help="Skip deploy_phase (no helm uninstall/reinstall, no OMOP "
             "dictionary restore). Requires a healthy existing release from "
             "a prior `idea4rc-capsule deploy`. Skips repo_sync unless "
             "--with-repo-sync.",
    )
    p.add_argument(
        "--with-repo-sync",
        action="store_true",
        help="With --skip-deploy: still run repo_sync before the data path.",
    )
    p.set_defaults(func=cmd_ingest)
