# Security model

Secrets move from operator prompts or files into Vault KV, briefly through process memory for helm install, into Kubernetes Secrets for workloads; understand storage locations and honest limits of Python and KV v2.

## Prerequisites

Basic familiarity with Vault KV v2 and Kubernetes Secrets.

## How It Works

### Where secrets live

| Location | Contents |
| --- | --- |
| Operator files | `$HOME/.vault-init.json` (init keys + root token), `$HOME/.vault-approle` (`VAULT_ROLE_ID`, `VAULT_SECRET_ID`) — **chmod 600** expected |
| Vault host disk | File backend default `/var/lib/vault/` (`vault install` data dir) |
| Vault KV | Versioned secrets under `<secret_mount>/<kv_base>/…` |
| Process memory | `fetch_install_secrets` returns plain Python strings. `deploy_phase()` clears the dict after the env-driven helm install path; `ingest.py` keeps its local dict until normal function scope cleanup. AppRole sessions are revoked in `finally` (`vault_cmd.fetch_install_secrets`, `fetch_query_executor_certs`) |
| Temporary PEM staging | `/dev/shm/idea4rc-certs.<run_id>.<pid>` with cleanup hooks (`deploy.py`) |
| Kubernetes etcd | Secrets created by chart / query-executor helper |
| Run logs | `paths.log_dir` controls the log directory; code default is `/var/log/idea4rc-capsule` when omitted, while the shipped sample/reference config uses `$HOME/capsule-ingestion-logs` |

Per-run AppRole token lifecycle: **login → read KV → revoke_self** in fetch helpers.

### `.cursorignore` guidance

Add patterns so editors and automation tools do not ingest PEMs or env files accidentally:

```gitignore
*.pem
*.key
*.crt
id_rsa*
.env*
secrets.*
```

Tune to your repo layout.

### Honest caveats

- **Process memory**: secrets appear in RAM during helm invocation and curl uploads; swap / core dumps may expose them — harden the host accordingly.
- **Python does not zero strings**: confidential values are not cryptographically wiped after use.
- **KV v2 versioning**: old secret versions may remain until garbage-collected per Vault policy — rotation does not instantly erase history.

### Pipeline hygiene

`capsule_helm_install.sh` avoids `helm --debug` because debug echoes resolved values.

## Reference

Policy file packaged at `idea4rc_capsule/data/policy-capsule-readonly.hcl` (AppRole scope).

## Common pitfalls

1. **0644 approle file** — readable by other users on shared shells.
2. **Committing `capsule.toml` with `[fallback_secrets]` populated** — treat as secret; prefer Vault.
3. **Sharing `$HOME/.vault-init.json` over chat** — contains root token and unseal keys.

---

*Previous: [10-troubleshooting.md](10-troubleshooting.md)* · *Next: [12-cli-reference.md](12-cli-reference.md)*
