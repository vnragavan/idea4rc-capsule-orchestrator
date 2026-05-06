"""kubectl port-forward lifecycle for INGEST_MODE=port-forward.

Bash counterparts: ``_port_is_listening``, ``_reap_stale_port_forward``,
``start_port_forward``, ``stop_port_forward``. The reaping logic is the
same: only kill kubectl port-forward children that bind both the same
``local:target`` port AND the same target string -- never blindly kill
anything that holds the port.
"""

from __future__ import annotations

import os
import re
import signal
import socket
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

from idea4rc_capsule.config import Config
from idea4rc_capsule.kube import Kube
from idea4rc_capsule.logging import fatal, log, warn
from idea4rc_capsule.runtime import RunContext


# ----------------------------------------------------------- low-level probes
def _port_is_listening(port: int, host: str = "127.0.0.1") -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        s.connect((host, port))
    except (ConnectionRefusedError, socket.timeout, OSError):
        return False
    finally:
        try:
            s.close()
        except OSError:
            pass
    return True


# ------------------------------------------------------- stale process reaper
_PGREP_RE = re.compile(r"(^|/)(microk8s\.)?kubectl(\s|$).*port-forward")


def _list_kubectl_pf_processes() -> list[tuple[int, str]]:
    """Return [(pid, full cmdline)] for all kubectl port-forward processes."""
    try:
        out = subprocess.run(
            ["pgrep", "-af", "(^|/)(microk8s\\.)?kubectl([[:space:]]|$).*port-forward"],
            capture_output=True, text=True, check=False,
        ).stdout
    except FileNotFoundError:
        return []
    procs: list[tuple[int, str]] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        pid_str, _, rest = line.partition(" ")
        if pid_str.isdigit():
            procs.append((int(pid_str), rest))
    return procs


def _reap_stale_port_forward(local_port: int, target_port: int,
                             target: str) -> bool:
    """Kill kubectl port-forward processes that bind exactly our
    ``local:target`` port AND mention our target. Returns True if any
    stale process was matched (whether or not we could signal it).

    If a stale PID is owned by another user (often **root** after a prior
    ``sudo kubectl port-forward``), ``os.kill`` raises ``PermissionError``.
    We surface that as a clear fatal with ``sudo kill`` recovery steps
    instead of an unstructured traceback.
    """
    sig = f"{local_port}:{target_port}"
    stale = [pid for pid, cmd in _list_kubectl_pf_processes()
             if sig in cmd and target in cmd]
    if not stale:
        return False

    log(f"Found stale kubectl port-forward(s) bound to {sig} for "
        f"{target}: {stale}; reaping")
    perm_denied: list[int] = []
    for pid in stale:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except PermissionError:
            perm_denied.append(pid)
            warn(f"Cannot signal pid={pid} (owned by another user, often "
                 f"root). If the port stays busy: sudo kill -TERM {pid}")

    for _ in range(5):
        if not _port_is_listening(local_port):
            break
        time.sleep(1)

    for pid in stale:
        try:
            os.kill(pid, 0)
            try:
                os.kill(pid, signal.SIGKILL)
            except PermissionError:
                if pid not in perm_denied:
                    perm_denied.append(pid)
                warn(f"Cannot SIGKILL pid={pid} (permission denied). "
                     f"sudo kill -KILL {pid}")
        except ProcessLookupError:
            continue

    time.sleep(1)

    if perm_denied and _port_is_listening(local_port):
        pids = " ".join(str(p) for p in sorted(set(perm_denied)))
        fatal(
            f"Stale kubectl port-forward still holds localhost:{local_port} "
            f"(pids {sorted(set(perm_denied))}); current user cannot signal "
            f"them — usually left over from a `sudo` run. Free the port with:\n"
            f"  sudo kill -TERM {pids}\n"
            f"then retry. Inspect: ss -ltnp 'sport = :{local_port}'"
        )
    return True


# ------------------------------------------------------------ port-forward CM
@contextmanager
def port_forward(ctx: RunContext, kube: Kube, cfg: Config):
    """Start kubectl port-forward; yield once the local port is reachable.

    On exit (success or exception) the child process is killed and reaped.
    No-op when the run is dry or the ingest mode is not port-forward.
    """
    if cfg.ingest.mode != "port-forward":
        yield None
        return
    target = cfg.ingest.port_forward_target
    target_port = cfg.ingest.port_forward_target_port
    local_port = cfg.ingest.port_forward_local_port
    ready_timeout = cfg.ingest.port_forward_ready_timeout

    if ctx.dry_run:
        log(f"[dry-run] {kube.bin} -n {cfg.k8s.namespace} port-forward "
            f"{target} {local_port}:{target_port}")
        yield None
        return

    if _port_is_listening(local_port):
        if not _reap_stale_port_forward(local_port, target_port, target):
            fatal(
                f"Local port {local_port} is in use by an unknown process. "
                f"Either stop it manually (e.g. ss -ltnp 'sport = :{local_port}') "
                f"or pick another port via ingest.port_forward_local_port."
            )
        if _port_is_listening(local_port):
            fatal(
                f"Local port {local_port} is still in use after reaping "
                f"stale port-forwards. Inspect with "
                f"ss -ltnp 'sport = :{local_port}' and resolve manually."
            )
        log(f"Stale port-forward reaped; localhost:{local_port} is free.")

    pf_log = ctx.log_dir / f"port-forward-{ctx.run_id}.log"
    log(f"Starting port-forward: {target} {local_port}:{target_port} "
        f"(log: {pf_log})")

    pf_log.parent.mkdir(parents=True, exist_ok=True)
    log_fh = pf_log.open("a", buffering=1, encoding="utf-8")
    proc = subprocess.Popen(
        [*kube.cmd("port-forward", target,
                   f"{local_port}:{target_port}",
                   ns=cfg.k8s.namespace)],
        stdout=log_fh, stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    pid = proc.pid

    def _kill():
        try:
            os.kill(pid, signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            log_fh.close()
        except Exception:  # noqa: BLE001
            pass

    ctx.add_cleanup(_kill)

    log(f"Port-forward PID={pid}; waiting up to {ready_timeout}s for "
        f"localhost:{local_port}")
    elapsed = 0
    while not _port_is_listening(local_port):
        if proc.poll() is not None:
            warn(f"Port-forward died during startup (rc={proc.returncode}). "
                 f"See {pf_log}")
            fatal(f"Port-forward did not become ready on localhost:{local_port}")
        if elapsed >= ready_timeout:
            fatal(
                f"Port-forward did not become ready on localhost:{local_port} "
                f"within {ready_timeout}s. See {pf_log}"
            )
        time.sleep(1)
        elapsed += 1

    log(f"Port-forward ready on localhost:{local_port}")
    try:
        yield Path(pf_log)
    finally:
        log(f"Stopping port-forward (PID {pid})")
        _kill()
