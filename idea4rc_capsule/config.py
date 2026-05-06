"""TOML-backed typed configuration. Replaces the bash-style capsule.env.

`load_config(path)` returns a fully-validated `Config` dataclass. Every
section is optional in the TOML file when defaults are sensible; missing
required values raise a `FatalError` with a clear pointer.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any, Optional

if sys.version_info >= (3, 11):
    import tomllib  # type: ignore[import-not-found]
else:
    import tomli as tomllib  # type: ignore[import-not-found,no-redef]

from idea4rc_capsule.logging import fatal


# --------------------------------------------------------------------- helpers
def _get(section: dict, key: str, default: Any = None, *, required: bool = False,
         type_: type = str, section_name: str = "") -> Any:
    if key not in section or section.get(key) in (None, ""):
        if required:
            sn = f"[{section_name}].{key}" if section_name else key
            fatal(f"Required config key missing: {sn}")
        return default
    val = section[key]
    if type_ is bool and not isinstance(val, bool):
        fatal(f"[{section_name}].{key} must be a boolean")
    if type_ is int and not isinstance(val, int):
        fatal(f"[{section_name}].{key} must be an integer")
    if type_ is str and not isinstance(val, str):
        fatal(f"[{section_name}].{key} must be a string")
    if type_ is list and not isinstance(val, list):
        fatal(f"[{section_name}].{key} must be a list")
    return val


def _path_value(value: str) -> str:
    """Expand shell-style home/env vars in operator-facing path settings."""
    if not value:
        return ""
    return os.path.expanduser(os.path.expandvars(value))


def bundled_install_script_path() -> str:
    """Return the bundled env-driven Helm installer path."""
    return str(
        resources.files("idea4rc_capsule.data")
        .joinpath("capsule_helm_install.sh")
    )


# ---------------------------------------------------------------- dataclasses
@dataclass
class K8sConfig:
    namespace: str
    extra_namespaces: list[str]
    release_name: str
    chart_path: str
    helm_values_file: str = ""
    kubectl_bin: str = "kubectl"
    helm_bin: str = "helm"
    wait_timeout: str = "600s"
    ready_label_selector: str = ""
    pvs_to_delete_before_destroy: list[str] = field(default_factory=list)
    delete_all_network_policies: bool = False
    force_recreate_only: bool = True


@dataclass
class CapsuleInstallConfig:
    use_install_script: bool = False
    install_script_path: str = ""
    install_script_args: list[str] = field(default_factory=list)
    public_ip: str = ""  # CAPSULE_PUB_IP fallback when Vault disabled


@dataclass
class RepoSyncConfig:
    enabled: bool = False
    url: str = ""
    branch: str = "main"
    reset: bool = False


@dataclass
class VaultConfig:
    enabled: bool = False
    addr: str = "http://127.0.0.1:8200"
    secret_mount: str = "secret"
    kv_base: str = "idea4rc-capsule"
    approle_file: str = ""


@dataclass
class FallbackSecretsConfig:
    """Plaintext secrets used when Vault is disabled. Keep them only here
    (chmod 600 the TOML file) or migrate to Vault."""

    v6node_apikey: str = ""
    v6node_name: str = ""
    v6node_k8s_nodename: str = ""
    fcbexec_keycloak_clientid: str = ""
    fcbexec_keycloak_clientsecret: str = ""
    fcbexec_keycloak_host: str = ""
    fcbexec_kafka_clientid: str = ""
    fcbexec_kafka_consumerid: str = ""


@dataclass
class QueryExecutorConfig:
    enabled: bool = True
    secret_script_path: str = ""


@dataclass
class OMOPConfig:
    db_name: str
    db_admin_user: str
    app_user: str
    schema: str
    etl_deployment: str
    etl_min_replicas: int
    db_pod_name: str = ""
    db_selector: str = ""
    db_container: str = ""
    dict_dump_path: str = ""


@dataclass
class DrainConfig:
    timeout: int
    poll_interval: int
    stable_polls: int
    min_rows: int
    min_wait_seconds: int
    max_stall_seconds: int


@dataclass
class AerospikeDrainConfig(DrainConfig):
    deployment: str = "aerospike"
    as_namespace: str = "idea4rc"
    as_set: str = "ExcelRecord"


@dataclass
class StagingDrainConfig(DrainConfig):
    deployment: str = "etl"
    user_env: str = "POSTGRES_USER"
    name_env: str = "POSTGRES_DB"
    schema: str = "public"
    exclude_tables: list[str] = field(default_factory=lambda: ["table_generator"])


@dataclass
class OMOPDrainConfig(DrainConfig):
    table: str = "person"


@dataclass
class IngestConfig:
    mode: str  # "public" | "port-forward"
    public_template: str = ""
    port_forward_template: str = ""
    port_forward_target: str = ""
    port_forward_target_port: int = 0
    port_forward_local_port: int = 0
    port_forward_ready_timeout: int = 60


@dataclass
class AuditConfig:
    command_template: str = ""
    summary_md_path: str = ""
    summary_html_path: str = ""


@dataclass
class PathsConfig:
    log_dir: str = "/var/log/idea4rc-capsule"


@dataclass
class HelmPostRendererConfig:
    """Strip selected resource kinds from helm-rendered manifests.

    Helm 3 supports ``--post-renderer EXEC``: rendered YAML is piped to
    EXEC's stdin; its stdout becomes what helm applies. We use this as
    the orchestrator-side replacement for forking the chart to gate
    Namespace resources behind a values flag — namespaces are managed
    externally by ``recreate_namespaces`` in destroy/deploy.

    ``binary`` defaults to ``idea4rc-helm-post-renderer`` (resolved via
    ``shutil.which`` at install time so pipx-installed location is fine).
    Set ``enabled = false`` to disable globally; helm runs unfiltered.
    """
    enabled: bool = True
    binary: str = "idea4rc-helm-post-renderer"
    kinds_to_drop: list[str] = field(default_factory=lambda: ["Namespace"])


@dataclass
class SafetyConfig:
    """Knobs controlling destructive-action safety prompts.

    Set ``auto_confirm_destruction = true`` on a single-operator host
    you trust to skip the "Type 'yes' to continue" prompt for
    ``deploy`` and ``ingest``. Equivalent to passing ``--yes`` on every
    invocation. Defaults to false so a freshly-cloned config is always
    safe.
    """
    auto_confirm_destruction: bool = False


@dataclass
class RuntimeOverridesConfig:
    """Post-install `kubectl set env` patches applied to specific deployments.

    Used to encode workarounds the helm chart doesn't bake in (e.g. raising
    Spring multipart limits to accept large CSV uploads). Each entry maps
    a deployment name to a dict of env vars merged into its podspec.
    Keys are env-var names; values are strings (kubectl serialises them).
    """
    enabled: bool = True
    deployment_env: dict[str, dict[str, str]] = field(
        default_factory=lambda: {
            "etl-idea": {
                "SPRING_SERVLET_MULTIPART_MAX_FILE_SIZE":    "200MB",
                "SPRING_SERVLET_MULTIPART_MAX_REQUEST_SIZE": "200MB",
            }
        }
    )
    rollout_timeout: str = "300s"


@dataclass
class Config:
    k8s: K8sConfig
    capsule_install: CapsuleInstallConfig
    repo_sync: RepoSyncConfig
    vault: VaultConfig
    fallback_secrets: FallbackSecretsConfig
    query_executor: QueryExecutorConfig
    omop: OMOPConfig
    aerospike_drain: AerospikeDrainConfig
    staging_drain: StagingDrainConfig
    omop_drain: OMOPDrainConfig
    ingest: IngestConfig
    audit: AuditConfig
    paths: PathsConfig
    runtime_overrides: RuntimeOverridesConfig
    helm_post_renderer: HelmPostRendererConfig
    safety: SafetyConfig
    raw: dict = field(default_factory=dict, repr=False)

    # ---- helpers ----
    def env_for_install(self, capsule_pub_ip: str, secrets: dict[str, str]) -> dict[str, str]:
        """Build the env dict the install script expects."""
        env = {
            "CHART_DIR":           self.k8s.chart_path,
            "NAMESPACE":           self.k8s.namespace,
            "RELEASE_NAME":        self.k8s.release_name,
            "HELM_BIN":            self.k8s.helm_bin,
            "CAPSULE_PUB_IP":      capsule_pub_ip,
        }
        env.update(secrets)
        return env


# ----------------------------------------------------------------------- load
def _drain_from(section: dict, name: str) -> dict[str, Any]:
    """Common drain knobs — extracted so each drain section can be sparse."""
    return {
        "timeout":           int(section.get("timeout", 14400)),
        "poll_interval":     int(section.get("poll_interval", 15)),
        "stable_polls":      int(section.get("stable_polls", 3)),
        "min_rows":          int(section.get("min_rows", 1)),
        "min_wait_seconds":  int(section.get("min_wait_seconds", 60)),
        "max_stall_seconds": int(section.get("max_stall_seconds", 600)),
    }


_TIMEOUT_RE = re.compile(r"^\d+[smh]$")


def load_config(path: Path) -> Config:
    if not path.is_file():
        fatal(f"Config file not found: {path}")
    try:
        raw = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        fatal(f"Invalid TOML in {path}: {exc}")

    # ---- k8s ----
    k8s_s = raw.get("k8s", {})
    k8s = K8sConfig(
        namespace=_get(k8s_s, "namespace", required=True, section_name="k8s"),
        extra_namespaces=list(k8s_s.get("extra_namespaces", [])),
        release_name=_get(k8s_s, "release_name", required=True, section_name="k8s"),
        chart_path=_path_value(_get(k8s_s, "chart_path", required=True, section_name="k8s")),
        helm_values_file=_path_value(k8s_s.get("helm_values_file", "")),
        kubectl_bin=k8s_s.get("kubectl_bin", "kubectl"),
        helm_bin=k8s_s.get("helm_bin", "helm"),
        wait_timeout=k8s_s.get("wait_timeout", "600s"),
        ready_label_selector=k8s_s.get("ready_label_selector", ""),
        pvs_to_delete_before_destroy=list(k8s_s.get("pvs_to_delete_before_destroy", [])),
        delete_all_network_policies=bool(k8s_s.get("delete_all_network_policies", False)),
        force_recreate_only=bool(k8s_s.get("force_recreate_only", True)),
    )
    if not _TIMEOUT_RE.match(k8s.wait_timeout):
        fatal(f"[k8s].wait_timeout must match <num>[s|m|h], got: {k8s.wait_timeout}")

    # ---- capsule install ----
    ci_s = raw.get("capsule_install", {})
    capsule_install = CapsuleInstallConfig(
        use_install_script=bool(ci_s.get("use_install_script", False)),
        install_script_path=_path_value(
            ci_s.get("install_script_path", "") or bundled_install_script_path()
        ),
        install_script_args=list(ci_s.get("install_script_args", [])),
        public_ip=ci_s.get("public_ip", ""),
    )

    # ---- repo sync ----
    rs_s = raw.get("repo_sync", {})
    repo_sync = RepoSyncConfig(
        enabled=bool(rs_s.get("enabled", False)),
        url=rs_s.get("url", ""),
        branch=rs_s.get("branch", "main"),
        reset=bool(rs_s.get("reset", False)),
    )

    # ---- vault ----
    v_s = raw.get("vault", {})
    vault = VaultConfig(
        enabled=bool(v_s.get("enabled", False)),
        addr=v_s.get("addr", "http://127.0.0.1:8200"),
        secret_mount=v_s.get("secret_mount", "secret"),
        kv_base=v_s.get("kv_base", "idea4rc-capsule"),
        approle_file=_path_value(v_s.get("approle_file", "")),
    )

    # ---- fallback secrets ----
    fs_s = raw.get("fallback_secrets", {})
    fallback_secrets = FallbackSecretsConfig(
        v6node_apikey=fs_s.get("v6node_apikey", ""),
        v6node_name=fs_s.get("v6node_name", ""),
        v6node_k8s_nodename=fs_s.get("v6node_k8s_nodename", ""),
        fcbexec_keycloak_clientid=fs_s.get("fcbexec_keycloak_clientid", ""),
        fcbexec_keycloak_clientsecret=fs_s.get("fcbexec_keycloak_clientsecret", ""),
        fcbexec_keycloak_host=fs_s.get("fcbexec_keycloak_host", ""),
        fcbexec_kafka_clientid=fs_s.get("fcbexec_kafka_clientid", ""),
        fcbexec_kafka_consumerid=fs_s.get("fcbexec_kafka_consumerid", ""),
    )

    # ---- query executor ----
    qe_s = raw.get("query_executor", {})
    query_executor = QueryExecutorConfig(
        enabled=bool(qe_s.get("enabled", True)),
        secret_script_path=_path_value(qe_s.get("secret_script_path", "")),
    )

    # ---- OMOP ----
    o_s = raw.get("omop", {})
    omop = OMOPConfig(
        db_name=_get(o_s, "db_name", required=True, section_name="omop"),
        db_admin_user=_get(o_s, "db_admin_user", required=True, section_name="omop"),
        app_user=_get(o_s, "app_user", required=True, section_name="omop"),
        schema=_get(o_s, "schema", required=True, section_name="omop"),
        etl_deployment=_get(o_s, "etl_deployment", required=True, section_name="omop"),
        etl_min_replicas=int(_get(o_s, "etl_min_replicas", default=1, type_=int, section_name="omop")),
        db_pod_name=o_s.get("db_pod_name", ""),
        db_selector=o_s.get("db_selector", ""),
        db_container=o_s.get("db_container", ""),
        dict_dump_path=_path_value(o_s.get("dict_dump_path", "")),
    )
    if not omop.db_pod_name and not omop.db_selector:
        fatal("Set either [omop].db_pod_name or [omop].db_selector")

    # ---- drains ----
    a_s = raw.get("aerospike_drain", {})
    aerospike_drain = AerospikeDrainConfig(
        **_drain_from(a_s, "aerospike_drain"),
        deployment=a_s.get("deployment", "aerospike"),
        as_namespace=a_s.get("as_namespace", "idea4rc"),
        as_set=a_s.get("as_set", "ExcelRecord"),
    )
    s_s = raw.get("staging_drain", {})
    staging_drain = StagingDrainConfig(
        **_drain_from(s_s, "staging_drain"),
        deployment=s_s.get("deployment", "etl"),
        user_env=s_s.get("user_env", "POSTGRES_USER"),
        name_env=s_s.get("name_env", "POSTGRES_DB"),
        schema=s_s.get("schema", "public"),
        exclude_tables=list(s_s.get("exclude_tables", ["table_generator"])),
    )
    od_s = raw.get("omop_drain", {})
    omop_drain = OMOPDrainConfig(
        **_drain_from(od_s, "omop_drain"),
        table=od_s.get("table", "person"),
    )

    # ---- ingest ----
    i_s = raw.get("ingest", {})
    mode = i_s.get("mode", "port-forward")
    if mode not in ("public", "port-forward"):
        fatal(f"[ingest].mode must be 'public' or 'port-forward', got: {mode}")
    ingest = IngestConfig(
        mode=mode,
        public_template=i_s.get("public_template", ""),
        port_forward_template=i_s.get("port_forward_template", ""),
        port_forward_target=i_s.get("port_forward_target", ""),
        port_forward_target_port=int(i_s.get("port_forward_target_port", 0)),
        port_forward_local_port=int(i_s.get("port_forward_local_port", 0)),
        port_forward_ready_timeout=int(i_s.get("port_forward_ready_timeout", 60)),
    )
    if mode == "port-forward":
        for required in ("port_forward_target", "port_forward_target_port",
                         "port_forward_local_port", "port_forward_ready_timeout",
                         "port_forward_template"):
            if not getattr(ingest, required):
                fatal(f"[ingest].{required} is required when mode=port-forward")
    elif mode == "public":
        if not ingest.public_template:
            fatal("[ingest].public_template is required when mode=public")

    # ---- audit / paths ----
    aud_s = raw.get("audit", {})
    audit = AuditConfig(
        command_template=aud_s.get("command_template", ""),
        summary_md_path=_path_value(aud_s.get("summary_md_path", "")),
        summary_html_path=_path_value(aud_s.get("summary_html_path", "")),
    )
    p_s = raw.get("paths", {})
    paths = PathsConfig(log_dir=_path_value(p_s.get("log_dir", "/var/log/idea4rc-capsule")))

    # ---- runtime overrides ----
    ro_s = raw.get("runtime_overrides", {})
    ro_default = RuntimeOverridesConfig()
    deployment_env: dict[str, dict[str, str]] = dict(ro_default.deployment_env)
    if "deployment_env" in ro_s:
        deployment_env = {}
        for dep, env_map in (ro_s.get("deployment_env") or {}).items():
            if not isinstance(env_map, dict):
                fatal(f"[runtime_overrides.deployment_env.{dep}] must be a table")
            deployment_env[str(dep)] = {str(k): str(v) for k, v in env_map.items()}
    runtime_overrides = RuntimeOverridesConfig(
        enabled=bool(ro_s.get("enabled", True)),
        deployment_env=deployment_env,
        rollout_timeout=str(ro_s.get("rollout_timeout", "300s")),
    )

    hpr_s = raw.get("helm_post_renderer", {})
    helm_post_renderer = HelmPostRendererConfig(
        enabled=bool(hpr_s.get("enabled", True)),
        binary=str(hpr_s.get("binary", "idea4rc-helm-post-renderer")),
        kinds_to_drop=[str(k) for k in (hpr_s.get("kinds_to_drop")
                                        or ["Namespace"])],
    )

    sf_s = raw.get("safety", {})
    if "auto_confirm" in sf_s and "auto_confirm_destruction" not in sf_s:
        fatal("[safety].auto_confirm has been renamed to "
              "[safety].auto_confirm_destruction. Please update your "
              "capsule.toml.")
    safety = SafetyConfig(
        auto_confirm_destruction=bool(
            sf_s.get("auto_confirm_destruction", False)
        ),
    )

    # ---- vault-conditional checks ----
    if vault.enabled:
        if not vault.approle_file:
            fatal("[vault].approle_file is required when vault.enabled=true")
        if not Path(vault.approle_file).is_file():
            fatal(f"[vault].approle_file not found: {vault.approle_file}")
    else:
        if not capsule_install.public_ip:
            fatal("[capsule_install].public_ip is required when vault.enabled=false")
        if capsule_install.use_install_script:
            missing = [k for k, v in {
                "v6node_apikey": fallback_secrets.v6node_apikey,
                "v6node_name": fallback_secrets.v6node_name,
                "v6node_k8s_nodename": fallback_secrets.v6node_k8s_nodename,
                "fcbexec_keycloak_clientid": fallback_secrets.fcbexec_keycloak_clientid,
                "fcbexec_keycloak_clientsecret": fallback_secrets.fcbexec_keycloak_clientsecret,
                "fcbexec_keycloak_host": fallback_secrets.fcbexec_keycloak_host,
                "fcbexec_kafka_clientid": fallback_secrets.fcbexec_kafka_clientid,
                "fcbexec_kafka_consumerid": fallback_secrets.fcbexec_kafka_consumerid,
            }.items() if not v]
            if missing:
                fatal("[fallback_secrets] required when vault.enabled=false: " + ", ".join(missing))

    if repo_sync.enabled and (not repo_sync.url or not repo_sync.branch):
        fatal("[repo_sync].url and .branch are required when repo_sync.enabled=true")

    if query_executor.enabled and not query_executor.secret_script_path:
        fatal("[query_executor].secret_script_path is required when query_executor.enabled=true")

    return Config(
        k8s=k8s, capsule_install=capsule_install, repo_sync=repo_sync,
        vault=vault, fallback_secrets=fallback_secrets,
        query_executor=query_executor, omop=omop,
        aerospike_drain=aerospike_drain, staging_drain=staging_drain,
        omop_drain=omop_drain, ingest=ingest, audit=audit, paths=paths,
        runtime_overrides=runtime_overrides,
        helm_post_renderer=helm_post_renderer, safety=safety,
        raw=raw,
    )
