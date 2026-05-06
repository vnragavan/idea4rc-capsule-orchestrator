"""Pre-deploy / pre-ingest sanity checks.

`run_preflight()` is the single entry point used by both `deploy` and
`ingest`. It runs every host/cluster/Vault/path/tool check in parallel
where possible, collects ALL failures, and raises ``FatalError`` at the
end with a structured summary — so an operator sees the full list of
problems in one pass instead of fixing one, re-running, fixing the next.

Categories:
  1. host tools          (git, openssl, curl, helm, kubectl, jq, pandoc...)
  2. file system paths   (chart dir, install script, dump file, ...)
  3. kube cluster reach  (kubectl get nodes, microk8s ready)
  4. vault state         (server reachable, AppRole works, full inventory)
  5. config sanity       (fallbacks complete when vault disabled)

Each check appends to ``Report.errors`` (hard, blocks deploy) or
``Report.warnings`` (non-blocking notes). Hard categories also short-
circuit later checks that depend on them (no point checking Vault
inventory if the AppRole file doesn't parse).
"""

from __future__ import annotations

import base64
import os
import shutil
import stat
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from idea4rc_capsule.config import Config
from idea4rc_capsule.logging import fatal, log, warn


# --------------------------------------------------------------------- report
@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    sections: list[tuple[str, list[str]]] = field(default_factory=list)
    _section_buffer: list[str] = field(default_factory=list)
    _section_name: str = ""

    def begin(self, name: str) -> None:
        if self._section_name:
            self.sections.append((self._section_name, self._section_buffer))
        self._section_name = name
        self._section_buffer = []
        log(f"--- preflight: {name} ---")

    def ok(self, msg: str) -> None:
        line = f"  OK    {msg}"
        self._section_buffer.append(line)
        log(line)

    def err(self, msg: str) -> None:
        line = f"  FAIL  {msg}"
        self._section_buffer.append(line)
        self.errors.append(f"[{self._section_name}] {msg}")
        log(line)

    def warn(self, msg: str) -> None:
        line = f"  WARN  {msg}"
        self._section_buffer.append(line)
        self.warnings.append(f"[{self._section_name}] {msg}")
        log(line)

    def end(self) -> None:
        if self._section_name:
            self.sections.append((self._section_name, self._section_buffer))
            self._section_name = ""
            self._section_buffer = []


# --------------------------------------------------------------------- helpers
def _which(tool: str) -> Optional[str]:
    return shutil.which(tool)


def _check_file(rep: Report, label: str, path: Optional[str], *,
                require_exec: bool = False, optional: bool = False) -> bool:
    if not path:
        if optional:
            rep.ok(f"{label}: not configured (skipped)")
            return False
        rep.err(f"{label}: required path is empty in config")
        return False
    p = Path(path)
    if not p.exists():
        rep.err(f"{label}: not found at {path}")
        return False
    if not p.is_file():
        rep.err(f"{label}: not a regular file: {path}")
        return False
    if require_exec and not os.access(p, os.X_OK):
        rep.err(f"{label}: not executable: {path}  (chmod +x {path})")
        return False
    rep.ok(f"{label}: {path}")
    return True


def _check_dir(rep: Report, label: str, path: Optional[str], *,
               must_exist: bool = True) -> bool:
    if not path:
        rep.err(f"{label}: required directory is empty in config")
        return False
    p = Path(path)
    if not p.exists():
        if must_exist:
            rep.err(f"{label}: not found at {path}")
            return False
        rep.warn(f"{label}: not present yet at {path} (will be created)")
        return False
    if not p.is_dir():
        rep.err(f"{label}: not a directory: {path}")
        return False
    rep.ok(f"{label}: {path}")
    return True


# --------------------------------------------------------------------- checks
def _check_host_tools(rep: Report, cfg: Config) -> None:
    rep.begin("host tools")
    required = {
        "git":   "needed for repo-sync",
        "openssl": "needed for cert validation + audit",
        "curl":  "needed for ingest CSV upload",
        "jq":    "useful for inspecting Vault output (warn if missing)",
    }
    for tool, why in required.items():
        path = _which(tool)
        if path:
            rep.ok(f"{tool} -> {path}")
        elif tool == "jq":
            rep.warn(f"{tool} not found ({why})")
        else:
            rep.err(f"{tool} not found ({why})")

    for label, val in (("kubectl_bin", cfg.k8s.kubectl_bin),
                       ("helm_bin",    cfg.k8s.helm_bin)):
        if not val:
            rep.err(f"{label}: empty in [k8s] config")
            continue
        path = _which(val)
        if path:
            rep.ok(f"{label} '{val}' -> {path}")
        else:
            rep.err(f"{label} '{val}' not found on PATH "
                    f"(install or override in capsule.toml)")

    if cfg.audit.command_template:
        if _which("pandoc"):
            rep.ok("pandoc found (audit summary will render to HTML)")
        else:
            rep.warn("pandoc not found; audit HTML summary will be skipped")

    hpr = cfg.helm_post_renderer
    if hpr.enabled and hpr.kinds_to_drop:
        from idea4rc_capsule.helm import resolve_companion_binary
        binary = resolve_companion_binary(hpr.binary)
        if binary:
            rep.ok(f"helm-post-renderer '{hpr.binary}' -> {binary} "
                   f"(drops: {hpr.kinds_to_drop})")
        else:
            rep.err(f"helm-post-renderer '{hpr.binary}' not found or not "
                    f"executable. Reinstall pipx package: "
                    f"`pipx reinstall idea4rc-capsule`")
    else:
        rep.warn("helm_post_renderer disabled or empty kinds_to_drop; "
                 "helm will see chart unmodified")


def _check_paths(rep: Report, cfg: Config) -> None:
    rep.begin("paths")
    # chart dir: ok to be missing if repo_sync will create it
    chart = cfg.k8s.chart_path
    if cfg.repo_sync.enabled:
        _check_dir(rep, "chart_path (will be auto-synced)", chart, must_exist=False)
    else:
        _check_dir(rep, "chart_path", chart)

    if cfg.capsule_install.use_install_script:
        _check_file(rep, "install_script_path", cfg.capsule_install.install_script_path,
                    require_exec=False)

    if cfg.k8s.helm_values_file:
        _check_file(rep, "k8s.helm_values_file", cfg.k8s.helm_values_file)

    if cfg.query_executor.enabled:
        _check_file(rep, "query_executor.secret_script_path",
                    cfg.query_executor.secret_script_path, require_exec=True)
    else:
        rep.ok("query_executor.enabled = false (skipped)")

    if cfg.omop.dict_dump_path:
        _check_file(rep, "omop.dict_dump_path", cfg.omop.dict_dump_path)
    else:
        rep.ok("omop.dict_dump_path empty (dictionary restore will be skipped)")

    log_dir = Path(cfg.paths.log_dir)
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        rep.ok(f"paths.log_dir writable: {log_dir}")
    except Exception as exc:  # noqa: BLE001
        rep.err(f"paths.log_dir not writable: {log_dir} ({exc!r})")


def _check_cluster(rep: Report, cfg: Config) -> None:
    rep.begin("cluster reachability")
    kubectl = cfg.k8s.kubectl_bin
    if not _which(kubectl):
        rep.err(f"cannot probe cluster: {kubectl} not on PATH")
        return
    try:
        proc = subprocess.run(
            [kubectl, "get", "nodes",
             "-o", "custom-columns=NAME:.metadata.name,READY:.status.conditions[?(@.type=='Ready')].status",
             "--no-headers"],
            capture_output=True, text=True, check=False, timeout=20,
        )
    except subprocess.TimeoutExpired:
        rep.err(f"{kubectl} get nodes: timeout (cluster unreachable?)")
        return
    if proc.returncode != 0:
        rep.err(f"{kubectl} get nodes failed (rc={proc.returncode}): "
                f"{proc.stderr.strip() or proc.stdout.strip()}")
        return
    nodes = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    if not nodes:
        rep.err("kubectl returned no nodes")
        return
    ready_count = sum(1 for ln in nodes if ln.split()[-1] == "True")
    rep.ok(f"{ready_count}/{len(nodes)} node(s) Ready")
    for ln in nodes:
        rep.ok(f"  node: {ln}")
    if ready_count == 0:
        rep.err("no Ready nodes; deploy will not succeed")


def _check_vault(rep: Report, cfg: Config, *, deep: bool) -> None:
    rep.begin("vault")
    if not cfg.vault.enabled:
        # In disabled mode, fallback secrets must all be present
        rep.ok("vault.enabled = false; using [fallback_secrets]")
        fb = cfg.fallback_secrets
        for name in ("v6node_apikey", "v6node_name", "v6node_k8s_nodename",
                     "fcbexec_keycloak_clientid", "fcbexec_keycloak_clientsecret",
                     "fcbexec_keycloak_host", "fcbexec_kafka_clientid",
                     "fcbexec_kafka_consumerid"):
            if not getattr(fb, name, ""):
                rep.err(f"fallback_secrets.{name} is empty (required when vault disabled)")
            else:
                rep.ok(f"fallback_secrets.{name}: set")
        if not cfg.capsule_install.public_ip:
            rep.err("capsule_install.public_ip empty (required when vault disabled)")
        return

    # Vault enabled path
    approle = Path(cfg.vault.approle_file or "")
    if not approle.is_file():
        rep.err(f"vault.approle_file not found: {approle}")
        return
    mode = stat.S_IMODE(approle.stat().st_mode)
    if mode in (0o600, 0o400):
        rep.ok(f"approle file: {approle} (mode {oct(mode)})")
    else:
        rep.warn(f"approle file mode is {oct(mode)}; recommend 600 "
                 f"(chmod 600 {approle})")

    try:
        from idea4rc_capsule.vault._common import (
            login_approle, parse_approle_file, revoke_self,
        )
        from idea4rc_capsule.vault.fetch import (
            CERT_FIELDS, CERTS_PATH, SECRET_MAP,
        )
        import hvac
    except Exception as exc:  # noqa: BLE001
        rep.err(f"vault python deps unavailable: {exc!r}")
        return

    try:
        role_id, secret_id = parse_approle_file(approle)
    except SystemExit as exc:
        rep.err(f"approle file unparsable: {exc}")
        return

    try:
        client = login_approle(cfg.vault.addr, role_id, secret_id)
    except SystemExit as exc:
        rep.err(f"AppRole login failed: {exc}")
        return

    try:
        rep.ok(f"AppRole login OK at {cfg.vault.addr}")

        missing_strings: list[str] = []
        for env_var, (sub, field) in SECRET_MAP.items():
            full = f"{cfg.vault.kv_base.strip('/')}/{sub}"
            try:
                data = client.secrets.kv.v2.read_secret_version(
                    mount_point=cfg.vault.secret_mount, path=full,
                    raise_on_deleted_version=True,
                )["data"]["data"]
            except hvac.exceptions.InvalidPath:
                missing_strings.append(f"{cfg.vault.secret_mount}/{full} (env {env_var})")
                continue
            val = data.get(field)
            if not val:
                missing_strings.append(
                    f"{cfg.vault.secret_mount}/{full}.{field} (env {env_var})"
                )
        if missing_strings:
            for m in missing_strings:
                rep.err(f"missing secret: {m}")
        else:
            rep.ok(f"all {len(SECRET_MAP)} install secrets present")

        full = f"{cfg.vault.kv_base.strip('/')}/{CERTS_PATH}"
        try:
            data = client.secrets.kv.v2.read_secret_version(
                mount_point=cfg.vault.secret_mount, path=full,
                raise_on_deleted_version=True,
            )["data"]["data"]
        except hvac.exceptions.InvalidPath:
            data = None
        if data is None:
            rep.err(f"missing cert path: {cfg.vault.secret_mount}/{full}")
            return
        decoded: dict[str, bytes] = {}
        for fname, vault_field in CERT_FIELDS.items():
            b64 = data.get(vault_field) or ""
            if not b64:
                rep.err(f"cert field empty: {vault_field}")
                continue
            try:
                raw = base64.b64decode(b64, validate=True)
            except Exception as exc:  # noqa: BLE001
                rep.err(f"cert field {vault_field} invalid base64: {exc!r}")
                continue
            if b"-----BEGIN" not in raw or b"-----END" not in raw:
                rep.err(f"cert field {vault_field}: no PEM markers")
                continue
            decoded[fname] = raw
            rep.ok(f"cert {fname}: {len(raw)} bytes (PEM ok)")

        if deep and len(decoded) == 3:
            _vault_deep_cert_check(rep, decoded)
    finally:
        revoke_self(client)


def _vault_deep_cert_check(rep: Report, payloads: dict[str, bytes]) -> None:
    if not _which("openssl"):
        rep.warn("openssl missing; --deep cert check skipped")
        return
    import tempfile
    scratch = Path(tempfile.mkdtemp(prefix="preflight-",
                                    dir=os.environ.get("XDG_RUNTIME_DIR", "/tmp")))
    try:
        os.chmod(scratch, 0o700)
        files = {}
        for fname, raw in payloads.items():
            p = scratch / fname
            prev = os.umask(0o077)
            try:
                p.write_bytes(raw)
            finally:
                os.umask(prev)
            os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)
            files[fname] = p

        proc = subprocess.run(
            ["openssl", "verify", "-CAfile", str(files["ca.pem"]),
             str(files["client.cert.pem"])],
            capture_output=True, text=True, check=False,
        )
        if proc.returncode == 0 and "OK" in proc.stdout:
            rep.ok("client.cert.pem signed by ca.pem (chain OK)")
        else:
            rep.err(f"cert chain verify FAILED: "
                    f"{(proc.stdout + proc.stderr).strip()}")

        def _modulus(path: Path, kind: str) -> Optional[str]:
            args = ["openssl", kind, "-noout", "-modulus", "-in", str(path)]
            r = subprocess.run(args, capture_output=True, text=True, check=False)
            if r.returncode != 0:
                return None
            for ln in r.stdout.splitlines():
                if ln.startswith("Modulus="):
                    import hashlib
                    return hashlib.sha256(ln.encode()).hexdigest()
            return None

        cert_mod = _modulus(files["client.cert.pem"], "x509")
        key_mod = _modulus(files["client.key.pem"], "rsa")
        if cert_mod and key_mod and cert_mod == key_mod:
            rep.ok("client.cert.pem ↔ client.key.pem modulus match")
        else:
            rep.err("client.cert.pem ↔ client.key.pem MODULUS MISMATCH")

        for fname in ("ca.pem", "client.cert.pem"):
            r = subprocess.run(
                ["openssl", "x509", "-in", str(files[fname]),
                 "-noout", "-checkend", "604800"],   # 7 days
                capture_output=True, text=True, check=False,
            )
            if r.returncode != 0:
                rep.warn(f"{fname} expires within 7 days")
    finally:
        for f in scratch.iterdir():
            try:
                if _which("shred"):
                    os.system(f"shred -u {f.as_posix()!s} >/dev/null 2>&1")  # noqa: S605
                else:
                    f.unlink()
            except Exception:  # noqa: BLE001
                pass
        try:
            scratch.rmdir()
        except OSError:
            pass


# ----------------------------------------------------------------------- main
def run_preflight(cfg: Config, *, deep: bool = False,
                  fail_on_warnings: bool = False) -> Report:
    """Run every preflight check, then raise FatalError if any failed.

    Returns the Report on success (so callers can inspect warnings).
    """
    log("=== preflight ===")
    rep = Report()
    _check_host_tools(rep, cfg)
    _check_paths(rep, cfg)
    _check_cluster(rep, cfg)
    _check_vault(rep, cfg, deep=deep)
    rep.end()

    log("")
    log(f"=== preflight summary: {len(rep.errors)} error(s), "
        f"{len(rep.warnings)} warning(s) ===")
    if rep.warnings:
        log("Warnings:")
        for w in rep.warnings:
            log(f"  - {w}")
    if rep.errors:
        log("Errors:")
        for e in rep.errors:
            log(f"  - {e}")
        fatal(f"Preflight failed with {len(rep.errors)} error(s). "
              "Fix the items above and re-run.")
    if fail_on_warnings and rep.warnings:
        fatal(f"Preflight: {len(rep.warnings)} warning(s) "
              "and --strict given. Aborting.")
    log("Preflight passed; safe to proceed.")
    return rep
