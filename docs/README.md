# idea4rc-capsule documentation

Guides for the `idea4rc-capsule` CLI, which automates Vault prep, Helm-based capsule deploy, CSV ingest, drains, and post-ingest audit on a microk8s-style host.

## Fresh clone learning path

Start with [01-overview.md](01-overview.md) to understand the system, then read [02-host-prerequisites.md](02-host-prerequisites.md) before installing anything. After that, follow [03-installation.md](03-installation.md), generate `$HOME/capsule.toml`, edit the site-specific values, and validate it with `idea4rc-capsule check-config --config $HOME/capsule.toml`.

Once the CLI and config are ready, continue by role: Vault administrators should follow [04-vault-admin-setup.md](04-vault-admin-setup.md) and [06-secrets-management.md](06-secrets-management.md); deployment operators should follow [05-operator-config.md](05-operator-config.md), [07-deploy.md](07-deploy.md), and [08-ingest-and-audit.md](08-ingest-and-audit.md).

## Who should read what

| Role | Start here | Then |
| --- | --- | --- |
| New operator (no prior repo knowledge) | [01-overview.md](01-overview.md) | [02-host-prerequisites.md](02-host-prerequisites.md), [03-installation.md](03-installation.md) |
| Vault administrator | [04-vault-admin-setup.md](04-vault-admin-setup.md) | [06-secrets-management.md](06-secrets-management.md) |
| Capsule operator (day-to-day) | [05-operator-config.md](05-operator-config.md) | [07-deploy.md](07-deploy.md), [08-ingest-and-audit.md](08-ingest-and-audit.md) |
| Incident / triage | [10-troubleshooting.md](10-troubleshooting.md) | [11-security-model.md](11-security-model.md) for secret handling context |
| Developer / maintainer | [13-architecture.md](13-architecture.md) | [12-cli-reference.md](12-cli-reference.md), [14-migration-from-bash.md](14-migration-from-bash.md) |

## Table of contents

1. [01-overview.md](01-overview.md) — what the package does, high-level workflow
2. [02-host-prerequisites.md](02-host-prerequisites.md) — OS, microk8s, helm, tools
3. [03-installation.md](03-installation.md) — pipx install, editable dev install
4. [04-vault-admin-setup.md](04-vault-admin-setup.md) — install server, init, unseal, AppRole
5. [05-operator-config.md](05-operator-config.md) — `capsule.toml`, `init-config`, `check-config`
6. [06-secrets-management.md](06-secrets-management.md) — `write-secrets`, cert staging, verify
7. [07-deploy.md](07-deploy.md) — `deploy` phase, helm, OMOP restore
8. [08-ingest-and-audit.md](08-ingest-and-audit.md) — `ingest`, drains, port-forward, audit
9. [09-day-2-operations.md](09-day-2-operations.md) — status, destroy, repo-sync, recovery
10. [10-troubleshooting.md](10-troubleshooting.md) — common failures, logs, lockfile
11. [11-security-model.md](11-security-model.md) — secrets at rest and in motion; caveats
12. [12-cli-reference.md](12-cli-reference.md) — every subcommand and flag (verified from code)
13. [13-architecture.md](13-architecture.md) — modules, data flow, extension points
14. [14-migration-from-bash.md](14-migration-from-bash.md) — legacy script to CLI mapping

## Also in this folder

- [namespace-ownership-and-helm-post-renderer.md](namespace-ownership-and-helm-post-renderer.md) — design note on Namespace handling
- [upstream-helper-script-hygiene.md](upstream-helper-script-hygiene.md) — chart helper scripts
- [known-harmless-warnings.md](known-harmless-warnings.md) — noise to ignore

## Reference config

The documentation targets a real `capsule.toml` on the operator host (example path used in examples: `$HOME/capsule.toml`). The template source in the package is `idea4rc_capsule/data/capsule.sample.toml`.

---

*Next: [01-overview.md](01-overview.md)*
