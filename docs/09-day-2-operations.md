# Day-2 operations

Routine commands after initial bring-up — `status`, `destroy`, `repo-sync`, standalone `audit`, Aerospike recovery, Vault secret rotation.

## Prerequisites

Working `capsule.toml` and cluster credentials.

## How It Works

### Inspect without changing anything

```bash
idea4rc-capsule status --config $HOME/capsule.toml
```

```console
# expected:
# === Capsule status ===
#   namespace = ...
#   helm release deployed: True
# --- Pods ---
```

### Tear down data volumes

```bash
idea4rc-capsule destroy --config $HOME/capsule.toml --yes
```

Runs `destroy()` in `idea4rc_capsule/destroy.py`: helm uninstall, namespace deletes, optional PV deletes per `[k8s].pvs_to_delete_before_destroy`.

### Refresh chart checkout

```bash
idea4rc-capsule repo-sync --config $HOME/capsule.toml
```

Supports `--url`, `--branch`, `--reset`, `--dry-run`.

### Audit only

```bash
idea4rc-capsule audit --config $HOME/capsule.toml --csv $HOME/data/upload.csv
```

Runs **`[audit].command_template`** only. Python log lines go to `<paths.log_dir>/audit-<run_id>.log`; raw subprocess output is not fd-captured by `audit.py`, so redirect inside `command_template` if you need full audit command stdout/stderr in that file.

### Recover wedged Aerospike buffer

```bash
idea4rc-capsule recover-aerospike --config $HOME/capsule.toml --yes
```

Truncates default sets `ExcelRecord` and `EtlProcessError` unless overridden; optional stability sampling unless `--force`. Unless `--no-restart` is passed, it rollout-restarts the deployment derived from `[k8s].ready_label_selector` by taking the value after `=`; if that selector is empty it falls back to `etl-idea`.

### Vault secret rotation

```bash
idea4rc-capsule vault bootstrap rotate-secret-id \
  --from-init-output $HOME/.vault-init.json \
  --output-approle $HOME/.vault-approle
```

### Vault host reboot

After reboot, Vault returns sealed — run **`vault bootstrap unseal`** with your key material (Shamir), then confirm with **`vault bootstrap status`**.

## Reference

The bundled env-driven installer remains the supported Helm install path — it isolates helm `--set` hygiene from Python argv. Override `capsule_install.install_script_path` only when you maintain a partner-specific installer.

Removed bash entry points (historical): `fresh_ingest.sh`, `repo_sync.sh`, separate vault installer scripts — superseded by this CLI ([14-migration-from-bash.md](14-migration-from-bash.md)).

## Common pitfalls

1. **`destroy` without backup** — PV deletion is irreversible for default setups.
2. **`recover-aerospike --force` during live ingest** — can truncate active uploads.
3. **Stale chart after git pull** — run `repo-sync` or rely on ingest/deploy auto sync when enabled.

---

*Previous: [08-ingest-and-audit.md](08-ingest-and-audit.md)* · *Next: [10-troubleshooting.md](10-troubleshooting.md)*
