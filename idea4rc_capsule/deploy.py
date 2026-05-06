"""Full capsule deploy phase: destroy -> repo sync -> install -> OMOP restore.

This is the Python port of fresh_ingest.sh's ``_run_setup_pipeline``,
plus the ``run_repo_sync`` and ``bootstrap_runtime_secrets`` invocations
that wrap it in ``main``. Notes on design choices:

* Helm installs always go through the env-driven external installer
  (``capsule_install.install_script_path``) so the helm ``--set ...``
  invocation can stay in bash where it already works. Only the orchestration
  is in Python; the secret-bearing argv never enters this process's argv.
* OMOP grants/perms have to run AFTER pg_restore but BEFORE staging
  drains (etl-idea would otherwise fail to read concept rows).
* ``restart_omop_etl`` is intentionally NOT done here — it must run AFTER
  staging is populated, otherwise the first batch sees an empty staging
  schema and goes idle.
"""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Optional

from idea4rc_capsule.config import Config, load_config
from idea4rc_capsule.destroy import destroy as run_destroy, recreate_namespaces
from idea4rc_capsule.git_sync import run_repo_sync
from idea4rc_capsule.helm import Helm
from idea4rc_capsule.kube import Kube
from idea4rc_capsule.logging import fatal, log, warn
from idea4rc_capsule.omop_db import (
    apply_omop_role_grants,
    restore_omop_dictionary,
    verify_omop_app_permissions,
    wait_omop_db_ready,
    wait_omop_vocab_ready,
)
from idea4rc_capsule.preflight import run_preflight
from idea4rc_capsule.runtime import RunContext, install_signal_handlers, tee_run_to_file
from idea4rc_capsule.shell import require_tool, run
from idea4rc_capsule.vault_cmd import (
    fetch_install_secrets,
    fetch_query_executor_certs,
)


# ----------------------------------------------------- env-driven helm install
def _run_helm_install_script(cfg: Config, secrets: dict[str, str],
                             *, dry_run: bool) -> None:
    """Run the env-driven helm installer with secrets in its environment."""
    if not cfg.capsule_install.install_script_path:
        fatal("capsule_install.install_script_path is required when "
              "use_install_script=true")
    script = Path(cfg.capsule_install.install_script_path)
    if not script.is_file():
        fatal(f"Install script not found: {script}")

    required = (
        "CAPSULE_PUB_IP",
        "V6NODE_NODE_APIKEY", "V6NODE_NODE_NAME", "V6NODE_NODE_K8S_NODENAME",
        "FCBEXEC_KEYCLOAK_CLIENTID", "FCBEXEC_KEYCLOAK_CLIENTSECRET",
        "FCBEXEC_KEYCLOAK_HOST",
        "FCBEXEC_KAFKA_CLIENTID", "FCBEXEC_KAFKA_CONSUMERID",
    )
    for name in required:
        if not secrets.get(name):
            fatal(f"Required install env var is empty: {name}")

    install_env = {
        "CHART_DIR":    cfg.k8s.chart_path,
        "NAMESPACE":    cfg.k8s.namespace,
        "RELEASE_NAME": cfg.k8s.release_name,
        "HELM_BIN":     cfg.k8s.helm_bin,
        **secrets,
    }
    hpr = cfg.helm_post_renderer
    if hpr.enabled and hpr.kinds_to_drop:
        from idea4rc_capsule.helm import (
            _stage_post_renderer_launcher,
            resolve_companion_binary,
        )
        binary = resolve_companion_binary(hpr.binary)
        if not binary:
            fatal(f"[helm_post_renderer].binary not found or not executable: "
                  f"{hpr.binary}. Reinstall the pipx package "
                  f"(`pipx reinstall idea4rc-capsule`), or pin an absolute "
                  f"path in capsule.toml.")
        launcher = _stage_post_renderer_launcher(binary)
        install_env["HELM_POST_RENDERER_PATH"] = launcher
        install_env["IDEA4RC_HPR_DROP_KINDS"] = ",".join(hpr.kinds_to_drop)
        log(f"Helm post-renderer: {binary}")
        log(f"  launcher (env-sanitised): {launcher}")
        log(f"  drop_kinds: {hpr.kinds_to_drop}")
    args = ["bash", str(script)] + list(cfg.capsule_install.install_script_args)

    log("Installing capsule using env-driven install script")
    if dry_run:
        log(f"[dry-run] export {' '.join(install_env.keys())} && {' '.join(args)}")
        return

    full_env = os.environ.copy()
    full_env.update({k: str(v) for k, v in install_env.items() if v is not None})
    proc = subprocess.run(args, env=full_env, check=False)
    if proc.returncode != 0:
        fatal(f"Install script failed (rc={proc.returncode}): {script}")


def _run_helm_upgrade_install(cfg: Config, *, dry_run: bool) -> None:
    """Pure helm upgrade --install path. Disallowed when force_recreate_only."""
    helm = Helm(cfg, dry_run=dry_run)
    helm.upgrade_install(
        wait=True,
        timeout=cfg.k8s.wait_timeout,
        values_file=cfg.k8s.helm_values_file or None,
    )


def _verify_release_present(cfg: Config, *, dry_run: bool) -> None:
    Helm(cfg, dry_run=dry_run).assert_release_present()


def verify_release_for_data_only_ingest(cfg: Config, *, dry_run: bool) -> None:
    """Used by ``ingest --skip-deploy``. Ensure helm release exists before
    skipping ``deploy_phase`` — otherwise upload/drains would run against a
    missing or stale cluster."""
    if dry_run:
        log("[dry-run] --skip-deploy: would verify helm release is present")
        return
    helm = Helm(cfg, dry_run=False)
    if not helm.status():
        fatal(
            f"--skip-deploy requires an existing helm release "
            f"'{cfg.k8s.release_name}' in namespace '{cfg.k8s.namespace}'. "
            f"Run: idea4rc-capsule deploy --config <capsule.toml>"
        )
    log(f"--skip-deploy: release '{cfg.k8s.release_name}' present in "
        f"'{cfg.k8s.namespace}'.")


# ------------------------------------------------------ query-executor secret
def _stage_qe_certs_from_vault(ctx: RunContext, cfg: Config,
                               secret_dir: Path) -> Optional[Path]:
    """If Vault is enabled and certs aren't already in ``secret_dir``,
    fetch them onto tmpfs and symlink into ``secret_dir``. Returns the
    tmpfs dir (or None if nothing was staged) so cleanup can shred it."""
    if not cfg.vault.enabled:
        return None
    have_all = all((secret_dir / f).is_file()
                   for f in ("ca.pem", "client.cert.pem", "client.key.pem"))
    if have_all:
        return None

    stage = Path(f"/dev/shm/idea4rc-certs.{ctx.run_id}.{os.getpid()}")
    log(f"Staging query-executor certs from Vault into {stage}")
    if ctx.dry_run:
        log(f"[dry-run] fetch query-executor certs into {stage}")
        return None

    fetch_query_executor_certs(cfg, stage, dry_run=False)
    placed: list[Path] = []
    for fname in ("ca.pem", "client.cert.pem", "client.key.pem"):
        link = secret_dir / fname
        if link.is_symlink() or link.exists():
            try:
                link.unlink()
            except OSError:
                pass
        os.symlink(str(stage / fname), str(link))
        placed.append(link)

    def _cleanup_links_and_stage():
        for link in placed:
            try:
                link.unlink()
            except OSError:
                pass
        if stage.is_dir():
            for f in stage.iterdir():
                try:
                    f.unlink()
                except OSError:
                    pass
            try:
                stage.rmdir()
            except OSError:
                pass
    ctx.add_cleanup(_cleanup_links_and_stage)
    return stage


def run_query_executor_secret_creation(ctx: RunContext, cfg: Config) -> None:
    if not cfg.query_executor.enabled:
        log("Query executor secret creation disabled.")
        return
    script = Path(cfg.query_executor.secret_script_path)
    if not script.is_file() or not os.access(script, os.X_OK):
        fatal(f"Query executor secret script is not executable: {script}")

    secret_dir = script.parent
    _stage_qe_certs_from_vault(ctx, cfg, secret_dir)

    have_all = all((secret_dir / f).is_file() or (secret_dir / f).is_symlink()
                   for f in ("ca.pem", "client.cert.pem", "client.key.pem"))
    if not have_all:
        warn(f"Missing query-executor cert inputs in {secret_dir} "
             f"(ca.pem/client.cert.pem/client.key.pem). Skipping secret creation.")
        return

    log("Creating query executor secret via script")
    if ctx.dry_run:
        log(f"[dry-run] (cd \"{secret_dir}\" && bash \"{script}\")")
        return
    # NOTE: invoked via `bash <script>` rather than direct exec because
    # the upstream chart's `utils/query-executor-create-secret.sh` ships
    # without a shebang line. A direct `execve()` then fails with
    # `[Errno 8] Exec format error` (ENOEXEC). `bash <script>` makes the
    # interpreter explicit; works whether or not the script eventually
    # gains a shebang upstream. Same pattern is used for any other
    # potentially-shebangless chart helper script invoked from the
    # orchestrator.
    proc = subprocess.run(["bash", str(script)],
                          cwd=str(secret_dir), check=False)
    if proc.returncode != 0:
        fatal(f"Query executor secret script failed (rc={proc.returncode})")


def _maybe_delete_all_network_policies(cfg: Config, kube: Kube) -> None:
    if not cfg.k8s.delete_all_network_policies:
        log("Network policy deletion workaround disabled.")
        return
    log("Deleting all network policies across namespaces (workaround).")
    kube.run("delete", "netpol", "-A", "--all", ns=None)


def _apply_runtime_env_overrides(cfg: Config, kube: Kube) -> None:
    """Patch deployment podspecs with extra env vars (e.g. multipart limits).

    Equivalent to ``kubectl set env deploy/<name> KEY=VAL ...`` per
    deployment, then ``kubectl rollout status``. Idempotent: if the env
    var is already set to the desired value the no-op patch is cheap.
    """
    ro = cfg.runtime_overrides
    if not ro.enabled or not ro.deployment_env:
        log("Runtime env overrides: disabled or empty; skipping.")
        return
    for deployment, env_map in ro.deployment_env.items():
        if not env_map:
            continue
        log(f"Applying runtime env overrides to deploy/{deployment}: "
            f"{sorted(env_map.keys())}")
        kv_args = [f"{k}={v}" for k, v in env_map.items()]
        kube.run("set", "env", f"deploy/{deployment}", *kv_args,
                 ns=cfg.k8s.namespace)
        kube.run("rollout", "status", f"deploy/{deployment}",
                 f"--timeout={ro.rollout_timeout}",
                 ns=cfg.k8s.namespace)


# ------------------------------------------------------ orchestration entries
def deploy_phase(ctx: RunContext, cfg: Config) -> None:
    """One-shot deploy: destroy + recreate + install + OMOP restore.

    Mirrors the bash _run_setup_pipeline except restart_omop_etl is not
    invoked here -- it belongs to the post-ingest phase.
    """
    helm = Helm(cfg, dry_run=ctx.dry_run)
    kube = Kube(cfg, dry_run=ctx.dry_run)

    log("=== Phase 1/4: destroy + recreate ===")
    run_destroy(cfg, dry_run=ctx.dry_run)
    recreate_namespaces(cfg, dry_run=ctx.dry_run)
    helm.assert_release_only_in_namespace()

    log("=== Phase 2/4: install ===")
    if cfg.capsule_install.use_install_script:
        secrets = fetch_install_secrets(cfg, dry_run=ctx.dry_run)
        _run_helm_install_script(cfg, secrets, dry_run=ctx.dry_run)
        # Drop the secrets dict ASAP; they were only needed for the helm exec.
        secrets.clear()
    else:
        _run_helm_upgrade_install(cfg, dry_run=ctx.dry_run)
    _verify_release_present(cfg, dry_run=ctx.dry_run)

    log("=== Phase 3/4: post-install secrets + waits ===")
    run_query_executor_secret_creation(ctx, cfg)
    _maybe_delete_all_network_policies(cfg, kube)
    _apply_runtime_env_overrides(cfg, kube)
    if cfg.k8s.ready_label_selector:
        kube.wait_pods_ready(selector=cfg.k8s.ready_label_selector,
                             ns=cfg.k8s.namespace,
                             timeout=cfg.k8s.wait_timeout)
    else:
        log("k8s.ready_label_selector is empty; skipping pod readiness wait.")
    wait_omop_db_ready(kube, cfg)

    log("=== Phase 4/4: OMOP dictionary + grants ===")
    if cfg.omop.dict_dump_path:
        restore_omop_dictionary(kube, cfg)
        apply_omop_role_grants(kube, cfg)
        wait_omop_vocab_ready(kube, cfg)
        verify_omop_app_permissions(kube, cfg)
    else:
        log("omop.dict_dump_path is empty; skipping dictionary restore.")


# --------------------------------------------------------------------- CLI
def cmd_deploy(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.config))
    ctx = RunContext(log_dir=Path(cfg.paths.log_dir),
                     dry_run=args.dry_run)
    ctx.log_dir.mkdir(parents=True, exist_ok=True)
    log_file = ctx.log_dir / f"deploy-{ctx.run_id}.log"

    rc = 0
    with install_signal_handlers(ctx):
        try:
            with tee_run_to_file(ctx, log_file):
                log(f"=== idea4rc-capsule deploy run_id={ctx.run_id} ===")
                log(f"Full log: {log_file}")

                if not args.skip_preflight:
                    run_preflight(cfg, deep=args.deep_preflight,
                                  fail_on_warnings=args.strict_preflight)
                else:
                    warn("--skip-preflight: skipping all sanity checks. "
                         "You're on your own.")

                if args.check_only:
                    log("--check-only: preflight complete; not deploying.")
                else:
                    if args.yes:
                        log("--yes: skipping destructive-action prompt.")
                    elif cfg.safety.auto_confirm_destruction:
                        log("[safety].auto_confirm_destruction=true in "
                            "capsule.toml: skipping destructive-action prompt.")
                    else:
                        ans = input(f"This will destroy capsule release "
                                    f"'{cfg.k8s.release_name}' in namespace "
                                    f"'{cfg.k8s.namespace}' and remove data "
                                    f"volumes.\nType 'yes' to continue: ").strip()
                        if ans != "yes":
                            log("Aborted by user.")
                            rc = 1
                    if rc == 0:
                        if cfg.repo_sync.enabled:
                            run_repo_sync(cfg, dry_run=ctx.dry_run)
                        deploy_phase(ctx, cfg)
                        log(f"=== Deploy complete. Full log: {log_file} ===")
        finally:
            ctx.cleanup()
    if rc == 0:
        log(f"Full log: {log_file}")
    return rc


def add_subcommands(parent: "argparse._SubParsersAction") -> None:
    p = parent.add_parser(
        "deploy",
        help="Full deploy phase only (no ingestion)",
    )
    p.add_argument("--config", required=True, help="Path to capsule.toml")
    p.add_argument("--yes", action="store_true",
                   help="Skip the destructive-action confirmation prompt")
    p.add_argument("--dry-run", action="store_true",
                   help="Print actions without executing them")
    p.add_argument("--check-only", action="store_true",
                   help="Run preflight only; do not deploy. Exits 0 if all checks pass.")
    p.add_argument("--skip-preflight", action="store_true",
                   help="Skip preflight checks (NOT recommended).")
    p.add_argument("--deep-preflight", action=argparse.BooleanOptionalAction,
                   default=True,
                   help="Run openssl chain/modulus/expiry checks on cert "
                        "payloads (default: enabled; opt out with --no-deep-preflight).")
    p.add_argument("--strict-preflight", action="store_true",
                   help="Treat preflight warnings as errors.")
    p.set_defaults(func=cmd_deploy)
