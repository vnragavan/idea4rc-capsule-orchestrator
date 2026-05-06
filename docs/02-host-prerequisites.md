# Host prerequisites

The orchestrator expects a Linux operator host with Kubernetes access (typically MicroK8s), Helm 3, Git, and common CLI tools; Vault server install and capsule prerequisite install are separate commands with different privilege needs.

## Prerequisites

Root or sudo for OS package installation where you choose `--auto` installs.

## How It Works

Separate **three layers**:

1. **Capsule tools** — `idea4rc-capsule install` checks/installs git, curl, gpg, jq, pandoc, python3, pip, microk8s, helm (`idea4rc_capsule/install.py` `TOOL_TABLE`). It intentionally does **not** install the `vault` client/server package.
2. **Vault server** — `sudo idea4rc-capsule vault install [--auto]` adds HashiCorp apt repo, installs `vault`, writes `/etc/vault.d/vault.hcl`, starts systemd (`idea4rc_capsule/vault/install_server.py`).
3. **Python package** — install via pipx ([03-installation.md](03-installation.md)).

### Why `sudo idea4rc-capsule` usually fails

`idea4rc-capsule` is commonly installed under `$HOME/.local/pipx/...`. **`sudo` strips user PATH** (`secure_path`). Symptom: `sudo idea4rc-capsule: command not found`.

**Fix:** run **`idea4rc-capsule`** without sudo for Vault bootstrap (`vault bootstrap …`), `deploy`, `ingest`, etc. Reserve **`sudo`** for:

- `sudo idea4rc-capsule install --auto` (root required by code when installing packages).
- `sudo idea4rc-capsule vault install --auto` (writes `/etc/vault.d`, systemd).

### Check prerequisites without installing

```bash
idea4rc-capsule install
```

```console
# expected (example when something missing):
# Checking capsule host prerequisites...
#   MISSING: pandoc  -> apt install pandoc  ...
```

Exit code **1** when anything is missing (unless everything is present).

Run with **`--auto`** only on Debian/Ubuntu with snapd if you want unattended snap installs:

```bash
sudo idea4rc-capsule install --auto
```

```console
# expected:
# apt-get install -y ...
# snap install ...
# install: done. (To install the Vault server: `sudo idea4rc-capsule vault install --auto`)
```

## Reference

| Tool / component | Checked by | Notes |
| --- | --- | --- |
| microk8s | `install` | Snap package name `microk8s` |
| helm | `install` | Snap `helm --classic` |
| pandoc | `install` | Used for audit HTML paths in some setups |
| openssl | preflight / `vault verify --deep` | Chain and modulus checks |

Defaults for orchestrator paths come from `$HOME/capsule.toml` once you create it ([05-operator-config.md](05-operator-config.md)).

## Common pitfalls

1. **`vault` binary missing** after capsule install — expected: run **`vault install`** separately.
2. **`kubectl` vs `microk8s.kubectl`** — set `[k8s].kubectl_bin` and `[k8s].helm_bin` in `capsule.toml` to match the cluster CLI wrappers you use.
3. Running **`sudo ingest`** — creates lockfiles and logs owned by root under `[paths].log_dir`; later non-root runs may hit permission errors ([10-troubleshooting.md](10-troubleshooting.md)).

---

*Previous: [01-overview.md](01-overview.md)* · *Next: [03-installation.md](03-installation.md)*
