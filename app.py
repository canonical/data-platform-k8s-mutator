# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""
Kubernetes mutating admission webhook that injects a sysctl into workload pod specs.

On CREATE of a StatefulSet, Deployment, or DaemonSet, the webhook returns a JSONPatch to add or update
spec.securityContext.sysctls (e.g. net.ipv4.tcp_retries2=5). Scope and target
containers are controlled via environment variables which are described in MutatorConfig.
"""

import base64
import logging
from typing import Any

from fastapi import Body, FastAPI
from pydantic import BaseModel, TypeAdapter, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

app = FastAPI()


webhook = logging.getLogger(__name__)
webhook.setLevel(logging.INFO)
logging.basicConfig(format="[%(asctime)s] %(levelname)s: %(message)s")


# ---------------------------------------------------------
# JSONPatch types


class Patch(BaseModel):
    """Single JSONPatch operation (op, path, value)."""

    op: str
    path: str
    value: Any | None = None


# JSONPatch path prefix for workloads that use spec.template.spec
_POD_TEMPLATE_SPEC_PREFIX = "/spec/template/spec"


# -------------------------------------------------------------------------
# Helpers for config parsing


def _is_true(value: str | None, default: bool = False) -> bool:
    """Parse a string as boolean (e.g. from env).

    Args:
        value: String to interpret (e.g. "true", "1", "yes"); may be None.
        default: Value to return when value is None.

    Returns:
        True if value is truthy, False otherwise; default when value is None.
    """
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "y", "on")


def _parse_label_selector(selector: str) -> dict[str, str]:
    """Parse comma-separated key=value pairs into a dict.

    Args:
        selector: Comma-separated key=value string (e.g. "k1=v1,k2=v2").

    Returns:
        Dict mapping label keys to values. Empty if selector is empty/whitespace.

    Raises:
        ValueError: If an entry has no "=" or has an empty key.
    """
    labels: dict[str, str] = {}
    for entry in selector.strip().split(","):
        entry = entry.strip()
        if not entry:
            continue
        if "=" not in entry:
            raise ValueError(f"Invalid label selector (expected key=value): {entry!r}")
        # partition("=") splits on first "=" only; _ is the "=" itself (unused)
        key, _, val = entry.partition("=")
        key, val = key.strip(), val.strip()
        if not key:
            raise ValueError(f"Invalid label selector (empty key): {entry!r}")
        labels[key] = val
    return labels


# -----------------------------------------------------------------------------
# Configuration (from environment)


class MutatorConfig(BaseSettings):
    """
    Webhook configuration loaded from environment variables.
    Used to decide which objects to mutate and which sysctl to inject.
    """

    model_config = SettingsConfigDict(extra="ignore")

    # Sysctl to inject into pod securityContext.sysctls
    sysctl_name: str = "net.ipv4.tcp_retries2"
    sysctl_value: str = "5"

    # Required: comma-separated container names to target (only these containers are checked)
    target_container_names: list[str]

    # Optional: only mutate objects in these namespaces (comma-separated); empty = any namespace
    target_namespaces: list[str] = []
    # Optional: only mutate objects that have these labels; if not set, applied to any object.
    target_labels: dict[str, str] = {}
    # Optional: only match containers whose image contains this substring, if not set, it will be applied to containers with any image.
    target_image_substr: str = ""

    # If True, only mutate objects with label app.kubernetes.io/managed-by=juju
    require_juju_managed: bool = True

    @field_validator("target_namespaces", mode="before")
    @classmethod
    def _parse_target_namespaces(cls, v: object) -> list[str]:
        """Parse comma-separated namespace names from env into a list.

        Args:
            v: Raw value (list or comma-separated string from env).

        Returns:
            List of stripped namespace names, empty if v is empty/whitespace.
        """
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
        raw = (v or "").strip()
        return [n.strip() for n in raw.split(",") if n.strip()]

    @field_validator("target_labels", mode="before")
    @classmethod
    def _parse_label_selector_field(cls, v: object) -> dict[str, str]:
        """Parse raw env value for target_labels into a label dict.

        Args:
            v: Raw value (dict or comma-separated key=value string from env).

        Returns:
            Dict of label key -> value; empty if v is empty/whitespace.
        """
        if isinstance(v, dict):
            return v
        return _parse_label_selector((v or "").strip())

    @field_validator("require_juju_managed", mode="before")
    @classmethod
    def _parse_require_juju_managed(cls, v: object) -> bool:
        """Parse raw env value for require_juju_managed into a bool.

        Args:
            v: Raw value (bool or string from env).

        Returns:
            True for truthy strings (e.g. "1", "true"), False otherwise; default True if None.
        """
        if isinstance(v, bool):
            return v
        return _is_true(v, default=True)

    @field_validator("target_container_names", mode="before")
    @classmethod
    def _parse_target_container_names(cls, v: object) -> list[str]:
        """Parse comma-separated container names from env into a list.

        Args:
            v: Raw value (list or comma-separated string from env).

        Returns:
            Non-empty list of stripped container names.

        Raises:
            ValueError: If no valid container names are present.
        """
        if isinstance(v, list):
            return v
        raw = (v or "").strip()
        names = [n.strip() for n in raw.split(",") if n.strip()]
        if not names:
            raise ValueError(
                "TARGET_CONTAINER_NAMES must contain at least one container name"
            )
        return names

    @field_validator("target_image_substr", mode="before")
    @classmethod
    def _strip_target_image_substr(cls, v: object) -> str:
        """Strip whitespace from target_image_substr env value.

        Args:
            v: Raw value (string or None from env).

        Returns:
            Stripped string, or empty string if v is None.
        """
        return (v or "").strip() if v is not None else ""


CFG = MutatorConfig()


# -------------------------------------------------
# AdmissionReview helpers


def _encode_patch_base64(patches: list[Patch]) -> str:
    """Serialize patch list to JSON and base64-encode for AdmissionResponse.patch.

    Args:
        patches: List of JSONPatch operations to encode.

    Returns:
        Base64-encoded JSON string suitable for AdmissionResponse.patch.
    """
    return base64.b64encode(TypeAdapter(list[Patch]).dump_json(patches)).decode()


# -----------------------------------------------------------------------------
# Locating the pod spec in different workload types


def _get_pod_spec_and_prefix(
    k8s_object: dict[str, Any],
) -> tuple[dict[str, Any], str] | None:
    """Find the pod spec inside the object and the JSONPatch path prefix to it.

    Supported kinds: StatefulSet, Deployment, DaemonSet, ReplicaSet, Job
    (all have spec.template.spec).

    Args:
        k8s_object: Full Kubernetes object (e.g Deployment) as a dict.

    Returns:
        (pod_spec, jsonpatch_prefix) for the pod template spec, or None if
        the object has no spec.template.spec.
    """
    spec = k8s_object.get("spec")
    if not isinstance(spec, dict):
        return None

    template = spec.get("template")
    if not isinstance(template, dict):
        return None

    pod_spec = template.get("spec")
    if not isinstance(pod_spec, dict):
        return None

    return (pod_spec, _POD_TEMPLATE_SPEC_PREFIX)


# -----------------------------------------------------------------------------
# Scope and target matching


def _object_labels_match_config(labels: dict[str, Any]) -> bool:
    """Check whether object labels satisfy target_labels.

    Args:
        labels: Object metadata.labels (or empty dict).

    Returns:
        True if every target label is present with the configured value
        (or target_labels is empty); False otherwise.
    """
    if not CFG.target_labels:
        return True
    for key, want in CFG.target_labels.items():
        if labels.get(key) != want:
            return False
    return True


def _pod_spec_has_matching_container(pod_spec: dict[str, Any]) -> bool:
    """Check if at least one container matches target names and image substring.

    Args:
        pod_spec: Pod spec dict (e.g. spec.template.spec) with a "containers" list.

    Returns:
        True if any container matches CFG.target_container_names (if set) and
        CFG.target_image_substr (if set), False otherwise.
    """
    for container in pod_spec.get("containers", []):
        if not isinstance(container, dict):
            continue
        name = container.get("name") or ""
        if CFG.target_container_names and name not in CFG.target_container_names:
            continue
        image = container.get("image", "")
        if CFG.target_image_substr and (
            not isinstance(image, str) or CFG.target_image_substr not in image
        ):
            continue
        return True
    return False


def _object_matches_scope(k8s_object: dict[str, Any]) -> bool:
    """Determine whether the object is in scope for mutation.

    Args:
        k8s_object: full Kubernetes object as a dict.

    Returns:
        True if namespace (when target_namespaces is set), labels, managed-by,
        kind and at least one container match the configured scope, False otherwise.
    """
    metadata = k8s_object.get("metadata") or {}
    labels = metadata.get("labels") or {}
    namespace = metadata.get("namespace") or ""

    # namespace scope, object must be in one of the configured namespaces (if namespace given)
    if CFG.target_namespaces and namespace not in CFG.target_namespaces:
        return False
    # labels scope, object must have all the configured labels with the configured values
    if not _object_labels_match_config(labels):
        return False
    # managed-by scope, object must be managed by juju (if require_juju_managed is True)
    if (
        CFG.require_juju_managed
        and labels.get("app.kubernetes.io/managed-by") != "juju"
    ):
        return False

    pod_spec_info = _get_pod_spec_and_prefix(k8s_object)
    if not pod_spec_info:
        return False
    pod_spec, _ = pod_spec_info
    # container scope, pod spec must have at least one container that matches the configured container names and image substring
    return _pod_spec_has_matching_container(pod_spec)


# -----------------------------------------------------------------------------
# Building the sysctl JSONPatch


def _build_sysctl_patch_ops(k8s_object: dict[str, Any]) -> list[Patch]:
    """Build JSONPatch operations to inject or update the configured sysctl.

    Args:
        k8s_object: full Kubernetes workload object with spec.template.spec.

    Returns:
        list of patch ops (add/replace) to apply
    """
    pod_spec_info = _get_pod_spec_and_prefix(k8s_object)
    if not pod_spec_info:
        return []

    pod_spec, prefix = pod_spec_info
    entry = {"name": CFG.sysctl_name, "value": str(CFG.sysctl_value)}
    security_context = pod_spec.get("securityContext")

    # no securityContext: add securityContext with our sysctl
    if security_context is None:
        return [
            Patch(
                op="add", path=f"{prefix}/securityContext", value={"sysctls": [entry]}
            ),
        ]

    # securityContext exists but no sysctls list: add sysctls list with our sysctl
    if security_context.get("sysctls") is None:
        return [
            Patch(op="add", path=f"{prefix}/securityContext/sysctls", value=[entry]),
        ]

    # sysctls exists: check if our sysctl is already there with same value
    sysctls = security_context.get("sysctls") or []
    for i, item in enumerate(sysctls):
        if not isinstance(item, dict) or item.get("name") != CFG.sysctl_name:
            continue
        # already set, no patch
        if str(item.get("value")) == str(CFG.sysctl_value):
            return []
        return [
            Patch(
                op="replace",
                path=f"{prefix}/securityContext/sysctls/{i}/value",
                value=str(CFG.sysctl_value),
            ),
        ]

    # sysctl exists but the key that we are interested in is not in the list
    # /securityContext/sysctls/- means appends to the end of the list
    return [
        Patch(op="add", path=f"{prefix}/securityContext/sysctls/-", value=entry),
    ]


# -----------------------------------------------------------------------------
# AdmissionReview response helper


def _admission_review_response(
    uid: str,
    message: str,
    patch_operations: list[Patch],
) -> dict[str, Any]:
    """Build an AdmissionReview response (allowed=True, optional JSONPatch).

    Args:
        uid: Request UID from the admission request.
        message: Status message for the response.
        patch_operations: optional list of JSONPatch operations

    Returns:
        AdmissionReview dict with apiVersion, kind and response
    """
    response: dict[str, Any] = {
        "uid": uid,
        "allowed": True,
        "status": {"message": message},
    }
    if patch_operations:
        response["patchType"] = "JSONPatch"
        response["patch"] = _encode_patch_base64(patch_operations)
    return {
        "apiVersion": "admission.k8s.io/v1",
        "kind": "AdmissionReview",
        "response": response,
    }


# -----------------------------------------------------------------------------
# Webhook HTTP endpoint


@app.post("/mutate")
def mutate_admission_review(admission_review_body: dict = Body(...)) -> dict[str, Any]:
    """Mutating webhook endpoint: inject sysctl into in-scope workloads.

    Receives an AdmissionReview request, checks scope, and returns an
    AdmissionReview response with optional JSONPatch. Always allows the
    request, on scope mismatch or error, allows without mutation.

    Args:
        admission_review_body: full AdmissionReview body with request.object

    Returns:
        AdmissionReview dict with response.allowed=True and optional
        response.patch (base64-encoded JSONPatch).
    """
    # extract the admission request: UID and the object being created
    request = admission_review_body.get("request") or {}
    uid = request.get("uid", "")
    obj = request.get("object")  # missing or non-dict is malformed
    name = (
        (obj.get("metadata") or {}).get("name") or "unknown"
        if isinstance(obj, dict)
        else "unknown"
    )

    webhook.info("mutate called for %s", name)

    # malformed request: we just skip our patch.
    if not uid or obj is None or not isinstance(obj, dict):
        return _admission_review_response(
            uid or "unknown", "malformed admission request.", []
        )

    # Only mutate if object matches scope (namespace, labels, managed-by, container match).
    if not _object_matches_scope(obj):
        return _admission_review_response(
            uid, "Object out of scope; allowing without mutation.", []
        )

    # build JSONPatch to add/update sysctl in pod spec, on error, allow without mutation.
    try:
        patches = _build_sysctl_patch_ops(obj)
    except Exception as e:
        webhook.exception("Failed building sysctl patch: %s", e)
        return _admission_review_response(
            uid, "Failed to build patch; allowing without mutation.", []
        )

    # return response with patch if we have changes, otherwise object already has the sysctl.
    if patches:
        webhook.info(
            "Injecting sysctl %s=%s via JSONPatch", CFG.sysctl_name, CFG.sysctl_value
        )
        return _admission_review_response(uid, "Injected pod sysctl.", patches)

    # Pod spec already has the configured sysctl with the correct value, no patch.
    return _admission_review_response(uid, "Sysctl already set; no mutation.", [])
