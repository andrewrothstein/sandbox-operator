# Security policy

## Reporting a vulnerability

Please report security issues privately via
[GitHub's private vulnerability reporting](https://github.com/andrewrothstein/sandbox-operator/security/advisories/new)
rather than a public issue.

Include what you need to make the problem reproducible: affected version or
commit, cluster shape (mesh or no mesh, CNI, ingress), and the steps. A working
proof of concept helps enormously.

This is a personal project without a funded security team, so no response-time
guarantee is honest. Expect acknowledgement within a week; I will tell you what
I plan to do and when.

## Scope

The interesting boundary is described in
[docs/isolation.md](docs/isolation.md). Especially in scope:

- A sandbox reaching a network destination **not** in the configured
  ServiceEntry allow-list.
- A sandbox reaching another sandbox, or another namespace, despite the
  NetworkPolicy.
- Privilege escalation out of a sandbox pod using something the operator
  configures (the ServiceAccount, a mount, an env var).
- One user's sandbox reading another user's state PVC or Vault secrets.
- The operator's RBAC permitting more than it needs.
- Credentials leaking into logs, status fields, or CR events.

## Known, documented, not vulnerabilities

These are stated in [docs/isolation.md](docs/isolation.md) as accepted limits.
Reports of them are welcome as *documentation* issues, but they are not
security bugs:

- **No container-escape protection.** These are ordinary containers. If your
  threat model includes hostile code rather than a misled agent, run them on a
  sandboxed runtime (gVisor, Kata) — this project does not provide that.
- **Exfiltration through an allowed host.** An allow-list constrains where
  traffic may go, never what it carries.
- **Unrestricted egress without a mesh**, or with Istio's default
  `outboundTrafficPolicy: ALLOW_ANY`. That is a deployment misconfiguration,
  and the doc explains how to check for it.
- **An unauthenticated ingress you built.** Nothing here authenticates users;
  the operator assumes the edge already did.

If you believe one of these is *more* exploitable than the documentation
implies, that is worth reporting — the concern is the gap between what the
docs promise and what the code delivers.

## Supported versions

Pre-1.0: only the latest release gets fixes.
