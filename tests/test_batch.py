# coding=utf-8

import pytest

from trendradar.notifier.batch import (
    _get_batch_header,
    _get_max_batch_header_size,
    _truncate_to_bytes,
    add_batch_headers,
    split_content_into_batches,
)


class TestGetBatchHeader:
    def test_telegram_header(self):
        result = _get_batch_header("telegram", 1, 5)
        assert "<b>[第 1/5 批次]</b>" in result

    def test_slack_header(self):
        result = _get_batch_header("slack", 2, 5)
        assert "*[第 2/5 批次]*" in result

    def test_wework_text_header(self):
        result = _get_batch_header("wework_text", 1, 3)
        assert "[第 1/3 批次]" in result

    def test_default_header(self):
        result = _get_batch_header("feishu", 1, 2)
        assert "**[第 1/2 批次]**" in result


class TestGetMaxBatchHeaderSize:
    def test_returns_positive_int(self):
        size = _get_max_batch_header_size("telegram")
        assert isinstance(size, int)
        assert size > 0


class TestTruncateToBytes:
    def test_short_text_unchanged(self):
        assert _truncate_to_bytes("hello", 100) == "hello"

    def test_truncates_long_text(self):
        text = "这是一个很长的中文字符串" * 10
        result = _truncate_to_bytes(text, 30)
        assert len(result.encode("utf-8")) <= 30

    def test_does_not_break_multibyte_char(self):
        text = "中文" * 100
        result = _truncate_to_bytes(text, 7)
        # 7 bytes should not break a 3-byte UTF-8 character
        assert len(result.encode("utf-8")) <= 7
        # Should be a valid string
        result.encode("utf-8")


class TestAddBatchHeaders:
    def test_single_batch_no_header(self):
        batches = ["content"]
        result = add_batch_headers(batches, "feishu", 1000)
        assert result == ["content"]

    def test_adds_headers(self):
        batches = ["content1", "content2"]
        result = add_batch_headers(batches, "feishu", 1000)
        assert len(result) == 2
        assert "**[第 1/2 批次]**" in result[0]
        assert "**[第 2/2 批次]**" in result[1]

    def test_truncates_oversized_content(self):
        batches = ["x" * 2000, "y" * 2000]
        result = add_batch_headers(batches, "feishu", 100)
        assert len(result) == 2
        # With headers, content should be truncated to fit
        assert len(result[0].encode("utf-8")) <= 100
        assert len(result[1].encode("utf-8")) <= 100


class TestSplitContentIntoBatches:
    def test_empty_report(self):
        report_data = {"stats": [], "new_titles": [], "failed_ids": []}
        result = split_content_into_batches(report_data, "feishu", mode="daily")
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_incremental_empty(self):
        report_data = {"stats": [], "new_titles": [], "failed_ids": []}
        result = split_content_into_batches(report_data, "feishu", mode="incremental")
        assert isinstance(result, list)
        assert len(result) >= 1
        assert "增量模式" in result[0]
