# The runtime image contract

Every `Sandbox` pod runs one container, from an image you supply. This repo
does not ship that image: what belongs inside it — which agent CLIs, which
language toolchains, your CA bundle, your dotfiles — is site-specific, and a
one-size image would be wrong for everyone. The contract is small.

Point the operator at yours:

```bash
helm upgrade sandbox-operator charts/sandbox-operator \
  --set image.registry=ghcr.io/you --set image.sandbox=my-sandbox-runtime
```

## What the operator requires

**1. Serve HTTP on port 7681** (interactive mode). The pod declares a
container port named `ttyd`, and both probes GET `/sandbox/<name>` against it.
Anything that answers there works; the reference implementation is
[ttyd](https://github.com/tsl0922/ttyd) wrapping `tmux`. Headless sandboxes
serve nothing — they get an `exec` liveness probe that greps for the harness
process instead, and no readiness probe.

The readiness probe starts at 5s and repeats every 10s, so be listening
quickly — slow setup belongs in an init container or a lazily-started shell,
not before the port opens.

**2. Honour the base path.** `SANDBOX_TTYD_BASE_PATH` (e.g. `/sandbox/demo`)
is where your server must mount, so many sandboxes can share one hostname
behind path routing. With ttyd that is `--base-path "$SANDBOX_TTYD_BASE_PATH"`.

**3. Run as uid 1001 with `/home/agent` writable.** The pod sets
`runAsNonRoot`, uid/gid 1001, `fsGroup` 1001. The per-user state PVC mounts at
`/home/agent/.claude`, so that path must exist and belong to 1001. Anything
written elsewhere in the container dies with the pod.

**4. Terminate when the terminal ends.** `restartPolicy: Never` — the operator
owns the lifecycle. Do not daemonize around a dead session.

## Environment the operator injects

| Variable | Example | Meaning |
|---|---|---|
| `SANDBOX_USER` | `alice` | Owner. Same value selects the state PVC. |
| `SANDBOX_HARNESS` | `claude-code` | Which agent CLI to launch. |
| `SANDBOX_MODE` | `interactive` | `interactive` or `headless`. |
| `SANDBOX_TTYD_BASE_PATH` | `/sandbox/demo` | Path prefix to serve on. |

Conditionally, when those features are configured:

| Variable | When |
|---|---|
| `ANTHROPIC_BASE_URL`, `ANTHROPIC_CUSTOM_HEADERS` | `observability.llmGatewayUrl` set |
| `OTEL_*`, `CLAUDE_CODE_ENABLE_TELEMETRY` | `observability.otelEndpoint` set |
| `SANDBOX_KNOWLEDGE_VAULT_ROOT` | a knowledge-vault repo is configured |

Ignore any you do not use; treat them as advisory.

> A caveat learned the hard way: pointing `ANTHROPIC_BASE_URL` at a gateway
> redirects *all* of the CLI's traffic there, including its startup probe and
> subscription-OAuth calls — not just inference. A gateway that only proxies
> `/v1/messages` will break login in confusing ways. See
> [observability.md](observability.md).

## Volumes

| Mount | Contents |
|---|---|
| `/home/agent/.claude` | Per-user PVC. Credentials, history — survives the pod. |
| `/vault/secrets` | Credentials Secret, when configured. See [credentials.md](credentials.md). |
| `/home/agent/.vault-repo` | Knowledge-vault clone, when configured. |

Per-harness state PVCs (`sandbox-codex-state-<user>`, etc.) mount at that
harness's own config directory when the harness is selected.

## A minimal example

```dockerfile
FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl git tmux locales \
 && sed -i 's/# en_US.UTF-8/en_US.UTF-8/' /etc/locale.gen && locale-gen \
 && rm -rf /var/lib/apt/lists/*

# ttyd serves the terminal over HTTP/WebSocket.
ARG TTYD=1.7.7
RUN curl -fsSL -o /usr/local/bin/ttyd \
      "https://github.com/tsl0922/ttyd/releases/download/${TTYD}/ttyd.x86_64" \
 && chmod +x /usr/local/bin/ttyd

# ...install your agent CLI(s) here...

RUN useradd -u 1001 -m -s /bin/bash agent \
 && mkdir -p /home/agent/.claude && chown -R 1001:1001 /home/agent
USER 1001
ENV LANG=en_US.UTF-8 COLORTERM=truecolor
WORKDIR /home/agent

COPY entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
```

```bash
#!/usr/bin/env bash
# entrypoint.sh
set -euo pipefail

# Credentials, if a Secret was mounted.
if [ -f /vault/secrets/credentials.json ]; then
  install -m 0600 /vault/secrets/credentials.json /home/agent/.claude/.credentials.json
fi

case "${SANDBOX_HARNESS:-claude-code}" in
  claude-code) CMD=claude ;;
  codex)       CMD=codex ;;
  opencode)    CMD=opencode ;;
  *)           CMD=bash ;;
esac

exec ttyd --writable --port 7681 \
  --base-path "${SANDBOX_TTYD_BASE_PATH:-/}" \
  tmux new-session -A -s main "$CMD"
```

## Things worth getting right

- **UTF-8.** Agent CLIs draw box-art TUIs. Without a UTF-8 locale you get
  mojibake, and it will look like a terminal-emulator bug rather than a
  missing `locale-gen`.
- **`tmux new-session -A -s main`** attaches to an existing session instead of
  starting a second one, so a browser reconnect resumes rather than replaces.
- **Copy/paste.** Browser terminals need OSC 52 to put text on the user's
  clipboard (`set -g set-clipboard on` in tmux). Without it, copy silently
  does nothing — a genuinely maddening failure to debug from the user's side.
- **Credentials belong on the PVC.** Write them under `/home/agent/.claude`
  so a restart does not force the user to log in again. If you also seed from
  a Secret, keep whichever token expires later — a stale Secret that
  overwrites a fresh in-pod login is a nasty, silent regression.
