"""kubectl wrappers (operate on a single namespace by default)."""

from __future__ import annotations

import time
from typing import Optional, Sequence

from idea4rc_capsule.config import Config
from idea4rc_capsule.logging import fatal, log
from idea4rc_capsule.shell import run, run_check_output


class Kube:
    def __init__(self, cfg: Config, *, dry_run: bool = False) -> None:
        self.cfg = cfg
        self.dry_run = dry_run
        self.bin = cfg.k8s.kubectl_bin
        self.ns = cfg.k8s.namespace

    # ---- low-level ----
    def cmd(self, *args: str, ns: Optional[str] = None) -> list[str]:
        out = [self.bin]
        if ns is not None:
            out += ["-n", ns]
        out += list(args)
        return out

    def run(self, *args: str, ns: Optional[str] = None, check: bool = True,
            log_cmd: bool = True) -> int:
        proc = run(self.cmd(*args, ns=ns), dry_run=self.dry_run, check=check, log_cmd=log_cmd)
        return proc.returncode

    def output(self, *args: str, ns: Optional[str] = None, check: bool = True) -> str:
        return run_check_output(self.cmd(*args, ns=ns), dry_run=self.dry_run, check=check)

    def silent_check(self, *args: str, ns: Optional[str] = None) -> bool:
        """Return True iff `kubectl <args>` returns rc=0; never log the cmd."""
        if self.dry_run:
            return True
        return run(self.cmd(*args, ns=ns), check=False, capture=True, log_cmd=False).returncode == 0

    # ---- namespace / PV ----
    def namespace_exists(self, ns: str) -> bool:
        return self.silent_check("get", "namespace", ns, ns=None)

    def delete_namespace(self, ns: str, *, wait_seconds: int = 600) -> None:
        if not self.namespace_exists(ns):
            log(f"Namespace '{ns}' does not exist. Skipping namespace delete.")
            return
        log(f"Deleting namespace '{ns}'")
        self.run("delete", "namespace", ns, "--wait=false", ns=None)
        if self.dry_run:
            return
        self.wait_for_namespace_absent(ns, timeout=wait_seconds)

    def wait_for_namespace_absent(self, ns: str, *, timeout: int = 600) -> None:
        elapsed, interval = 0, 5
        log(f"Waiting for namespace '{ns}' to be fully deleted.")
        while self.silent_check("get", "namespace", ns, ns=None):
            if elapsed >= timeout:
                fatal(f"Timed out waiting for namespace '{ns}' deletion.")
            time.sleep(interval)
            elapsed += interval

    def wait_for_pvc_absent(self, ns: str, *, timeout: int = 300) -> None:
        elapsed, interval = 0, 5
        log(f"Waiting for PVCs to be absent in namespace '{ns}'.")
        while self.silent_check("get", "pvc", ns=ns):
            if elapsed >= timeout:
                fatal(f"Timed out waiting for PVC deletion in namespace '{ns}'.")
            time.sleep(interval)
            elapsed += interval

    def ensure_namespace(self, ns: str) -> None:
        log(f"Ensuring namespace exists: {ns}")
        if self.dry_run:
            log(f"[dry-run] {self.bin} create namespace {ns} --dry-run=client -o yaml | apply -f -")
            return
        # `create namespace --dry-run=client -o yaml | kubectl apply -f -`
        # The --dry-run=client form makes this idempotent.
        proc = run(self.cmd("create", "namespace", ns, "--dry-run=client", "-o", "yaml",
                            ns=None),
                   capture=True, check=True, log_cmd=False)
        run(self.cmd("apply", "-f", "-", ns=None),
            input_text=proc.stdout, check=True, log_cmd=False)

    def delete_pv(self, pv: str, *, wait: bool = True) -> None:
        if not self.silent_check("get", "pv", pv, ns=None):
            log(f"PV '{pv}' not found; skipping.")
            return
        log(f"Deleting PV '{pv}' (wait={wait})")
        self.run("delete", "pv", pv, f"--wait={'true' if wait else 'false'}", ns=None)
        if not wait and not self.dry_run:
            elapsed, interval, timeout = 0, 5, 180
            while self.silent_check("get", "pv", pv, ns=None):
                if elapsed >= timeout:
                    fatal(f"Timed out deleting PV '{pv}'. Check finalizers/claim deps.")
                time.sleep(interval)
                elapsed += interval

    # ---- pods / wait ----
    def wait_pods_ready(self, *, selector: str, ns: Optional[str] = None,
                        timeout: str) -> None:
        ns = ns or self.ns
        if not self.dry_run:
            if not self.silent_check("get", "pod", "-l", selector, "-o", "name", ns=ns):
                fatal(f"No pods found for readiness selector '{selector}' in namespace '{ns}'.")
        log(f"Waiting for pods Ready (selector='{selector}', ns='{ns}', timeout={timeout})")
        self.run("wait", "--for=condition=Ready", "pod", "-l", selector,
                 f"--timeout={timeout}", ns=ns)

    def wait_pod_ready(self, *, pod: str, ns: Optional[str] = None,
                       timeout: str) -> None:
        ns = ns or self.ns
        log(f"Waiting for pod '{pod}' Ready (ns='{ns}', timeout={timeout})")
        self.run("wait", "--for=condition=Ready", f"pod/{pod}",
                 f"--timeout={timeout}", ns=ns)

    def get_running_pod_for_selector(self, selector: str, ns: Optional[str] = None,
                                     *, appear_timeout: int = 600) -> str:
        ns = ns or self.ns
        if self.dry_run:
            return "dryrun-pod"
        elapsed, interval = 0, 5
        while True:
            names = self.output("get", "pod", "-l", selector, "-o",
                                'jsonpath={range .items[?(@.status.phase=="Running")]}'
                                '{.metadata.name}{"\\n"}{end}',
                                ns=ns, check=False).splitlines()
            names = [n for n in (n.strip() for n in names) if n]
            if names:
                return names[0]
            # fall back to any pod
            any_pod = self.output("get", "pod", "-l", selector, "-o",
                                  "jsonpath={.items[0].metadata.name}",
                                  ns=ns, check=False).strip()
            if any_pod:
                return any_pod
            if elapsed >= appear_timeout:
                fatal(f"No pod found for selector '{selector}' in namespace '{ns}' "
                      f"within {appear_timeout}s")
            time.sleep(interval)
            elapsed += interval

    def exec(self, pod: str, *cmd: str, ns: Optional[str] = None,
             container: Optional[str] = None, check: bool = True,
             capture: bool = False) -> "subprocess.CompletedProcess":
        ns = ns or self.ns
        args = ["exec", pod]
        if container:
            args += ["-c", container]
        args += ["--", *cmd]
        return run(self.cmd(*args, ns=ns), dry_run=self.dry_run,
                   check=check, capture=capture, log_cmd=True)

    def exec_capture(self, pod: str, *cmd: str, ns: Optional[str] = None,
                     container: Optional[str] = None, check: bool = True) -> str:
        ns = ns or self.ns
        args = ["exec", pod]
        if container:
            args += ["-c", container]
        args += ["--", *cmd]
        return run_check_output(self.cmd(*args, ns=ns),
                                dry_run=self.dry_run, check=check, log_cmd=False)

    def cp_to_pod(self, src: str, pod: str, dst: str, *,
                  ns: Optional[str] = None, container: Optional[str] = None) -> None:
        ns = ns or self.ns
        target = f"{pod}:{dst}"
        args = ["cp", src, target]
        if container:
            args += ["-c", container]
        run(self.cmd(*args, ns=ns), dry_run=self.dry_run, check=True)

    # ---- helm release queries (still via kubectl labels) ----
    def helm_release_in_ns(self, release: str, ns: Optional[str] = None) -> bool:
        ns = ns or self.ns
        # Helm secrets are labelled name=<release>; presence == release exists.
        return self.silent_check(
            "get", "secret", "-l",
            f"name={release},owner=helm", "-o", "name", ns=ns,
        )
