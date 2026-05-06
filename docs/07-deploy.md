# Deploy phase

`idea4rc-capsule deploy --config $HOME/capsule.toml` runs preflight, optional repo sync, then `deploy_phase`: destroy + recreate namespaces, Helm install, query-executor secret creation, optional network-policy workaround, runtime env overrides, pod waits, OMOP dictionary restore.

## Prerequisites

Valid `capsule.toml`, working `kubectl`, reachable cluster, secrets available (Vault or fallback), chart checkout path correct.

## How It Works

Order inside `deploy_phase` (`idea4rc_capsule/deploy.py`):

1. **Phase 1** — `destroy()` then `recreate_namespaces()` (namespaces exist empty before helm).
2. **Phase 2** — Helm install:
   - If **`capsule_install.use_install_script`**: fetch secrets via `fetch_install_secrets`, then export env to the bundled installer or your configured `capsule_install.install_script_path`. Required env vars are `CHART_DIR`, `NAMESPACE`, `RELEASE_NAME`, `HELM_BIN`, and the capsule secret values.
   - Else: `helm upgrade --install` via `Helm.upgrade_install`; this path is blocked unless `[k8s].force_recreate_only=false`.
3. **Phase 3** — Query Executor Kubernetes secret script (`run_query_executor_secret_creation`): optionally fetch PEMs from Vault to tmpfs and symlink beside **`query_executor.secret_script_path`** parent dir. If the script is missing/non-executable the deploy is fatal; if the three cert inputs are absent after staging, the code warns and skips secret creation.
4. Optional **`delete_all_network_policies`** when `[k8s].delete_all_network_policies=true`.
5. **`runtime_overrides`** — `kubectl set env` + rollout wait per deployment.
6. **`kubectl wait`** when `ready_label_selector` set.
7. **`wait_omop_db_ready`**
8. **Phase 4** — If `omop.dict_dump_path` non-empty: `pg_restore` vocabulary, grants, vocabulary wait, permission verification.

`deploy` CLI **also** runs **`repo_sync`** when `[repo_sync].enabled` **before** `deploy_phase`.

### Installer environment contract

Required variables are enforced inside the bash script (see comments at top of file). The orchestrator passes **`HELM_POST_RENDERER_PATH`** and **`IDEA4RC_HPR_DROP_KINDS`** when post-renderer enabled so Helm strips Namespace manifests.

### Dry-run deploy

```bash
idea4rc-capsule deploy --config $HOME/capsule.toml --dry-run --yes
```

```console
# expected:
# [dry-run] lines for destroy / helm / waits — no cluster mutation
```

### Preflight only

```bash
idea4rc-capsule deploy --config $HOME/capsule.toml --check-only
```

```console
# expected:
# --- preflight: ... ---
# --check-only: preflight complete; not deploying.
```

## Reference

| Flag | Meaning |
| --- | --- |
| `--yes` | Skip destructive confirmation |
| `--dry-run` | Log actions only |
| `--check-only` | Preflight then exit |
| `--skip-preflight` | Danger: bypass checks |
| `--deep-preflight` / `--no-deep-preflight` | Default deep openssl cert checks: on |
| `--strict-preflight` | Treat warnings as errors |

## Common pitfalls

1. **`helm_post_renderer.binary` not on PATH** — fatal suggests `pipx reinstall idea4rc-capsule` or absolute path in TOML.
2. **Pure Helm path with default `force_recreate_only=true`** — `Helm.upgrade_install()` fatals unless you set `[k8s].force_recreate_only=false`. Keep `use_install_script=true` for the normal recreate-only deployment path.
3. **Query executor script missing executable bit** — fatal before cluster secret creation.

---

*Previous: [06-secrets-management.md](06-secrets-management.md)* · *Next: [08-ingest-and-audit.md](08-ingest-and-audit.md)*
