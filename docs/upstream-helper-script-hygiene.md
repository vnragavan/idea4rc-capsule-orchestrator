# Upstream chart helper-script hygiene

> **TL;DR**
> The upstream `idea4rc-helm-capsule` chart ships small "helper"
> shell scripts under `utils/` (e.g. `query-executor-create-secret.sh`)
> that the orchestrator invokes during the deploy phase. These scripts
> are written for casual operator use and routinely have hygiene gaps
> that bite a strict `subprocess.run(...)` in Python:
>
> - missing `#!/usr/bin/env bash` shebang line
> - CRLF line endings from a Windows author
> - unset/incorrect executable bit (`chmod +x`)
> - `cd` into the wrong directory
> - hard-coded relative paths
>
> **The orchestrator fix is to invoke every chart helper script through
> an explicit interpreter (`bash <script>`)** rather than relying on
> the kernel's `execve()` to find one. Same rule we use for the
> namespace-ownership fix: **patch the orchestrator, never the chart.**
> `repo_sync` keeps the chart byte-for-byte identical to upstream.

---

## 1. The concrete failure that triggered this rule

During a deploy after a successful `helm install`
via the post-renderer, the orchestrator failed at Phase 3 step 1:

```
[YYYY-MM-DD HH:MM:SS] Creating query executor secret via script
Traceback (most recent call last):
  ...
  File ".../idea4rc_capsule/deploy.py", line 198, in run_query_executor_secret_creation
    proc = subprocess.run([str(script)], cwd=str(secret_dir), check=False)
  ...
OSError: [Errno 8] Exec format error: '$HOME/idea4rc-helm-capsule/utils/query-executor-create-secret.sh'
```

`Errno 8` is `ENOEXEC`. The kernel returned it because the file's
executable bit is set but its first line is not a valid shebang:

```sh
$ head -1 $HOME/idea4rc-helm-capsule/utils/query-executor-create-secret.sh
echo "You need to place in the current path the following files provided by the manager of the Orchestrator:"
```

The file has `chmod +x` but no `#!/usr/bin/env bash`. When you `cd
utils && ./query-executor-create-secret.sh` interactively, your shell
notices the missing shebang and silently falls back to `/bin/sh`. But
when Python's `subprocess.run([str(script)])` issues `execve()`
directly, the kernel has nowhere to fall back to and bails with
`ENOEXEC`.

## 2. Why we don't fix the chart

Same reasoning as in `namespace-ownership-and-helm-post-renderer.md`:

1. We want `repo_sync.reset = true` to do `git reset --hard
   origin/<branch>` on every deploy. Local edits would be silently
   wiped.
2. We don't want to maintain a fork.
3. The fix needs to ship today — upstream PRs (when desirable) take
   weeks.

So the orchestrator absorbs the workaround.

## 3. The fix

`deploy.py::run_query_executor_secret_creation` now invokes the script
via `bash <script>` rather than `execve(<script>)`:

```python
# OLD (relies on kernel execve resolving a shebang):
proc = subprocess.run([str(script)],
                      cwd=str(secret_dir), check=False)

# NEW (interpreter is explicit; no shebang required):
proc = subprocess.run(["bash", str(script)],
                      cwd=str(secret_dir), check=False)
```

Why `bash` and not `sh`:

- The chart helper scripts use bash idioms (`set -euo pipefail`,
  `[[ ... ]]`, here-strings, `${var:-default}`) even when they don't
  declare bash as interpreter.
- `/bin/sh` on Ubuntu is `dash`, which would error on those idioms.
- All target hosts are expected to have bash on `PATH`; `ingest.py`
  checks this with `require_tool("bash")` before running the pipeline,
  while deploy-only failures would surface at helper invocation time.

This works whether or not the script eventually grows a shebang
upstream. If upstream later adds `#!/usr/bin/env bash`, our explicit
invocation is a no-op (bash just reads the file like any other source
file). If the file becomes a different language (would be surprising
for a `.sh` file), we'd need to revisit.

## 4. Generalised rule for the orchestrator

> **Any chart helper script invoked by the orchestrator MUST go through
> an explicit interpreter, even when the file is `chmod +x`.**

Currently this rule applies to:

| Script | Where invoked | Interpreter |
|---|---|---|
| `idea4rc-helm-capsule/utils/query-executor-create-secret.sh` | `deploy.py::run_query_executor_secret_creation` | `bash` |

When new chart helpers appear upstream and we wire them in, follow
the same pattern. Two minutes of explicit `["bash", ...]` saves the
next operator a 4-minute helm install + a confusing traceback.

The rule does **not** apply to:

- `idea4rc_capsule/data/capsule_helm_install.sh` — owned by us, has a
  proper `#!/usr/bin/env bash` shebang, `set -euo pipefail`, etc. The
  orchestrator invokes it through `bash` so package file modes do not
  matter.
- The pipx-installed Python entry points (`idea4rc-capsule`,
  `idea4rc-helm-post-renderer`). pipx generates correct shebangs
  pointing at the venv's interpreter; the kernel finds it; no Python
  path discovery problems (modulo the snap-helm `PYTHONPATH`
  pollution, which is handled by the env-sanitising launcher
  documented in
  `namespace-ownership-and-helm-post-renderer.md`).

## 5. What could break the workaround

| Scenario | Impact | Mitigation |
|---|---|---|
| `bash` not on `PATH` | `[Errno 2] No such file or directory: 'bash'` from `subprocess.run`. | `ingest.py` calls `require_tool("bash")` before running the pipeline. The deploy-only path relies on preflight host-tool checks but does not currently require `bash` explicitly, so a missing `bash` would surface at query-executor helper invocation time. |
| Upstream rewrites `query-executor-create-secret.sh` in Python | `bash` would emit syntax errors trying to parse it. | Add the new interpreter to a small `_invoke_helper(script)` dispatch (`.py` → `python3`, `.sh` → `bash`, default → `bash`). |
| Upstream renames or moves the helper | `cfg.query_executor.secret_script_path` no longer points at it; preflight `_check_paths` reports `query_executor.secret_script_path: <path> not found`. | Update `[query_executor].secret_script_path` in `capsule.toml`. |
| Helper script does its own `cd ../something` based on `$0` | Our `cwd=str(secret_dir)` already places us in the right dir; `$0` from `bash <script>` is the absolute path we passed, so script-internal `dirname $0` works. | Test after upstream changes; covered by deploy logs. |
| Helper expects to be run under `set +x` (no debug output) but our parent shell has `set -x` | Bash invocation is a fresh process; parent shell options don't carry over. | n/a |
| CRLF line endings from a Windows author | `bash` tolerates a trailing `\r` on most lines but errors on quoted strings or here-docs containing `\r`. | Add a `dos2unix` pass in `repo_sync.py` if it ever happens. Cheap detection: `file <path>` reports `with CRLF line terminators`. Not yet observed; leave for now. |

## 6. Detection / drift signal

After every `repo_sync` (`reset = true` makes this every deploy run),
the chart's helper scripts are re-pulled clean from upstream. If
upstream changes any helper script's:

- shebang line (adds, removes, or changes interpreter)
- executable bit
- working-directory assumptions (e.g. now expects to be run from
  `utils/`, not from the project root)

…the orchestrator's `bash <script>` invocation continues to work for
the most common cases. A behavioural change inside the helper (e.g.
the secret name changes) is harder to catch — it would surface as a
runtime failure deep in the deploy or as a missing k8s secret in the
post-install pod-readiness wait.

A 5-minute smoke after each upstream pull (similar to the rendered
chart kind-count smoke in `namespace-ownership-and-helm-post-renderer.md
§6`) would catch helper-script drift:

```bash
# 1. Inventory the helpers we depend on
ls -la $HOME/idea4rc-helm-capsule/utils/*.sh

# 2. Lint-only parse with bash -n (does not execute)
for s in $HOME/idea4rc-helm-capsule/utils/*.sh; do
  echo "=== $s ==="
  head -1 "$s"
  bash -n "$s" && echo "  syntax OK" || echo "  SYNTAX ERROR"
done

# 3. Diff helpers vs the version that worked last time
git -C $HOME/idea4rc-helm-capsule log --oneline -5 -- utils/
```

## 7. Related files

- `idea4rc_capsule/deploy.py::run_query_executor_secret_creation`
  — the call site, with inline comment explaining the `bash` prefix
- `idea4rc-helm-capsule/utils/query-executor-create-secret.sh`
  — the affected upstream helper (do not edit; chart syncs via
  `repo_sync`)
- `idea4rc_capsule/data/capsule_helm_install.sh` — the helper we DO own; serves as
  the reference for what a hygienic chart helper looks like (proper
  shebang, `set -euo pipefail`, env-driven, no secret leaks)
- `idea4rc_capsule/preflight.py::_check_paths` — already verifies the
  helper script exists; could optionally start probing for a shebang
  (warning, not error) so we surface the hygiene gap during preflight
  instead of letting it fail at deploy time

## 8. Future considerations

1. **Add `_check_helper_script_hygiene()` to preflight.** Walk every
   chart helper invoked by the orchestrator (today: just one). For
   each: read the first line, warn (not error) if it's not a valid
   shebang. Cheap, gives operators advance notice before deploy, and
   documents which scripts are in our care.

2. **Centralise helper invocation behind a `_run_chart_helper(path,
   cwd)` function.** One place to add interpreter dispatch (`.py` vs
   `.sh`), shebang-mismatch warnings, and stricter checks. Easy
   refactor when the second helper script lands.

3. **PR upstream the missing shebang on
   `query-executor-create-secret.sh`.** The fix is one line:
   prepend `#!/usr/bin/env bash`. Marked as "not now" per the
   orchestrator-only policy, but trivial to upstream when convenient.
   Once merged, our `bash <script>` pattern remains a no-op safety
   net.
