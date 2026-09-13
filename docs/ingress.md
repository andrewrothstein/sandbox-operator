# Getting to the terminal

The operator gives each sandbox a `Service` on port 7681 and deliberately
stops there. Turning that into a URL is your decision, because it is the
decision most entangled with your existing infrastructure — your DNS, your
certificates, your identity provider.

This page states what any answer must satisfy, then walks through one
complete worked example.

## Requirements

**WebSockets.** Terminals are WebSocket streams. An ingress that buffers or
downgrades them yields a terminal that connects and then silently freezes.
Whatever you pick, confirm it proxies `Upgrade` correctly and does not impose
a short idle timeout — an agent thinking for two minutes must not be
disconnected.

**Per-sandbox routing.** Each sandbox serves under its own path prefix
(`SANDBOX_TTYD_BASE_PATH`, e.g. `/sandbox/refactor-auth`), so one hostname can
front the whole fleet. Route `/sandbox/<name>` to `sandbox-<name>:7681`. Your
runtime image must serve on that prefix — see
[runtime-image.md](runtime-image.md) — because most terminal servers do not
tolerate having their prefix stripped by the proxy.

**Authentication — and this one is on you.** *Nothing in this repo
authenticates anyone.* The operator assumes that whatever fronts port 7681 has
already established who is talking. A sandbox terminal is a root shell into a
pod with your credentials in it; exposing it unauthenticated is equivalent to
publishing those credentials. The NetworkPolicy restricts ingress to your
gateway's namespace precisely so this is the *only* door.

**Route lifecycle.** Sandboxes come and go on a TTL. Either generate routes
per sandbox (and garbage-collect them) or use one wildcard route that
path-matches — the example below does the latter, which is far less moving
machinery.

## Options, honestly compared

| Approach | Auth | Good for |
|---|---|---|
| `kubectl port-forward` | Kubernetes RBAC | Trying it out; one operator, one sandbox. Cannot share. |
| Gateway API + OIDC | Your IdP | Multi-user. What the example below builds on. |
| Ingress controller | Controller-specific (`oauth2-proxy`, etc.) | Existing nginx/traefik shops. |
| Private network (VPN / Tailscale) | Network identity | Small teams; removes the public edge entirely. |

Starting with `port-forward` is a legitimate way to evaluate this:

```bash
kubectl -n sandboxes port-forward svc/sandbox-refactor-auth 7681:7681
# open http://localhost:7681/sandbox/refactor-auth
```

## Worked example: Tailscale + Gateway API

`examples/tailscale/` contains the author's own arrangement. It is offered as
a proven reference, not a recommendation — the shape it demonstrates
(one gateway, path-routed, authenticated at the edge, never publicly exposed)
transfers to whatever you use instead.

```
  browser on the tailnet
        │  HTTPS, MagicDNS name, cert from Tailscale
        ▼
  Tailscale Ingress  ──▶  Gateway (Envoy Gateway)
                              │  OIDC filter: unauthenticated → IdP
                              │  path match /sandbox/<name>
                              ▼
                         sandbox-<name>:7681
```

Why this combination works well:

- **No public edge at all.** The gateway is reachable only from the tailnet,
  so an unauthenticated request from the open internet cannot arrive. The
  OIDC layer is defence in depth rather than the only wall.
- **Certificates are free and automatic** via Tailscale's HTTPS. Terminals
  need `wss://`, so TLS is not optional; not having to run cert-manager for
  a private tool is a real saving.
- **One route for the whole fleet.** A single path-prefix rule covers every
  sandbox, so nothing needs reconciling as sandboxes are created and expire.
- **Identity comes from the IdP**, and the same identity should be what you
  put in `spec.user` — that is what binds a sandbox's state and credentials to
  a person.

The manifests are in [`examples/tailscale/`](../examples/tailscale/) with the
site-specific bits marked. You will need to substitute your tailnet name, your
issuer URL, and your client credentials.

## Verifying the edge

Whatever you build, check these before trusting it:

```bash
# 1. Unauthenticated request is refused (302 to your IdP, or 401/403).
curl -s -o /dev/null -w '%{http_code}\n' https://<your-host>/sandbox/<name>

# 2. WebSocket upgrade survives the proxy.
curl -s -i -N -H 'Connection: Upgrade' -H 'Upgrade: websocket' \
     -H 'Sec-WebSocket-Version: 13' -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZQ==' \
     https://<your-host>/sandbox/<name>/ws | head -1     # expect 101

# 3. The sandbox is NOT reachable except through the gateway.
kubectl -n sandboxes run probe --rm -it --image=curlimages/curl --restart=Never -- \
  curl -sS --max-time 5 http://sandbox-<name>:7681      # expect a timeout
```

Check 3 is the one people skip. If it succeeds, your NetworkPolicy is not
doing what you think, and the authenticated gateway is a front door beside an
open window.
