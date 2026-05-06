"""`idea4rc-capsule install` — capsule-side host prerequisite installer.

Installs the OS-level tools the capsule deploy/ingest pipeline needs
(kubectl/microk8s, helm, git, curl, jq, pandoc, ...). It does NOT install
the Vault server -- that lives behind ``idea4rc-capsule vault install``
so the two setup phases (Vault admin vs capsule operator) stay cleanly
separated.

When run with ``--auto`` the command actually installs missing packages
(apt + snap; Linux/Debian only). Without ``--auto`` it only checks what
is missing and prints actionable instructions, so partners on macOS or
non-apt distros can follow them manually.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path
from typing import Iterable, Optional

from idea4rc_capsule.logging import fatal, log
from idea4rc_capsule.shell import require_tool, run, run_check_output


# Tools we expect on the host. (binary, apt-package, snap-package-or-None, hint)
# NOTE: `vault` is intentionally NOT in this table -- the Vault server is
# installed via `idea4rc-capsule vault install` so partners that don't run
# their own Vault server (e.g. those using a remote shared instance) don't
# get an unwanted apt install of vault by `idea4rc-capsule install --auto`.
TOOL_TABLE: list[tuple[str, str, Optional[str], str]] = [
    ("git",     "git",          None,                  "version control"),
    ("curl",    "curl",         None,                  "HTTP client"),
    ("gpg",     "gnupg",        None,                  "apt key verification"),
    ("jq",      "jq",           None,                  "JSON processing in helpers"),
    ("pandoc",  "pandoc",       None,                  "Markdown -> HTML for audit summary"),
    ("python3", "python3",      None,                  "Python 3"),
    ("pip",     "python3-pip",  None,                  "pip"),
    # microk8s is the typical k8s on these hosts, but partners may use any.
    ("microk8s",None,           "microk8s",            "MicroK8s (or substitute kubeadm/k3s)"),
    ("helm",    None,           "helm --classic",      "Helm 3"),
]


def _have(binary: str) -> bool:
    return shutil.which(binary) is not None


def _is_root() -> bool:
    return os.geteuid() == 0


def _apt_install(pkgs: Iterable[str]) -> None:
    pkgs = [p for p in pkgs if p]
    if not pkgs:
        return
    if not _is_root():
        fatal(f"Need root to apt-install: {pkgs}. Re-run with sudo.")
    log(f"apt-get install -y {' '.join(pkgs)}")
    run(["apt-get", "update", "-y"], check=True)
    run(["apt-get", "install", "-y", *pkgs], check=True)


def _snap_install(snap_spec: str) -> None:
    if not _is_root():
        fatal(f"Need root to snap install: {snap_spec}. Re-run with sudo.")
    parts = snap_spec.split()
    log(f"snap install {' '.join(parts)}")
    run(["snap", "install", *parts], check=True)


# ----------------------------------------------------------------------- CLI
def cmd_install(args: argparse.Namespace) -> int:
    auto = args.auto
    missing_apt: list[str] = []
    missing_snap: list[str] = []
    missing_other: list[tuple[str, str]] = []

    log("Checking capsule host prerequisites...")
    for binary, apt_pkg, snap_pkg, hint in TOOL_TABLE:
        if _have(binary):
            log(f"  ok: {binary}  ({hint})")
            continue
        if apt_pkg:
            log(f"  MISSING: {binary}  -> apt install {apt_pkg}  ({hint})")
            missing_apt.append(apt_pkg)
        elif snap_pkg:
            log(f"  MISSING: {binary}  -> snap install {snap_pkg}  ({hint})")
            missing_snap.append(snap_pkg)
        else:
            log(f"  MISSING: {binary}  ({hint})")
            missing_other.append((binary, hint))

    if not auto:
        if missing_apt or missing_snap or missing_other:
            log("\nRun with --auto to install everything above (Linux/Debian + snapd).")
            log("Note: this command does NOT install Vault. For that, run "
                "`sudo idea4rc-capsule vault install --auto`.")
            return 1
        log("All capsule prerequisites present.")
        return 0

    if not _is_root():
        fatal("--auto requires root; re-run with sudo.")
    if missing_apt:
        _apt_install(missing_apt)
    for s in missing_snap:
        _snap_install(s)
    if missing_other:
        log("These tools are not installable from apt/snap; install them manually:")
        for b, hint in missing_other:
            log(f"  {b}  ({hint})")

    log("install: done. (To install the Vault server: `sudo idea4rc-capsule "
        "vault install --auto`)")
    return 0


def add_subcommands(parent: "argparse._SubParsersAction") -> None:
    p = parent.add_parser(
        "install",
        help="Check / install capsule prerequisites (helm, microk8s, git, pandoc, ...)",
    )
    p.add_argument("--auto", action="store_true",
                   help="Actually install missing packages (apt + snap; Linux/Debian only).")
    p.set_defaults(func=cmd_install)
