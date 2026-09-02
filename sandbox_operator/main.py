"""CCSM Operator — kopf handlers for Sandbox CRDs."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import kopf
import kubernetes
from kubernetes.client.exceptions import ApiException

from .config import config
from .resources import (
    build_agy_pvc,
    build_codex_pvc,
    build_httproute,
    build_opencode_pvc,
    build_pi_pvc,
    build_pod,
    build_pvc,
    build_service,
    build_service_account,
)
from .vault_client import VaultClient

logger = logging.getLogger(__name__)

# Lazy-init Vault client (only when vault is enabled)
_vault: VaultClient | None = None


def vault_client() -> VaultClient:
    global _vault
    if _vault is None:
        _vault = VaultClient(config.vault_addr)
    return _vault


def parse_ttl(ttl_str: str) -> timedelta:
    """Parse Go-style duration string (e.g. '8h', '30m', '1h30m')."""
    total = timedelta()
    num = ""
    for ch in ttl_str:
        if ch.isdigit():
            num += ch
        elif ch == "h" and num:
            total += timedelta(hours=int(num))
            num = ""
        elif ch == "m" and num:
            total += timedelta(minutes=int(num))
            num = ""
        elif ch == "s" and num:
            total += timedelta(seconds=int(num))
            num = ""
    return total or timedelta(hours=8)


def patch_status(name: str, namespace: str, status: dict):
    """Patch the Sandbox status subresource."""
    api = kubernetes.client.CustomObjectsApi()
    api.patch_namespaced_custom_object_status(
        group="sandboxes.dev",
        version="v1alpha1",
        namespace=namespace,
        plural="sandboxes",
        name=name,
        body={"status": status},
    )


def set_phase(
    name: str,
    namespace: str,
    phase: str,
    reason: str,
    message: str = "",
    extra: dict | None = None,
    ready: str | None = None,
):
    """Patch phase + a flat reason + a rollup `Ready` condition in one shot.

    The Ready condition is the single authoritative "is this sandbox usable"
    signal; `reason` pinpoints WHICH stage we're in or failed at, so a stuck
    sandbox self-describes (e.g. reason=PodCreationFailed) without
    operator-stderr spelunking. Pod-level detail (ImagePullBackOff, etc.) stays
    visible via the live pod, which the cockpit already reads.

    `ready` overrides the condition status. By default it tracks phase, but for
    interactive sandboxes Ready reflects the pod's actual ttyd readiness, not
    just "phase=Running" — the operator reconciles it so Ready
    means attachable, not merely scheduled.
    """
    now = datetime.now(UTC).isoformat()
    if ready is None:
        ready = "True" if phase == "Running" else "False"
    status: dict = {
        "phase": phase,
        "reason": reason,
        "conditions": [
            {
                "type": "Ready",
                "status": ready,
                "reason": reason,
                "message": message,
                "lastTransitionTime": now,
            }
        ],
    }
    if message:
        status["message"] = message
    if extra:
        status.update(extra)
    patch_status(name, namespace, status)


def _ensure_pvc(pvc_name: str, builder, user: str, namespace: str, label: str):
    """Create a per-user PVC if absent, tolerating a concurrent creator.

    Per-user PVCs are shared by every sandbox that user owns, so two
    sandboxes starting together both find it missing and both try to create
    it. The loser used to get a 409 and fail the whole sandbox; an
    already-existing PVC is the state we wanted, so treat it as success.
    """
    v1 = kubernetes.client.CoreV1Api()
    try:
        v1.read_namespaced_persistent_volume_claim(pvc_name, namespace)
        logger.info("%s PVC %s already exists", label, pvc_name)
        return
    except ApiException as e:
        if e.status != 404:
            raise
    try:
        v1.create_namespaced_persistent_volume_claim(
            namespace, builder(user, namespace, config)
        )
        logger.info("Created %s PVC %s", label, pvc_name)
    except ApiException as e:
        if e.status == 409:  # raced another sandbox for the same user
            logger.info("%s PVC %s created concurrently", label, pvc_name)
            return
        raise


def ensure_pvc(user: str, namespace: str):
    """Per-user state PVC (agent config, credentials, history)."""
    _ensure_pvc(f"sandbox-state-{user}", build_pvc, user, namespace, "state")


def ensure_agy_pvc(user: str, namespace: str):
    """Per-user Antigravity state (~/.gemini OAuth token)."""
    _ensure_pvc(f"sandbox-agy-state-{user}", build_agy_pvc, user, namespace, "agy")


def ensure_codex_pvc(user: str, namespace: str):
    """Per-user Codex state (~/.codex ChatGPT OAuth)."""
    _ensure_pvc(f"sandbox-codex-state-{user}", build_codex_pvc, user, namespace, "codex")


def ensure_opencode_pvc(user: str, namespace: str):
    """Per-user opencode state."""
    _ensure_pvc(
        f"sandbox-opencode-state-{user}", build_opencode_pvc, user, namespace, "opencode"
    )


def ensure_pi_pvc(user: str, namespace: str):
    """Per-user Pi state (~/.pi/agent/auth.json OAuth tokens)."""
    _ensure_pvc(f"sandbox-pi-state-{user}", build_pi_pvc, user, namespace, "pi")


def _rollback(
    name: str,
    namespace: str,
    vault_enabled: bool,
    msg: str,
    reason: str = "ProvisioningFailed",
):
    """Best-effort cleanup after a provisioning failure."""
    logger.error("Provisioning failed for sandbox %s: %s", name, msg)
    v1 = kubernetes.client.CoreV1Api()
    for resource, delete_fn in [
        (f"pod/sandbox-{name}", lambda: v1.delete_namespaced_pod(f"sandbox-{name}", namespace)),
        (f"svc/sandbox-{name}", lambda: v1.delete_namespaced_service(f"sandbox-{name}", namespace)),
        (f"sa/sandbox-{name}", lambda: v1.delete_namespaced_service_account(f"sandbox-{name}", namespace)),
    ]:
        try:
            delete_fn()
            logger.info("Rollback: deleted %s", resource)
        except ApiException as e:
            if e.status != 404:
                logger.warning("Rollback: failed to delete %s: %s", resource, e)
    if vault_enabled:
        try:
            vault_client().delete_sandbox_resources(name)
        except Exception:
            logger.warning("Rollback: Vault cleanup failed for %s", name, exc_info=True)
    set_phase(name, namespace, "Failed", reason, msg)


@kopf.on.create("sandboxes.dev", "v1alpha1", "sandboxes")
def on_create(spec, name, namespace, body, **_):
    """Provision all sub-resources for a new Sandbox."""
    user = spec["user"]
    mode = spec.get("mode", "interactive")
    ttl = spec.get("ttl", config.default_ttl)

    logger.info("Creating sandbox %s for user %s (mode=%s)", name, user, mode)
    set_phase(name, namespace, "Provisioning", "Provisioning", "Creating sandbox resources")

    v1 = kubernetes.client.CoreV1Api()
    vault_cfg = spec.get("vault", {})
    vault_enabled = vault_cfg.get("enabled", config.vault_enabled)

    # 1. Per-user PVCs. claude-state is universal; pi-state only when
    # harness=pi ( — persists ~/.pi/agent/auth.json across
    # sandboxes for `pi /login` durability).
    try:
        ensure_pvc(user, namespace)
    except ApiException as e:
        _rollback(name, namespace, False, f"PVC creation failed: {e.reason}", reason="PVCCreationFailed")
        return
    if spec.get("harness") == "pi":
        try:
            ensure_pi_pvc(user, namespace)
        except ApiException as e:
            _rollback(name, namespace, False, f"Pi PVC creation failed: {e.reason}", reason="PiPVCCreationFailed")
            return
    if spec.get("harness") == "opencode":
        try:
            ensure_opencode_pvc(user, namespace)
        except ApiException as e:
            _rollback(name, namespace, False, f"opencode PVC creation failed: {e.reason}", reason="OpencodePVCCreationFailed")
            return
    if spec.get("harness") == "codex":
        try:
            ensure_codex_pvc(user, namespace)
        except ApiException as e:
            _rollback(name, namespace, False, f"Codex PVC creation failed: {e.reason}", reason="CodexPVCCreationFailed")
            return
    if spec.get("harness") == "antigravity":
        try:
            ensure_agy_pvc(user, namespace)
        except ApiException as e:
            _rollback(name, namespace, False, f"Agy PVC creation failed: {e.reason}", reason="AgyPVCCreationFailed")
            return

    # 2. ServiceAccount
    sa = build_service_account(name, namespace, user, mode)
    try:
        v1.create_namespaced_service_account(namespace, sa)
    except ApiException as e:
        if e.status != 409:
            _rollback(name, namespace, False, f"ServiceAccount creation failed: {e.reason}", reason="ServiceAccountFailed")
            return

    # 3. Vault integration
    if vault_enabled:
        try:
            vc = vault_client()
            policy = vc.create_sandbox_policy(name, user)
            vc.create_k8s_auth_role(name, namespace, f"sandbox-{name}", policy, ttl=ttl)
        except Exception as e:
            _rollback(name, namespace, vault_enabled, f"Vault setup failed: {e}", reason="VaultSetupFailed")
            return

    # 4. Pod
    pod = build_pod(body, namespace, config)
    kopf.adopt(pod)
    try:
        v1.create_namespaced_pod(namespace, pod)
    except ApiException as e:
        if e.status != 409:
            _rollback(name, namespace, vault_enabled, f"Pod creation failed: {e.reason}", reason="PodCreationFailed")
            return

    # 5. Service (interactive mode only)
    if mode == "interactive":
        svc = build_service(name, namespace, user, mode)
        kopf.adopt(svc)
        try:
            v1.create_namespaced_service(namespace, svc)
        except ApiException as e:
            if e.status != 409:
                _rollback(name, namespace, vault_enabled, f"Service creation failed: {e.reason}", reason="ServiceCreationFailed")
                return

        # 6. HTTPRoute for Envoy Gateway ingress
        route = build_httproute(name, namespace, user)
        kopf.adopt(route)
        try:
            api = kubernetes.client.CustomObjectsApi()
            api.create_namespaced_custom_object(
                group="gateway.networking.k8s.io",
                version="v1",
                namespace=namespace,
                plural="httproutes",
                body=route,
            )
        except ApiException as e:
            if e.status != 409:
                logger.warning("HTTPRoute creation failed (non-fatal): %s", e.reason)

    # 7. Update status
    now = datetime.now(UTC)
    expires = now + parse_ttl(ttl)
    extra = {
        "podName": f"sandbox-{name}",
        "startedAt": now.isoformat(),
        "expiresAt": expires.isoformat(),
    }
    if mode == "interactive":
        extra["serviceName"] = f"sandbox-{name}"
        extra["ttydPort"] = 7681
        extra["connectURL"] = f"/sandbox/{name}"
        # Pod just created — ttyd isn't up yet. Phase stays Pending (not
        # Running) until the pod's readiness probe passes; reconcile() flips
        # it. phase=Running now MEANS attachable, so the
        # cockpit's open-terminal gating and `sandbox connect` can't race a
        # ContainerCreating pod, and the UI never shows a green Running chip
        # with a "still creating" reason.
        set_phase(
            name, namespace, "Pending", "PodStarting",
            "Waiting for ttyd to become ready", extra=extra, ready="False",
        )
        logger.info(
            "Sandbox %s is Pending until ttyd is ready (expires %s)",
            name, expires.isoformat(),
        )
    else:
        # Headless has no ttyd; the pgrep liveness probe is the readiness signal.
        set_phase(name, namespace, "Running", "Running", extra=extra)
        logger.info("Sandbox %s is Running (expires %s)", name, expires.isoformat())


@kopf.on.delete("sandboxes.dev", "v1alpha1", "sandboxes")
def on_delete(spec, name, namespace, **_):
    """Clean up Vault resources. K8s resources are GC'd via ownerReferences."""
    logger.info("Deleting sandbox %s", name)

    vault_cfg = spec.get("vault", {})
    vault_enabled = vault_cfg.get("enabled", config.vault_enabled)
    if vault_enabled:
        try:
            vault_client().delete_sandbox_resources(name)
        except Exception:
            logger.warning(
                "Vault cleanup failed for sandbox %s", name, exc_info=True
            )

    # ServiceAccount is not owned by the CR (may be shared), delete explicitly
    v1 = kubernetes.client.CoreV1Api()
    try:
        v1.delete_namespaced_service_account(f"sandbox-{name}", namespace)
    except ApiException as e:
        if e.status != 404:
            logger.warning("Failed to delete SA sandbox-%s: %s", name, e)


def _pod_readiness(pod) -> tuple[bool, str, str]:
    """Derive (ready, reason, message) from a pod's status.

    Ready mirrors the pod's own Ready condition (gated by the ttyd readiness
    probe). When not ready, surface the most informative container waiting
    reason (ContainerCreating, ImagePullBackOff, CrashLoopBackOff, …) so it
    bubbles up onto the Sandbox's Ready condition instead of only living on
    the pod.
    """
    st = pod.status
    if any(c.type == "Ready" and c.status == "True" for c in (st.conditions or [])):
        return True, "Running", ""
    for cs in st.container_statuses or []:
        w = cs.state.waiting if cs.state else None
        if w and w.reason:
            return False, w.reason, (w.message or "")
    return False, "PodNotReady", f"Pod phase {st.phase}"


@kopf.timer("sandboxes.dev", "v1alpha1", "sandboxes", interval=10)
def reconcile(spec, name, namespace, status, **_):
    """TTL expiry, pod-failure detection, and Ready reconciliation (vx6)."""
    phase = (status or {}).get("phase")
    if phase in ("Failed", "Expired", "Terminating"):
        return

    expires_at = (status or {}).get("expiresAt")
    if expires_at and datetime.now(UTC) > datetime.fromisoformat(expires_at):
        logger.info("Sandbox %s has expired, deleting", name)
        set_phase(name, namespace, "Expired", "Expired", "TTL exceeded")
        kubernetes.client.CustomObjectsApi().delete_namespaced_custom_object(
            group="sandboxes.dev",
            version="v1alpha1",
            namespace=namespace,
            plural="sandboxes",
            name=name,
        )
        return

    v1 = kubernetes.client.CoreV1Api()
    try:
        pod = v1.read_namespaced_pod(f"sandbox-{name}", namespace)
    except ApiException as e:
        if e.status == 404 and phase in ("Pending", "Running"):
            set_phase(name, namespace, "Failed", "PodNotFound", "Pod not found")
        return

    if pod.status.phase in ("Failed", "Succeeded"):
        set_phase(
            name,
            namespace,
            "Failed",
            "PodFailed",
            f"Pod {pod.status.phase}: {pod.status.reason or 'unknown'}",
        )
        return

    # Reconcile phase + Ready against the pod's real readiness (interactive
    # only; headless readiness == pgrep liveness).
    if spec.get("mode", "interactive") == "interactive":
        _reconcile_readiness(name, namespace, status, pod)


def _reconcile_readiness(name: str, namespace: str, status: dict, pod) -> None:
    """Promote Pending->Running when the pod turns attachable — and demote
    Running->Pending if it later stops being (crashloop, probe failure).

    phase=Running MEANS "ttyd is up / attachable"; Pending
    carries the real stage in `reason` (PodStarting, ContainerCreating,
    ImagePullBackOff, ...). The original fix moved on_create to Pending but
    left this gate reading phase=="Running", so nothing ever promoted a
    sandbox — hence the shared helper, driven by BOTH the 10s timer and the
    pod-event watch (which lands the flip within ~1s of the probe passing).
    """
    phase = (status or {}).get("phase")
    if phase not in ("Pending", "Running"):
        return
    ready, reason, message = _pod_readiness(pod)
    desired_phase = "Running" if ready else "Pending"
    desired_ready = "True" if ready else "False"
    cur = next(
        (c for c in ((status or {}).get("conditions") or []) if c.get("type") == "Ready"),
        {},
    )
    current = (phase, cur.get("status"), (status or {}).get("reason"))
    if current != (desired_phase, desired_ready, reason):
        set_phase(name, namespace, desired_phase, reason, message, ready=desired_ready)


@kopf.on.event("", "v1", "pods", labels={"sandboxes.dev/name": kopf.PRESENT})
def on_sandbox_pod_event(event, meta, namespace, **_):
    """Event-driven phase flips: react to the sandbox pod's
    readiness the moment kubelet reports it instead of waiting for the 10s
    timer (which stays as the safety net for missed events).
    """
    if event.get("type") == "DELETED":
        return  # reconcile()'s 404 path owns pod-gone -> Failed
    sandbox_name = (meta.get("labels") or {}).get("sandboxes.dev/name")
    if not sandbox_name:
        return
    api = kubernetes.client.CustomObjectsApi()
    try:
        s = api.get_namespaced_custom_object(
            group="sandboxes.dev",
            version="v1alpha1",
            namespace=namespace,
            plural="sandboxes",
            name=sandbox_name,
        )
    except ApiException as e:
        if e.status == 404:
            return  # sandbox gone; pod GC in flight
        raise
    if s.get("spec", {}).get("mode", "interactive") != "interactive":
        return
    v1 = kubernetes.client.CoreV1Api()
    try:
        pod = v1.read_namespaced_pod(f"sandbox-{sandbox_name}", namespace)
    except ApiException as e:
        if e.status == 404:
            return
        raise
    _reconcile_readiness(sandbox_name, namespace, s.get("status") or {}, pod)
