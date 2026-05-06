"""`idea4rc-capsule init-config` — print a documented starter capsule.toml.

The sample lives in ``idea4rc_capsule.data.capsule.sample.toml`` and is
loaded via ``importlib.resources`` so it works whether installed from a
wheel or run from source.
"""

from __future__ import annotations

import argparse
import sys
from importlib import resources


def _sample() -> str:
    return resources.files("idea4rc_capsule.data") \
        .joinpath("capsule.sample.toml") \
        .read_text(encoding="utf-8")


def cmd_init_config(args: argparse.Namespace) -> int:
    out = _sample()
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(out)
        print(f"Wrote sample config to {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(out)
    return 0


def add_subcommands(parent: "argparse._SubParsersAction") -> None:
    p = parent.add_parser(
        "init-config",
        help="Print a documented starter capsule.toml",
    )
    p.add_argument("-o", "--output",
                   help="Write to file instead of stdout")
    p.set_defaults(func=cmd_init_config)
