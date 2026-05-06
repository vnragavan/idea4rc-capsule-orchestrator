"""OMOP Postgres helpers: dictionary restore, role grants, vocab readiness.

Ports the bash equivalents in fresh_ingest.sh:
``restore_omop_dictionary``, ``apply_omop_role_grants``,
``wait_for_omop_vocab_ready``, ``verify_omop_app_permissions``,
``restart_omop_etl``.
"""

from __future__ import annotations

import time
from pathlib import Path

from idea4rc_capsule.config import Config
from idea4rc_capsule.kube import Kube
from idea4rc_capsule.logging import fatal, log


def resolve_omop_pod(kube: Kube, cfg: Config) -> str:
    """Return the OMOP DB pod name. Honours explicit OMOP_DB_POD_NAME, else
    queries the configured selector. Mirrors bash get_omop_db_pod()."""
    if kube.dry_run:
        return cfg.omop.db_pod_name or "omop-db-pod-dryrun"
    if cfg.omop.db_pod_name:
        if not kube.silent_check("get", "pod", cfg.omop.db_pod_name):
            fatal(f"Configured OMOP db_pod_name not found: {cfg.omop.db_pod_name}")
        return cfg.omop.db_pod_name
    if not cfg.omop.db_selector:
        fatal("Set either omop.db_pod_name or omop.db_selector")
    return kube.get_running_pod_for_selector(cfg.omop.db_selector,
                                             ns=cfg.k8s.namespace,
                                             appear_timeout=600)


def wait_omop_db_ready(kube: Kube, cfg: Config) -> None:
    """Wait for OMOP Postgres pod to reach Ready. Honours pod_name|selector."""
    if kube.dry_run:
        log("[dry-run] Skip wait for OMOP DB pod readiness.")
        return
    if cfg.omop.db_pod_name:
        log(f"Waiting for OMOP DB pod '{cfg.omop.db_pod_name}' Ready")
        kube.wait_pod_ready(pod=cfg.omop.db_pod_name,
                            ns=cfg.k8s.namespace,
                            timeout=cfg.k8s.wait_timeout)
        return
    if not cfg.omop.db_selector:
        fatal("Set either omop.db_pod_name or omop.db_selector")

    log(f"Waiting for at least one OMOP DB pod to exist (selector="
        f"{cfg.omop.db_selector})")
    elapsed, interval, timeout_s = 0, 5, 600
    while True:
        names = kube.output("get", "pod", "-l", cfg.omop.db_selector,
                            "-o", "name", ns=cfg.k8s.namespace,
                            check=False).splitlines()
        if any(n.strip() for n in names):
            break
        if elapsed >= timeout_s:
            fatal(f"Timed out waiting for OMOP DB pod (selector="
                  f"{cfg.omop.db_selector}).")
        time.sleep(interval)
        elapsed += interval

    log(f"Waiting for OMOP DB pods Ready (selector={cfg.omop.db_selector})")
    kube.wait_pods_ready(selector=cfg.omop.db_selector,
                         ns=cfg.k8s.namespace,
                         timeout=cfg.k8s.wait_timeout)


def _psql_admin(kube: Kube, cfg: Config, pod: str, sql: str) -> None:
    """Run ``sql`` against the OMOP DB as the admin user, ON_ERROR_STOP=1."""
    kube.exec(pod, "psql", "-U", cfg.omop.db_admin_user,
              "-d", cfg.omop.db_name, "-v", "ON_ERROR_STOP=1",
              "-c", sql,
              ns=cfg.k8s.namespace,
              container=cfg.omop.db_container or None)


def restore_omop_dictionary(kube: Kube, cfg: Config) -> None:
    """``kubectl cp`` the dump into the OMOP pod, then pg_restore it."""
    pod = resolve_omop_pod(kube, cfg)
    dump = Path(cfg.omop.dict_dump_path)
    if not dump.is_file():
        fatal(f"OMOP dump not found: {dump}")
    remote = "/tmp/omop-dictionary.dump"

    log(f"Uploading OMOP dictionary dump into pod {pod}")
    kube.cp_to_pod(str(dump), pod, remote,
                   ns=cfg.k8s.namespace,
                   container=cfg.omop.db_container or None)

    log("Restoring OMOP dictionary dump (--no-owner --no-privileges; we "
        "re-apply grants explicitly later)")
    kube.exec(pod, "pg_restore", "--no-owner", "--no-privileges",
              "-U", cfg.omop.db_admin_user,
              "-d", cfg.omop.db_name, remote,
              ns=cfg.k8s.namespace,
              container=cfg.omop.db_container or None)

    log(f"Asserting OMOP vocabulary table {cfg.omop.schema}.concept exists "
        f"after restore")
    if kube.dry_run:
        return
    out = kube.exec_capture(
        pod, "psql", "-U", cfg.omop.db_admin_user,
        "-d", cfg.omop.db_name, "-At", "-v", "ON_ERROR_STOP=1",
        "-c",
        f"SELECT 1 FROM information_schema.tables "
        f"WHERE table_schema='{cfg.omop.schema}' AND table_name='concept';",
        ns=cfg.k8s.namespace,
        container=cfg.omop.db_container or None,
    ).strip()
    if out != "1":
        fatal(
            f"pg_restore finished but {cfg.omop.schema}.concept does not "
            f"exist. Dump may be incomplete: {dump}"
        )


def apply_omop_role_grants(kube: Kube, cfg: Config) -> None:
    """CREATE ROLE app_user; GRANT ...; ALTER DEFAULT PRIVILEGES ..."""
    pod = resolve_omop_pod(kube, cfg)
    log(f"Applying OMOP role and privilege fixes for {cfg.omop.app_user}")
    if kube.dry_run:
        log("[dry-run] role create + grants + default privileges + "
            "temp_custom_concepts ownership fix")
        return

    user = cfg.omop.app_user
    schema = cfg.omop.schema
    admin = cfg.omop.db_admin_user

    _psql_admin(kube, cfg, pod, f"""DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{user}') THEN
    CREATE ROLE {user} LOGIN;
  END IF;
END
$$;""")
    _psql_admin(kube, cfg, pod, f"GRANT USAGE ON SCHEMA {schema} TO {user};")
    _psql_admin(kube, cfg, pod,
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
                f"IN SCHEMA {schema} TO {user};")
    _psql_admin(kube, cfg, pod,
                f"GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES "
                f"IN SCHEMA {schema} TO {user};")
    _psql_admin(kube, cfg, pod,
                f"ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} "
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {user};")
    _psql_admin(kube, cfg, pod,
                f"ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} "
                f"GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO {user};")
    _psql_admin(kube, cfg, pod, f"""DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname='{schema}' AND c.relname='temp_custom_concepts'
  ) THEN
    EXECUTE 'ALTER TABLE {schema}.temp_custom_concepts OWNER TO {admin}';
  END IF;
END
$$;""")


def wait_omop_vocab_ready(kube: Kube, cfg: Config) -> None:
    """Poll concept count > 0 to confirm the dictionary really landed."""
    pod = resolve_omop_pod(kube, cfg)
    log(f"Waiting for OMOP vocabulary readiness ({cfg.omop.schema}.concept "
        f"count > 0)")
    if kube.dry_run:
        return
    elapsed, interval, timeout_s = 0, 10, 900
    while True:
        out = kube.exec_capture(
            pod, "psql", "-U", cfg.omop.db_admin_user,
            "-d", cfg.omop.db_name, "-At",
            "-c", f"SELECT COUNT(*) FROM {cfg.omop.schema}.concept;",
            ns=cfg.k8s.namespace,
            container=cfg.omop.db_container or None,
            check=False,
        )
        count_str = "".join(out.split())
        if count_str.isdigit() and int(count_str) > 0:
            log(f"OMOP vocabulary ready: concept count={count_str}")
            return
        if elapsed >= timeout_s:
            fatal(f"Timed out waiting for {cfg.omop.schema}.concept to "
                  f"become non-empty")
        time.sleep(interval)
        elapsed += interval


def verify_omop_app_permissions(kube: Kube, cfg: Config) -> None:
    pod = resolve_omop_pod(kube, cfg)
    log(f"Verifying {cfg.omop.app_user} can query "
        f"{cfg.omop.schema}.person")
    if kube.dry_run:
        return
    kube.exec(pod, "psql", "-U", cfg.omop.app_user,
              "-d", cfg.omop.db_name, "-v", "ON_ERROR_STOP=1",
              "-c", f"SELECT 1 FROM {cfg.omop.schema}.person LIMIT 1;",
              ns=cfg.k8s.namespace,
              container=cfg.omop.db_container or None)


def restart_omop_etl(kube: Kube, cfg: Config) -> None:
    """Scale-up if needed, then `rollout restart` to pick up freshly
    populated staging tables. Mirrors restart_omop_etl()."""
    if kube.dry_run:
        log(f"[dry-run] Ensure deploy/{cfg.omop.etl_deployment} replicas "
            f">= {cfg.omop.etl_min_replicas} and rollout restart.")
        return

    current = kube.output(
        "get", "deploy", cfg.omop.etl_deployment,
        "-o", "jsonpath={.spec.replicas}",
        ns=cfg.k8s.namespace, check=False,
    ).strip()
    if current.isdigit() and int(current) < cfg.omop.etl_min_replicas:
        log(f"Scaling deployment {cfg.omop.etl_deployment} to "
            f"{cfg.omop.etl_min_replicas} replica(s)")
        kube.run("scale", f"deploy/{cfg.omop.etl_deployment}",
                 f"--replicas={cfg.omop.etl_min_replicas}",
                 ns=cfg.k8s.namespace)

    log(f"Restarting deployment {cfg.omop.etl_deployment} to avoid "
        f"startup race")
    kube.run("rollout", "restart",
             f"deploy/{cfg.omop.etl_deployment}",
             ns=cfg.k8s.namespace)
    kube.run("rollout", "status",
             f"deploy/{cfg.omop.etl_deployment}",
             "--timeout=300s",
             ns=cfg.k8s.namespace)
