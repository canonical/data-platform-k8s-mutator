# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Simple integration tests for the /mutate webhook endpoint."""

import base64
import json

def test_mutate_deployment_in_scope_returns_sysctl_patch(
    client, admission_review_deployment
):
    """POST /mutate with an in-scope Deployment returns 200 and a patch with the sysctl."""
    r = client.post("/mutate", json=admission_review_deployment)
    assert r.status_code == 200
    data = r.json()
    assert data["response"]["allowed"] is True
    assert data["response"]["patchType"] == "JSONPatch"
    patch_b64 = data["response"].get("patch")
    assert patch_b64 is not None

    patch_json = json.loads(base64.b64decode(patch_b64).decode())
    assert isinstance(patch_json, list)
    assert len(patch_json) >= 1
    # Patch should add or update securityContext with our sysctl
    sysctl_entries = []
    for op in patch_json:
        val = op.get("value")
        if isinstance(val, dict) and "sysctls" in val:
            sysctl_entries.extend(val["sysctls"])
    assert any(
        e.get("name") == "net.ipv4.tcp_retries2" and e.get("value") == "5"
        for e in sysctl_entries
    ), f"Expected sysctl in patch: {patch_json}"


def test_mutate_statefulset_in_scope_returns_sysctl_patch(client):
    """POST /mutate with an in-scope StatefulSet returns 200 and a patch."""
    body = {
        "apiVersion": "admission.k8s.io/v1",
        "kind": "AdmissionReview",
        "request": {
            "uid": "integration-req-2",
            "object": {
                "apiVersion": "apps/v1",
                "kind": "StatefulSet",
                "metadata": {
                    "name": "mysts",
                    "namespace": "default",
                    "labels": {"app.kubernetes.io/managed-by": "juju"},
                },
                "spec": {
                    "template": {
                        "spec": {
                            "containers": [
                                {"name": "app", "image": "sts-image:latest"},
                            ],
                        },
                    },
                },
            },
        },
    }
    r = client.post("/mutate", json=body)
    assert r.status_code == 200
    data = r.json()
    assert data["response"]["allowed"] is True
    assert data["response"]["patchType"] == "JSONPatch"
    patch_b64 = data["response"]["patch"]
    patch_json = json.loads(base64.b64decode(patch_b64).decode())
    assert isinstance(patch_json, list)
    assert len(patch_json) >= 1
    sysctl_entries = []
    for op in patch_json:
        val = op.get("value")
        if isinstance(val, dict) and "sysctls" in val:
            sysctl_entries.extend(val["sysctls"])
    assert any(
        e.get("name") == "net.ipv4.tcp_retries2" and e.get("value") == "5"
        for e in sysctl_entries
    ), f"Expected sysctl in patch: {patch_json}"
