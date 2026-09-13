"""Kubernetes resource builders for Sandbox sub-resources."""

from __future__ import annotations

from typing import Any

from .config import OperatorConfig

# Vault Agent template to render Claude credentials.json
VAULT_CREDENTIAL_TEMPLATE = """\
{{- with secret "%s" -}}
{{ .Data.data | toJSON }}
{{- end -}}
"""


def labels(sandbox_name: str, user: str, mode: str) -> dict[str, str]:
    return {
        "app.kubernetes.io/managed-by": "sandbox-operator",
        "sandboxes.dev/name": sandbox_name,
        "sandboxes.dev/user": user,
        "sandboxes.dev/mode": mode,
    }


def build_pvc(user: str, namespace: str, cfg: OperatorConfig) -> dict[str, Any]:
    """Per-user PVC for ~/.claude state. Persists across sandboxes."""
    return {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": {
            "name": f"sandbox-state-{user}",
            "namespace": namespace,
            "labels": {
                "app.kubernetes.io/managed-by": "sandbox-operator",
                "app.kubernetes.io/component": "state",
                "sandboxes.dev/user": user,
            },
        },
        "spec": {
            "accessModes": ["ReadWriteOnce"],
            "storageClassName": cfg.storage_class,
            "resources": {"requests": {"storage": cfg.state_pvc_size}},
        },
    }


def build_pi_pvc(user: str, namespace: str, cfg: OperatorConfig) -> dict[str, Any]:
    """Per-user PVC for ~/.pi state. Pi stores OAuth tokens
    at ~/.pi/agent/auth.json; this PVC makes them survive across sandboxes
    so a user runs `pi /login` once and stays authenticated.
    """
    return {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": {
            "name": f"sandbox-pi-state-{user}",
            "namespace": namespace,
            "labels": {
                "app.kubernetes.io/managed-by": "sandbox-operator",
                "app.kubernetes.io/component": "pi-state",
                "sandboxes.dev/user": user,
            },
        },
        "spec": {
            "accessModes": ["ReadWriteOnce"],
            "storageClassName": cfg.storage_class,
            "resources": {"requests": {"storage": cfg.state_pvc_size}},
        },
    }



def build_agy_pvc(user: str, namespace: str, cfg: OperatorConfig) -> dict[str, Any]:
    """Per-user PVC for ~/.gemini state. Antigravity stores
    its OAuth token at ~/.gemini/antigravity-cli/antigravity-oauth-token
    plus brain/knowledge/conversation state under the same root; this PVC
    makes one sign-in survive across sandboxes (mirror of pi-state).
    """
    return {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": {
            "name": f"sandbox-agy-state-{user}",
            "namespace": namespace,
            "labels": {
                "app.kubernetes.io/managed-by": "sandbox-operator",
                "app.kubernetes.io/component": "agy-state",
                "sandboxes.dev/user": user,
            },
        },
        "spec": {
            "accessModes": ["ReadWriteOnce"],
            "storageClassName": cfg.storage_class,
            "resources": {"requests": {"storage": cfg.state_pvc_size}},
        },
    }

def build_codex_pvc(user: str, namespace: str, cfg: OperatorConfig) -> dict[str, Any]:
    """Per-user PVC for ~/.codex state. Codex stores its
    ChatGPT OAuth at ~/.codex/auth.json; one `codex login` survives across
    sandboxes (mirror of pi/agy state)."""
    return {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": {
            "name": f"sandbox-codex-state-{user}",
            "namespace": namespace,
            "labels": {
                "app.kubernetes.io/managed-by": "sandbox-operator",
                "app.kubernetes.io/component": "codex-state",
                "sandboxes.dev/user": user,
            },
        },
        "spec": {
            "accessModes": ["ReadWriteOnce"],
            "storageClassName": cfg.storage_class,
            "resources": {"requests": {"storage": cfg.state_pvc_size}},
        },
    }


def build_opencode_pvc(user: str, namespace: str, cfg: OperatorConfig) -> dict[str, Any]:
    """Per-user PVC for ~/.local/share/opencode. Auth from
    `opencode auth login` survives across sandboxes (mirror of codex-state)."""
    return {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": {
            "name": f"sandbox-opencode-state-{user}",
            "namespace": namespace,
            "labels": {
                "app.kubernetes.io/managed-by": "sandbox-operator",
                "app.kubernetes.io/component": "opencode-state",
                "sandboxes.dev/user": user,
            },
        },
        "spec": {
            "accessModes": ["ReadWriteOnce"],
            "storageClassName": cfg.storage_class,
            "resources": {"requests": {"storage": cfg.state_pvc_size}},
        },
    }


def build_service_account(
    sandbox_name: str, namespace: str, user: str, mode: str
) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "ServiceAccount",
        "metadata": {
            "name": f"sandbox-{sandbox_name}",
            "namespace": namespace,
            "labels": labels(sandbox_name, user, mode),
        },
    }


def build_pod(sandbox: dict, namespace: str, cfg: OperatorConfig) -> dict[str, Any]:
    """Build the sandbox Pod spec."""
    spec = sandbox["spec"]
    name = sandbox["metadata"]["name"]
    user = spec["user"]
    mode = spec.get("mode", "interactive")
    res = spec.get("resources", {})

    vault_cfg = spec.get("vault", {})
    vault_enabled = vault_cfg.get("enabled", cfg.vault_enabled)
    vault_secret_path = vault_cfg.get(
        "secretPath", f"secret/data/sandboxes/{user}/claude-auth"
    )

    harness = spec.get("harness", "claude-code")

    env = [
        {"name": "SANDBOX_MODE", "value": mode},
        {"name": "SANDBOX_USER", "value": user},
        # Harness selection. Defaults to claude-code; pi
        # is supported in interactive mode (headless-pi is untested
        # as of 2026-05-03 — entrypoint will refuse and exit).
        {"name": "SANDBOX_HARNESS", "value": harness},
    ]

    # In-experience observability.
    if cfg.otel_endpoint:
        env.extend([
            {"name": "CLAUDE_CODE_ENABLE_TELEMETRY", "value": "1"},
            {"name": "OTEL_METRICS_EXPORTER", "value": "otlp"},
            {"name": "OTEL_LOGS_EXPORTER", "value": "otlp"},
            {"name": "OTEL_EXPORTER_OTLP_PROTOCOL", "value": "http/protobuf"},
            {"name": "OTEL_EXPORTER_OTLP_ENDPOINT", "value": cfg.otel_endpoint},
            {
                "name": "OTEL_RESOURCE_ATTRIBUTES",
                "value": f"sandbox.name={name},sandbox.user={user},sandbox.harness={harness}",
            },
        ])
    if cfg.llm_gateway_url and harness in ("claude-code", "pi"):
        # Route Anthropic traffic through the EAIG front door; the header
        # becomes the sandbox.name attribute on gateway gen_ai metrics.
        # codex/opencode/agy stay DIRECT until an OpenAI/Gemini backend is
        # proven on the gateway (tracked in notes).
        env.extend([
            {"name": "ANTHROPIC_BASE_URL", "value": f"{cfg.llm_gateway_url}/anthropic"},
            {"name": "ANTHROPIC_CUSTOM_HEADERS", "value": f"x-sandbox-name: {name}"},
        ])

    # ttyd base path. Interactive sandboxes are reached through
    # the gateway at /sandbox/<name>, so ttyd must serve under that prefix —
    # otherwise its absolute asset/token/ws requests (/token, /ws) escape the
    # HTTPRoute and 404. The entrypoint passes this to `ttyd --base-path`, and
    # the per-sandbox HTTPRoute forwards the prefix unchanged (no URL rewrite).
    base_path = f"/sandbox/{name}" if mode == "interactive" else "/"
    if mode == "interactive":
        env.append({"name": "SANDBOX_TTYD_BASE_PATH", "value": base_path})

    # Pi credential injection. Only consulted when
    # spec.harness=pi. Mirrors the GitHub-PAT pattern below: secretKeyRef
    # → env var. Pi reads whatever provider env it's configured for.
    if harness == "pi":
        pi_cfg = spec.get("pi", {}) or {}
        pi_token = pi_cfg.get("tokenSecret", {}) or {}
        pi_secret_name = pi_token.get("name")
        if pi_secret_name:
            env.append(
                {
                    "name": pi_token.get("envVar", "ANTHROPIC_API_KEY"),
                    "valueFrom": {
                        "secretKeyRef": {
                            "name": pi_secret_name,
                            "key": pi_token.get("key", "api-key"),
                        }
                    },
                }
            )

    # Working directory config
    working_dir = spec.get("workingDir", {})
    wd_modes = [
        m for m in ("gitRepo", "existingPVC", "hostPath") if working_dir.get(m)
    ]
    if len(wd_modes) > 1:
        raise ValueError(
            f"workingDir modes are mutually exclusive; got {wd_modes}"
        )
    if working_dir.get("gitRepo"):
        env.append({"name": "GIT_REPO", "value": working_dir["gitRepo"]})
        env.append({"name": "GIT_REF", "value": working_dir.get("gitRef", "main")})
    elif working_dir.get("hostPath"):
        env.append({"name": "SANDBOX_WORKDIR_MODE", "value": "hostPath"})

    # Headless config
    headless = spec.get("headless", {})
    if mode == "headless":
        if headless.get("prompt"):
            env.append({"name": "HEADLESS_PROMPT", "value": headless["prompt"]})
        if headless.get("epic"):
            env.append({"name": "AGENT_EPIC", "value": headless["epic"]})
        if headless.get("role"):
            env.append({"name": "HEADLESS_ROLE", "value": headless["role"]})

    # Opt-in MCP servers (Tier 2/3). Each block sets a SANDBOX_MCP_<NAME>_ENABLED
    # env var that the entrypoint uses to gate `claude mcp add`.
    mcp_spec = spec.get("mcp", {}) or {}

    def _mcp_flag(key: str, var: str) -> None:
        cfg = mcp_spec.get(key, {})
        if cfg.get("enabled"):
            env.append({"name": var, "value": "true"})

    _mcp_flag("github", "SANDBOX_MCP_GITHUB_ENABLED")
    _mcp_flag("fetch", "SANDBOX_MCP_FETCH_ENABLED")
    _mcp_flag("memory", "SANDBOX_MCP_MEMORY_ENABLED")
    _mcp_flag("worktree", "SANDBOX_MCP_WORKTREE_ENABLED")
    _mcp_flag("kubernetes", "SANDBOX_MCP_KUBERNETES_ENABLED")
    # k8s-mcp pins the cluster target via these vars (so a misconfigured
    # kubeconfig current-context can't redirect calls). Defaults: in-cluster
    # auth from the pod's ServiceAccount when both are unset.
    k8s_cfg = mcp_spec.get("kubernetes", {}) or {}
    if k8s_cfg.get("kubeconfig"):
        env.append({"name": "SANDBOX_K8S_KUBECONFIG", "value": k8s_cfg["kubeconfig"]})
    if k8s_cfg.get("context"):
        env.append({"name": "SANDBOX_K8S_CONTEXT", "value": k8s_cfg["context"]})

    _mcp_flag("gitnexus", "SANDBOX_MCP_GITNEXUS_ENABLED")

    browser_cfg = mcp_spec.get("browser", {})
    if browser_cfg.get("enabled"):
        env.append({"name": "SANDBOX_MCP_BROWSER_ENABLED", "value": "true"})
        env.append(
            {
                "name": "SANDBOX_MCP_BROWSER_KIND",
                "value": browser_cfg.get("kind", "playwright"),
            }
        )

    sqlite_cfg = mcp_spec.get("sqlite", {})
    if sqlite_cfg.get("enabled"):
        env.append({"name": "SANDBOX_MCP_SQLITE_ENABLED", "value": "true"})
        env.append(
            {
                "name": "SANDBOX_MCP_SQLITE_PATH",
                "value": sqlite_cfg.get(
                    "path", "/home/agent/.claude/scratch.sqlite"
                ),
            }
        )

    worktree_cfg = mcp_spec.get("worktree", {})
    if worktree_cfg.get("enabled") and worktree_cfg.get("root"):
        env.append(
            {"name": "SANDBOX_MCP_WORKTREE_ROOT", "value": worktree_cfg["root"]}
        )

    # GitHub MCP needs a PAT — reuse the vault tokenSecret if not overridden.
    github_cfg = mcp_spec.get("github", {})
    if github_cfg.get("enabled"):
        gh_token = github_cfg.get("tokenSecret", {})
        gh_name = gh_token.get("name") or cfg.knowledge_vault_token_secret_name
        gh_key = gh_token.get("key") or cfg.knowledge_vault_token_secret_key
        if gh_name:
            env.append(
                {
                    "name": "GITHUB_PERSONAL_ACCESS_TOKEN",
                    "valueFrom": {
                        "secretKeyRef": {"name": gh_name, "key": gh_key}
                    },
                }
            )

    volume_mounts = [
        {"name": "claude-state", "mountPath": "/home/agent/.claude"},
    ]

    volumes = [
        {
            "name": "claude-state",
            "persistentVolumeClaim": {"claimName": f"sandbox-state-{user}"},
        },
    ]

    # Pi-state PVC. Persists ~/.pi/agent/auth.json (OAuth
    # tokens from `pi /login`) and any other Pi runtime state so the user
    # stays logged in across sandboxes. Mounted only when harness=pi —
    # claude-only sandboxes don't need it.
    if harness == "pi":
        volume_mounts.append(
            {"name": "pi-state", "mountPath": "/home/agent/.pi"}
        )
        volumes.append(
            {
                "name": "pi-state",
                "persistentVolumeClaim": {"claimName": f"sandbox-pi-state-{user}"},
            }
        )

    # opencode-state PVC. Mirror of codex-state.
    if harness == "opencode":
        volume_mounts.append(
            {"name": "opencode-state", "mountPath": "/home/agent/.local/share/opencode"}
        )
        volumes.append(
            {
                "name": "opencode-state",
                "persistentVolumeClaim": {"claimName": f"sandbox-opencode-state-{user}"},
            }
        )

    # Codex-state PVC. Persists ~/.codex (ChatGPT OAuth)
    # across sandboxes. Mirror of pi-state.
    if harness == "codex":
        volume_mounts.append(
            {"name": "codex-state", "mountPath": "/home/agent/.codex"}
        )
        volumes.append(
            {
                "name": "codex-state",
                "persistentVolumeClaim": {"claimName": f"sandbox-codex-state-{user}"},
            }
        )

    # Antigravity-state PVC. Persists ~/.gemini (OAuth token,
    # brain/knowledge/conversations) across sandboxes. Mirror of pi-state.
    if harness == "antigravity":
        volume_mounts.append(
            {"name": "agy-state", "mountPath": "/home/agent/.gemini"}
        )
        volumes.append(
            {
                "name": "agy-state",
                "persistentVolumeClaim": {"claimName": f"sandbox-agy-state-{user}"},
            }
        )

    # workingDir.tokenSecret — mount a git PAT so the agent's clone+push of
    # spec.workingDir.gitRepo authenticates. Entrypoint reads the file and
    # writes ~/.netrc (0600) before `git clone`. Mirrors the knowledgeVault
    # tokenSecret pattern, except mounted on the main container (the workdir
    # clone happens in the entrypoint, not an init container).
    wd_token_secret = working_dir.get("tokenSecret", {}) or {}
    wd_token_name = wd_token_secret.get("name")
    wd_token_key = wd_token_secret.get("key", "token")
    if wd_token_name:
        volumes.append(
            {
                "name": "workdir-git-token",
                "secret": {"secretName": wd_token_name, "defaultMode": 0o400},
            }
        )
        volume_mounts.append(
            {
                "name": "workdir-git-token",
                "mountPath": "/run/secrets/workdir-git",
                "readOnly": True,
            }
        )
        env.append(
            {"name": "SANDBOX_WORKDIR_GIT_TOKEN_FILE", "value": f"/run/secrets/workdir-git/{wd_token_key}"}
        )

    init_containers: list[dict[str, Any]] = []

    # Knowledge vault: shallow-clone the markdown vault into a shared
    # emptyDir; main container symlinks ~/vault to the cloned subdir.
    kv_cfg = spec.get("knowledgeVault", {})
    kv_enabled = kv_cfg.get("enabled", cfg.knowledge_vault_enabled)
    kv_repo = kv_cfg.get("repo", cfg.knowledge_vault_repo)
    # No repo configured -> no vault, whatever `enabled` says. Keeps a
    # fresh install from needing a per-Sandbox opt-out.
    if kv_enabled and kv_repo:
        kv_ref = kv_cfg.get("ref", cfg.knowledge_vault_ref)
        kv_subdir = kv_cfg.get("subdir", cfg.knowledge_vault_subdir)
        kv_path = cfg.knowledge_vault_clone_path
        volumes.append({"name": "knowledge-vault", "emptyDir": {}})
        volume_mounts.append({"name": "knowledge-vault", "mountPath": kv_path})
        env.extend(
            [
                {"name": "SANDBOX_KNOWLEDGE_VAULT_REPO_PATH", "value": kv_path},
                {"name": "SANDBOX_KNOWLEDGE_VAULT_ROOT", "value": f"{kv_path}/{kv_subdir}"},
            ]
        )

        # Token wiring (private-repo support). Per-sandbox override beats
        # cluster-wide default; both are optional. When set, the secret is
        # mounted at /run/secrets/git/token and a .netrc steers the clone.
        kv_token_secret = kv_cfg.get("tokenSecret", {})
        kv_token_name = kv_token_secret.get("name") or cfg.knowledge_vault_token_secret_name
        kv_token_key = kv_token_secret.get("key") or cfg.knowledge_vault_token_secret_key

        init_volume_mounts = [{"name": "knowledge-vault", "mountPath": kv_path}]
        clone_setup = ""
        if kv_token_name:
            volumes.append(
                {
                    "name": "knowledge-vault-token",
                    "secret": {"secretName": kv_token_name, "defaultMode": 0o400},
                }
            )
            init_volume_mounts.append(
                {
                    "name": "knowledge-vault-token",
                    "mountPath": "/run/secrets/git",
                    "readOnly": True,
                }
            )
            # Match the host of the configured repo URL so the helper applies.
            # For github.com (the overwhelmingly common case) the literal works.
            clone_setup = (
                f'TOKEN=$(cat /run/secrets/git/{kv_token_key}) '
                f'&& printf "machine github.com login oauth2 password %s\\n" "$TOKEN" > /root/.netrc '
                f'&& chmod 600 /root/.netrc && '
            )

        init_containers.append(
            {
                "name": "knowledge-vault-clone",
                "image": cfg.knowledge_vault_init_image,
                "securityContext": {
                    # alpine/git's chown needs root; main container is still non-root.
                    "runAsNonRoot": False,
                    "runAsUser": 0,
                },
                "command": ["sh", "-euc"],
                "args": [
                    clone_setup
                    + " && ".join(
                        [
                            (
                                f"git clone --depth 1 --branch {kv_ref} "
                                f"--filter=blob:none --sparse {kv_repo} {kv_path}"
                            ),
                            f"git -C {kv_path} sparse-checkout set {kv_subdir}",
                            f"chown -R 1001:1001 {kv_path}",
                        ]
                    )
                ],
                "volumeMounts": init_volume_mounts,
                "resources": {
                    "requests": {"cpu": "50m", "memory": "64Mi"},
                    "limits": {"cpu": "500m", "memory": "256Mi"},
                },
            }
        )

        # Optional: write-back sidecar. Kubernetes-native sidecar pattern
        # (init container with restartPolicy=Always) so the kubelet runs it
        # concurrently with the main container and SIGTERMs it when the
        # main container exits, allowing one final push on the way out.
        wb_cfg = kv_cfg.get("writeBack", {})
        wb_enabled = wb_cfg.get("enabled", cfg.knowledge_vault_writeback_enabled)
        if wb_enabled and kv_token_name:
            wb_interval = int(
                wb_cfg.get("intervalSeconds", cfg.knowledge_vault_writeback_interval)
            )
            wb_script = (
                'export HOME=/tmp; '
                f'TOKEN=$(cat /run/secrets/git/{kv_token_key}); '
                'printf "machine github.com login oauth2 password %s\\n" "$TOKEN" > /tmp/.netrc; '
                'chmod 600 /tmp/.netrc; '
                f'cd {kv_path}; '
                f'git config user.name "sandbox-vault-sync"; '
                f'git config user.email "vault-sync@sandbox.local"; '
                'trap "echo received SIGTERM; exit 0" TERM; '
                'while :; do '
                '  if [ -n "$(git status --porcelain)" ]; then '
                '    git add -A; '
                '    git commit -m "vault: auto-sync from $POD_NAME @ $(date -u +%Y-%m-%dT%H:%M:%SZ)" || true; '
                '    if ! git push 2>&1; then '
                '      git fetch origin "$KV_REF" && git rebase "origin/$KV_REF" || git rebase --abort; '
                '      git push 2>&1 || echo "[vault-sync] push failed, will retry"; '
                '    fi; '
                '  fi; '
                f'  sleep {wb_interval} & wait $!; '
                'done'
            )
            init_containers.append(
                {
                    "name": "knowledge-vault-sync",
                    "image": cfg.knowledge_vault_init_image,
                    "restartPolicy": "Always",  # k8s sidecar pattern (>= 1.28 / GA in 1.29)
                    "securityContext": {
                        # Run as the same UID that owns the cloned files so git
                        # doesn't trip its safe.directory check; /tmp serves as HOME.
                        "runAsNonRoot": True,
                        "runAsUser": 1001,
                        "runAsGroup": 1001,
                    },
                    "command": ["sh", "-euc"],
                    "args": [wb_script],
                    "env": [
                        {"name": "KV_REF", "value": kv_ref},
                        {
                            "name": "POD_NAME",
                            "valueFrom": {
                                "fieldRef": {"fieldPath": "metadata.name"}
                            },
                        },
                    ],
                    "volumeMounts": [
                        {"name": "knowledge-vault", "mountPath": kv_path},
                        {
                            "name": "knowledge-vault-token",
                            "mountPath": "/run/secrets/git",
                            "readOnly": True,
                        },
                    ],
                    "resources": {
                        "requests": {"cpu": "10m", "memory": "32Mi"},
                        "limits": {"cpu": "200m", "memory": "128Mi"},
                    },
                }
            )

    # Working directory volume — one of: existingPVC, hostPath, or none (clone-into-emptyDir)
    if working_dir.get("existingPVC"):
        volume_mounts.append(
            {"name": "workdir", "mountPath": "/home/agent/work"},
        )
        volumes.append(
            {
                "name": "workdir",
                "persistentVolumeClaim": {
                    "claimName": working_dir["existingPVC"]
                },
            },
        )
    elif working_dir.get("hostPath"):
        volume_mounts.append(
            {"name": "workdir", "mountPath": "/home/agent/work"},
        )
        volumes.append(
            {
                "name": "workdir",
                "hostPath": {
                    "path": working_dir["hostPath"],
                    "type": "DirectoryOrCreate",
                },
            },
        )

    annotations = {}
    # User-persona cred handoff. A single Secret with
    # `credentials.json` + optional `pi-auth.json` keys, mounted at
    # /vault/secrets. Takes precedence over both vault and the per-harness
    # fallback branches below, and is harness-agnostic so a claude-code
    # sandbox can also see pi-auth.json (for ad-hoc `pi` invocations).
    user_creds = spec.get("userCreds", {}) or {}
    user_creds_secret = user_creds.get("secretName")

    # claude-code OAuth credentials path. Only relevant when
    # harness=claude-code; Pi takes credentials via spec.pi.tokenSecret env
    # and doesn't need /vault/secrets/credentials.json.
    if user_creds_secret:
        volumes.append(
            {
                "name": "user-creds",
                "secret": {
                    "secretName": user_creds_secret,
                    "defaultMode": 0o400,
                },
            },
        )
        volume_mounts.append(
            {
                "name": "user-creds",
                "mountPath": "/vault/secrets",
                "readOnly": True,
            },
        )
    elif harness == "claude-code":
        if vault_enabled:
            annotations.update(
                {
                    "vault.hashicorp.com/agent-inject": "true",
                    "vault.hashicorp.com/agent-inject-secret-credentials.json": vault_secret_path,
                    "vault.hashicorp.com/agent-inject-template-credentials.json": VAULT_CREDENTIAL_TEMPLATE
                    % vault_secret_path,
                    "vault.hashicorp.com/role": vault_cfg.get("role", f"sandbox-{name}"),
                }
            )
        else:
            # Without Vault: mount credentials from K8s secret
            secret_name = vault_cfg.get("secretName", "sandbox-claude-auth")
            volumes.append(
                {
                    "name": "claude-auth",
                    "secret": {
                        "secretName": secret_name,
                        "items": [{"key": "credentials.json", "path": "credentials.json"}],
                    },
                },
            )
            volume_mounts.append(
                {
                    "name": "claude-auth",
                    "mountPath": "/vault/secrets",
                    "readOnly": True,
                },
            )

    # Pi OAuth credential injection. When harness=pi and
    # spec.pi.oauthSecret.name is set, mount the Secret as a file at
    # /vault/secrets/pi-auth.json. The entrypoint copies it to
    # ~/.pi/agent/auth.json with 0600 on sandbox start. Useful for
    # headless agents that can't run interactive `pi /login`.
    # Skipped when userCreds.secretName is set — that path mounts both
    # creds via a single Secret with multiple keys.
    if harness == "pi" and not user_creds_secret:
        pi_cfg = spec.get("pi", {}) or {}
        pi_oauth = pi_cfg.get("oauthSecret", {}) or {}
        pi_oauth_name = pi_oauth.get("name")
        if pi_oauth_name:
            volumes.append(
                {
                    "name": "pi-auth",
                    "secret": {
                        "secretName": pi_oauth_name,
                        "items": [
                            {
                                "key": pi_oauth.get("key", "auth.json"),
                                "path": "pi-auth.json",
                            }
                        ],
                        "defaultMode": 0o400,
                    },
                },
            )
            volume_mounts.append(
                {
                    "name": "pi-auth",
                    "mountPath": "/vault/secrets",
                    "readOnly": True,
                },
            )

    container = {
        "name": "sandbox",
        "image": cfg.sandbox_image,
        "env": env,
        "volumeMounts": volume_mounts,
        "resources": {
            "requests": {"cpu": "250m", "memory": "512Mi"},
            "limits": {
                "cpu": res.get("cpu", "1"),
                "memory": res.get("memory", "1Gi"),
            },
        },
    }

    if mode == "interactive":
        container["ports"] = [{"containerPort": 7681, "name": "ttyd"}]
        container["livenessProbe"] = {
            "httpGet": {"path": base_path, "port": 7681},
            "initialDelaySeconds": 10,
            "periodSeconds": 30,
            "failureThreshold": 3,
        }
        container["readinessProbe"] = {
            "httpGet": {"path": base_path, "port": 7681},
            "initialDelaySeconds": 5,
            "periodSeconds": 10,
        }
    else:
        # Headless: check that the harness process is alive. Was hardcoded
        # to "claude", which would have had the kubelet kill healthy
        # headless pi/antigravity pods.
        proc_pattern = {
            "claude-code": "claude",
            "pi": "pi",
            "antigravity": "agy|antigravity",
        }.get(harness, "claude")
        container["livenessProbe"] = {
            "exec": {"command": ["pgrep", "-f", proc_pattern]},
            "initialDelaySeconds": 30,
            "periodSeconds": 30,
            "failureThreshold": 3,
        }

    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": f"sandbox-{name}",
            "namespace": namespace,
            "labels": labels(name, user, mode),
            "annotations": annotations,
        },
        "spec": {
            "serviceAccountName": f"sandbox-{name}",
            # fsGroup makes the kubelet chown attached volumes to the agent UID
            # so the non-root container can write to claude-state PVC and the
            # vault emptyDir. Matches the Dockerfile's `useradd -u 1001 agent`.
            "securityContext": {
                "runAsNonRoot": True,
                "runAsUser": 1001,
                "runAsGroup": 1001,
                "fsGroup": 1001,
            },
            "initContainers": init_containers,
            "containers": [container],
            "volumes": volumes,
            "restartPolicy": "Never",
            **(
                {"imagePullSecrets": cfg.image_pull_secrets}
                if cfg.image_pull_secrets
                else {}
            ),
        },
    }


def build_service(
    sandbox_name: str, namespace: str, user: str, mode: str
) -> dict[str, Any]:
    """ClusterIP service for ttyd (interactive mode only)."""
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {
            "name": f"sandbox-{sandbox_name}",
            "namespace": namespace,
            "labels": labels(sandbox_name, user, mode),
        },
        "spec": {
            "type": "ClusterIP",
            "selector": {"sandboxes.dev/name": sandbox_name},
            "ports": [
                {
                    "name": "ttyd",
                    "port": 7681,
                    "targetPort": 7681,
                    "protocol": "TCP",
                }
            ],
        },
    }


def build_httproute(
    sandbox_name: str, namespace: str, user: str
) -> dict[str, Any]:
    """HTTPRoute for Envoy Gateway — routes to this sandbox's ttyd service.

    No URL rewrite: ttyd serves under --base-path /sandbox/<name> (set via
    SANDBOX_TTYD_BASE_PATH, , so the gateway forwards the prefix
    unchanged. Rewriting to "/" would break ttyd's absolute /token and /ws
    requests, which must stay under the sandbox prefix to match this route.
    """
    return {
        "apiVersion": "gateway.networking.k8s.io/v1",
        "kind": "HTTPRoute",
        "metadata": {
            "name": f"sandbox-{sandbox_name}",
            "namespace": namespace,
            "labels": labels(sandbox_name, user, "interactive"),
        },
        "spec": {
            "parentRefs": [{"name": "sandbox-gateway", "namespace": namespace}],
            "rules": [
                {
                    "matches": [
                        {
                            "path": {
                                "type": "PathPrefix",
                                "value": f"/sandbox/{sandbox_name}",
                            }
                        }
                    ],
                    "backendRefs": [
                        {"name": f"sandbox-{sandbox_name}", "port": 7681}
                    ],
                }
            ],
        },
    }
