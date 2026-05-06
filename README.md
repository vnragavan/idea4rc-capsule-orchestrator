# idea4rc-capsule

End-to-end IDEA4RC capsule deployment and ingestion automation: one pipx-installed CLI (`idea4rc-capsule`) drives Vault setup, Helm install via an env-driven bash helper, OMOP dictionary restore, CSV upload, adaptive drains, and optional audit scripts configured in `capsule.toml`.

## Start Here

If you cloned this repository for the first time, use this README as the landing page, then continue into the operator docs:

- New operator: read [docs/README.md](docs/README.md), then [docs/01-overview.md](docs/01-overview.md).
- Host setup: read [docs/02-host-prerequisites.md](docs/02-host-prerequisites.md).
- Installation: read [docs/03-installation.md](docs/03-installation.md).
- Configuration: generate `capsule.toml`, edit site-specific paths and secrets strategy, then validate it with `check-config`.

## Prerequisites

The operator host should have:

- Linux with shell access and `sudo` for OS package installation.
- Python 3.10+ and `pipx`.
- Kubernetes access, usually MicroK8s, plus Helm 3.
- Git, curl, gpg, jq, pandoc, and openssl.
- A capsule Helm chart checkout or a configured `[repo_sync]` source.
- A secrets plan: Vault AppRole is recommended; plaintext `[fallback_secrets]` is supported for controlled environments.

After installing the CLI, `idea4rc-capsule install` can check the host tools and `sudo idea4rc-capsule install --auto` can install supported missing packages on Debian/Ubuntu hosts.

## Quick Start From A Fresh Clone

```bash
git clone https://github.com/vnragavan/idea4rc-capsule-orchestrator.git
cd idea4rc-capsule-orchestrator
pipx install . --force
pipx ensurepath
```

If `pipx` is not installed yet on a Debian/Ubuntu host, run the bundled bootstrap script from the clone:

```bash
sudo ./bootstrap.sh
```

Verify the command-line tools are available:

```bash
idea4rc-capsule --version
idea4rc-capsule --help
idea4rc-helm-post-renderer --help
```

Check host prerequisites:

```bash
idea4rc-capsule install
```

Create a starter config:

```bash
idea4rc-capsule init-config -o $HOME/capsule.toml
```

Edit `$HOME/capsule.toml` before deploy. At minimum, confirm Kubernetes binaries, namespace/release names, Helm chart path or repo sync settings, Vault/AppRole or fallback secrets, ingest mode, log directory, and optional audit paths. Then validate it:

```bash
idea4rc-capsule check-config --config $HOME/capsule.toml
```

## Typical Workflow

1. **Confirm Vault daemon state** — `idea4rc-capsule vault bootstrap status` decides whether you init, unseal, or configure ([docs/04-vault-admin-setup.md](docs/04-vault-admin-setup.md)).
2. **Vault admin: init / unseal / configure** — `vault bootstrap init|unseal|configure|all` with Shamir defaults and optional `--output-approle` ([docs/04-vault-admin-setup.md](docs/04-vault-admin-setup.md)).
3. **Save AppRole credentials** — chmod `600` file with `VAULT_ROLE_ID` / `VAULT_SECRET_ID`; verify with `vault fetch ping` ([docs/04-vault-admin-setup.md](docs/04-vault-admin-setup.md)).
4. **Generate and validate `capsule.toml`** — `init-config`, edit paths, `check-config` ([docs/05-operator-config.md](docs/05-operator-config.md)).
5. **Push secrets into Vault** — `vault write-secrets` (interactive prompts + optional PEM upload) ([docs/06-secrets-management.md](docs/06-secrets-management.md)).
6. **Verify Vault inventory** — `vault verify` and `vault verify --deep` before deploy ([docs/06-secrets-management.md](docs/06-secrets-management.md)).
7. **Deploy** — `deploy --config ~/capsule.toml` runs repo sync (if enabled), destroy/recreate, Helm install, query-executor secret hook, OMOP restore ([docs/07-deploy.md](docs/07-deploy.md)).
8. **Ingest + audit** — `ingest --csv …` runs deploy (unless `--skip-deploy`), upload, drains, audit template ([docs/08-ingest-and-audit.md](docs/08-ingest-and-audit.md)).
9. **Day-2** — `status`, `destroy`, `repo-sync`, `audit`, Aerospike recovery, Vault rotation ([docs/09-day-2-operations.md](docs/09-day-2-operations.md)).

## Important Usage Notes

- Run ordinary commands such as `deploy`, `ingest`, `status`, and `vault bootstrap` as your normal user. Use `sudo` only for package/service installation commands documented in [docs/02-host-prerequisites.md](docs/02-host-prerequisites.md).
- Treat `$HOME/capsule.toml`, `.vault-init.json`, `.vault-approle`, PEM files, and any fallback secrets as sensitive operator files.
- Run `idea4rc-capsule check-config --config $HOME/capsule.toml` before deploy, and use `deploy --check-only` for a preflight-only pass.
- Start troubleshooting from [docs/10-troubleshooting.md](docs/10-troubleshooting.md); secret-handling limits are summarized in [docs/11-security-model.md](docs/11-security-model.md).

Full operator documentation: [docs/README.md](docs/README.md). CLI flags and defaults: [docs/12-cli-reference.md](docs/12-cli-reference.md). Secrets and threat notes: [docs/11-security-model.md](docs/11-security-model.md).
