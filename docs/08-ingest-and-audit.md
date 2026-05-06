# Ingest and audit

`idea4rc-capsule ingest --config $HOME/capsule.toml --csv /absolute/path.csv` runs repo sync when enabled, deploy unless skipped, curl upload with optional port-forward, three adaptive drains, `omop-etl` restart, OMOP wait, then `audit.command_template`.

## Prerequisites

Successful prior deploy unless you intentionally pass **`--skip-deploy`** (requires existing Helm release). CSV path must be absolute (`runtime.validate_csv_path`).

## How It Works

High-level order (`idea4rc_capsule/ingest.py`):

1. Preflight (unless `--skip-preflight`).
2. Confirmation prompt unless `--yes` or `[safety].auto_confirm_destruction`.
3. `prepare_logs` + **`acquire_lock`** → lock dir `<paths.log_dir>/.fresh_ingest.lock` (for example `$HOME/capsule-ingestion-logs/.fresh_ingest.lock`; see [10-troubleshooting.md](10-troubleshooting.md)).
4. **`repo_sync`** when enabled — skipped for `--skip-deploy` unless **`--with-repo-sync`**.
5. **`fetch_install_secrets`** — sets process env e.g. `CAPSULE_PUB_IP`, port-forward local port for templates.
6. **`deploy_phase`** unless `--skip-deploy`.
7. **Upload** — bash runs `ingest.public_template` or `ingest.port_forward_template` with substitutions (`__CSV_PATH__`, `__NAMESPACE__`, `${INGEST_PORT_FORWARD_LOCAL_PORT}`). Wrapped in **`port_forward`** context when mode is port-forward (`idea4rc_capsule/port_forward.py`).
8. **`wait_aerospike`** — uses `[aerospike_drain]` knobs.
9. **`wait_staging`** — `[staging_drain]` knobs.
10. **`restart_omop_etl`** after staging populated.
11. **`wait_omop`** — `[omop_drain]` knobs.
12. **`run_audit_command`** — executes `[audit].command_template` via bash; reconciles `summary_md_path` / `summary_html_path` (`idea4rc_capsule/audit.py`).

### Audit hand-off

| Key | Role |
| --- | --- |
| `audit.command_template` | Shell command; may reference `__CSV_PATH__` when you pass `idea4rc-capsule audit --csv` |
| `audit.summary_md_path` | If non-empty and file exists after command, optional pandoc/HTML fallback |
| `audit.summary_html_path` | Expected HTML output location |

Empty `command_template` skips audit with log message.

`run_audit_command()` uses `tee_to_file`, not the fd-level `tee_run_to_file`; Python log lines go to `audit-<run_id>.log`, but raw stdout/stderr from the audit subprocess is not captured unless the command template redirects it itself.

### Full ingest (confirmed)

```bash
idea4rc-capsule ingest --config $HOME/capsule.toml --csv $HOME/data/upload.csv
```

```console
# expected:
# Type 'yes' to continue:
```

After typing `yes`, expect phased log sections and final “Ingestion run completed successfully.”

### Data-only re-run (no redeploy)

Requires healthy release:

```bash
idea4rc-capsule ingest --config $HOME/capsule.toml \
  --csv $HOME/data/upload.csv \
  --skip-deploy --yes
```

## Reference

Drain defaults are wired in `config.py` (`_drain_from`): e.g. `timeout` default `14400`, `stable_polls` default `3`, `max_stall_seconds` default `600` for staging/omop (`aerospike` uses `900` in sample for `max_stall_seconds`).

| Ingest flag | Meaning |
| --- | --- |
| `--keep-logs` | Do not delete `*.log` in `paths.log_dir` before run |
| `--skip-deploy` | Skip `deploy_phase`; verify release exists |
| `--with-repo-sync` | With `--skip-deploy`, still run `repo_sync` |
| `--check-only` | Preflight (+ release check when `--skip-deploy`) |

## Common pitfalls

1. **`--skip-deploy` without prior deploy** — fatal: missing Helm release.
2. **Port-forward upload timeout** — adjust curl `--max-time` in templates or ingress path for `public` mode.
3. **Audit non-zero exit** — orchestrator treats audit shell failure as fatal pipeline error (distinct from audit verdict files).

---

*Previous: [07-deploy.md](07-deploy.md)* · *Next: [09-day-2-operations.md](09-day-2-operations.md)*
