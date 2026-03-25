# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for JSONPatch encoding, AdmissionReview response, and /mutate endpoint."""

import base64
import json
from unittest.mock import patch

import pytest
from app import Patch, _admission_review_response, _encode_patch_base64
from app import app as fastapi_app
from fastapi.testclient import TestClient


class TestEncodePatchBase64:
    def test_encoded_patch_decodes_to_valid_jsonpatch(self):
        patches = [
            Patch(
                op="add",
                path="/spec/template/spec/securityContext",
                value={"sysctls": [{"name": "net.ipv4.tcp_retries2", "value": "5"}]},
            ),
        ]
        encoded = _encode_patch_base64(patches)
        raw = base64.b64decode(encoded)
        decoded = json.loads(raw)
        assert decoded[0]["op"] == "add"
        assert "securityContext" in decoded[0]["path"]


class TestAdmissionReviewResponse:
    def test_no_patch(self):
        out = _admission_review_response("uid-1", "message", [])
        assert out["response"]["uid"] == "uid-1"
        assert out["response"]["allowed"] is True
        assert out["response"]["status"]["message"] == "message"
        assert "patch" not in out["response"]
        assert out["apiVersion"] == "admission.k8s.io/v1"
        assert out["kind"] == "AdmissionReview"

    def test_with_patch(self):
        patches = [Patch(op="add", path="/x", value={"y": 1})]
        out = _admission_review_response("uid-2", "patched", patches)
        assert out["response"]["patchType"] == "JSONPatch"
        assert out["response"]["patch"] == _encode_patch_base64(patches)


class TestMutateEndpoint:
    """HTTP POST /mutate endpoint."""

    @pytest.fixture
    def client(self):
        return TestClient(fastapi_app)

    def test_mutate_when_object_in_scope_then_returns_patch(
        self, client, admission_review_request, mock_config
    ):
        with patch("app.CFG", mock_config):
            r = client.post("/mutate", json=admission_review_request)
        assert r.status_code == 200
        data = r.json()
        assert data["response"]["allowed"] is True
        assert "patch" in data["response"]
        assert data["response"]["patchType"] == "JSONPatch"

    def test_mutate_when_object_out_of_scope_then_allowed_no_patch(
        self, client, admission_review_request, mock_config
    ):
        mock_config.require_juju_managed = True
        admission_review_request["request"]["object"]["metadata"]["labels"] = {}
        with patch("app.CFG", mock_config):
            r = client.post("/mutate", json=admission_review_request)
        assert r.status_code == 200
        data = r.json()
        assert data["response"]["allowed"] is True
        assert "patch" not in data["response"] or data["response"].get("patch") is None

    def test_mutate_when_request_malformed_then_allowed_no_patch(self, client):
        r = client.post("/mutate", json={"request": {"uid": "x"}})  # no object
        assert r.status_code == 200
        data = r.json()
        assert data["response"]["allowed"] is True
        assert data["response"]["status"]["message"] == "malformed admission request."

    def test_mutate_when_request_missing_then_allowed_no_patch(self, client):
        r = client.post("/mutate", json={})
        assert r.status_code == 200
        data = r.json()
        assert data["response"]["allowed"] is True
