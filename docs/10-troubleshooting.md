# Troubleshooting

Diagnose failures using phase logs under `[paths].log_dir`, understand lockfile semantics, preflight error aggregation, and Vault/AppRole connectivity.

## Prerequisites

Access to the deployment host and `kubectl` configuration used by `[k8s].kubectl_bin`.

## How It Works

### Log locations

| Command | Log file pattern |
| --- | --- |
| `deploy` | `<paths.log_dir>/deploy-<run_id>.log` |
| `ingest` | `<paths.log_dir>/ingest-<run_id>.log` |
| `audit` | `<paths.log_dir>/audit-<run_id>.log` |

`run_id` format: `YYYYMMDD-HHMMSS` (`runtime.RunContext`).

`deploy` and `ingest` use `tee_run_to_file`, which captures Python and subprocess stdout/stderr. `audit` uses `tee_to_file`, which captures only Python `log()` / `print()` output; redirect inside `[audit].command_template` if you need full audit subprocess output in `audit-<run_id>.log`.

### Lockfile stuck

Ingest creates `<paths.log_dir>/.fresh_ingest.lock/pid`. Another concurrent ingest prints fatal.

If a previous run used **`sudo`**, the lock may be root-owned:

```bash
sudo rm -rf $HOME/capsule-ingestion-logs/.fresh_ingest.lock
```

(replace path with your `[paths].log_dir`).

### Preflight lists many errors at once

`run_preflight()` collects all hard failures before exiting — fix the printed list in one pass (`idea4rc_capsule/preflight.py`).

### Vault verify failures

Run:

```bash
idea4rc-capsule vault verify --approle-file $HOME/.vault-approle
```

Exit `1` → missing KV fields; exit `2` with `--deep` → openssl validation.

### Helm install script failures

Check the deploy log for `[capsule-helm-install]` lines; verify **`CHART_DIR`** exists and **`HELM_BIN`** executes.

### Port-forward failures

When `[ingest].mode = "port-forward"`, startup logs are written to `<paths.log_dir>/port-forward-<run_id>.log`. The port-forward manager only reaps stale `kubectl port-forward` processes that match both the configured target and `local:target` port pair.

Common failures:

| Symptom | What to check |
| --- | --- |
| `Local port <port> is in use by an unknown process` | Inspect with `ss -ltnp 'sport = :<port>'` or change `[ingest].port_forward_local_port` |
| Stale root-owned port-forward | Code prints `sudo kill -TERM <pid>` / `sudo kill -KILL <pid>` instructions |
| `Port-forward did not become ready` | Read `port-forward-<run_id>.log`; check target service/pod and `[ingest].port_forward_ready_timeout` |

### Query Executor secret script

Symptoms: deploy stops after install with fatal from `run_query_executor_secret_creation`. Ensure certs exist or Vault staging succeeded; script must be executable.

## Reference

| Symptom | Likely cause |
| --- | --- |
| `Another fresh_ingest run appears active` | Lock dir + live PID |
| `Permission denied` on lock | Mixed sudo / non-sudo runs |
| `helm_post_renderer... not found` | pipx path missing binary |
| `Ingestion failed (rc=…)` | bash curl upload returned non-zero |

## Common pitfalls

1. Expecting **audit** subprocess output in `audit-<run_id>.log` — unlike deploy/ingest, standalone audit currently uses the Python-level tee helper.
2. Ignoring **`--strict-preflight`** warnings — promoted to errors when flag set.
3. **`kubectl` context wrong** — `status` shows namespace missing even though cluster healthy elsewhere.

---

*Previous: [09-day-2-operations.md](09-day-2-operations.md)* · *Next: [11-security-model.md](11-security-model.md)*
