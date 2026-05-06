# CLI reference

Every subcommand exposed by `idea4rc-capsule` and nested `vault` parsers, with defaults copied from `argparse` definitions in the repository.

## Global options

| Option | Meaning |
| --- | --- |
| `-h`, `--help` | Help |
| `--version` | Prints `idea4rc_capsule.__version__` |

---

## `install` (capsule prerequisites)

Synopsis: `idea4rc-capsule install [--auto]`

| Flag | Default | Meaning |
| --- | --- | --- |
| `--auto` | false | apt + snap installs missing tools (requires root) |

Exit: `0` if nothing missing (check mode); `1` if missing tools without `--auto`; `0` after successful `--auto`.

---

## `init-config`

Synopsis: `idea4rc-capsule init-config [-o OUTPUT]`

| Flag | Meaning |
| --- | --- |
| `-o`, `--output` | Write sample TOML to path instead of stdout |

---

## `check-config`

Synopsis: `idea4rc-capsule check-config --config CONFIG`

| Flag | Required | Meaning |
| --- | --- | --- |
| `--config` | yes | Path to `capsule.toml`; loaded through the same `load_config()` validation used by runtime commands |

---

## `status`

Synopsis: `idea4rc-capsule status --config CONFIG`

| Flag | Required | Meaning |
| --- | --- | --- |
| `--config` | yes | Path to `capsule.toml` |

Exit code `0` when informative snapshot completes.

---

## `repo-sync`

Synopsis: `idea4rc-capsule repo-sync --config CONFIG [--url URL] [--branch BRANCH] [--reset] [--dry-run]`

If `[repo_sync].enabled=false`, the command logs a no-op and exits `0` unless you pass `--url` or `--branch`, which enable an ad-hoc sync for that run.

| Flag | Required | Meaning |
| --- | --- | --- |
| `--config` | yes | Path to `capsule.toml` |
| `--url` | no | Override `[repo_sync].url`; any `--url` or `--branch` enables repo sync ad hoc when `[repo_sync].enabled=false`, but a usable URL must still be present |
| `--branch` | no | Override `[repo_sync].branch`; any `--url` or `--branch` enables repo sync ad hoc when `[repo_sync].enabled=false` |
| `--reset` | no | Sets `repo_sync.reset=true` for this run; performs `git reset --hard origin/<branch>` after checkout |
| `--dry-run` | no | Print intended git operations only |

---

## `destroy`

Synopsis: `idea4rc-capsule destroy --config CONFIG [--yes] [--dry-run]`

| Flag | Required | Meaning |
| --- | --- | --- |
| `--config` | yes | Path to `capsule.toml` |
| `--yes` | no | Skip destructive confirmation prompt |
| `--dry-run` | no | Print helm/kubectl actions without executing |

---

## `deploy`

Synopsis:

```text
idea4rc-capsule deploy --config CONFIG [--yes] [--dry-run]
  [--check-only] [--skip-preflight]
  [--deep-preflight | --no-deep-preflight] [--strict-preflight]
```

| Flag | Default | Meaning |
| --- | --- | --- |
| `--config` | required | Path to `capsule.toml` |
| `--yes` | false | Skip destructive confirmation prompt |
| `--dry-run` | false | Print actions without executing |
| `--check-only` | false | Run preflight only; do not deploy |
| `--skip-preflight` | false | Skip preflight checks |
| `--deep-preflight` | True | BooleanOptionalAction; pair with `--no-deep-preflight` to disable |
| `--strict-preflight` | false | Treat preflight warnings as errors |

---

## `ingest`

Synopsis:

```text
idea4rc-capsule ingest --config CONFIG --csv CSV [--yes] [--keep-logs]
  [--dry-run] [--check-only] [--skip-preflight]
  [--deep-preflight | --no-deep-preflight] [--strict-preflight]
  [--skip-deploy] [--with-repo-sync]
```

| Flag | Default | Meaning |
| --- | --- | --- |
| `--config` | required | Path to `capsule.toml` |
| `--csv` | required | Absolute path to input `.csv`; relative paths and non-CSV suffixes are rejected |
| `--yes` | false | Skip destructive/data-path confirmation prompt |
| `--keep-logs` | false | Preserve existing `*.log` files in `paths.log_dir` |
| `--dry-run` | false | Print actions without executing |
| `--check-only` | false | Run preflight (and release presence check when `--skip-deploy`) only |
| `--skip-preflight` | false | Skip preflight checks |
| `--deep-preflight` | True | BooleanOptionalAction; pair with `--no-deep-preflight` to disable |
| `--strict-preflight` | false | Treat preflight warnings as errors |
| `--skip-deploy` | false | Skip destroy/reinstall/OMOP restore; requires existing healthy release |
| `--with-repo-sync` | false | With `--skip-deploy`, still run repo sync before upload/drains |

---

## `audit`

Synopsis: `idea4rc-capsule audit --config CONFIG [--csv CSV] [--dry-run]`

| Flag | Default | Meaning |
| --- | --- | --- |
| `--config` | required | Path to `capsule.toml`; uses `[audit]` section |
| `--csv` | none | Substitutes `__CSV_PATH__` in `audit.command_template` |
| `--dry-run` | false | Print resolved audit command and expected HTML handling |

---

## `recover-aerospike`

Synopsis:

```text
idea4rc-capsule recover-aerospike --config CONFIG
  [--sets SETS [SETS ...]] [--yes] [--dry-run] [--force] [--no-restart]
  [--stability-samples N] [--stability-interval SEC]
```

| Flag | Default | Meaning |
| --- | --- | --- |
| `--config` | required | Path to `capsule.toml` |
| `--sets` | `ExcelRecord EtlProcessError` | Aerospike sets to truncate |
| `--yes` | false | Skip confirmation prompt |
| `--dry-run` | false | Print intended actions without changing Aerospike or restarting |
| `--force` | false | Skip row-count stability check; dangerous during a real ingest |
| `--no-restart` | false | Do not rollout-restart `etl-idea` after truncating |
| `--stability-samples` | `3` | Stability polling samples |
| `--stability-interval` | `5` | Seconds between samples |

---

## `vault` (group)

Child groups: `install`, `bootstrap`, `write-secrets`, `fetch`, `verify`.

### `vault install`

Synopsis:

```text
idea4rc-capsule vault install [--auto] [--addr ADDR]
  [--data-dir DATA_DIR] [--config-dir CONFIG_DIR] [--skip-service]
```

| Flag | Default | Meaning |
| --- | --- | --- |
| `--auto` | false | Install `vault` via apt if missing; needs root when packages/config/service are touched |
| `--addr` | `http://127.0.0.1:8200` | Listener/api address baked into `vault.hcl` |
| `--data-dir` | `/var/lib/vault` | Storage path |
| `--config-dir` | `/etc/vault.d` | Config directory |
| `--skip-service` | false | Skip writing config + systemd restart |

Requires root for apt/systemd paths when not skipping service.

### `vault bootstrap`

Synopsis: `idea4rc-capsule vault bootstrap [--addr ADDR] <subcommand> …`

Shared **`--addr`** default: `$VAULT_ADDR` or `http://127.0.0.1:8200`.

#### `vault bootstrap status`

No additional flags.

#### `vault bootstrap init`

| Flag | Default | Meaning |
| --- | --- | --- |
| `--key-shares` | `5` | Number of Shamir key shares generated |
| `--key-threshold` | `3` | Number of shares required to unseal |
| `--output` | none | Write init JSON to file and chmod 600; otherwise print one-shot JSON |

#### `vault bootstrap unseal`

| Flag | Default | Meaning |
| --- | --- | --- |
| `--from-init-output` | none | Auto-unseal using keys from init JSON; otherwise prompt hidden for threshold keys |

#### `vault bootstrap configure`

| Flag | Default | Meaning |
| --- | --- | --- |
| `--secret-mount` | `secret` | KV v2 mount path to enable/use |
| `--kv-base` | `idea4rc-capsule` | Prefix under KV mount for capsule paths |
| `--policy-name` | `capsule-readonly` | Policy written from packaged HCL unless overridden |
| `--role-name` | `capsule-installer` | AppRole name to create/update |
| `--policy-file` | packaged `policy-capsule-readonly.hcl` | Custom HCL policy path |
| `--token-ttl` | `30m` | AppRole token TTL |
| `--token-max-ttl` | `1h` | AppRole token max TTL |
| `--token` | none | Vault token; fallback order is `--token`, `$VAULT_TOKEN`, `--from-init-output`, hidden prompt |
| `--from-init-output` | none | Read `root_token` from init JSON |
| `--output-approle` | none | Write `VAULT_ROLE_ID` / `VAULT_SECRET_ID` file chmod 600 instead of printing |

#### `vault bootstrap rotate-secret-id`

| Flag | Default | Meaning |
| --- | --- | --- |
| `--role-name` | `capsule-installer` | AppRole whose Secret ID is rotated |
| `--token` | none | Vault token; fallback order is `--token`, `$VAULT_TOKEN`, `--from-init-output`, hidden prompt |
| `--from-init-output` | none | Read `root_token` from init JSON |
| `--output-approle` | none | Overwrite AppRole file with same Role ID and new Secret ID, chmod 600 |

#### `vault bootstrap all`

Combines flags from `init`, `unseal`, and `configure`: `--key-shares`, `--key-threshold`, `--output`, `--from-init-output`, `--secret-mount`, `--kv-base`, `--policy-name`, `--role-name`, `--policy-file`, `--token-ttl`, `--token-max-ttl`, `--token`, `--output-approle`.

For unattended fresh setup, pass the same init JSON path to both `--output` and `--from-init-output`; `cmd_init()` writes the file before `cmd_unseal()` / `cmd_configure()` read it.

### `vault write-secrets`

| Flag | Default | Meaning |
| --- | --- | --- |
| `--vault-addr` | `$VAULT_ADDR` or `http://127.0.0.1:8200` | Vault API URL |
| `--secret-mount` | `secret` | KV v2 mount path |
| `--kv-base` | `idea4rc-capsule` | KV prefix |
| `--token` | none | Vault token; fallback order is `--token`, `$VAULT_TOKEN`, `--from-init-output`, hidden prompt |
| `--from-init-output` | none | Read `root_token` from init JSON |
| `--certs-dir` | none | Directory containing `ca.pem`, `client.cert.pem`, `client.key.pem` to base64-write to KV |
| `--shred-certs` | false | After successful upload, shred/remove exactly the three known cert filenames |
| `--dry-run` | false | Prompt and load certs, then print target paths/keys without writing |

### `vault fetch ping|secrets|certs`

Common fetch flags (`_add_common`):

| Flag | Default | Meaning |
| --- | --- | --- |
| `--vault-addr` | `$VAULT_ADDR` or `http://127.0.0.1:8200` | Vault API URL |
| `--approle-file` | **required** | Chmod-600 file containing `VAULT_ROLE_ID` / `VAULT_SECRET_ID` |
| `--secret-mount` | `secret` | KV v2 mount path |
| `--kv-base` | `idea4rc-capsule` | KV prefix |

Subcommands:

- `ping` checks Vault reachability + AppRole login, then revokes the token.
- `secrets` requires `--out-env` and writes a chmod-600 shell env file containing capsule install env vars.
- `certs` requires `--out-dir` and writes `ca.pem`, `client.cert.pem`, `client.key.pem` with private file mode.

### `vault verify`

| Flag | Default | Meaning |
| --- | --- | --- |
| `--vault-addr` | `$VAULT_ADDR` or `http://127.0.0.1:8200` | Vault API URL |
| `--approle-file` | `~/.vault-approle` | AppRole file; path is expanded with `expanduser` |
| `--secret-mount` | `secret` | KV v2 mount path |
| `--kv-base` | `idea4rc-capsule` | KV prefix |
| `--deep` | false | Also decode certs and run openssl chain/modulus/date checks |

Exit codes: `0` ok, `1` missing/invalid fields, `2` deep cert failure.

---

## Example invocations

```bash
idea4rc-capsule check-config --config $HOME/capsule.toml
idea4rc-capsule vault verify --deep --approle-file $HOME/.vault-approle
idea4rc-capsule deploy --config $HOME/capsule.toml --yes
idea4rc-capsule ingest --config $HOME/capsule.toml --csv $HOME/data/file.csv --yes
```

---

*Previous: [11-security-model.md](11-security-model.md)* · *Next: [13-architecture.md](13-architecture.md)*
