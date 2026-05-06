"""Runtime infrastructure shared across deploy/ingest commands.

Provides:
- :class:`RunContext`: per-invocation state (RUN_ID, log_dir, csv_path,
  dry_run, lockfile, cleanup callbacks).
- :func:`acquire_lock`: idempotent lock honouring stale PIDs.
- :func:`tee_log`: helper that runs a function while teeing every
  ``log()`` call into a phase-specific log file.
"""

from __future__ import annotations

import datetime as _dt
import os
import pwd
import signal
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from idea4rc_capsule.logging import fatal, log, warn


def _ts_for_run_id() -> str:
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S")


@dataclass
class RunContext:
    """Mutable per-invocation state.

    Cleanup callbacks are run LIFO from :meth:`cleanup`. Use them for any
    resource that must be released regardless of how the run exits
    (port-forward children, tmpfs cert dirs, vault env files, ...).
    """

    log_dir: Path
    dry_run: bool = False
    run_id: str = field(default_factory=_ts_for_run_id)
    csv_path: Optional[Path] = None
    last_log_file: Optional[Path] = None
    lock_dir: Optional[Path] = None
    _cleanups: list[Callable[[], None]] = field(default_factory=list)

    def add_cleanup(self, fn: Callable[[], None]) -> None:
        self._cleanups.append(fn)

    def cleanup(self) -> None:
        while self._cleanups:
            fn = self._cleanups.pop()
            try:
                fn()
            except Exception as exc:  # noqa: BLE001
                warn(f"cleanup hook failed: {exc!r}")
        if self.lock_dir is not None:
            try:
                self.lock_dir.joinpath("pid").unlink(missing_ok=True)
                self.lock_dir.rmdir()
            except OSError:
                pass
            self.lock_dir = None


def prepare_logs(ctx: RunContext, *, keep_logs: bool) -> None:
    """Make sure ctx.log_dir exists and is chmod 700.

    When ``keep_logs`` is False (the default for fresh runs), wipe any
    pre-existing ``*.log`` files. Mirrors fresh_ingest.sh's prepare_logs.
    """
    ctx.log_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(ctx.log_dir, 0o700)
    except PermissionError:
        pass
    if not keep_logs:
        log(f"Removing old logs in {ctx.log_dir}")
        if not ctx.dry_run:
            for path in ctx.log_dir.glob("*.log"):
                try:
                    path.unlink()
                except OSError:
                    pass


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def acquire_lock(ctx: RunContext) -> None:
    """Acquire ctx.log_dir/.fresh_ingest.lock. Honours stale PIDs.

    Robust against the lockdir or its inner pid file being owned by
    another user (e.g. left over from a previous ``sudo`` run): when
    we lack permission to inspect the inner pid file, we fatal with a
    clear instruction instead of a PermissionError traceback.
    """
    lock = ctx.log_dir / ".fresh_ingest.lock"
    pid_file = lock / "pid"
    try:
        lock.mkdir()
        pid_file.write_text(f"{os.getpid()}\n")
        ctx.lock_dir = lock
        return
    except FileExistsError:
        pass

    # An inherited / stale lockdir we cannot stat into typically means
    # the previous holder ran as a different user. We can't safely
    # determine liveness or remove it as the current user; surface the
    # exact cleanup command instead.
    try:
        is_pid_file = pid_file.is_file()
    except PermissionError:
        try:
            owner_uid = lock.stat().st_uid
            owner_user = pwd.getpwuid(owner_uid).pw_name
            owner_str = f"user '{owner_user}' (uid {owner_uid})"
        except Exception:  # noqa: BLE001
            owner_str = "another user"
        fatal(
            f"Lockdir {lock} exists and is owned by {owner_str}, "
            f"so this process cannot inspect or release it. This usually "
            f"means a previous run was launched with `sudo`. Run once:\n"
            f"  sudo rm -rf {lock}\n"
            f"then retry your ingest."
        )

    if is_pid_file:
        try:
            existing = pid_file.read_text().strip()
        except PermissionError:
            fatal(
                f"Cannot read {pid_file} (permission denied). The "
                f"lockfile is owned by another user. Run once:\n"
                f"  sudo rm -rf {lock}\n"
                f"then retry your ingest."
            )
        if existing.isdigit() and not _pid_alive(int(existing)):
            log(f"Removing stale lock from dead pid {existing}.")
            try:
                pid_file.unlink(missing_ok=True)
                lock.rmdir()
            except OSError:
                pass
            try:
                lock.mkdir()
                pid_file.write_text(f"{os.getpid()}\n")
                ctx.lock_dir = lock
                return
            except FileExistsError:
                pass
        fatal(f"Another fresh_ingest run appears active (pid: {existing}).")
    fatal(f"Another fresh_ingest run appears active (lock: {lock}).")


@contextmanager
def install_signal_handlers(ctx: RunContext):
    """Ensure cleanup runs even on SIGINT/SIGTERM."""
    received: dict[str, int] = {}

    def _handler(signum, _frame):
        received["sig"] = signum
        warn(f"received signal {signum}; cleaning up...")
        ctx.cleanup()
        sys.exit(128 + signum)

    old_int = signal.signal(signal.SIGINT, _handler)
    old_term = signal.signal(signal.SIGTERM, _handler)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, old_int)
        signal.signal(signal.SIGTERM, old_term)


@contextmanager
def tee_to_file(ctx: RunContext, log_path: Path):
    """Tee subsequent stdout writes to ``log_path`` at the Python level.

    Captures only ``print()`` / ``log()`` calls — subprocess output is
    NOT captured because it bypasses ``sys.stdout``. Use
    :func:`tee_run_to_file` for a full-fidelity capture instead.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fh = log_path.open("a", buffering=1, encoding="utf-8")
    ctx.last_log_file = log_path

    class _Tee:
        def __init__(self, *streams):
            self._streams = streams

        def write(self, data):
            for s in self._streams:
                s.write(data)
                s.flush()
            return len(data)

        def flush(self):
            for s in self._streams:
                s.flush()

    real_out, real_err = sys.stdout, sys.stderr
    sys.stdout = _Tee(real_out, fh)
    sys.stderr = _Tee(real_err, fh)
    try:
        yield log_path
    finally:
        sys.stdout = real_out
        sys.stderr = real_err
        fh.close()


@contextmanager
def tee_run_to_file(ctx: RunContext, log_path: Path):
    """Tee fds 1 + 2 (stdout + stderr) to ``log_path`` AND original terminal.

    Equivalent to running the entire process with ``2>&1 | tee log_path``.
    Captures both Python ``print()`` and subprocess output (kubectl, helm,
    git, etc.) by redirecting at the OS file-descriptor level.

    Implementation: spawn a `tee -i -a log_path` subprocess whose stdin is
    a pipe; `dup2` our fds 1 + 2 onto that pipe; on exit, restore the
    original fds and reap tee.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.touch(exist_ok=True)
    ctx.last_log_file = log_path

    # Save originals so we can restore on exit.
    saved_out_fd = os.dup(1)
    saved_err_fd = os.dup(2)

    # `-i` makes tee ignore SIGINT so Ctrl-C is delivered to us, not tee.
    # `-a` appends rather than truncating (idempotent across phases).
    tee = subprocess.Popen(
        ["tee", "-i", "-a", str(log_path)],
        stdin=subprocess.PIPE,
        stdout=saved_out_fd,
        stderr=saved_err_fd,
    )
    if tee.stdin is None:  # pragma: no cover
        os.close(saved_out_fd)
        os.close(saved_err_fd)
        raise RuntimeError("tee subprocess produced no stdin pipe")

    # Redirect our stdout/stderr to tee's stdin.
    sys.stdout.flush()
    sys.stderr.flush()
    os.dup2(tee.stdin.fileno(), 1)
    os.dup2(tee.stdin.fileno(), 2)

    try:
        yield log_path
    finally:
        # Flush Python buffers before swapping fds back, otherwise we'd
        # write into the soon-to-be-closed pipe.
        try:
            sys.stdout.flush()
        except Exception:  # noqa: BLE001
            pass
        try:
            sys.stderr.flush()
        except Exception:  # noqa: BLE001
            pass
        # Restore original fds.
        os.dup2(saved_out_fd, 1)
        os.dup2(saved_err_fd, 2)
        os.close(saved_out_fd)
        os.close(saved_err_fd)
        # Close tee's stdin and reap.
        try:
            tee.stdin.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            tee.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            tee.kill()
            tee.wait(timeout=2)


def validate_csv_path(csv: Optional[str]) -> Path:
    """Mirror bash validate_csv_path: absolute, exists, .csv suffix."""
    if not csv:
        fatal("--csv is required")
    p = Path(csv)
    if not p.is_absolute():
        fatal("--csv must be an absolute path")
    if not p.is_file():
        fatal(f"CSV file does not exist at path: {p}")
    if p.suffix.lower() != ".csv":
        fatal("--csv must point to a .csv file")
    return p
