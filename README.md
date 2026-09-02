# sandbox-operator

Run coding-agent CLIs — Claude Code, Codex, opencode, and friends — as
isolated, per-user Kubernetes **sandboxes** you reach from a browser.

You create a `Sandbox`; the operator gives it a pod, a terminal on port 7681,
a persistent home directory scoped to its owner, its own ServiceAccount, and a
time-to-live. The companion isolation chart puts that pod behind mTLS, an L7
authorization policy, and a default-deny egress allow-list — so an agent that
goes off the rails can only reach the hosts you named.

```yaml
apiVersion: sandboxes.dev/v1alpha1
kind: Sandbox
metadata:
  name: refactor-auth
  namespace: sandboxes
spec:
  user: alice
  harness: claude-code
  ttl: 4h
```

```console
$ kubectl get sandboxes
NAME            USER    MODE          PHASE     EXPIRES                AGE
refactor-auth   alice   interactive   Running   2026-09-02T18:04:11Z   38s
```

## Why this exists

A coding agent is a program you have deliberately given the ability to write
and run arbitrary code. Running it on your laptop means its blast radius is
your laptop. This operator moves that blast radius into a pod with an explicit
boundary: what it can reach, how long it lives, and whose data it can see are
all declared, not assumed.

The isolation is the product. The terminal is just how you get to it.

## Status

Early. The operator has been running a small private fleet for months, but
this is its first public release: expect rough edges in packaging, and expect
the CRD to change before a v1 group. Issues and PRs welcome — see
[CONTRIBUTING.md](CONTRIBUTING.md).

## What's in this repo

| Path | What it is |
|---|---|
| `sandbox_operator/` | The operator (Python, [kopf](https://kopf.readthedocs.io)) |
| `crd/sandbox-crd.yaml` | The `Sandbox` CustomResourceDefinition |
| `charts/sandbox-operator/` | Operator Deployment, RBAC, ServiceAccount |
| `charts/sandbox-isolation/` | Istio + NetworkPolicy guardrails — [read this](docs/isolation.md) |
| `examples/tailscale/` | One worked ingress example (the author's) |
| `docs/` | Architecture, isolation model, runtime-image contract |

## What you need to bring

This repo is the control plane, not a turnkey product. Three things are
deliberately yours to choose:

**A runtime image.** Every Sandbox pod runs an image that serves a terminal on
port 7681 and has your agent CLI installed. This repo does not ship one,
because what belongs in it — which agents, which language toolchains, which
company CA — is entirely site-specific. The contract is small and fully
specified in [docs/runtime-image.md](docs/runtime-image.md).

**An ingress path.** For interactive sandboxes the operator creates a Service
on port 7681 and an `HTTPRoute` for `/sandbox/<name>` — but that route expects
a Gateway named `sandbox-gateway` to exist, and it is yours to provide.
Whether you front it with Gateway API, an Ingress controller, a VPN, or just
`kubectl port-forward` is your call, and **nothing here authenticates anyone**.
[docs/ingress.md](docs/ingress.md) covers the requirements (WebSockets, path
routing, auth) and `examples/tailscale/` is one complete answer.

**A credential story.** Agents need provider credentials. The operator can
broker them per-user through Vault, or you can mount a Secret yourself.
See [docs/credentials.md](docs/credentials.md).

## Install

Requires a Kubernetes cluster. For the isolation guarantees you also need
Istio in [ambient mode](https://istio.io/latest/docs/ambient/) — the operator
runs without it, but then "sandbox" is aspirational; see
[docs/isolation.md](docs/isolation.md).

```bash
kubectl create namespace sandboxes
kubectl apply -f crd/sandbox-crd.yaml

helm install sandbox-operator charts/sandbox-operator \
  --namespace sandboxes \
  --set image.registry=<your-registry> \
  --set image.sandbox=<your-runtime-image>

# The guardrails. Read docs/isolation.md first — the defaults deny more
# than you may expect, which is the intent.
helm install sandbox-isolation charts/sandbox-isolation \
  --namespace sandboxes
```

Then create a sandbox:

```bash
kubectl apply -f examples/sandbox.yaml
kubectl get sandbox refactor-auth -o wide
kubectl port-forward svc/sandbox-refactor-auth 7681:7681   # then open localhost:7681
```

## How it works

```
  Sandbox CR ──▶ operator ──┬──▶ Pod (ttyd :7681 ─ tmux ─ agent CLI)
                            ├──▶ Service + HTTPRoute   interactive mode only
                            ├──▶ PVC              per USER, outlives the sandbox
                            ├──▶ ServiceAccount   per SANDBOX, identity for Vault
                            └──▶ TTL timer        deletes it when time is up

  your ingress ──▶ Service :7681
  agent egress ──▶ waypoint (L7 authz) ──▶ ServiceEntry allow-list ──▶ internet
```

The pod is created once, at admission. Restarting a sandbox means deleting and
recreating the CR — the per-user PVC is what carries state across that, so
credentials and shell history survive while the compute does not.

Details in [docs/architecture.md](docs/architecture.md).

## The `Sandbox` API

| Field | Default | Meaning |
|---|---|---|
| `spec.user` | *(required)* | Owner. Selects the state PVC and Vault scope. |
| `spec.harness` | `claude-code` | Which agent CLI to launch. |
| `spec.mode` | `interactive` | `interactive` (terminal) or `headless`. |
| `spec.ttl` | `8h` | Lifetime, e.g. `30m`, `4h`. |
| `spec.workingDir` | empty dir | Working directory: `gitRepo`, `existingPVC`, or `hostPath`. |
| `spec.knowledgeVault` | no repo → off | Git repo shallow-cloned in as shared context. |
| `spec.resources` | small | CPU/memory requests and limits. |

`kubectl explain sandbox.spec` has the full schema.

## Security

The threat model, what each layer actually enforces, and what is explicitly
*not* protected are written down in [docs/isolation.md](docs/isolation.md).
Please read it before trusting this with anything sensitive, and report
vulnerabilities per [SECURITY.md](SECURITY.md).

## License

MIT — see [LICENSE](LICENSE).
