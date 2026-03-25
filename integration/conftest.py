# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Pytest fixtures for integration tests.

Sets required env before app is imported so app.CFG is correct for integration tests.
"""

import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("TARGET_CONTAINER_NAMES", '["app"]')

# Import app after env so app.CFG reads TARGET_CONTAINER_NAMES etc.
from app import app as fastapi_app


@pytest.fixture
def client():
    return TestClient(fastapi_app)


@pytest.fixture
def admission_review_deployment():
    """AdmissionReview wrapping a Deployment in scope (container app, juju managed)."""
    return {
        "apiVersion": "admission.k8s.io/v1",
        "kind": "AdmissionReview",
        "request": {
            "uid": "integration-req-1",
            "object": {
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
            },
        },
    }
