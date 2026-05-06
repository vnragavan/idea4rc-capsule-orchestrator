"""Post-ingestion audit pipeline + Markdown -> HTML summary conversion.

Bash counterparts: ``run_audit``, ``generate_audit_html``,
``build_audit_command``.
"""

from __future__ import annotations

import argparse
import html
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from idea4rc_capsule.config import Config, load_config
from idea4rc_capsule.logging import fatal, log
from idea4rc_capsule.runtime import RunContext, tee_to_file


_TEMPLATE_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _substitute_template(template: str, *,
                         csv_path: Optional[Path], namespace: str,
                         template_name: str,
                         env: Optional[dict[str, str]] = None) -> str:
    """Replace ${VAR}, __CSV_PATH__, __NAMESPACE__ in ``template``.

    fatal()s if a referenced ${VAR} is not in the environment.
    """
    import os
    env = env or os.environ
    out = template

    for match in _TEMPLATE_VAR.finditer(template):
        var = match.group(1)
        if var not in env:
            fatal(f"{template_name} references ${{{var}}} but {var} is not set "
                  f"in the environment.")

    def _repl(m: re.Match) -> str:
        return env[m.group(1)]

    out = _TEMPLATE_VAR.sub(_repl, out)
    if csv_path is not None:
        out = out.replace("__CSV_PATH__", str(csv_path))
    out = out.replace("__NAMESPACE__", namespace)
    if "__CSV_PATH__" in out:
        fatal(f"{template_name} still contains __CSV_PATH__ after substitution")
    if "__NAMESPACE__" in out:
        fatal(f"{template_name} still contains __NAMESPACE__ after substitution")
    return out


def build_audit_command(cfg: Config, csv: Optional[Path]) -> str:
    if not cfg.audit.command_template:
        return ""
    return _substitute_template(
        cfg.audit.command_template,
        csv_path=csv,
        namespace=cfg.k8s.namespace,
        template_name="audit.command_template",
    )


def generate_audit_html(cfg: Config, *, dry_run: bool = False) -> bool:
    """Convert ``cfg.audit.summary_md_path`` -> ``cfg.audit.summary_html_path``.

    Prefers ``pandoc``; falls back to a tiny inline template via
    ``html.escape`` if pandoc is unavailable. Returns True on success.
    """
    md = Path(cfg.audit.summary_md_path) if cfg.audit.summary_md_path else None
    out = Path(cfg.audit.summary_html_path) if cfg.audit.summary_html_path else None
    if md is None and out is None:
        log("audit.summary_md_path / summary_html_path not configured; "
            "skipping HTML conversion.")
        return True

    if md is None:
        # No markdown path — audit pipeline may write HTML itself (e.g. summarize --html).
        if dry_run:
            log("[dry-run] No markdown source; HTML expected from audit.command_template.")
        return True

    if out is None:
        log("audit.summary_html_path not set; skipping HTML conversion.")
        return True

    log(f"Generating audit HTML summary: {out}")
    if dry_run:
        log(f"[dry-run] Convert {md} -> {out}")
        return True
    if not md.is_file():
        log(f"ERROR: Audit markdown summary not found: {md}")
        return False
    out.parent.mkdir(parents=True, exist_ok=True)

    if shutil.which("pandoc"):
        proc = subprocess.run(["pandoc", str(md), "-o", str(out)],
                              check=False)
        if proc.returncode == 0:
            log(f"Audit HTML summary written: {out}")
            return True
        log(f"ERROR: pandoc conversion failed for {md} -> {out}.")
        return False

    content = md.read_text(encoding="utf-8")
    out.write_text(
        "<!doctype html>\n"
        "<html lang=\"en\">\n"
        "<head>\n"
        "  <meta charset=\"utf-8\" />\n"
        "  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />\n"
        "  <title>Audit Summary</title>\n"
        "  <style>\n"
        "    body { font-family: system-ui, -apple-system, sans-serif; margin: 2rem; }\n"
        "    pre { white-space: pre-wrap; word-wrap: break-word; }\n"
        "  </style>\n"
        "</head>\n"
        "<body>\n"
        "  <h1>Audit Summary</h1>\n"
        f"  <pre>{html.escape(content)}</pre>\n"
        "</body>\n"
        "</html>\n",
        encoding="utf-8",
    )
    log(f"Audit HTML summary written (python fallback): {out}")
    return True


def run_audit_command(ctx: RunContext, cfg: Config) -> int:
    """Run the configured audit pipeline. Always tries to write the HTML
    summary, even if the audit script returned a non-zero exit code (the
    bash version intentionally does the same so post-mortems still get a
    rendered SUMMARY.html). Returns the audit pipeline rc."""
    cmd = build_audit_command(cfg, ctx.csv_path)
    if not cmd:
        log("audit.command_template is empty; skipping post-ingestion audit.")
        return 0

    log_file = ctx.log_dir / f"audit-{ctx.run_id}.log"
    log(f"Running post-ingestion audit. Log: {log_file}")
    if ctx.dry_run:
        log(f"[dry-run] {cmd}")
        generate_audit_html(cfg, dry_run=True)
        return 0

    with tee_to_file(ctx, log_file):
        proc = subprocess.run(["bash", "-o", "pipefail", "-c", cmd],
                              check=False)
    rc = proc.returncode

    md = Path(cfg.audit.summary_md_path) if cfg.audit.summary_md_path else None
    html_out = Path(cfg.audit.summary_html_path) if cfg.audit.summary_html_path else None

    if md is not None and md.is_file():
        if not generate_audit_html(cfg):
            log(f"WARNING: HTML summary generation failed; markdown is at {md}.")
    elif md is not None:
        log(f"Audit markdown summary not found at {md}; skipping HTML conversion.")
    elif html_out is not None:
        if html_out.is_file():
            log(f"Audit HTML summary present: {html_out}")
        else:
            log(f"WARNING: Expected audit HTML at {html_out} not found.")

    if rc != 0:
        # Non-zero here means the bash pipeline crashed, missing scripts,
        # kubectl/psql errors, etc. — not the same as “pipeline verdict FAILED”
        # in SUMMARY.md / audit-verdict.env (use audit-summarize --exit-on-verdict for CI).
        md_note = (
            f"Markdown (if any): {cfg.audit.summary_md_path}. "
            if cfg.audit.summary_md_path
            else ""
        )
        fatal(
            f"Audit pipeline error (exit {rc}) — collection/summarizer did not complete. "
            f"See log: {log_file}. {md_note}"
            f"HTML (if any): {cfg.audit.summary_html_path}."
        )
    return rc


def cmd_audit(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.config))
    ctx = RunContext(log_dir=Path(cfg.paths.log_dir),
                     dry_run=args.dry_run,
                     csv_path=Path(args.csv) if args.csv else None)
    ctx.log_dir.mkdir(parents=True, exist_ok=True)
    return run_audit_command(ctx, cfg)


def add_subcommands(parent: "argparse._SubParsersAction") -> None:
    p = parent.add_parser(
        "audit",
        help="Run the audit pipeline only",
    )
    p.add_argument("--config", required=True, help="Path to capsule.toml")
    p.add_argument("--csv", help="CSV path (used to resolve __CSV_PATH__ "
                   "in audit.command_template)")
    p.add_argument("--dry-run", action="store_true",
                   help="Print actions without executing them")
    p.set_defaults(func=cmd_audit)
