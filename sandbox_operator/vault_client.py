"""Vault policy and K8s auth role lifecycle management."""

import logging
import os
import re

import hvac

logger = logging.getLogger(__name__)

# User identities must be safe for Vault paths
_VALID_USER_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,62}$")


def validate_user(user: str) -> str:
    """Validate user string is safe for Vault policy paths."""
    if not _VALID_USER_RE.match(user):
        raise ValueError(
            f"Invalid user identity {user!r}: must match [a-zA-Z0-9][a-zA-Z0-9._-]{{0,62}}"
        )
    if ".." in user:
        raise ValueError(f"Invalid user identity {user!r}: path traversal not allowed")
    return user


class VaultClient:
    def __init__(self, addr: str):
        self.client = hvac.Client(url=addr)
        # In-cluster: authenticate via K8s SA token
        sa_token_path = "/var/run/secrets/kubernetes.io/serviceaccount/token"
        try:
            with open(sa_token_path) as f:
                jwt = f.read()
            self.client.auth.kubernetes.login(role="sandbox-operator", jwt=jwt)
            logger.info("Authenticated to Vault via K8s SA token")
        except FileNotFoundError:
            if os.environ.get("VAULT_TOKEN"):
                logger.info("Using VAULT_TOKEN env var for Vault auth")
            else:
                raise RuntimeError(
                    "No Vault authentication available: no K8s SA token at "
                    f"{sa_token_path} and VAULT_TOKEN env var not set"
                )

    def create_sandbox_policy(self, sandbox_name: str, user: str) -> str:
        """Create a Vault policy scoped to this user's secrets."""
        user = validate_user(user)
        policy_name = f"sandbox-{sandbox_name}"
        policy_hcl = f"""
path "secret/data/sandboxes/{user}/*" {{
  capabilities = ["read"]
}}
path "secret/metadata/sandboxes/{user}/*" {{
  capabilities = ["list"]
}}
"""
        self.client.sys.create_or_update_policy(policy_name, policy_hcl)
        logger.info("Created Vault policy %s for user %s", policy_name, user)
        return policy_name

    def create_k8s_auth_role(
        self,
        sandbox_name: str,
        namespace: str,
        sa_name: str,
        policy: str,
        ttl: str = "8h",
    ):
        """Create Vault K8s auth role bound to sandbox's ServiceAccount."""
        role_name = f"sandbox-{sandbox_name}"
        self.client.auth.kubernetes.create_role(
            name=role_name,
            bound_service_account_names=[sa_name],
            bound_service_account_namespaces=[namespace],
            policies=[policy],
            ttl=ttl,
        )
        logger.info("Created Vault K8s auth role %s in namespace %s", role_name, namespace)

    def delete_sandbox_resources(self, sandbox_name: str):
        """Clean up Vault role and policy for a sandbox."""
        role_name = f"sandbox-{sandbox_name}"
        try:
            self.client.auth.kubernetes.delete_role(role_name)
            logger.info("Deleted Vault role %s", role_name)
        except Exception:
            logger.warning("Failed to delete Vault role %s", role_name, exc_info=True)
        try:
            self.client.sys.delete_policy(role_name)
            logger.info("Deleted Vault policy %s", role_name)
        except Exception:
            logger.warning("Failed to delete Vault policy %s", role_name, exc_info=True)
