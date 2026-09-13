# Architecture

How the operator turns a `Sandbox` custom resource into a running pod, what it
creates alongside that pod, and how it decides whether the result is usable.

The operator is a single-replica Python process built on
[kopf](https://kopf.readthedocs.io). It keeps no cache of its own: every
decision is made from the live `Sandbox` object and the live pod. That keeps
it small and restartable, and it explains several of the limitations at the
bottom of this page.

```
  Sandbox CR
      │
      ├─ on_create ─────────────────────────────────────────────┐
      │    1. per-user state PVC(s)   sandbox-state-<user>       │
      │    2. ServiceAccount          sandbox-<name>             │  create order
      │    3. Vault policy + k8s auth role                       │  matters:
      │    4. Pod                     sandbox-<name>             │  each step
      │    5. Service (interactive)   sandbox-<name>             │  depends on
      │    6. HTTPRoute (interactive) sandbox-<name>             │  the one above
      │    7. status: Pending/Running ───────────────────────────┘
      │
      ├─ reconcile (timer, 10s) ── TTL expiry, pod-failure, Ready reconciliation
      ├─ on_sandbox_pod_event ──── same readiness logic, ~1s after kubelet reports
      └─ on_delete ─────────────── Vault cleanup + ServiceAccount; rest is GC
```

## The reconciliation model

Four handlers, all in `sandbox_operator/main.py`.

| Handler | Trigger | Responsibility |
|---|---|---|
| `on_create` | `@kopf.on.create` on `sandboxes.dev/v1alpha1/sandboxes` | Provisions every sub-resource, in order, and writes the initial status. |
| `on_delete` | `@kopf.on.delete` on the same | Removes the Vault policy/role and the ServiceAccount. Everything else is garbage-collected. |
| `reconcile` | `@kopf.timer(..., interval=10)` | Enforces TTL, detects pod failure, and reconciles the `Ready` condition. |
| `on_sandbox_pod_event` | `@kopf.on.event("", "v1", "pods", labels={"sandboxes.dev/name": kopf.PRESENT})` | Runs the same readiness logic the moment kubelet reports a change, instead of waiting up to 10s for the timer. |

`on_create` is strictly sequential and fails closed. Each step that can fail
calls `_rollback()`, which best-effort deletes the pod, Service, and
ServiceAccount, removes the Vault resources if any were created, and sets
`phase=Failed` with a reason naming the step (`PVCCreationFailed`,
`VaultSetupFailed`, `PodCreationFailed`, …). PVCs are deliberately *not*
rolled back — they are per-user and may be in use by another sandbox.

Two ordering constraints are load-bearing:

- The **ServiceAccount precedes the Vault role**, because the role is bound to
  that ServiceAccount by name.
- The **Vault role precedes the pod**, because the Vault Agent sidecar
  injected into the pod authenticates with that role on startup. Create the
  pod first and the injector races the role into existence.

The Service and HTTPRoute are created only for `mode: interactive`; headless
sandboxes have no ttyd and nothing to route to. HTTPRoute failure is logged
and ignored rather than rolled back — the sandbox is still usable via
`kubectl port-forward` on a cluster with no Gateway API installed.

`on_sandbox_pod_event` and `reconcile` share `_reconcile_readiness()`, so the
promotion logic exists once. The timer is the safety net for events the watch
missed; the watch is what makes the UI feel immediate.

## Sub-resources per Sandbox

The critical distinction is **per-sandbox** (dies with the CR) versus
**per-user** (outlives it, and is how anything survives a restart).

| Resource | Name | Scope | Owned by CR | Lifetime |
|---|---|---|---|---|
| Pod | `sandbox-<name>` | per sandbox | yes (`kopf.adopt`) | deleted with the CR |
| Service | `sandbox-<name>` | per sandbox | yes | deleted with the CR |
| HTTPRoute | `sandbox-<name>` | per sandbox | yes | deleted with the CR |
| ServiceAccount | `sandbox-<name>` | per sandbox | **no** | deleted by `on_delete` |
| State PVC | `sandbox-state-<user>` | **per user** | no | never deleted |
| Harness PVC | `sandbox-pi-state-<user>`, `sandbox-codex-state-<user>`, `sandbox-opencode-state-<user>`, `sandbox-agy-state-<user>` | **per user** | no | never deleted |

Everything carries the labels `sandboxes.dev/name`, `sandboxes.dev/user`,
`sandboxes.dev/mode`, and `app.kubernetes.io/managed-by: sandbox-operator`.
The pod-event watch and the isolation chart's NetworkPolicy both select on
`sandboxes.dev/name`, so a runtime image that strips labels breaks both.

`kopf.adopt()` stamps an `ownerReference` on the pod, Service, and HTTPRoute,
which is what makes Kubernetes delete them when the `Sandbox` goes away. The
ServiceAccount is exempt and gets deleted explicitly in `on_delete`, so if the
operator is down when a `Sandbox` is deleted, the pod and Service still
disappear (the API server does that) but the ServiceAccount and the Vault
policy leak.

### Per-user state, and what "restart" means

`sandbox-state-<user>` mounts at `/home/agent/.claude` in every sandbox that
user owns. It holds credentials, history, and agent config, and it is
`ReadWriteOnce`, so two concurrent sandboxes for the same user land on the
same node — that is intentional shared state, not a bug.

Harness PVCs follow the same rule but are created and mounted only when
`spec.harness` selects them, because each agent CLI keeps its OAuth token
somewhere different:

| `spec.harness` | PVC | Mount |
|---|---|---|
| `pi` | `sandbox-pi-state-<user>` | `/home/agent/.pi` |
| `codex` | `sandbox-codex-state-<user>` | `/home/agent/.codex` |
| `opencode` | `sandbox-opencode-state-<user>` | `/home/agent/.local/share/opencode` |
| `antigravity` | `sandbox-agy-state-<user>` | `/home/agent/.gemini` |

The point is that a user runs `codex login` once, not once per sandbox.

Everything else in the pod is ephemeral: the workdir (unless
`spec.workingDir.existingPVC` or `hostPath` is set), the knowledge-vault
clone (an `emptyDir` populated by an init container), and any credential
Secret mounted at `/vault/secrets`.

## Status and conditions

The status carries a `phase`, a flat `reason`, an optional `message`, and one
condition of type `Ready`. `phase` answers "what is this doing", `reason`
answers "at which stage", and `Ready` answers the only question a caller
usually has: can I attach to it.

| Phase | Meaning |
|---|---|
| `Provisioning` | `on_create` is partway through creating sub-resources. |
| `Pending` | Sub-resources exist; the pod is not attachable yet. `reason` says why. |
| `Running` | Interactive: ttyd's readiness probe passes. Headless: the pod was created. |
| `Failed` | Provisioning failed, or the pod failed. `reason` names the step. |
| `Expired` | TTL exceeded; the CR is being deleted. |

For interactive sandboxes `phase=Running` **means attachable**, not merely
scheduled. `on_create` deliberately writes `Pending`/`PodStarting` after
creating the pod and lets `_reconcile_readiness()` promote it once the pod's
own `Ready` condition flips. Without that, a UI would show a green "Running"
chip pointing at a container that is still pulling its image.

The promotion is symmetric: a `Running` sandbox whose pod stops being ready —
a crash loop, a failing probe — is demoted back to `Pending` with the current
reason. `Ready` is recomputed from the pod every time, never latched.

When the pod is not ready, `_pod_readiness()` copies the most informative
container *waiting* reason onto the `Ready` condition, so the pod-level detail
surfaces on the resource you were already looking at:

```console
$ kubectl get sandbox refactor-auth -o wide
NAME            USER    MODE          PHASE     REASON              EXPIRES
refactor-auth   alice   interactive   Pending   ImagePullBackOff    2026-05-04T09:12:00+00:00

$ kubectl get sandbox refactor-auth -o jsonpath='{.status.conditions[0]}' | jq
{
  "type": "Ready",
  "status": "False",
  "reason": "ImagePullBackOff",
  "message": "Back-off pulling image \"ghcr.io/you/sandbox-runtime:v0.3\"",
  "lastTransitionTime": "2026-05-04T01:12:44+00:00"
}
```

That is the intended debugging loop: `REASON` (a printer column) tells you
which stage is stuck, `message` tells you why, and neither requires operator
logs. `PodStarting` and `ContainerCreating` are normal for the first few
seconds; `VaultSetupFailed`, `PodNotFound`, and `ImagePullBackOff` are not.

## Lifecycle and TTL

`spec.ttl` is parsed once, at create time, by `parse_ttl()` — a small
hand-rolled parser for `h`/`m`/`s` suffixes — and the resulting deadline is
written to `status.expiresAt`. Nothing re-reads `spec.ttl` afterwards.

Every 10 seconds, for every `Sandbox`, `reconcile()`:

1. Returns immediately if the phase is terminal (`Failed`, `Expired`,
   `Terminating`).
2. Compares `status.expiresAt` to now. If past, sets `phase=Expired` and
   **deletes the `Sandbox` CR**, which cascades to the pod, Service, and
   HTTPRoute via `ownerReferences`, and fires `on_delete` for Vault and the
   ServiceAccount.
3. Reads `sandbox-<name>`. A 404 while the phase is `Pending` or `Running`
   means `Failed`/`PodNotFound`.
4. A pod in phase `Failed` or `Succeeded` means `Failed`/`PodFailed`.
5. Otherwise, for interactive sandboxes, reconciles readiness.

Expiry granularity is therefore up to 10 seconds, and TTL is enforced by
deleting the resource — there is no "stopped" state to return from.

**The pod is built exactly once, in `on_create`.** There is no update handler:
editing `spec.harness`, `spec.resources`, or `spec.ttl` on a live `Sandbox`
changes nothing about the running pod, and `restartPolicy: Never` means a
crashed container is not restarted either. Restarting a sandbox means deleting
and recreating the CR. What survives that is exactly the per-user PVCs — which
is why credentials and shell history persist while the workdir, unless it is
an `existingPVC` or `hostPath`, does not.

## Vault integration

Optional, controlled by `spec.vault.enabled` (falling back to the operator's
`SANDBOX_VAULT_ENABLED`). When on, `on_create` provisions two Vault objects
per sandbox, both named `sandbox-<name>`:

- **A policy** granting `read` on `secret/data/sandboxes/<user>/*` and `list`
  on `secret/metadata/sandboxes/<user>/*`. Scoped to the *user*, not the
  sandbox — two sandboxes owned by Alice can read the same secrets; Bob's are
  unreachable from either. `validate_user()` rejects anything outside
  `[a-zA-Z0-9][a-zA-Z0-9._-]{0,62}`, and `..` specifically, so a user string
  cannot escape its subtree in the policy path.
- **A Kubernetes auth role** bound to `bound_service_account_names=["sandbox-<name>"]`
  in the sandbox's namespace, with the policy attached and the sandbox's TTL
  as the token TTL.

The bound identity is the **per-sandbox ServiceAccount** — this is the reason
each sandbox gets its own rather than sharing one. A sandbox presents its
projected SA token, Vault matches the binding, and issues a token carrying
only that user's policy.

The operator itself authenticates to Vault with its own SA token under the
`sandbox-operator` role, falling back to `VAULT_TOKEN` when running outside
the cluster.

With Vault enabled and `harness: claude-code`, the pod gets
`vault.hashicorp.com/agent-inject` annotations that render the secret to
`/vault/secrets/credentials.json`. With Vault disabled, the same path is
populated from a plain Kubernetes Secret (`spec.vault.secretName`, default
`sandbox-claude-auth`). `spec.userCreds.secretName` overrides both. Deleting a
sandbox removes its policy and role; failures there are logged, not retried.

## RBAC

The operator runs under a single `ClusterRole`
(`charts/sandbox-operator/templates/rbac.yaml`):

| Rule | Why |
|---|---|
| `sandboxes.dev`: `sandboxes`, `sandboxes/status` — full verbs | Watches the CRs; patches the status subresource; `delete` is what enforces TTL expiry. |
| core: `pods`, `services`, `serviceaccounts`, `persistentvolumeclaims` — full verbs | The sub-resources it creates, plus `delete` for rollback and cleanup. |
| `gateway.networking.k8s.io`: `httproutes` — full verbs | Per-sandbox ingress routes. Harmless if Gateway API is not installed. |
| `apiextensions.k8s.io`: `customresourcedefinitions` — read-only | kopf discovers the CRD's scope and served versions at startup. |
| core: `events` — `create`, `patch` | kopf emits Kubernetes events against the objects it handles. |
| `coordination.k8s.io`: `leases` — full verbs | kopf's peering/leader election. |

Two things to note. This is a `ClusterRole` bound cluster-wide, so the
operator can create and delete pods, Services, ServiceAccounts, and PVCs in
*any* namespace even though it only ever acts in one — narrowing it to a
namespaced `Role` is a reasonable local hardening. And it has no RBAC on
`secrets`: it references Secrets by name in pod specs but never reads them.

## Known limitations

Observed in the code as it stands, not speculative:

- **Deleting a Sandbox requires a running operator.** kopf adds a finalizer,
  so if the operator is down, `kubectl delete sandbox` hangs indefinitely
  rather than failing. Start the operator, or clear the finalizer by hand:
  `kubectl patch sandbox <name> --type=merge -p '{"metadata":{"finalizers":[]}}'`.
- **Run exactly one operator instance.** Two processes watching the same
  namespace race each other over sub-resource creation and both write status,
  producing spurious `Failed` phases. The chart deploys a single replica; do
  not run a local `kopf run` against a cluster that already has one.

- **No update path.** There is no `on.update` or `on.field` handler. Spec
  edits on a live `Sandbox` are silently ignored; only delete-and-recreate
  applies them.
- **No retry after a failed create.** `_rollback()` returns normally, so kopf
  considers the create handler successful. A `Sandbox` in `Failed` stays there
  until deleted, even if the underlying cause (Vault down, registry
  unreachable) clears.
- **A completed headless run reports `Failed`.** `restartPolicy: Never` means
  a finished agent leaves the pod in phase `Succeeded`, and `reconcile()`
  treats `Succeeded` and `Failed` identically as `PodFailed`. There is
  currently no `Completed` phase.
- **PVC creation is read-then-create.** `ensure_pvc()` and friends check for a
  404 and then create. Two sandboxes created simultaneously for the same new
  user can race; the loser gets a 409, which is re-raised and rolls the
  sandbox back to `Failed`.
- **Status writes have no optimistic concurrency.** `set_phase()` replaces the
  whole `conditions` array with no `resourceVersion` precondition, so the
  timer and the pod-event watch can overwrite each other. Both derive their
  answer from the same live pod, so this converges rather than oscillating,
  but a stale write can briefly win.
- **Init-container failures are invisible in `reason`.** `_pod_readiness()`
  inspects `container_statuses` only. A knowledge-vault clone that cannot
  reach its git remote leaves the sandbox reporting `PodNotReady` /
  `Pod phase Pending` rather than the init container's actual error.
- **A sandbox stuck in `Provisioning` is never rescued.** The 404-pod branch
  in `reconcile()` only acts when the phase is `Pending` or `Running`.
- **The Vault client is created once and never re-authenticated.** It is
  cached in a module-level global; when its token expires, Vault operations
  fail until the operator pod restarts.
- **PVCs are never garbage-collected.** Per-user state persists indefinitely,
  including for users who no longer exist. Reclaiming it is a manual job.
- **`parse_ttl()` is not a real Go duration parser.** It understands only
  `h`, `m`, and `s`, and silently ignores everything else — `1d` parses to
  zero and falls back to the 8h default, and `500ms` is read as 500 minutes.
- **Single replica, no HA.** The chart sets `replicas: 1`. The lease
  permissions are there for kopf's peering, but nothing in this deployment
  exercises a second instance.
