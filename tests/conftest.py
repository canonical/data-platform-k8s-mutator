# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Pytest fixtures for data-platform-k8s-mutator tests."""

# Set required env before app is imported (app.CFG is created at import time).
# pydantic-settings parses list fields from env as JSON.

import os

os.environ.setdefault("TARGET_CONTAINER_NAMES", '["app"]')

import pytest


@pytest.fixture
def mock_config():
    """A minimal config-like object for patching app.CFG in scope/patch tests."""

    class Cfg:
        sysctl_name = "net.ipv4.tcp_retries2"
        sysctl_value = "5"
        target_container_names = ["app", "sidecar"]
        target_namespaces = []
        target_labels = {}
        target_image_substr = ""
        require_juju_managed = True

    return Cfg()


@pytest.fixture
def deployment_with_pod_template():
    """Minimal Deployment-like object with spec.template.spec and no securityContext."""
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": "myapp",
            "namespace": "default",
            "labels": {"app.kubernetes.io/managed-by": "juju", "app": "myapp"},
        },
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {"name": "app", "image": "myapp:1.0"},
                    ],
                },
            },
        },
    }


@pytest.fixture
def admission_review_request(deployment_with_pod_template):
    """AdmissionReview request body wrapping a workload object."""
    return {
        "apiVersion": "admission.k8s.io/v1",
        "kind": "AdmissionReview",
        "request": {
            "uid": "req-123",
            "object": deployment_with_pod_template,
        },
    }
