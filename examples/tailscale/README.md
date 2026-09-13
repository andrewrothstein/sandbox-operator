# Example: Tailscale + Envoy Gateway + OIDC

A complete, working ingress arrangement — the author's own — offered as a
reference rather than a recommendation. Copy the shape, substitute your
details, or use it to sanity-check whatever you build instead.

```
  browser on the tailnet
        │  https://sandboxes.<your-tailnet>.ts.net/sandbox/<name>
        ▼
  Tailscale Ingress          TLS terminates here; no public edge exists
        ▼
  Gateway (Envoy Gateway)
        │  SecurityPolicy: OIDC → your IdP, then allow-list by email claim
        │  HTTPRoute: /sandbox/<name> → sandbox-<name>:7681
        ▼
  Sandbox pod (ttyd :7681)
```

Two independent gates: you must be **on the tailnet** *and* **authenticated to
the IdP**. The terminal is a shell with live credentials in it, so one gate is
not enough.

## What you need

- A [Tailscale](https://tailscale.com) tailnet with the
  [Kubernetes operator](https://tailscale.com/kb/1236/kubernetes-operator)
  installed (it provides `ingressClassName: tailscale`), MagicDNS and HTTPS on
- [Envoy Gateway](https://gateway.envoyproxy.io) in the cluster
- An OIDC provider (Google, Keycloak, Okta, Auth0 — anything standards-compliant)

## Apply it

1. **Register an OAuth client** with your IdP. Its redirect URI must be
   `https://sandboxes.<your-tailnet>.ts.net/oauth2/callback`.

2. **Store the client secret** — never in git:

   ```bash
   kubectl -n sandboxes create secret generic sandbox-oidc-client \
     --from-literal=client-secret='<the secret>'
   ```

3. **Substitute the placeholders.** Every one is marked `REPLACE_ME`:

   ```bash
   grep -rn REPLACE_ME .
   ```

   | Placeholder | Example |
   |---|---|
   | `REPLACE_ME_TAILNET` | `tail1234.ts.net` |
   | `REPLACE_ME_ISSUER` | `https://accounts.google.com` |
   | `REPLACE_ME_CLIENT_ID` | `123-abc.apps.googleusercontent.com` |
   | `REPLACE_ME_JWKS_URI` | `https://www.googleapis.com/oauth2/v3/certs` |
   | `REPLACE_ME_ALLOWED_EMAIL` | `you@example.com` |

4. **Apply**, then visit `https://sandboxes.<your-tailnet>.ts.net/sandbox/<name>`:

   ```bash
   kubectl apply -f gateway.yaml -f security-policy.yaml -f httproute.yaml -f ingress.yaml
   ```

## Notes from running this

- **The proxy Service must be ClusterIP.** On a bare-metal cluster with no
  cloud load-balancer controller, the default `LoadBalancer` Service never
  gets an address and the Gateway sits at `Programmed=False` forever. The
  `EnvoyProxy` resource in `gateway.yaml` forces ClusterIP; the Tailscale
  Ingress fronts that Service directly.

- **One HTTPRoute covers the whole fleet.** A path *prefix* match on
  `/sandbox/` with no per-sandbox route means nothing to reconcile as
  sandboxes come and go on their TTLs. The trade-off is that the route cannot
  name a backend per sandbox — see the comment in `httproute.yaml` for how to
  generate per-sandbox routes if you want strict backend pinning.

- **Log out means logging out of both.** Clearing the gateway's cookie leaves
  the IdP session alive, so the next request silently re-authenticates. If you
  want a real logout, send users to the IdP's end-session endpoint with a
  `post_logout_redirect_uri` back to the gateway's `logoutPath`.

- **Keep the email allow-list.** OIDC proves *who* someone is, not that they
  should have a shell. Without the authorization rule, anyone with an account
  at your IdP — which for Google means anyone at all — passes.
