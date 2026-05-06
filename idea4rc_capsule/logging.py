"""Tiny logging helpers — match the bash style ([YYYY-MM-DD HH:MM:SS] msg)."""

from __future__ import annotations

import datetime as _dt
import sys


def _ts() -> str:
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg: str) -> None:
    print(f"[{_ts()}] {msg}", flush=True)


def warn(msg: str) -> None:
    print(f"[{_ts()}] WARN: {msg}", file=sys.stderr, flush=True)


def err(msg: str) -> None:
    print(f"[{_ts()}] ERROR: {msg}", file=sys.stderr, flush=True)


class FatalError(SystemExit):
    """Raised to abort the run with a clear message."""

    def __init__(self, msg: str, code: int = 1) -> None:
        err(msg)
        super().__init__(code)


def fatal(msg: str, code: int = 1) -> None:
    raise FatalError(msg, code)
