# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for sysctl JSONPatch building (_build_sysctl_patch_ops)."""

from unittest.mock import patch

from app import _build_sysctl_patch_ops


class TestBuildSysctlPatchOps:
    def test_build_sysctl_patch_when_no_security_context_then_adds_security_context(
        self, mock_config, deployment_with_pod_template
    ):
        with patch("app.CFG", mock_config):
            patches = _build_sysctl_patch_ops(deployment_with_pod_template)
        assert len(patches) == 1
        assert patches[0].op == "add"
        assert patches[0].path == "/spec/template/spec/securityContext"
        assert patches[0].value == {
            "sysctls": [{"name": "net.ipv4.tcp_retries2", "value": "5"}],
        }

    def test_build_sysctl_patch_when_security_context_has_no_sysctls_then_adds_sysctls(
        self, mock_config, deployment_with_pod_template
    ):
        deployment_with_pod_template["spec"]["template"]["spec"]["securityContext"] = {}
        with patch("app.CFG", mock_config):
            patches = _build_sysctl_patch_ops(deployment_with_pod_template)
        assert len(patches) == 1
        assert patches[0].op == "add"
        assert patches[0].path == "/spec/template/spec/securityContext/sysctls"
        assert patches[0].value == [{"name": "net.ipv4.tcp_retries2", "value": "5"}]

    def test_build_sysctl_patch_when_sysctl_already_correct_then_no_patch(
        self, mock_config, deployment_with_pod_template
    ):
        deployment_with_pod_template["spec"]["template"]["spec"]["securityContext"] = {
            "sysctls": [{"name": "net.ipv4.tcp_retries2", "value": "5"}],
        }
        with patch("app.CFG", mock_config):
            patches = _build_sysctl_patch_ops(deployment_with_pod_template)
        assert patches == []

    def test_build_sysctl_patch_when_sysctl_value_differs_then_replace(
        self, mock_config, deployment_with_pod_template
    ):
        deployment_with_pod_template["spec"]["template"]["spec"]["securityContext"] = {
            "sysctls": [{"name": "net.ipv4.tcp_retries2", "value": "3"}],
        }
        with patch("app.CFG", mock_config):
            patches = _build_sysctl_patch_ops(deployment_with_pod_template)
        assert len(patches) == 1
        assert patches[0].op == "replace"
        assert patches[0].path == "/spec/template/spec/securityContext/sysctls/0/value"
        assert patches[0].value == "5"

    def test_build_sysctl_patch_when_sysctls_exists_our_key_missing_then_append(
        self, mock_config, deployment_with_pod_template
    ):
        deployment_with_pod_template["spec"]["template"]["spec"]["securityContext"] = {
            "sysctls": [{"name": "other.sysctl", "value": "1"}],
        }
        with patch("app.CFG", mock_config):
            patches = _build_sysctl_patch_ops(deployment_with_pod_template)
        assert len(patches) == 1
        assert patches[0].op == "add"
        assert patches[0].path == "/spec/template/spec/securityContext/sysctls/-"
        assert patches[0].value == {"name": "net.ipv4.tcp_retries2", "value": "5"}

    def test_build_sysctl_patch_when_no_pod_spec_then_returns_empty(self, mock_config):
        with patch("app.CFG", mock_config):
            assert _build_sysctl_patch_ops({}) == []
