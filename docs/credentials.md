# Getting credentials into a sandbox

An agent is useless without provider credentials and dangerous with the wrong
ones. There are three supported shapes, in increasing order of effort and
safety.

## The constraint that shapes everything

Agent CLIs authenticate interactively — a browser OAuth flow, a pasted code —
and then cache a token under `$HOME`. That model assumes a laptop. In a
browser terminal it is genuinely awful: the OAuth handoff has to survive a
web terminal that cannot open a browser and often cannot paste. Plan to get
credentials in **before** the user arrives, not by having them log in inside
the sandbox.

The per-user state PVC mounted at `/home/agent/.claude` is what makes that
work: authenticate once, and the token persists across every future sandbox
that user creates.

## Option 1 — a plain Secret (simplest)

Mount a Secret at `/vault/secrets` and have your runtime image install it on
boot (see [runtime-image.md](runtime-image.md)):

```bash
kubectl -n sandboxes create secret generic sandbox-user-creds \
  --from-file=credentials.json=$HOME/.claude/.credentials.json
```

```yaml
spec:
  user: alice
  userCreds:
    secretName: sandbox-user-creds
```

Fine for a single user. Its limits: everyone shares one Secret, rotation is
manual, and a stale copy can silently overwrite a fresher in-pod token —
which is why the entrypoint pattern in the runtime-image doc compares expiry
and keeps whichever token lives longer.

## Option 2 — Vault, brokered per sandbox

With `vaultEnabled: true`, the operator provisions per sandbox:

- a Vault policy scoped to that sandbox's **user** path, and
- a Kubernetes auth role bound to that sandbox's **own ServiceAccount**.

So a sandbox can read `alice`'s secrets only if it is `alice`'s sandbox. Both
are torn down with the sandbox. The identity is the per-sandbox
ServiceAccount, which is why the operator creates one rather than sharing.

```yaml
spec:
  user: alice
  vault:
    enabled: true
    secretPath: secret/data/sandboxes/alice/claude-auth
```

## Option 3 — an external secrets operator

If you already run [External Secrets](https://external-secrets.io) or the
[Vault Secrets Operator](https://developer.hashicorp.com/vault/docs/platform/k8s/vso),
let it sync per-user secrets into the namespace and point `userCreds.secretName`
at the result. The operator needs to know nothing; set `vaultEnabled: false`.

This is the best answer for multi-user installs: rotation, audit, and identity
stay in the system already responsible for them.

## Rules worth keeping

- **Scope tokens to the blast radius.** A sandbox with an org-wide GitHub PAT
  can do anything that PAT can. Prefer short-lived, narrowly scoped
  credentials; the isolation layers cannot un-grant a permission you gave.
- **`spec.user` must come from your authenticated edge**, never from client
  input. It selects the state PVC and the Vault scope — a caller who can set
  it freely can read another user's credentials. Take it from the identity
  header your gateway injects.
- **Never bake credentials into the runtime image.** They end up in registry
  layers, in every sandbox, and in anyone's `docker pull`.
- **Expect the PVC to hold live tokens.** It is the durable copy that matters.
  Back it up like it holds credentials, because it does, and delete it when
  offboarding a user.
