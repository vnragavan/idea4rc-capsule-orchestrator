# Vault admin setup

Install the Vault server with `sudo`, then use `idea4rc-capsule vault bootstrap` over HTTP to initialize, unseal, and configure KV v2 + AppRole for the capsule installer.

> Run once per Vault host (or when building a new environment). `vault install` and OS-level package installs need `sudo`. `vault bootstrap` subcommands do not.

## Prerequisites

HashiCorp Vault server installed and reachable at `VAULT_ADDR` (default `http://127.0.0.1:8200`), or you will install it per step 1.

## How It Works

1. **Install daemon** — `idea4rc-capsule vault install --auto` (apt + config + systemd).
2. **Check state** — `idea4rc-capsule vault bootstrap status` prints JSON: `initialized`, `sealed`, `version`.
3. **Initialize** — `vault bootstrap init` uses Shamir sharing: defaults **`--key-shares 5`**, **`--key-threshold 3`** (read as “5 key shares, threshold 3 of 5 to unseal” in CLI help naming).
4. **Unseal** — `vault bootstrap unseal` (`--from-init-output` for non-interactive use with the init JSON file).
5. **Configure** — `vault bootstrap configure` enables KV v2, AppRole, policy, role, pre-creates paths, prints or writes `VAULT_ROLE_ID` / `VAULT_SECRET_ID`.
6. **End-to-end** — `vault bootstrap all` runs init + unseal + configure in one process. For a fresh non-interactive run, pass both `--output $HOME/.vault-init.json` and `--from-init-output $HOME/.vault-init.json`; `cmd_init()` writes the file, then `cmd_unseal()` and `cmd_configure()` read it. Add `--output-approle` to write the AppRole file directly.

### Why `bootstrap status` does not need `sudo`

It is an HTTP client to the Vault API (`read_seal_status`). It uses the same `idea4rc-capsule` on your user `PATH` as other subcommands.

### Read seal status

```bash
idea4rc-capsule vault bootstrap status
```

```console
# expected:
# {
#   "initialized": false,
#   "sealed": true,
#   "version": "..."
# }
```

Branching:

| `initialized` | `sealed` | Next step |
| --- | --- | --- |
| false | true | Run `vault bootstrap init` |
| true | true | Run `vault bootstrap unseal` |
| true | false | Run `vault bootstrap configure` (with token) or operational use |

### Initialize and save output securely

```bash
idea4rc-capsule vault bootstrap init --output $HOME/.vault-init.json
```

```console
# expected:
# Init output written to $HOME/.vault-init.json (chmod 600).
```

Keep `$HOME/.vault-init.json` offline; it contains **unseal keys** and **root_token**.

### Unseal using the saved file

```bash
idea4rc-capsule vault bootstrap unseal --from-init-output $HOME/.vault-init.json
```

```console
# expected:
# Vault unsealed.
```

### Configure KV + AppRole and write AppRole file

```bash
idea4rc-capsule vault bootstrap configure \
  --from-init-output $HOME/.vault-init.json \
  --output-approle $HOME/.vault-approle
```

```console
# expected:
# ... policy written ...
# Wrote AppRole credentials to $HOME/.vault-approle (chmod 600).
```

`--output-approle` writes:

```text
VAULT_ROLE_ID=<role-id>
VAULT_SECRET_ID=<secret-id>
```

`VAULT_SECRET_ID` is shown only once unless you write it with `--output-approle`; the role itself sets `secret_id_num_uses=0` (unlimited uses), so rotate the Secret ID when you need to invalidate a leaked file.

### Rotate only the secret_id

```bash
idea4rc-capsule vault bootstrap rotate-secret-id \
  --from-init-output $HOME/.vault-init.json \
  --output-approle $HOME/.vault-approle
```

## Reference

| Flag / key | Default | Meaning |
| --- | --- | --- |
| `bootstrap` `--addr` | `$VAULT_ADDR` or `http://127.0.0.1:8200` | Vault API base URL |
| `init` `--key-shares` | `5` | Number of unseal key shares |
| `init` `--key-threshold` | `3` | Unseal keys required |
| `configure` `--secret-mount` | `secret` | KV mount path |
| `configure` `--kv-base` | `idea4rc-capsule` | Path prefix for application secrets |
| `configure` `--policy-name` | `capsule-readonly` | Policy name |
| `configure` `--role-name` | `capsule-installer` | AppRole name |
| `configure` `--policy-file` | packaged policy | Optional policy HCL override |
| `configure` `--token-ttl` / `--token-max-ttl` | `30m` / `1h` | AppRole token bounds |
| `configure` `--output-approle` | (none) | If set, writes `VAULT_ROLE_ID` / `VAULT_SECRET_ID` lines |
| `all` | — | Composes `init`, `unseal`, `configure` |

**Root token hygiene:** after `configure`, use the root token only for admin tasks, then **revoke** it per your org policy. Day-to-day operator access should use AppRole, not the root token.

## Common pitfalls

1. Pasting **AppRole** lines without `VAULT_` prefix — the parser only accepts `VAULT_ROLE_ID` and `VAULT_SECRET_ID` (`_APPROLE_LINE_RE` in `idea4rc_capsule/vault/_common.py`).
2. **World-readable approle file** — permissions not `600`/`400` log a warning; tight perms are required for any serious deployment.
3. Re-running **`configure`** with a token that is not valid — you must pass `--token`, set `VAULT_TOKEN`, or `--from-init-output` with a file containing `root_token`.

---

*Previous: [03-installation.md](03-installation.md)* · *Next: [05-operator-config.md](05-operator-config.md)*
