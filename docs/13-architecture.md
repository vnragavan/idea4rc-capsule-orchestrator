# Architecture

Python orchestration wraps Helm/kubectl/subprocess bash, Vault access via hvac + short-lived AppRole tokens, and optional helm post-rendering to strip Namespace objects.

## Module map

| Module | CLI surface | Replaced legacy (conceptual) |
| --- | --- | --- |
| `cli.py` | top-level dispatch | N/A |
| `install.py` | `install` | ad-hoc prerequisite docs |
| `init_config.py` | `init-config` | manual sample copying |
| `check_config.py` | `check-config` | manual validation |
| `status.py` | `status` | kubectl/helm one-liners |
| `vault_cmd.py` | `vault` group wiring + `fetch_install_secrets` | split vault tooling |
| `vault/install_server.py` | `vault install` | `install_vault.sh` |
| `vault/bootstrap.py` | `vault bootstrap …` | manual vault operator steps |
| `vault/write_secrets.py` | `vault write-secrets` | manual `vault kv` |
| `vault/fetch.py` | `vault fetch …` | env file crafting |
| `vault/verify.py` | `vault verify` | manual inventory |
| `repo_sync.py` | `repo-sync` | `repo_sync.sh` |
| `destroy.py` | `destroy`; used by `deploy_phase` | excerpt from `fresh_ingest.sh` |
| `deploy.py` | `deploy`; `deploy_phase` | `_run_setup_pipeline` |
| `ingest.py` | `ingest` | `fresh_ingest.sh` main |
| `drains.py` | (library) | polling loops in bash |
| `omop_db.py` | (library) | pg_restore block in bash |
| `port_forward.py` | (library) | manual kubectl port-forward |
| `audit.py` | `audit` | post-ingest audit hook |
| `recover.py` | `recover-aerospike` | manual Aerospike truncate instructions |
| `preflight.py` | pre-deploy/ingest checks | scattered validations |
| `runtime.py` | RunContext, locks, tee | `fresh_ingest.sh` helpers |
| `helm_post_renderer.py` | `idea4rc-helm-post-renderer` binary | chart forks for Namespace |

**Bundled bash helper:** `idea4rc_capsule/data/capsule_helm_install.sh` — env-driven helm install; keeps `--set` hygiene out of Python argv (`deploy.py`).

## Data flow (ASCII)

### `deploy`

```text
capsule.toml --> load_config
        |
        v
 optional repo_sync --> destroy + recreate_namespaces
        |
        v
 use_install_script=true? --> fetch_install_secrets (Vault revoke) --> bundled installer env
        |
        +-- use_install_script=false and force_recreate_only=false --> Helm.upgrade_install
        |
        v
 Helm install --> query_executor bash helper --> runtime_overrides / waits --> OMOP restore
```

### `ingest`

```text
capsule.toml + CSV --> preflight --> lock --> repo_sync? --> fetch_install_secrets
        |
        v
 deploy_phase? --> port_forward + bash curl upload
        |
        v
 wait_aerospike --> wait_staging --> restart_omop_etl --> wait_omop --> run_audit_command
```

## Where to extend

| Change | Start in |
| --- | --- |
| New drain heuristic | `drains.py`, matching `[*_drain]` schema in `config.py` |
| New Vault paths | `vault/fetch.py` `SECRET_MAP`, `write_secrets.py` `PROMPTS`, `vault/verify.py` inventory |
| Alternate Helm strategy | `deploy.py` `_run_helm_upgrade_install` vs install script path |
| New top-level command | New module with `add_subcommands`, register in `cli.py` `_subcommand_modules` |

---

*Previous: [12-cli-reference.md](12-cli-reference.md)* · *Next: [14-migration-from-bash.md](14-migration-from-bash.md)*
