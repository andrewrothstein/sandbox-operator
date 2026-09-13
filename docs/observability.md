# Observability

Two optional, independent hooks. Both are off unless you set them.

```yaml
observability:
  llmGatewayUrl: ""   # route harness LLM traffic through a gateway
  otelEndpoint: ""    # export agent telemetry over OTLP
```

## `otelEndpoint` — agent telemetry

Set it and the operator injects the OTLP environment every sandbox pod needs,
plus resource attributes identifying the sandbox:

```
sandbox.name=<name>,sandbox.user=<user>,sandbox.harness=<harness>
```

Point it at any OTLP/HTTP collector:

```yaml
observability:
  otelEndpoint: http://otel-collector.observability.svc:4318
```

Claude Code additionally gets `CLAUDE_CODE_ENABLE_TELEMETRY=1`, which emits
token counts, cost estimates, and tool-call events. Other harnesses emit
whatever they natively support; the endpoint is set for all of them.

Two things that will confuse you otherwise:

- **Metrics appear only on real usage.** Instruments are created lazily, so a
  sandbox nobody has typed in exports nothing. An empty dashboard usually
  means an idle agent, not a broken pipeline.
- **Export is periodic** (~60s by default). Do not conclude it is broken
  because a prompt you just ran is not visible yet.

## `llmGatewayUrl` — proxying provider traffic

Set it and claude/pi sandboxes get `ANTHROPIC_BASE_URL=<url>/anthropic`, plus
`ANTHROPIC_CUSTOM_HEADERS: x-sandbox-name: <name>` so the gateway can attribute
requests per sandbox. Useful for central token accounting, rate limiting, or a
uniform view across harnesses ([Envoy AI Gateway](https://aigateway.envoyproxy.io)
is one such gateway).

> **The one that bites.** `ANTHROPIC_BASE_URL` redirects *everything* the CLI
> sends, not just inference: its startup health probe (`/api/hello`) and its
> subscription-OAuth calls go to your gateway too. A gateway that only routes
> `/v1/messages` returns 404 for those, and the CLI fails in ways that look
> like a broken login rather than a routing gap.
>
> If you proxy, proxy the whole API surface — forward unmatched `/api/*` to
> `api.anthropic.com` — or leave this unset.

Also remember the mesh: a gateway URL is another egress destination. If it is
in-cluster and your `ServiceEntry` allow-list does not include it, sandboxes
cannot reach it and every request fails closed.

## What is not here

No dashboards, no collector, no storage. Those are yours: the operator's job
ends at putting correct environment variables in the pod, and every cluster
already has opinions about where telemetry goes.
