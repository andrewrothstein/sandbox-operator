"""Operator configuration from environment variables."""

import os
from dataclasses import dataclass, field


@dataclass
class OperatorConfig:
    sandbox_image: str = field(
        default_factory=lambda: os.environ.get(
            "SANDBOX_RUNTIME_IMAGE", "localhost:30500/sandbox-runtime:latest"
        )
    )
    namespace: str = field(
        default_factory=lambda: os.environ.get("SANDBOX_NAMESPACE", "sandboxes")
    )
    vault_addr: str = field(
        default_factory=lambda: os.environ.get(
            "VAULT_ADDR", "http://vault.vault.svc:8200"
        )
    )
    vault_enabled: bool = field(
        default_factory=lambda: os.environ.get("SANDBOX_VAULT_ENABLED", "true").lower()
        == "true"
    )
    default_ttl: str = field(
        default_factory=lambda: os.environ.get("SANDBOX_DEFAULT_TTL", "8h")
    )
    storage_class: str = field(
        default_factory=lambda: os.environ.get("SANDBOX_STORAGE_CLASS", "local-path")
    )
    state_pvc_size: str = field(
        default_factory=lambda: os.environ.get("SANDBOX_STATE_PVC_SIZE", "2Gi")
    )

    # imagePullSecrets to attach to every sandbox pod the operator creates.
    # Comma-separated list of Secret names in the operator's namespace. Empty
    # by default (dev fleet pulls from unauth in-cluster registry); user-mode
    # installs set this to e.g. "ghcr-pull" so private GHCR images work.
    image_pull_secrets: list = field(
        default_factory=lambda: [
            {"name": n.strip()}
            for n in os.environ.get("SANDBOX_IMAGE_PULL_SECRETS", "").split(",")
            if n.strip()
        ]
    )

    # Knowledge vault (Obsidian-compatible markdown) — shallow-cloned into the
    # sandbox pod via an init container, surfaced to the main container at ~/vault.
    knowledge_vault_enabled: bool = field(
        default_factory=lambda: os.environ.get("SANDBOX_KNOWLEDGE_VAULT_ENABLED", "true").lower()
        == "true"
    )
    # No default repo: a baked-in URL would clone SOMEONE ELSE'S vault into
    # every sandbox pod of every deployment. Empty = feature off regardless
    # of knowledge_vault_enabled (see build_pod), so an operator install
    # only mounts a vault when its owner names one.
    knowledge_vault_repo: str = field(
        default_factory=lambda: os.environ.get("SANDBOX_KNOWLEDGE_VAULT_REPO", "")
    )
    knowledge_vault_ref: str = field(
        default_factory=lambda: os.environ.get("SANDBOX_KNOWLEDGE_VAULT_REF", "main")
    )
    knowledge_vault_subdir: str = field(
        default_factory=lambda: os.environ.get("SANDBOX_KNOWLEDGE_VAULT_SUBDIR", "vault")
    )
    knowledge_vault_clone_path: str = field(
        default_factory=lambda: os.environ.get(
            "SANDBOX_KNOWLEDGE_VAULT_CLONE_PATH", "/home/agent/.vault-repo"
        )
    )
    knowledge_vault_init_image: str = field(
        default_factory=lambda: os.environ.get(
            "SANDBOX_KNOWLEDGE_VAULT_INIT_IMAGE", "alpine/git:latest"
        )
    )
    # Optional cluster-wide default token secret for cloning private vault repos.
    # Per-sandbox override via spec.knowledgeVault.tokenSecret.{name,key}.
    knowledge_vault_token_secret_name: str = field(
        default_factory=lambda: os.environ.get(
            "SANDBOX_KNOWLEDGE_VAULT_TOKEN_SECRET_NAME", ""
        )
    )
    knowledge_vault_token_secret_key: str = field(
        default_factory=lambda: os.environ.get(
            "SANDBOX_KNOWLEDGE_VAULT_TOKEN_SECRET_KEY", "token"
        )
    )
    knowledge_vault_writeback_enabled: bool = field(
        default_factory=lambda: os.environ.get(
            "SANDBOX_KNOWLEDGE_VAULT_WRITEBACK_ENABLED", "true"
        ).lower()
        == "true"
    )
    knowledge_vault_writeback_interval: int = field(
        default_factory=lambda: int(
            os.environ.get("SANDBOX_KNOWLEDGE_VAULT_WRITEBACK_INTERVAL", "60")
        )
    )

    # In-experience observability. Empty = feature off.
    # llm_gateway_url: stable EAIG front door; claude/pi get
    # ANTHROPIC_BASE_URL={url}/anthropic (+ x-sandbox-name header for
    # gateway-side per-sandbox gen_ai attribution).
    # otel_endpoint: OTLP http endpoint for Claude Code's native telemetry.
    llm_gateway_url: str = os.environ.get("SANDBOX_LLM_GATEWAY_URL", "")
    otel_endpoint: str = os.environ.get("SANDBOX_OTEL_ENDPOINT", "")


config = OperatorConfig()
