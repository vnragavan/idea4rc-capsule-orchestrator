# Namespace ownership and the helm post-renderer workaround

> **TL;DR**
> The upstream `idea4rc-helm-capsule` chart unconditionally renders
> `kind: Namespace` resources for `datamesh` and the v6node tasks
> namespace. The orchestrator pre-creates those namespaces externally,
> so a vanilla `helm install` fails with an ownership-annotation
> conflict.
>
> **The fix lives in the orchestrator, not the chart.** A small helm
> `--post-renderer` (`idea4rc-helm-post-renderer`, shipped with this
> package) strips `kind: Namespace` documents from helm's rendered
> output before `kubectl apply`. The chart is consumed unmodified, so
> `repo_sync` can hard-reset to `origin/<branch>` on every run with
> zero local maintenance.
>
> Configured via `[helm_post_renderer]` in `capsule.toml`. Verified by
> preflight. Active for both install paths (the bundled env-driven installer
> + `helm.py upgrade_install`).

---

## 1. The problem

The chart at
[`IDEA4RC/idea4rc-helm-capsule`](https://github.com/IDEA4RC/idea4rc-helm-capsule)
contains two unconditional Namespace templates:

- `templates/capsule-env/datamesh-namespace.yaml`
  → renders `Namespace/datamesh`
- `templates/v6node/tasks-namespace.yaml`
  → renders `Namespace/<v6node.node.taskNamespace>` (typically `v6-tasks`)

A vanilla install pipeline does this:

```
helm install ... → renders templates → kubectl apply
                                       └─ tries to apply Namespace/datamesh
                                          → AlreadyExists (we created it)
                                          → ownership annotation mismatch
                                          → install aborts mid-rollout
```

That breaks the destroy → recreate-namespaces → install sequence the
orchestrator runs in `deploy_phase` (`destroy.recreate_namespaces`
runs *before* the helm install, so the namespaces always exist).

**Why does the orchestrator pre-create namespaces?**
Several reasons, none of which are negotiable:

1. **Quota / labels / istio injection** are applied by ops outside helm
   (network policies, istio-injection labels, resource quotas).
2. **`extra_namespaces`** in `capsule.toml` (e.g. v6-tasks) is created
   the same way and survives chart uninstall/reinstall — `helm uninstall`
   would otherwise delete it on every redeploy.
3. **Race avoidance**: a `Namespace` rendered by helm and a
   `Deployment` in the same chart can race against the namespace
   becoming Active. Pre-creating eliminates that timing window.
4. **Multi-namespace fan-out**: some chart resources reference the
   namespace by name; helm does not guarantee ordering for cross-NS
   references.

So the chart MUST stop emitting Namespace resources, but we cannot edit
the chart (we want clean `git pull` from upstream).

## 2. Why we did NOT fork the chart

The historical workaround was a local fork that wrapped each Namespace
template in `{{- if .Values.createNamespaceResources }}` and added
`createNamespaceResources: false` to `values.yaml`. We then ran
`repo_sync` with `reset = false` so it would refuse to overwrite local
edits.

This created multiple problems:

| Problem | Concrete pain |
|---|---|
| Every upstream pull needed manual conflict resolution | The same multipart-limit fix landed both upstream (parameterised) and locally (hard-coded). Diverged versions, manual rebase. |
| `repo_sync` failed loudly until cleaned up by hand | `error: Your local changes to the following files would be overwritten by merge`. Operator had to know `git stash`, `git reset --hard`, etc. |
| Easy to miss critical upstream fixes | Local `values.yaml` pinned `etl:2.1.1` while upstream had moved to `etl:2.1.3` (which also reduced log verbosity to avoid PII exposure). The local pin masked the upgrade. |
| Anyone reading the chart sees something different from GitHub | Surprise factor for new operators, hard to diff against what's "supposed" to be there. |
| Cannot upstream the workaround as-is | The conditional flag is genuinely useful, but PR review takes weeks; we needed it shipped today. |

## 3. The post-renderer fix

Helm 3 supports `helm install --post-renderer EXEC`: helm pipes its
fully-rendered manifests to `EXEC`'s stdin; `EXEC`'s stdout becomes
what helm actually applies. ([Helm 3 docs §
post-renderer](https://helm.sh/docs/topics/advanced/#post-rendering)).

We ship a tiny stdlib-only Python program,
`idea4rc-helm-post-renderer`, whose entire job is:

```
read multi-doc YAML on stdin
for each `---`-separated document:
    if document's top-level `kind:` is in IDEA4RC_HPR_DROP_KINDS:
        drop it
    else:
        keep it
emit the survivors on stdout
```

That's it. ~80 lines. No PyYAML dep. Source:
[`idea4rc_capsule/helm_post_renderer.py`](../idea4rc_capsule/helm_post_renderer.py).

### How it gets wired up

The orchestrator passes `--post-renderer <path>` to helm, plus
`IDEA4RC_HPR_DROP_KINDS=Namespace` in the install environment.

| Code path | Where the post-renderer is added |
|---|---|
| `capsule_install.use_install_script = true` (default) | `deploy.py::_run_helm_install_script` exports `HELM_POST_RENDERER_PATH` + `IDEA4RC_HPR_DROP_KINDS`; the bundled installer reads them and appends `--post-renderer` to its `helm install`. |
| `capsule_install.use_install_script = false` | `helm.py::upgrade_install` calls `_resolve_post_renderer(cfg)` and appends `--post-renderer` directly. |

`resolve_companion_binary()` in `helm.py` finds the post-renderer by
trying:

1. `shutil.which("idea4rc-helm-post-renderer")` (interactive shells)
2. `Path(sys.executable).parent / name` (pipx venv layout — always works)
3. `~/.local/bin/<name>` (the pipx user-bin symlink)

So the post-renderer resolves whether you're in an interactive shell,
under sudo with a stripped PATH, or running from CI.

### The env-sanitising launcher wrapper

`HELM_POST_RENDERER_PATH` is **not** the pipx-installed binary path
directly. It's a tiny `/tmp/idea4rc-hpr-launcher-*.sh` shell wrapper
that the orchestrator materialises at install time:

```sh
#!/bin/sh
unset PYTHONPATH PYTHONHOME PYTHONNOUSERSITE PYTHONSTARTUP
exec "$HOME/.local/pipx/venvs/idea4rc-capsule/bin/idea4rc-helm-post-renderer" "$@"
```

Why: `microk8s.helm` is a snap-wrapped binary. The snap injects its
own `PYTHONPATH` (pointing at the snap's bundled python 3.8 paths) for
every child process it spawns, including the post-renderer. Our pipx
venv runs python 3.10 and discovers its own `site-packages` via
`sys.executable` + `pyvenv.cfg`. When the snap's `PYTHONPATH` leaks
in, those python 3.8 paths displace the venv's `site-packages` from
`sys.path`, and the launcher's first line — `from
idea4rc_capsule.helm_post_renderer import main` — fails with
`ModuleNotFoundError`.

The wrapper unsets every `PYTHON*` variable before `exec`'ing the real
binary, restoring clean venv discovery. Lifecycle:

- **Created** by `helm.py::_stage_post_renderer_launcher()` once per
  orchestrator process, mode `0700` (no read access for other users).
- **Used** by helm during `helm install`. Helm hands its rendered
  manifests to the wrapper on stdin; the wrapper hands them to the real
  binary; the real binary's stdout becomes what helm applies.
- **Removed** at orchestrator exit via `atexit.register()`. If the
  process is killed with `SIGKILL` (no atexit), the file lingers in
  `/tmp` until the next reboot — harmless.

This is invisible to operators in normal runs. You'll see it logged
on every deploy as:

```
Helm post-renderer: $HOME/.local/pipx/venvs/.../idea4rc-helm-post-renderer
  launcher (env-sanitised): /tmp/idea4rc-hpr-launcher-<random>.sh
  drop_kinds: ['Namespace']
```

### What gets dropped, exactly

Configured in `capsule.toml`:

```toml
[helm_post_renderer]
enabled       = true
binary        = "idea4rc-helm-post-renderer"
kinds_to_drop = ["Namespace"]
```

Today only `Namespace` is dropped. If the upstream chart later starts
emitting other resources we want to manage externally (e.g.
`PersistentVolume`, `ClusterRoleBinding`), add them to
`kinds_to_drop` — no code change needed.

## 4. End-to-end flow after this change

```
              ┌─────────────────────────────────────────┐
              │  idea4rc-capsule deploy                 │
              └────────────────┬────────────────────────┘
                               ▼
       ┌────────────── preflight ──────────────────────┐
       │  ✓ host tools: helm, kubectl, openssl, ...    │
       │  ✓ helm-post-renderer binary resolves         │
       │  ✓ vault inventory (9 secrets + 3 certs)      │
       │  ✓ deep cert checks (chain, modulus, expiry)  │
       └────────────────┬──────────────────────────────┘
                        ▼
       ┌─ repo_sync ────────────────────────────────┐
       │  fetch + RESET --hard origin/omop-etl-dev  │  ← reset=true; no
       │  → chart matches upstream, byte for byte   │     local diff at all
       └────────────────┬───────────────────────────┘
                        ▼
       ┌─ destroy + recreate_namespaces ────────────┐
       │  helm uninstall (if present)               │
       │  kubectl delete namespace datamesh, v6-... │
       │  kubectl create namespace datamesh         │  ← orchestrator owns
       │  kubectl create namespace v6-tasks         │     namespaces
       └────────────────┬───────────────────────────┘
                        ▼
       ┌─ helm install ─────────────────────────────┐
       │  helm install ... \                        │
       │    --post-renderer idea4rc-helm-post-rend… │  ← rendered
       │    --set capsulePublicHost=... \           │     Namespace
       │    --set v6node.node.apiKey=... \          │     docs are
       │    ...                                     │     stripped
       │                                            │     before apply
       │  Result: every k8s resource EXCEPT         │
       │  Namespace gets applied.                   │
       └────────────────┬───────────────────────────┘
                        ▼
       ┌─ runtime overrides ────────────────────────┐
       │  kubectl set env deploy/etl-idea \         │
       │    SPRING_SERVLET_MULTIPART_MAX_FILE_SIZE… │  ← belt-and-suspenders;
       │  kubectl rollout status deploy/etl-idea    │     chart already
       │                                            │     defaults to 200MB
       └────────────────────────────────────────────┘
```

## 5. The "stay synced with upstream" guarantee

After the post-renderer rollout, **the chart at
`$HOME/idea4rc-helm-capsule` should have ZERO tracked-file
modifications relative to `origin/<branch>`.** This is enforced two
ways:

1. **`[repo_sync] reset = true`** in `capsule.toml` — `git_sync.py`
   runs `git reset --hard origin/<branch>` before pulling. Any local
   edit you accidentally make on the host is silently undone on the
   next `deploy`/`ingest`.
2. **No reason to ever edit the chart locally** — every workaround we
   used to need is now in the orchestrator (post-renderer for
   namespaces, `[runtime_overrides]` for env-var patches).

Verify at any time:

```bash
cd $HOME/idea4rc-helm-capsule
git fetch origin
git status --short                        # expect: empty (or only ?? utils/uos.tgz)
git diff --stat HEAD origin/omop-etl-dev  # expect: empty
```

If `git status --short` ever shows `M ` lines on tracked files, **STOP
and investigate** — someone has hand-edited the chart, which means
the next `repo_sync` run will silently reset their work. That's a
signal to either:

- ask why and either revert or capture the change in the orchestrator
  (post-renderer, runtime override, values file), OR
- if it's a true upstream gap, file a PR upstream and pin the chart to
  a fork branch in `capsule.toml [repo_sync].url`.

## 6. After every upstream pull — the 30-second smoke

When `repo_sync` brings down new commits from
`origin/omop-etl-dev`, run this once before your first deploy on the
new SHA. It catches the only realistic regression: upstream starts
emitting a new Namespace template under a different filename, or
introduces a resource kind we need to add to `kinds_to_drop`.

```bash
cd $HOME/idea4rc-helm-capsule

# 1. Make sure deps are local (dependency build pulls subcharts)
microk8s.helm dependency build .

# 2. Render the chart with placeholder secrets
microk8s.helm template idea4rc-capsule . -n datamesh \
  --set capsulePublicHost=10.0.0.1 --set istio.tls.commonName=10.0.0.1 \
  --set v6node.node.apiKey=x --set v6node.node.name=x --set v6node.node.k8sNodeName=x \
  --set fcbexec.keyCloak.clientId=x --set fcbexec.keyCloak.clientSecret=x \
  --set fcbexec.keyCloak.host=x \
  --set fcbexec.kafka.clientId=x --set fcbexec.kafka.consumerId=x \
  > /tmp/rendered.yaml 2>/dev/null

# 3. Inventory of kinds rendered
echo "Kinds present BEFORE filter:"
rg '^kind:' /tmp/rendered.yaml | sort | uniq -c | sort -rn

# 4. Run through the post-renderer
idea4rc-helm-post-renderer < /tmp/rendered.yaml > /tmp/filtered.yaml 2>&1 \
  | sed 's/^/[hpr] /'    # prints the "dropped N doc(s)" status line

# 5. Inventory of kinds AFTER the filter
echo "Kinds present AFTER  filter:"
rg '^kind:' /tmp/filtered.yaml | sort | uniq -c | sort -rn

# 6. Pass criterion: zero Namespace docs after filter
test "$(rg -c '^kind: Namespace' /tmp/filtered.yaml || echo 0)" -eq 0 && \
  echo "✓ post-renderer correctly stripped all Namespace docs" || \
  echo "✗ FAIL: Namespace doc(s) still present after filter"

rm -f /tmp/rendered.yaml /tmp/filtered.yaml
```

If the post-filter Namespace count is **0**, you're good — proceed
with `idea4rc-capsule deploy`.

If it's **non-zero**, investigate: upstream may have introduced a new
file that emits a Namespace under a different chart path. Same
mitigation works (`Namespace` is `Namespace` regardless of which
template emitted it), but the count discrepancy is a useful canary.

## 7. What could break the workaround

| Scenario | Impact | Mitigation |
|---|---|---|
| `pipx install` missing or broken | Preflight reports `helm-post-renderer ... not found or not executable`. Deploy refuses to start. | `pipx reinstall idea4rc-capsule` (preflight tells you this exact command). |
| Upstream renames `kind: Namespace` | Impossible — k8s API kind, not a chart field. | n/a |
| Upstream introduces a new kind we want stripped (e.g. `ClusterRoleBinding`) | Chart applies it, may conflict with externally-managed RBAC. | Add the kind to `kinds_to_drop` in `capsule.toml`. No code change. |
| Helm version < 3.1 (no post-renderer support) | Helm errors `unknown flag: --post-renderer`. | We require helm 3.x; preflight could check version. (Today microk8s.helm is 3.x; if we change deployment target, add a version probe.) |
| `IDEA4RC_HPR_DROP_KINDS` set to empty in env | `binary` runs but drops nothing — passes input through unchanged. Helm tries to apply Namespaces → conflict, install fails. | Don't unset the env var manually. The orchestrator always sets it from `capsule.toml`. |
| Operator hand-edits the chart locally | `repo_sync` with `reset = true` wipes edits silently on next run. | The hand-edit is the bug; surface it via a `git status --short` in deploy logs (already visible) and capture the change in the orchestrator. |
| **Snap-shipped helm (e.g. `microk8s.helm`) injects `PYTHONPATH`** pointing at its own python 3.8 paths into every child process. Our pipx-installed post-renderer (python 3.10) inherits it, fails with `ModuleNotFoundError: No module named 'idea4rc_capsule'`. | Install fails with `error while running command .../idea4rc-helm-post-renderer ... ModuleNotFoundError`. | **Already mitigated.** `helm.py::_stage_post_renderer_launcher()` materialises a tiny `/tmp/idea4rc-hpr-launcher-*.sh` shell wrapper that does `unset PYTHONPATH PYTHONHOME PYTHONNOUSERSITE PYTHONSTARTUP` before `exec`'ing the real binary. helm gets the wrapper path; the wrapper sanitises env; then the venv python finds its own site-packages. Cleaned up at orchestrator exit via `atexit`. |

## 8. Why a post-renderer instead of `kustomize`/`yq`

Considered alternatives and why we didn't take them:

- **`kustomize` + chart-render → kustomize-patch**: adds a build step
  and a new tool dep (kustomize). Two artifacts (rendered yaml,
  kustomization) to keep in sync. Overkill for "drop one kind".
- **`yq eval-all 'select(.kind != "Namespace")'`** as the
  post-renderer: requires Go-yq (mikefarah/yq) on every host, plus
  Python-yq is also called yq with a different syntax. Tool ambiguity
  for sysadmins. Avoided.
- **Local chart fork**: see § 2. Maintenance debt, easy to drift.
- **PR the conditional flag upstream**: the right *long-term* answer.
  Doesn't help us today and review may take weeks. Worth pursuing in
  parallel with the post-renderer (which would then become a no-op:
  set `kinds_to_drop = []` once the upstream gate is merged and
  enabled).

A self-contained Python script with no external deps minimises operator
surface area. The whole filter is auditable in one screen of code.

## 9. Configuration reference

### `capsule.toml`

```toml
[repo_sync]
enabled = true
url     = "https://github.com/IDEA4RC/idea4rc-helm-capsule"
branch  = "omop-etl-dev"
reset   = true   # hard-reset to origin before pull; safe because chart edits are now zero
                 # (post-renderer handles namespace gating)

[helm_post_renderer]
# Strip selected k8s 'kind:' values from helm-rendered manifests before
# kubectl apply. The orchestrator pre-creates namespaces externally, so
# helm should NOT also try to create them (would fail with ownership
# annotation conflict against the namespaces we already manage).
enabled       = true
binary        = "idea4rc-helm-post-renderer"
kinds_to_drop = ["Namespace"]
```

### Environment variables (set automatically; do not export by hand)

| Var | Set by | Read by |
|---|---|---|
| `HELM_POST_RENDERER_PATH` | `deploy.py::_run_helm_install_script` | Bundled env-driven installer |
| `IDEA4RC_HPR_DROP_KINDS` | `deploy.py::_run_helm_install_script`, `helm.py::_resolve_post_renderer` | `idea4rc-helm-post-renderer` (the binary itself) |

## 10. Related files

- `idea4rc_capsule/helm_post_renderer.py` — the filter program
- `idea4rc_capsule/helm.py` — `resolve_companion_binary()`,
  `_resolve_post_renderer()`, install/upgrade-install integration
- `idea4rc_capsule/deploy.py` — `_run_helm_install_script` adds the
  env vars before calling the bash installer
- `idea4rc_capsule/preflight.py` — `_check_host_tools` verifies the
  binary resolves
- `idea4rc_capsule/config.py` — `HelmPostRendererConfig` dataclass
- `idea4rc_capsule/data/capsule.sample.toml` — documented sample
- `idea4rc_capsule/data/capsule_helm_install.sh` — appends `--post-renderer` when
  `HELM_POST_RENDERER_PATH` is set
- `pyproject.toml` — registers the
  `idea4rc-helm-post-renderer` console script

## 11. Future considerations

1. **Upstream the conditional flag.** A `createNamespaceResources`
   values-flag would obsolete the post-renderer for the
   namespace-specific use case. PR target:
   `IDEA4RC/idea4rc-helm-capsule`, both Namespace templates and
   `values.yaml`. Once merged + on a tag we use, set
   `kinds_to_drop = []` and the post-renderer becomes a passthrough.
   The post-renderer machinery stays — useful for the next time
   upstream emits something we manage externally.

2. **Add a `--validate-rendered` preflight step.** Optional
   `[helm_post_renderer] validate_on_preflight = true` would, in
   `--check-only` runs, do `helm template | post-renderer | grep
   '^kind: Namespace'` and assert zero hits. Catches "upstream added a
   new Namespace template under a new filename" before the destructive
   install.

3. **Add a helm version check** to preflight (e.g. require helm >=
   3.1 for `--post-renderer` support). Not urgent on this host (helm
   3.x is current) but worth documenting.

4. **Track the upstream chart's tagged releases**, not just a moving
   branch (`omop-etl-dev`). Pin `[repo_sync] branch = "v3.4.5"` once
   upstream starts cutting tags; protects against unannounced breaking
   changes.
