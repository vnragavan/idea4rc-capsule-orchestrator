"""Subprocess helpers with dry-run support and structured error handling."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Sequence, Union

from idea4rc_capsule.logging import fatal, log

CmdLike = Union[str, Sequence[str]]


def _fmt(cmd: Sequence[str]) -> str:
    return " ".join(shlex.quote(str(p)) for p in cmd)


def require_tool(tool: str) -> str:
    """Resolve ``tool`` on PATH or fatal()."""
    path = shutil.which(tool)
    if not path:
        fatal(f"Required tool not found: {tool}")
    return path


def run(
    cmd: Sequence[str],
    *,
    dry_run: bool = False,
    check: bool = True,
    env: Optional[dict] = None,
    cwd: Optional[Union[str, Path]] = None,
    input_text: Optional[str] = None,
    capture: bool = False,
    log_cmd: bool = True,
) -> subprocess.CompletedProcess:
    """Run ``cmd`` synchronously.

    On dry_run prints the command and returns a synthetic CompletedProcess
    with rc=0, empty stdout/stderr. On non-zero exit and ``check=True``,
    fatal()s with the captured/visible output.
    """
    cmd_list = [str(p) for p in cmd]
    if log_cmd:
        log(f"$ {_fmt(cmd_list)}")
    if dry_run:
        return subprocess.CompletedProcess(cmd_list, 0, "", "")

    full_env = None
    if env is not None:
        full_env = os.environ.copy()
        full_env.update({k: str(v) for k, v in env.items() if v is not None})

    try:
        proc = subprocess.run(
            cmd_list,
            input=input_text,
            text=True,
            capture_output=capture,
            env=full_env,
            cwd=str(cwd) if cwd else None,
            check=False,
        )
    except FileNotFoundError as exc:
        fatal(f"Command not found: {cmd_list[0]} ({exc})")
    if check and proc.returncode != 0:
        msg = f"Command failed (rc={proc.returncode}): {_fmt(cmd_list)}"
        if capture:
            if proc.stdout:
                msg += f"\nstdout: {proc.stdout.strip()}"
            if proc.stderr:
                msg += f"\nstderr: {proc.stderr.strip()}"
        fatal(msg, code=proc.returncode or 1)
    return proc


def run_check_output(
    cmd: Sequence[str],
    *,
    dry_run: bool = False,
    check: bool = True,
    env: Optional[dict] = None,
    cwd: Optional[Union[str, Path]] = None,
    input_text: Optional[str] = None,
    log_cmd: bool = False,
) -> str:
    """Run ``cmd`` and return its stdout (stripped). For value-fetching."""
    if dry_run:
        log(f"[dry-run] (capture) {_fmt(cmd)}")
        return ""
    proc = run(
        cmd,
        dry_run=False,
        check=check,
        env=env,
        cwd=cwd,
        input_text=input_text,
        capture=True,
        log_cmd=log_cmd,
    )
    return (proc.stdout or "").strip()


def run_pipe(
    producer: Sequence[str],
    consumer: Sequence[str],
    *,
    dry_run: bool = False,
    check: bool = True,
    log_cmd: bool = True,
) -> int:
    """Equivalent of `producer | consumer`. Returns the producer's rc.

    Used to honour `set -o pipefail` semantics: a non-zero producer rc
    aborts (matching `tee`-piped sections in fresh_ingest.sh).
    """
    if log_cmd:
        log(f"$ {_fmt(producer)} | {_fmt(consumer)}")
    if dry_run:
        return 0
    p1 = subprocess.Popen(list(producer), stdout=subprocess.PIPE, text=True)
    p2 = subprocess.Popen(list(consumer), stdin=p1.stdout, text=True)
    if p1.stdout is not None:
        p1.stdout.close()
    p2.communicate()
    p1.wait()
    rc = p1.returncode
    if check and rc != 0:
        fatal(f"Pipeline producer failed (rc={rc}): {_fmt(producer)}")
    return rc
