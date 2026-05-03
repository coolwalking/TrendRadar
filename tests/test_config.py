# coding=utf-8

import pytest

from trendradar.config import (
    parse_multi_account_config,
    validate_paired_configs,
    limit_accounts,
    get_account_at_index,
    SMTP_CONFIGS,
)


class TestParseMultiAccountConfig:
    def test_empty_string_returns_empty(self):
        assert parse_multi_account_config("") == []

    def test_whitespace_only_returns_empty(self):
        assert parse_multi_account_config("   ;   ") == []

    def test_single_account(self):
        assert parse_multi_account_config("url1") == ["url1"]

    def test_multiple_accounts(self):
        assert parse_multi_account_config("url1;url2;url3") == ["url1", "url2", "url3"]

    def test_custom_separator(self):
        assert parse_multi_account_config("a,b,c", separator=",") == ["a", "b", "c"]

    def test_strips_whitespace(self):
        assert parse_multi_account_config(" url1 ; url2 ") == ["url1", "url2"]


class TestValidatePairedConfigs:
    def test_empty_configs_returns_true_zero(self):
        valid, count = validate_paired_configs({}, "Test")
        assert valid is True
        assert count == 0

    def test_matching_lengths(self):
        valid, count = validate_paired_configs(
            {"a": ["1", "2"], "b": ["3", "4"]}, "Test"
        )
        assert valid is True
        assert count == 2

    def test_mismatched_lengths(self):
        valid, count = validate_paired_configs(
            {"a": ["1"], "b": ["3", "4"]}, "Test"
        )
        assert valid is False
        assert count == 0

    def test_missing_required_key(self):
        valid, count = validate_paired_configs(
            {"a": ["1"]}, "Test", required_keys=["a", "b"]
        )
        assert valid is True
        assert count == 0


class TestLimitAccounts:
    def test_under_limit_returns_all(self):
        assert limit_accounts(["a", "b"], 5, "Test") == ["a", "b"]

    def test_over_limit_truncates(self):
        assert limit_accounts(["a", "b", "c", "d"], 2, "Test") == ["a", "b"]


class TestGetAccountAtIndex:
    def test_valid_index(self):
        assert get_account_at_index(["a", "b"], 0) == "a"
        assert get_account_at_index(["a", "b"], 1) == "b"

    def test_out_of_range(self):
        assert get_account_at_index(["a"], 5) == ""

    def test_empty_value_returns_default(self):
        assert get_account_at_index(["a", ""], 1, "default") == "default"


class TestSMTPConfigs:
    def test_has_common_providers(self):
        assert "gmail.com" in SMTP_CONFIGS
        assert "qq.com" in SMTP_CONFIGS
        assert "163.com" in SMTP_CONFIGS

    def test_config_structure(self):
        for domain, cfg in SMTP_CONFIGS.items():
            assert "server" in cfg
            assert "port" in cfg
            assert "encryption" in cfg
