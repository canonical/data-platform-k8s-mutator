# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for helper functions (_is_true, _parse_label_selector)."""

import pytest
from app import _is_true, _parse_label_selector


class TestIsTrue:
    def test_none_returns_default(self):
        assert _is_true(None, default=True) is True
        assert _is_true(None, default=False) is False

    def test_truthy_strings(self):
        for s in ("1", "true", "yes", "y", "on", "TRUE", "  true  "):
            assert _is_true(s) is True

    def test_falsy_strings(self):
        for s in ("0", "false", "no", "n", "off", "", "foo"):
            assert _is_true(s) is False


class TestParseLabelSelector:
    def test_empty_or_whitespace(self):
        assert _parse_label_selector("") == {}
        assert _parse_label_selector("  ") == {}
        assert _parse_label_selector(",") == {}

    def test_single_pair(self):
        assert _parse_label_selector("k1=v1") == {"k1": "v1"}

    def test_multiple_pairs(self):
        assert _parse_label_selector("k1=v1,k2=v2") == {"k1": "v1", "k2": "v2"}

    def test_strips_whitespace(self):
        assert _parse_label_selector(" k1 = v1 ") == {"k1": "v1"}

    def test_value_with_equals(self):
        key, _, val = "key=value=extra".partition("=")
        assert key == "key"
        assert val == "value=extra"
        assert _parse_label_selector("key=value=extra") == {"key": "value=extra"}

    def test_invalid_no_equals(self):
        with pytest.raises(ValueError, match="expected key=value"):
            _parse_label_selector("noequals")

    def test_invalid_empty_key(self):
        with pytest.raises(ValueError, match="empty key"):
            _parse_label_selector("=value")
