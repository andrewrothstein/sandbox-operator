# The isolation model

What makes a `Sandbox` a sandbox, layer by layer — and, just as importantly,
what it does not protect you from.

## The threat being modelled

A coding agent is a program you have deliberately given the ability to write
files and execute commands, driven by a model responding to text that may
include a hostile README, a poisoned dependency, or a prompt-injecting web
page. The realistic failure is not "the agent turns evil"; it is **the agent
is induced to do something reasonable-looking with the credentials and network
access it happens to have**.

So the design question is not "how do we stop the agent from misbehaving" but
"what is reachable when it does". Every layer below narrows that.

## Layer 1 — the pod

Set by the operator on every sandbox pod:

- **Non-root**: `runAsNonRoot`, uid/gid 1001, no privilege escalation path
  from the container's own configuration.
- **Per-sandbox ServiceAccount**: identity is per-sandbox, not shared. It is
  what Vault binds policy to, so one sandbox cannot assume another's identity.
- **Per-user state PVC**: `alice`'s agent state (`/home/agent/.claude`, plus a
  per-harness directory) is a different volume from `bob`'s. Two sandboxes
  owned by the same user share it deliberately — that is how credentials
  persist across restarts — but users are separated from each other.
- **TTL**: every sandbox has a deadline and the operator enforces it. An
  abandoned agent stops being an ambient risk.

## Layer 2 — NetworkPolicy (`charts/sandbox-isolation`)

Default-deny, both directions, for anything carrying the
`sandboxes.dev/name` label.

- **Ingress**: only from your ingress gateway's namespace, only to 7681.
  Sandboxes cannot reach each other. Lateral movement between two compromised
  agents is not a thing.
- **Egress**: DNS, the Istio control plane, optionally Vault — and TCP 443.

That last rule is the honest part. **NetworkPolicy has no notion of
hostnames**, only IPs and ports. On its own it says "may make TLS connections
anywhere". Which hostnames are actually reachable is decided by layer 3.

## Layer 3 — Istio ambient: mTLS, waypoint authz, egress registry

This is where "sandbox" stops being a figure of speech.

**PeerAuthentication STRICT** — all in-mesh traffic is mTLS. A pod that is not
enrolled cannot talk to one that is, so an unenrolled workload cannot quietly
reach a sandbox.

**Waypoint + AuthorizationPolicy** — L7 enforcement. The `allow-ttyd` policy
admits 7681; `deny-plaintext-egress` refuses plain HTTP to external hosts, so
an agent cannot downgrade to cleartext to dodge inspection.

**ServiceEntry allow-list** — one entry per hostname you permit
(`values.yaml → egress.groups`). This is the layer that turns TCP 443 into
"api.anthropic.com and github.com, nothing else".

> **This layer only works if istiod runs with
> `meshConfig.outboundTrafficPolicy.mode=REGISTRY_ONLY`.**
>
> That setting is what makes an unlisted host *refused*. In Istio's default
> `ALLOW_ANY`, ServiceEntries merely add routes and block nothing at all, and
> your egress allow-list is decoration. This chart cannot set it for you — it
> is installed with the mesh, not with the workload. Verify:
>
> ```bash
> kubectl -n istio-system get configmap istio -o yaml | grep -A2 outboundTrafficPolicy
> ```
>
> If that prints nothing or `ALLOW_ANY`, your sandboxes have unrestricted
> internet egress no matter what this chart renders.

## Running without a mesh

The operator works fine without Istio, and NetworkPolicy still isolates
sandboxes from each other and from the rest of the cluster. What you lose is
egress control: agents reach anything on 443. That may be acceptable for a
single-user homelab; it is not what this repo means by "sandbox". Decide
deliberately rather than by default.

## What this does NOT protect against

Stated plainly, because a security doc that only lists wins is marketing:

- **Kernel-level escape.** These are ordinary containers, not gVisor,
  Kata, or a VM. A container-escape vulnerability defeats every layer here.
  If your threat model includes hostile *code* rather than a misled agent, add
  a sandboxed runtime.
- **Exfiltration through an allowed host.** If `api.anthropic.com` is
  permitted — and it must be, or nothing works — then data can be sent to it.
  An allow-list constrains *where*, never *what*.
- **Abuse of the agent's own credentials.** A sandbox holding a GitHub token
  can do whatever that token permits. Scope tokens narrowly; the sandbox
  cannot un-grant a permission you gave it.
- **`spec.workingDir` mounts.** Pointing a sandbox at a host path or an
  existing PVC puts real files inside the blast radius. That is the point of
  the feature and also its risk; `hostPath` additionally is not
  fsGroup-chowned, so the directory must already be writable by uid 1001.
- **The ingress edge.** Nothing here authenticates users; the operator assumes
  whatever fronts port 7681 has already established who is talking. Get that
  wrong and the terminal is open to whoever can route to it. See
  [ingress.md](ingress.md).
- **Malicious cluster-adjacent workloads.** Someone with `exec` rights in the
  namespace bypasses all of this. RBAC is yours to get right.

## Verifying it works

Do not trust the manifests; test the boundary from inside a live sandbox:

```bash
POD=$(kubectl -n sandboxes get pod -l sandboxes.dev/name=<name> -o name)

# Allowed host: expect an HTTP response (401 without credentials is a pass —
# it proves you reached Anthropic).
kubectl -n sandboxes exec $POD -- curl -sS -o /dev/null -w '%{http_code}\n' \
  https://api.anthropic.com/v1/messages

# Unlisted host: expect failure. A 200 here means REGISTRY_ONLY is not in
# effect and your allow-list is not enforcing anything.
kubectl -n sandboxes exec $POD -- curl -sS --max-time 10 https://example.com

# Sandbox-to-sandbox: expect a timeout.
kubectl -n sandboxes exec $POD -- curl -sS --max-time 5 http://sandbox-<other>:7681
```

Run these after any change to the mesh, the CNI, or this chart. The failure
mode of a misconfigured allow-list is silence — everything keeps working,
which is exactly what it looks like when nothing is enforced.
