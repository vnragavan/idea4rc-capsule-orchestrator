# Known harmless warnings during deploy/ingest

> **TL;DR**
> Every successful deploy / ingest emits a small set of warnings from
> kubectl, helm, and microk8s that look alarming but are functionally
> benign. This doc enumerates the known ones, explains *why* they're
> safe to ignore, and gives a recipe for triaging *new* warnings that
> show up after upstream changes.
>
> If you see a warning that's not on this list, treat it as a real
> defect until proven otherwise. Use § "Triaging a new warning" to
> classify it, then either patch the orchestrator or extend this doc.

---

## 1. The known list

### 1.1 `kubectl` server-side validation: `unknown field "spec.egress[1].ports[2].to"`

**What you see in the log:**

```
[capsule-helm-install] Installing release 'idea4rc-capsule' into namespace 'datamesh'
W0506 05:06:52.232924 1928118 warnings.go:70] unknown field "spec.egress[1].ports[2].to"
NAME: idea4rc-capsule
LAST DEPLOYED: ...
STATUS: deployed
REVISION: 1
```

**Source resource:** `idea4rc-capsule-central-compute-policy`
NetworkPolicy, rendered from
`templates/v6node/tasks-namespace.yaml`. Egress rule index `[1]` has
a `ports` list whose third entry was incorrectly authored as `- to:
[]` (a bare `to` keyword nested under `ports`):

```yaml
spec:
  egress:
  - ...
  - to:
    - namespaceSelector:
        matchLabels:
          kubernetes.io/metadata.name: kube-system
    ports:
    - { protocol: UDP, port: 53 }
    - { protocol: TCP, port: 53 }
    - to: []                                # ← MISPLACED, generates the warning
```

**Why it's harmless:**

The k8s API server's `NetworkPolicyPort` schema does not define a
`to` field. The server logs the `unknown field` warning and **drops
the field** from the persisted manifest. The remaining
`NetworkPolicyPort` entries (UDP/53, TCP/53) and the `to` selector
on the egress rule itself (kube-system) ARE applied. Net behaviour:
egress to kube-system on DNS port 53 is allowed, exactly as the
chart authors clearly intended.

**Why we don't suppress it:**

The post-renderer in this orchestrator drops resources at the `kind`
level only (see
`namespace-ownership-and-helm-post-renderer.md`). Field-level YAML
surgery (delete one `[2].to` entry inside one specific
NetworkPolicy) is over-engineered for a benign warning. A one-line
upstream PR would fix the chart definitively, but per project policy
we don't open upstream PRs from this repo.

**What to re-check after each `repo_sync`:**

```bash
# After upstream pulls, render the chart and confirm the warning's
# source path is still benign (Empty `to: []` inside `ports`):
cd $HOME/idea4rc-helm-capsule
microk8s.helm template idea4rc-capsule . -n datamesh \
  --set capsulePublicHost=10.0.0.1 --set istio.tls.commonName=10.0.0.1 \
  --set v6node.node.apiKey=x --set v6node.node.name=x --set v6node.node.k8sNodeName=x \
  --set fcbexec.keyCloak.clientId=x --set fcbexec.keyCloak.clientSecret=x \
  --set fcbexec.keyCloak.host=x --set fcbexec.kafka.clientId=x --set fcbexec.kafka.consumerId=x \
  > /tmp/render.yaml 2>/dev/null

python3 -c "
import yaml
for d in yaml.safe_load_all(open('/tmp/render.yaml')):
    if d and d.get('kind') == 'NetworkPolicy':
        for i, e in enumerate(d['spec'].get('egress') or []):
            for j, p in enumerate(e.get('ports') or []):
                if 'to' in p:
                    print(d['metadata']['name'],
                          f'egress[{i}].ports[{j}].to =', p['to'])
"
```

Expected output: each match's `to` value is **`[]`** (empty list).
If it ever becomes a populated selector, that selector would be
silently dropped by the API server — that's no longer benign and
needs an upstream fix or post-render patch.

---

### 1.2 `vantage6` chart repo TLS timeout during `helm dependency update`

**What you see in the log:**

```
[capsule-helm-install] Updating Helm chart dependencies
Hang tight while we grab the latest from your chart repositories...
...Unable to get an update from the "vantage6" chart repository (https://harbor2.vantage6.ai/chartrepo/infrastructure):
        Get "https://harbor2.vantage6.ai/chartrepo/infrastructure/index.yaml": dial tcp 20.50.179.220:443: connect: connection timed out
Update Complete. ⎈Happy Helming!⎈
Saving 8 charts
Downloading node from repo oci://ghcr.io/iknl
Pulled: ghcr.io/iknl/node:5.0.0-b1
```

**Why it happens:**

`microk8s.helm dependency update` walks every entry in
`~/.config/helm/repositories.yaml` (or its microk8s-snap equivalent)
and re-fetches each repo's `index.yaml`. The legacy `vantage6` chart
repo at `https://harbor2.vantage6.ai/chartrepo/infrastructure/` is
either offline or firewalled from this host. helm tries, times out
after ~30s per attempt (twice — once during `update`, once during
`build`), prints the warning, **and continues**.

**Why it's harmless:**

The chart we actually need is downloaded from a different source:
`oci://ghcr.io/iknl` (`Pulled: ghcr.io/iknl/node:5.0.0-b1`). That
is the operative location declared in `Chart.yaml`'s
`dependencies:` block. The harbor2 entry is a stale repo
registration left in `~/.config/helm/repositories.yaml`, NOT
referenced by the chart's actual dependencies.

helm's exit code is 0; the install proceeds to the
`Pulled: ghcr.io/iknl/node:5.0.0-b1` line and beyond. Nothing
upstream depends on harbor2 succeeding.

**What you can do (optional):**

Remove the stale repo registration to silence the warning entirely:

```bash
microk8s.helm repo list                       # confirm vantage6 entry exists
microk8s.helm repo remove vantage6            # remove it
microk8s.helm repo list                       # confirm it's gone
```

This change persists in `~/snap/microk8s/current/.config/helm/`
(which is per-user, not per-deploy). The warning will not return
unless someone re-adds the repo via `microk8s.helm repo add
vantage6 ...`. Cost: ~30s saved per deploy.

**What to re-check after each `repo_sync`:**

If the next pull from `idea4rc-helm-capsule` makes harbor2 a real
dependency (chart's `Chart.yaml` `dependencies:` block adds it),
this stops being benign. Watch for new entries:

```bash
grep -A3 'name:' $HOME/idea4rc-helm-capsule/Chart.yaml | head -40
grep -RA3 'name:' $HOME/idea4rc-helm-capsule/charts/*/Chart.yaml | head
```

Today the upstream pulls `node` from `ghcr.io/iknl` (OCI), `etl`,
`feasibility-cohort-builder-exec`, etc. from local sub-charts. No
chart actively depends on `harbor2.vantage6.ai`.

---

## 2. Triaging a new warning

When deploy or ingest logs a warning you don't recognise, ask the
following four questions in order. The answers tell you whether to
ignore, document, or fix.

### Q1: Did the parent operation succeed?

Look at the line *immediately after* the warning, plus the next
"phase" log line from the orchestrator.

- `STATUS: deployed REVISION: N` → helm install succeeded; warning
  is informational only.
- Stack trace / `Error:` / orchestrator's `[FATAL]` → real error
  hiding behind the warning text. Treat as a defect.

### Q2: Is the warning from the API server, helm, or kubectl?

- **API-server validation warning** (`Wmmdd HH:MM:SS PID warnings.go`):
  almost always `unknown field` or `deprecated apiVersion`. The
  resource is still created; the unknown/deprecated field is
  ignored. Document it (here) and move on.
- **helm dependency / repo warning** (`Unable to get an update from
  the "..." chart repository`): network-only. Confirm that the
  failing repo is NOT in `Chart.yaml`'s `dependencies:`. If not,
  document or remove the repo registration.
- **kubectl client warning** (e.g.
  `Warning: would violate "PodSecurity" ...`): the cluster's
  PodSecurity admission policy. The pod is still admitted under
  `restricted`-mode warnings; if it's `enforce`, the create call
  fails outright and you'd see an error, not a warning.

### Q3: Does the warning recur on every run?

- **Yes** → it's chart-shaped. Document here, watch for upstream
  fix.
- **One-time / sporadic** → likely a transient network or cluster
  hiccup. If it's reproducible, hunt the root cause; if not,
  ignore.

### Q4: Could a future upstream change turn it into a real
problem?

For each warning, write down (in this doc) the conditions under
which it would no longer be benign. That's the point of the "What
to re-check after each `repo_sync`" sub-section in each entry —
forward-protection against the workaround silently becoming wrong.

---

## 3. What is NOT considered harmless

These warning patterns are real defects masquerading as warnings.
If you see one, do NOT add it to this doc; fix it.

- `Warning: resource ... already exists in another release` → helm
  is about to corrupt your release state. Resolve before
  proceeding (delete the offending resource or re-target the
  release).
- `Warning: would violate "PodSecurity" ... level "restricted"` on
  a cluster with `enforce: restricted` → install will fail at the
  next try. Patch the chart values to comply.
- `Error from server (NotFound)` after a `kubectl get`/`exec` →
  the resource you wanted is missing. Probably a phase-ordering
  bug.
- `Pod has unbound immediate PersistentVolumeClaims` → a PV is
  missing or its `storageClassName` doesn't match. Real
  configuration issue.
- Anything from `pg_restore` containing `ERROR:` → the OMOP dump
  load failed mid-stream. Fix before continuing (check pg
  permissions, dump file integrity, free disk space).

When in doubt, follow the rule: **a warning that doesn't change
behaviour is documented; a warning that does is fixed.**

---

## 4. Related files

- `docs/namespace-ownership-and-helm-post-renderer.md` — for the
  pattern we use when an upstream chart resource needs to be
  *removed* (drop at the `kind` level via post-renderer)
- `docs/upstream-helper-script-hygiene.md` — for the pattern we
  use when an upstream helper script can't be exec'd safely
  (interpose `bash <script>`)
- `idea4rc_capsule/preflight.py` — could grow a `_check_known_warnings`
  step that pre-renders the chart and asserts the known warnings'
  signatures haven't changed. Open question; today the burden is
  on the operator to eyeball deploy logs.

---

## 5. Future considerations

1. **Add a `warnings_to_silence` post-renderer mode.** If the list
   of harmless warnings grows past 3–5 entries, consider extending
   `idea4rc-helm-post-renderer` to do field-level patches (drop
   specific empty/misplaced fields by JSON-pointer-like paths)
   driven from `[helm_post_renderer]` config. Today it's
   kind-level only.

2. **Pin chart to a tagged release**, not a moving branch
   (`omop-etl-dev`). Once upstream cuts tags we can rely on, set
   `[repo_sync] branch = "v3.4.5"` and the harmless-warnings list
   becomes stable until you opt into a new tag.

3. **Surface unrecognised warnings during preflight.** A future
   iteration could `helm template` the chart, run `kubectl apply
   --dry-run=server -f -` against it, capture warnings, and
   diff against this doc's known-list. Anything new → preflight
   warning. Anything missing from the deploy log that we expected
   → preflight warning ("upstream may have fixed it; consider
   removing the entry").
