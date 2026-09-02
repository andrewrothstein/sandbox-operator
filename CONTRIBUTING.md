# Contributing

Thanks for looking. This is a small project with a small maintainer surface,
so the most useful contributions are focused ones.

## Before a big change

Open an issue first. The operator has opinions baked in — the pod is built
only at admission, state lives on a per-user PVC, isolation is layered a
particular way — and a PR that quietly changes one of those is a hard review.
A paragraph of intent up front saves you the rewrite.

Small fixes (docs, a clear bug, a missing value) need no ceremony: just send
the PR.

## Development

You need Python 3.11+, `helm`, `kubectl`, and a cluster you can break — [k3d](https://k3d.io)
or [kind](https://kind.sigs.k8s.io) is ideal.

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e .[dev]

ruff check sandbox_operator/                          # lint
helm lint charts/sandbox-operator charts/sandbox-isolation
helm template t charts/sandbox-operator | kubectl apply --dry-run=client -f -
```

To run the operator against a cluster without building an image, point kopf at
your kubeconfig:

```bash
k3d cluster create dev
kubectl create namespace sandboxes
kubectl apply -f crd/sandbox-crd.yaml

export SANDBOX_VAULT_ENABLED=false SANDBOX_NAMESPACE=sandboxes \
       SANDBOX_STORAGE_CLASS=local-path SANDBOX_RUNTIME_IMAGE=busybox:latest
kopf run -m sandbox_operator.main --verbose --namespace sandboxes
```

Then `kubectl apply -f examples/sandbox.yaml` in another shell. With
`busybox` standing in for a runtime image the pods never become Ready — it
serves no terminal — but every sub-resource is created, which is enough to
develop against. This is what CI runs.

Two things that will waste your afternoon otherwise:

- **Run only one operator at a time** against a cluster. Two instances race
  over sub-resources and write conflicting statuses.
- **Deleting a `Sandbox` needs a live operator** — kopf's finalizer makes
  `kubectl delete` hang otherwise. Clear it by hand if you get stuck:
  `kubectl patch sandbox <name> --type=merge -p '{"metadata":{"finalizers":[]}}'`

## What a good PR looks like

- **One concern.** Unrelated cleanups in the same diff make review slower, not
  faster.
- **A stated verification.** Say what you ran and what you saw. "Created a
  Sandbox on k3d, confirmed the pod reached Running and the TTL deleted it"
  tells me more than a green lint. For isolation changes, run the checks in
  [docs/isolation.md](docs/isolation.md#verifying-it-works) — a policy that
  renders is not a policy that enforces.
- **Docs updated with behaviour.** If you change what an operator-user sees,
  the doc that describes it changes in the same PR.
- **Comments that explain why.** The existing code is commented for the reader
  who arrives at 2am wondering why a line exists. Match that: the constraint,
  not the mechanics.

## Security issues

Please do not open a public issue. See [SECURITY.md](SECURITY.md).

## Note on internal references

This repo was extracted from a larger private codebase. If you find a stray
reference to that history — an unexplained identifier, a dangling link, a
mention of tooling not present here — that is a bug and a PR removing it is
welcome.

## License

By contributing, you agree your contributions are licensed under the MIT
License covering this project.
