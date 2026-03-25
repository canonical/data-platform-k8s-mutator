# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for MutatorConfig (env-based settings)."""

import pytest
from app import MutatorConfig
from pydantic import ValidationError


class TestMutatorConfig:
    def test_config_when_target_container_names_missing_then_raises(self, monkeypatch):
        monkeypatch.delenv("TARGET_CONTAINER_NAMES", raising=False)
        with pytest.raises(
            (ValueError, ValidationError), match="at least one container name|Field required"
        ):
            MutatorConfig()

    def test_target_container_names_parsed(self, monkeypatch):
        # pydantic-settings parses list fields from env as JSON
        monkeypatch.setenv("TARGET_CONTAINER_NAMES", '["app", "sidecar"]')
        cfg = MutatorConfig()
        assert cfg.target_container_names == ["app", "sidecar"]

    def test_require_juju_managed_default_true(self, monkeypatch):
        monkeypatch.setenv("TARGET_CONTAINER_NAMES", '["app"]')
        cfg = MutatorConfig()
        assert cfg.require_juju_managed is True

    def test_require_juju_managed_false_from_env(self, monkeypatch):
        monkeypatch.setenv("TARGET_CONTAINER_NAMES", '["app"]')
        monkeypatch.setenv("REQUIRE_JUJU_MANAGED", "0")
        cfg = MutatorConfig()
        assert cfg.require_juju_managed is False

    def test_target_labels_parsed(self, monkeypatch):
        monkeypatch.setenv("TARGET_CONTAINER_NAMES", '["app"]')
        # pydantic-settings parses dict fields from env as JSON
        monkeypatch.setenv(
            "TARGET_LABELS", '{"app.kubernetes.io/name": "myapp", "managed-by": "juju"}'
        )
        cfg = MutatorConfig()
        assert cfg.target_labels == {
            "app.kubernetes.io/name": "myapp",
            "managed-by": "juju",
        }

    def test_target_namespaces_parsed(self, monkeypatch):
        monkeypatch.setenv("TARGET_CONTAINER_NAMES", '["app"]')
        monkeypatch.setenv("TARGET_NAMESPACES", '["ns1", "ns2"]')
        cfg = MutatorConfig()
        assert cfg.target_namespaces == ["ns1", "ns2"]
