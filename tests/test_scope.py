# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for pod spec extraction and scope/target matching."""

from unittest.mock import patch

from app import (
    _get_pod_spec_and_prefix,
    _object_labels_match_config,
    _object_matches_scope,
    _pod_spec_has_matching_container,
)


class TestGetPodSpecAndPrefix:
    """Pod spec extraction from workload objects (_get_pod_spec_and_prefix)."""

    def test_deployment(self):
        obj = {
            "spec": {
                "template": {
                    "spec": {"containers": []},
                },
            },
        }
        out = _get_pod_spec_and_prefix(obj)
        assert out is not None
        pod_spec, prefix = out
        assert prefix == "/spec/template/spec"
        assert pod_spec == {"containers": []}

    def test_missing_spec(self):
        assert _get_pod_spec_and_prefix({}) is None
        assert _get_pod_spec_and_prefix({"spec": None}) is None

    def test_missing_template(self):
        assert _get_pod_spec_and_prefix({"spec": {}}) is None

    def test_missing_pod_spec(self):
        assert _get_pod_spec_and_prefix({"spec": {"template": {}}}) is None


class TestObjectLabelsMatchConfig:
    def test_empty_target_labels_matches_any(self, mock_config):
        mock_config.target_labels = {}
        with patch("app.CFG", mock_config):
            assert _object_labels_match_config({}) is True
            assert _object_labels_match_config({"any": "label"}) is True

    def test_all_labels_required(self, mock_config):
        mock_config.target_labels = {"a": "1", "b": "2"}
        with patch("app.CFG", mock_config):
            assert _object_labels_match_config({"a": "1", "b": "2"}) is True
            assert _object_labels_match_config({"a": "1"}) is False
            assert _object_labels_match_config({"a": "1", "b": "3"}) is False


class TestPodSpecHasMatchingContainer:
    def test_matching_name(self, mock_config):
        mock_config.target_container_names = ["app"]
        mock_config.target_image_substr = ""
        with patch("app.CFG", mock_config):
            pod_spec = {"containers": [{"name": "app", "image": "any"}]}
            assert _pod_spec_has_matching_container(pod_spec) is True

    def test_name_not_in_target(self, mock_config):
        mock_config.target_container_names = ["app"]
        with patch("app.CFG", mock_config):
            pod_spec = {"containers": [{"name": "other", "image": "any"}]}
            assert _pod_spec_has_matching_container(pod_spec) is False

    def test_image_keyword_filter(self, mock_config):
        mock_config.target_container_names = ["app"]
        mock_config.target_image_substr = "myimage"
        with patch("app.CFG", mock_config):
            assert (
                _pod_spec_has_matching_container(
                    {
                        "containers": [{"name": "app", "image": "repo/myimage:1.0"}],
                    }
                )
                is True
            )
            assert (
                _pod_spec_has_matching_container(
                    {
                        "containers": [{"name": "app", "image": "other:1.0"}],
                    }
                )
                is False
            )


class TestObjectMatchesScope:
    def test_object_matches_scope_when_juju_managed_and_container_match_then_true(
        self, mock_config, deployment_with_pod_template
    ):
        mock_config.target_namespaces = []
        mock_config.target_labels = {}
        mock_config.require_juju_managed = True
        mock_config.target_container_names = ["app"]
        with patch("app.CFG", mock_config):
            assert _object_matches_scope(deployment_with_pod_template) is True

    def test_object_matches_scope_when_no_juju_label_then_false(
        self, mock_config, deployment_with_pod_template
    ):
        mock_config.require_juju_managed = True
        mock_config.target_container_names = ["app"]
        deployment_with_pod_template["metadata"]["labels"].pop(
            "app.kubernetes.io/managed-by", None
        )
        with patch("app.CFG", mock_config):
            assert _object_matches_scope(deployment_with_pod_template) is False

    def test_object_matches_scope_when_namespace_not_in_target_then_false(
        self, mock_config, deployment_with_pod_template
    ):
        mock_config.target_namespaces = ["other-ns"]
        mock_config.target_labels = {}
        mock_config.require_juju_managed = False
        mock_config.target_container_names = ["app"]
        with patch("app.CFG", mock_config):
            assert _object_matches_scope(deployment_with_pod_template) is False

    def test_object_matches_scope_when_no_pod_spec_then_false(self, mock_config):
        mock_config.target_namespaces = []
        mock_config.require_juju_managed = False
        mock_config.target_container_names = ["app"]
        with patch("app.CFG", mock_config):
            assert _object_matches_scope({"metadata": {}, "spec": {}}) is False
