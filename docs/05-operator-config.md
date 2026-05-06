# Operator configuration (`capsule.toml`)

`idea4rc-capsule` loads a single TOML file through `load_config()` in `idea4rc_capsule/config.py`; every key has either a default in code or a validation rule.

## Prerequisites

You know your namespace, release name, chart path, whether Vault is enabled, and your ingest mode (`public` vs `port-forward`).

## How It Works

Generate a starter file, edit absolute paths, validate, then use the same path for `deploy` / `ingest` / `status`.

### Generate a template

```bash
idea4rc-capsule init-config -o $HOME/capsule.toml
```

```console
# expected:
# Wrote sample config to $HOME/capsule.toml
```

### Validate

```bash
idea4rc-capsule check-config --config $HOME/capsule.toml
```

```console
# expected:
# Configuration valid.
#   k8s.namespace        = ...
#   vault.enabled        = ...
```

### Critical boolean: `[vault].enabled`

| Value | Secret source | Requirements |
| --- | --- | --- |
| `true` | Vault KV via AppRole | `[vault].approle_file` must exist and parse |
| `false` | `[fallback_secrets]` + `[capsule_install].public_ip` | All fallback keys non-empty when `use_install_script=true`; `public_ip` required |

This single flag switches the entire secret pipeline (`idea4rc_capsule/config.py` validates accordingly).

### Sections (field semantics)

Reference implementation: `idea4rc_capsule/config.py`. Highlights:

| Section | Role |
| --- | --- |
| `[k8s]` | `namespace`, `release_name`, `chart_path` (required strings); `kubectl_bin`, `helm_bin`; `wait_timeout` must match `^\d+[smh]$`; `pvs_to_delete_before_destroy`; `force_recreate_only` |
| `[capsule_install]` | `use_install_script`, `install_script_path`, `public_ip` when Vault disabled |
| `[repo_sync]` | When `enabled=true`, `url` and `branch` required |
| `[vault]` | `enabled`, `addr`, `secret_mount`, `kv_base`, `approle_file` |
| `[fallback_secrets]` | Plaintext partners when Vault off |
| `[query_executor]` | When `enabled=true`, `secret_script_path` required |
| `[omop]` | Requires either `db_pod_name` or `db_selector`; dictionary restore optional via `dict_dump_path` |
| `[aerospike_drain]` / `[staging_drain]` / `[omop_drain]` | Drain tuning: `timeout`, `poll_interval`, `stable_polls`, `min_rows`, `min_wait_seconds`, `max_stall_seconds` plus section-specific keys |
| `[ingest]` | `mode` ∈ {`public`,`port-forward`}; templates for curl upload |
| `[audit]` | `command_template`, `summary_md_path`, `summary_html_path` |
| `[paths]` | `log_dir` — logs and lockfile location |
| `[runtime_overrides]` | `kubectl set env` patches after install |
| `[helm_post_renderer]` | Strip YAML kinds from helm output (default drops `Namespace`) |
| `[safety]` | `auto_confirm_destruction` mirrors `--yes` for destructive prompts |

If output differs from “Configuration valid.”, read the fatal error: it names the exact missing or inconsistent key.

## Reference

Template with comments: `idea4rc_capsule/data/capsule.sample.toml` in the package checkout.

## Common pitfalls

1. **`[ingest]` port-forward keys incomplete** — when `mode=port-forward`, all of `port_forward_target`, `port_forward_target_port`, `port_forward_local_port`, `port_forward_ready_timeout`, `port_forward_template` must be non-empty.
2. **`[omop]` pod resolution** — empty `db_pod_name` and `db_selector` is fatal.
3. **`[safety].auto_confirm`** — renamed; using the old key fatals with a migration message (`config.py`).

---

*Previous: [04-vault-admin-setup.md](04-vault-admin-setup.md)* · *Next: [06-secrets-management.md](06-secrets-management.md)*
