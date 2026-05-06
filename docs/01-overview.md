# Overview

The IDEA4RC research capsule is a Kubernetes-deployed data pipeline; `idea4rc-capsule` is the Python CLI that installs prerequisites, manages Vault-backed secrets, runs Helm install via an env-driven bash helper, restores OMOP vocabulary, ingests CSVs, waits for adaptive drains, and runs an optional audit command.

## Prerequisites

You know whether this host runs its own Vault server or uses shared Vault / plaintext `[fallback_secrets]`, and you have SSH access to the deployment machine.

## How It Works

The package folds earlier bash workflows into one **pipx-installable** entry point (`idea4rc-capsule`). It coordinates:

1. **Host prerequisites** (`install`) — git, curl, jq, pandoc, microk8s, helm, … (not the Vault server binary).
2. **Vault server install** (`vault install`, requires `sudo`) — HashiCorp Vault via apt, `vault.hcl`, systemd.
3. **Vault bootstrap** (`vault bootstrap …`) — init, unseal, KV + AppRole configuration (HTTP to localhost; no `sudo`).
4. **Secrets** (`vault write-secrets`, `vault verify`) — interactive KV writes and inventory checks.
5. **Configuration** (`init-config`, `check-config`) — generate and validate `$HOME/capsule.toml`.
6. **Deploy** (`deploy`) — optional `repo_sync`, then destroy + recreate namespaces, Helm install via the bundled env-driven installer when `capsule_install.use_install_script=true`, query-executor secret hook, OMOP dictionary restore when `omop.dict_dump_path` is set.
7. **Ingest** (`ingest`) — same deploy phase unless `--skip-deploy`, then CSV upload (public URL or `kubectl port-forward`), Aerospike + staging + OMOP drains, optional audit.

## Confirm the CLI is available

```bash
export PYTHONPATH="/path/to/idea4rc-capsule"
export PY="$HOME/.local/pipx/venvs/idea4rc-capsule/bin/python"
```

Use `pipx`-bundled Python only if system Python lacks dependencies (`tomli`, `hvac`). Installed users run `idea4rc-capsule` directly.

```bash
idea4rc-capsule --help
```

```console
# expected:
# usage: idea4rc-capsule [-h] [--version]
#                        {install,init-config,check-config,status,vault,...}
```

If `command not found`, install per [03-installation.md](03-installation.md).

If `ModuleNotFoundError` when running from a bare checkout, install deps or use `pipx run`.

## Reference

| Concept | Where it lives |
| --- | --- |
| CLI entry point | `idea4rc_capsule/cli.py` → `idea4rc-capsule` console script |
| Typed config | `idea4rc_capsule/config.py` → `load_config(Path)` |
| Sample TOML | `idea4rc_capsule/data/capsule.sample.toml` |

## Common pitfalls

1. Running **`sudo idea4rc-capsule`** for ordinary commands — `sudo` resets `PATH`; pipx bins often disappear. Use **`sudo`** only for `install --auto` and **`vault install --auto`** (see [02-host-prerequisites.md](02-host-prerequisites.md)).
2. Confusing **`deploy`** with **`ingest`** — `deploy` never uploads CSV or runs drains; `ingest` runs the full pipeline (with optional `--skip-deploy`).
3. **`[vault].enabled`** — when `true`, `[vault].approle_file` must exist and parse as `VAULT_ROLE_ID` / `VAULT_SECRET_ID`. When `false`, `[fallback_secrets]` and `[capsule_install].public_ip` are required instead.

---

*Previous: [README.md](README.md)* · *Next: [02-host-prerequisites.md](02-host-prerequisites.md)*
