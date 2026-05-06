# Migration from bash scripts

Map retired shell entry points to `idea4rc-capsule` subcommands; the env-driven Helm installer is now bundled with the package.

## Prerequisites

Read [01-overview.md](01-overview.md) for terminology alignment.

## Migration table

| Legacy file/command | New `idea4rc-capsule` invocation | Notes |
| --- | --- | --- |
| `scripts/fresh_ingest.sh` (full pipeline) | `idea4rc-capsule ingest --config $HOME/capsule.toml --csv /abs/path.csv` | Confirmation prompt unless `--yes` or `[safety].auto_confirm_destruction` |
| `scripts/fresh_ingest.sh` / recreate-only setup path | `idea4rc-capsule deploy --config $HOME/capsule.toml` | Destroy + reinstall + OMOP restore when configured |
| `scripts/fresh_ingest.sh` data-only re-upload | `idea4rc-capsule ingest --config $HOME/capsule.toml --csv /abs/path.csv --skip-deploy --yes` | Requires existing release |
| `scripts/repo_sync.sh` | `idea4rc-capsule repo-sync --config $HOME/capsule.toml` |  |
| `scripts/vault/install_vault.sh` | `sudo idea4rc-capsule vault install --auto` | Requires root |
| Manual Vault init/unseal | `idea4rc-capsule vault bootstrap …` | See [04-vault-admin-setup.md](04-vault-admin-setup.md) |
| Manual secret paste to vault kv | `idea4rc-capsule vault write-secrets …` | Interactive |
| `capsule.env` exports | `[fallback_secrets]` or Vault + `fetch_install_secrets` | chmod 600 plaintext TOML when Vault off |
| Ad-hoc `kubectl port-forward` + curl | `[ingest]` templates + `ingest` command | Port-forward managed in Python context |
| Inline Aerospike truncate instructions | `idea4rc-capsule recover-aerospike --config …` |  |

**Bundled helper:** `idea4rc_capsule/data/capsule_helm_install.sh` — invoked when `[capsule_install].use_install_script=true` and `install_script_path` is empty. Set `install_script_path` only to use a partner-specific installer.

If you disable the retained helper (`[capsule_install].use_install_script=false`), also set `[k8s].force_recreate_only=false`; otherwise the pure `helm upgrade --install` path fatals by design.

> **NOTE:** Exact legacy paths (`scripts/fresh_ingest.sh`, etc.) may not exist in every checkout; the mapping reflects design intent expressed in module docstrings (`deploy.py`, `ingest.py`).

---

*Previous: [13-architecture.md](13-architecture.md)* · *Next: [README.md](README.md)*
