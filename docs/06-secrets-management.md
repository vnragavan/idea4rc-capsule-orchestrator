# Secrets management

Push operator-supplied secrets into Vault KV with `vault write-secrets`, optionally shred local PEM copies, then prove readiness with `vault verify` (optionally `--deep` for openssl checks).

> requires a valid Vault token (root from init output, or `VAULT_TOKEN`) for `write-secrets`; verification uses AppRole.

## Prerequisites

Vault bootstrapped ([04-vault-admin-setup.md](04-vault-admin-setup.md)), KV paths writable with your token.

## How It Works

`write-secrets` prompts in the order defined by `PROMPTS` in `idea4rc_capsule/vault/write_secrets.py` (nine value prompts across four groups):

1. **capsule** — `pubIp` (visible) — maps to `CAPSULE_PUB_IP`
2. **vantage6** — `apiKey` (hidden), `nodeName`, `k8sNodeName`
3. **keycloak** — `clientId`, `clientSecret` (hidden), `host`
4. **kafka** — `clientId`, `consumerId`

Hidden prompts use `getpass` (no echo). **`_prompt()` does not strip quotes** — type bare values; quotes become part of the stored string.

Optional **`--certs-dir`** must contain **`ca.pem`**, **`client.cert.pem`**, **`client.key.pem`**. Files are base64-encoded into KV under `certs/query-executor`.

### `_shred_dir()` behaviour (fixed semantics)

When **`--shred-certs`** is set, only those three known filenames are shredded/removed inside `--certs-dir`; sibling files (for example `query-executor-create-secret.sh` in the same folder) are **preserved**. The directory is removed only if empty afterward (`write_secrets.py`).

### Interactive write with token from init file

```bash
idea4rc-capsule vault write-secrets \
  --from-init-output $HOME/.vault-init.json \
  --certs-dir $HOME/query-exec-pems \
  --shred-certs
```

```console
# expected:
# Paste each value when prompted...
# --- capsule ---
# CAPSULE_PUB_IP ...
# ...
# All values written successfully.
```

Use **`--dry-run`** to skip KV writes after prompts complete.

> **NOTE:** `--dry-run` still runs **`_gather_values()`** — you must enter all prompts; only the Vault writes are skipped (`write_secrets.py`).

### Verify inventory (AppRole)

Point **`--approle-file`** at your chmod-600 file; default is **`~/.vault-approle`** (`expanduser`).

```bash
idea4rc-capsule vault verify --deep --approle-file $HOME/.vault-approle
```

Exit codes (`idea4rc_capsule/vault/verify.py`):

| Code | Meaning |
| --- | --- |
| 0 | All strings and certs present / structurally valid |
| 1 | Missing or malformed fields |
| 2 | Inventory passed, but `--deep` openssl validation failed (including missing `openssl`) |

`--deep` uses a temp directory (prefers `XDG_RUNTIME_DIR`, else `/tmp`), chmod 700, runs openssl; scratch space is cleaned up after.

### Read certificates back from Vault

The deploy path normally calls `fetch_query_executor_certs()` in-process and stages PEM files into `/dev/shm/idea4rc-certs.<run_id>.<pid>`. For an operator smoke test, use the CLI fetch command:

```bash
mkdir -p /dev/shm/idea4rc-qe-certs
idea4rc-capsule vault fetch certs \
  --approle-file $HOME/.vault-approle \
  --out-dir /dev/shm/idea4rc-qe-certs
```

```console
# expected:
# Fetching Query Executor certs into /dev/shm/idea4rc-qe-certs
# Cert files staged (['ca.pem', 'client.cert.pem', 'client.key.pem'])
```

This writes `ca.pem`, `client.cert.pem`, and `client.key.pem` with private file modes, then revokes the AppRole token. Remove the scratch directory after inspection.

## Reference

| CLI | Purpose |
| --- | --- |
| `vault write-secrets` | Interactive KV writer |
| `vault verify` | Read-only inventory + optional openssl |
| `vault fetch ping` | AppRole login smoke test |
| `vault fetch certs` | Read Query Executor cert PEMs from Vault into a private directory |

KV layout mirrors `SECRET_MAP` in `idea4rc_capsule/vault/fetch.py`.

## Common pitfalls

1. **Quoting secrets at prompts** — unnecessary quotes are stored literally.
2. **`--certs-dir` shared with scripts** — older `_shred_dir` behaviour could delete unrelated files; current code only removes the three PEM names (still prefer a dedicated staging dir chmod 700).
3. **KV v2 versioning** — superseded values remain in version history; plan Vault retention and rotation policies ([11-security-model.md](11-security-model.md)).

---

*Previous: [05-operator-config.md](05-operator-config.md)* · *Next: [07-deploy.md](07-deploy.md)*
