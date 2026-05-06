# Installation

Install `idea4rc-capsule` with pipx from the git checkout or wheel so the `idea4rc-capsule` and `idea4rc-helm-post-renderer` binaries land on `PATH`.

## Prerequisites

Python 3.10+, network access to PyPI or a local checkout, `pipx` (recommended) or `pip --user`.

## How It Works

The package declares entry points in `pyproject.toml`:

- `idea4rc-capsule` → `idea4rc_capsule.cli:main`
- `idea4rc-helm-post-renderer` → `idea4rc_capsule.helm_post_renderer:main`

Helm uses the post-renderer when `[helm_post_renderer].enabled` is true ([07-deploy.md](07-deploy.md)).

### Install from a fresh clone

```bash
git clone https://github.com/vnragavan/idea4rc-capsule-orchestrator.git
cd idea4rc-capsule-orchestrator
pipx install . --force
pipx ensurepath
```

```console
# expected:
# installed package idea4rc-capsule ... done
```

Open a new shell if `pipx ensurepath` reports that it changed your `PATH`.

Verify:

```bash
idea4rc-capsule --version
which idea4rc-helm-post-renderer
```

```console
# expected:
# idea4rc-capsule 0.1.0
# $HOME/.local/bin/idea4rc-helm-post-renderer
```

If you already cloned the repository, run `pipx install . --force` from the directory that contains `pyproject.toml`.

### Bootstrap pipx on Debian/Ubuntu

If the host does not have `pipx` yet, the package includes a bootstrap script that installs Python/pipx prerequisites and then installs this package for the invoking user:

```bash
sudo ./bootstrap.sh
```

### Uninstall

```bash
pipx uninstall idea4rc-capsule
```

## Reference

| Item | Value |
| --- | --- |
| Package name | `idea4rc-capsule` (`pyproject.toml`) |
| Python requirement | `^3.10` |
| Runtime deps | `hvac`, `requests`, `tomli` on Python \< 3.11 |

## Common pitfalls

1. **`ModuleNotFoundError: tomli`** when running `python -m idea4rc_capsule` from a bare checkout — install deps or use the pipx environment’s Python with `PYTHONPATH` pointing at the checkout.
2. **Stale pipx env** after git pull — `pipx reinstall idea4rc-capsule` or reinstall from path.
3. **Missing post-renderer** — deploy fatals if `[helm_post_renderer].binary` cannot be resolved; reinstall package or set an absolute `binary` in `capsule.toml`.

---

*Previous: [02-host-prerequisites.md](02-host-prerequisites.md)* · *Next: [04-vault-admin-setup.md](04-vault-admin-setup.md)*
