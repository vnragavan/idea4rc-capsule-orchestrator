"""Top-level entry point for the `idea4rc-capsule` console script.

Dispatches to one of the package's subcommands. Each subcommand registers
itself by exposing a module-level `add_subcommands(parent)` function. We
collect them here so adding a new command means: write a new module + add
one line to ``_SUBCOMMAND_MODULES`` below.
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional

from idea4rc_capsule import __version__
from idea4rc_capsule.logging import FatalError, err


# Each module must expose `add_subcommands(parent: _SubParsersAction) -> None`
# and register a `func` default on its sub-parser.
def _subcommand_modules():
    # Imported lazily so a single broken module doesn't crash --help.
    from idea4rc_capsule import (  # noqa: F401
        audit as _audit,
        check_config as _check_config,
        deploy as _deploy,
        destroy as _destroy_cmd,
        init_config as _init_config,
        ingest as _ingest,
        install,
        recover as _recover_cmd,
        repo_sync as _repo_sync_cmd,
        status as _status,
        vault_cmd,
    )
    return [
        install,
        _init_config,
        _check_config,
        _status,
        vault_cmd,
        _repo_sync_cmd,
        _destroy_cmd,
        _deploy,
        _ingest,
        _audit,
        _recover_cmd,
    ]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="idea4rc-capsule",
        description="End-to-end IDEA4RC capsule deployment + ingestion automation.",
    )
    p.add_argument("--version", action="version",
                   version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)
    for mod in _subcommand_modules():
        mod.add_subcommands(sub)
    return p


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 2
    try:
        return int(func(args) or 0)
    except FatalError as exc:
        return int(exc.code) if isinstance(exc.code, int) else 1
    except KeyboardInterrupt:
        err("aborted by user.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
