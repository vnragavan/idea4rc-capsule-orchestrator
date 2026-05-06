"""`idea4rc-helm-post-renderer` — strip selected kinds from helm output.

Helm 3 supports ``--post-renderer EXEC``: the rendered manifests are piped
to EXEC's stdin, EXEC's stdout becomes the manifests that helm actually
applies.  We use it as the orchestrator-side replacement for forking the
chart to gate Namespace resources behind a values flag.

Usage as a helm post-renderer (not invoked directly by users):
    helm install ... --post-renderer /path/to/idea4rc-helm-post-renderer

Behaviour:
    * stdin  ->  multi-doc YAML produced by helm (each doc separated by a
                 ``---`` line)
    * stdout ->  same documents, MINUS any whose top-level ``kind`` is in
                 ``IDEA4RC_HPR_DROP_KINDS`` (comma-separated env var,
                 default ``Namespace``)

Implementation notes:
    * No external deps.  We only need to read ``kind:`` at column 0 of each
      document, which is unambiguous for helm-rendered output.  Avoids
      taking a hard runtime dep on PyYAML solely for this filter.
    * Exit code 0 even when nothing is dropped (helm contract).
    * Comments and blank lines inside a doc are preserved verbatim.
    * The first ``kind:`` at column 0 wins per document (k8s manifests
      have exactly one).
"""

from __future__ import annotations

import os
import re
import sys
from typing import Iterable

_KIND_RE = re.compile(r"^kind:\s*(\S+)\s*$")
_SEP_RE = re.compile(r"^---\s*$")


def _split_docs(stream: Iterable[str]) -> list[list[str]]:
    """Split a YAML stream into per-document line lists.

    A leading ``---`` opens a new document; we treat the input as a series
    of documents, where each document includes everything up to (but not
    including) the next ``---`` line.  Empty leading docs are kept so we
    can round-trip the input faithfully.
    """
    docs: list[list[str]] = [[]]
    for line in stream:
        if _SEP_RE.match(line):
            docs.append([])
        else:
            docs[-1].append(line)
    return docs


def _doc_kind(doc: list[str]) -> str:
    for line in doc:
        m = _KIND_RE.match(line)
        if m:
            return m.group(1)
    return ""


def _drop_kinds_from_env() -> set[str]:
    raw = os.environ.get("IDEA4RC_HPR_DROP_KINDS", "Namespace")
    return {k.strip() for k in raw.split(",") if k.strip()}


def filter_stream(input_lines: Iterable[str], drop_kinds: set[str]) -> str:
    docs = _split_docs(input_lines)
    out_docs: list[list[str]] = []
    dropped: list[str] = []
    for doc in docs:
        if not any(line.strip() for line in doc):
            out_docs.append(doc)
            continue
        kind = _doc_kind(doc)
        if kind in drop_kinds:
            dropped.append(kind)
            continue
        out_docs.append(doc)

    if dropped:
        sys.stderr.write(
            f"[idea4rc-helm-post-renderer] dropped {len(dropped)} doc(s) "
            f"(kinds: {sorted(set(dropped))}); "
            f"set IDEA4RC_HPR_DROP_KINDS to override.\n"
        )

    out = ""
    for i, doc in enumerate(out_docs):
        if i > 0:
            out += "---\n"
        out += "".join(doc)
    return out


def main() -> int:
    if any(arg in ("-h", "--help") for arg in sys.argv[1:]):
        sys.stdout.write(
            "usage: idea4rc-helm-post-renderer < rendered.yaml > filtered.yaml\n\n"
            "Strip selected Kubernetes resource kinds from Helm-rendered YAML.\n"
            "Configure the comma-separated kind list with "
            "IDEA4RC_HPR_DROP_KINDS; default: Namespace.\n"
        )
        return 0
    drop_kinds = _drop_kinds_from_env()
    if not drop_kinds:
        sys.stdout.write(sys.stdin.read())
        return 0
    rendered = filter_stream(sys.stdin, drop_kinds)
    sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
