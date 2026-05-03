# coding=utf-8

import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import pytest

from trendradar.logging_config import get_logger
from trendradar.utils import (
    clean_title,
    html_escape,
    matches_word_groups,
    format_rank_display,
    format_title_for_platform,
    load_frequency_words,
    ensure_directory_exists,
    get_output_path,
)


logger = get_logger(__name__)

class TestCleanTitle:
    def test_removes_newlines(self):
        assert clean_title("hello\nworld") == "hello world"

    def test_removes_carriage_returns(self):
        assert clean_title("hello\rworld") == "hello world"

    def test_collapses_multiple_spaces(self):
        assert clean_title("hello    world") == "hello world"

    def test_strips_whitespace(self):
        assert clean_title("  hello world  ") == "hello world"

    def test_handles_non_string(self):
        assert clean_title(123) == "123"


class TestHtmlEscape:
    def test_escapes_ampersand(self):
        assert html_escape("A & B") == "A &amp; B"

    def test_escapes_less_than(self):
        assert html_escape("A < B") == "A &lt; B"

    def test_escapes_greater_than(self):
        assert html_escape("A > B") == "A &gt; B"

    def test_escapes_quotes(self):
        assert html_escape('"hello"') == "&quot;hello&quot;"
        assert html_escape("'hello'") == "&#x27;hello&#x27;"

    def test_handles_non_string(self):
        assert html_escape(123) == "123"


class TestMatchesWordGroups:
    def test_no_groups_matches_all(self):
        assert matches_word_groups("any title", [], []) is True

    def test_global_filter_blocks(self):
        assert matches_word_groups("hello world", [], [], global_filters=["world"]) is False

    def test_filter_word_blocks(self):
        word_groups = [{"required": [], "normal": ["hello"], "group_key": "hello"}]
        assert matches_word_groups("hello world", word_groups, ["world"]) is False

    def test_required_words_must_all_match(self):
        word_groups = [{"required": ["hello", "world"], "normal": [], "group_key": "hw"}]
        assert matches_word_groups("hello world", word_groups, []) is True
        assert matches_word_groups("hello only", word_groups, []) is False

    def test_normal_words_any_match(self):
        word_groups = [{"required": [], "normal": ["hello", "world"], "group_key": "hw"}]
        assert matches_word_groups("hello there", word_groups, []) is True
        assert matches_word_groups("world cup", word_groups, []) is True
        assert matches_word_groups("neither", word_groups, []) is False

    def test_combined_required_and_normal(self):
        word_groups = [{"required": ["hello"], "normal": ["world"], "group_key": "hw"}]
        assert matches_word_groups("hello world", word_groups, []) is True
        assert matches_word_groups("hello there", word_groups, []) is False

    def test_empty_title_returns_false(self):
        assert matches_word_groups("", [], []) is False
        assert matches_word_groups("   ", [], []) is False

    def test_none_title_returns_false(self):
        assert matches_word_groups(None, [], []) is False


class TestFormatRankDisplay:
    def test_empty_ranks(self):
        assert format_rank_display([], 5, "html") == ""

    def test_single_rank_below_threshold(self):
        assert format_rank_display([3], 5, "html") == "<font color='red'><strong>[3]</strong></font>"

    def test_single_rank_above_threshold(self):
        assert format_rank_display([6], 5, "html") == "[6]"

    def test_range_below_threshold(self):
        assert format_rank_display([3, 4], 5, "html") == "<font color='red'><strong>[3 - 4]</strong></font>"

    def test_range_above_threshold(self):
        assert format_rank_display([6, 7], 5, "html") == "[6 - 7]"

    def test_dingtalk_format(self):
        assert format_rank_display([2], 5, "dingtalk") == "**[2]**"

    def test_telegram_format(self):
        assert format_rank_display([2], 5, "telegram") == "<b>[2]</b>"


class TestFormatTitleForPlatform:
    def _make_title_data(self, **overrides: Any) -> Dict[str, Any]:
        defaults = {
            "title": "Test Title",
            "source_name": "TestSource",
            "ranks": [1],
            "rank_threshold": 5,
            "url": "http://example.com",
            "mobile_url": "http://m.example.com",
            "time_display": "10:00",
            "count": 1,
            "is_new": False,
        }
        defaults.update(overrides)
        return defaults

    def test_feishu_with_source(self):
        data = self._make_title_data()
        result = format_title_for_platform("feishu", data, show_source=True)
        assert "TestSource" in result
        assert "Test Title" in result

    def test_feishu_new_title(self):
        data = self._make_title_data(is_new=True)
        result = format_title_for_platform("feishu", data)
        assert "🆕" in result

    def test_dingtalk_format(self):
        data = self._make_title_data()
        result = format_title_for_platform("dingtalk", data)
        assert "Test Title" in result

    def test_telegram_escapes_html(self):
        data = self._make_title_data(title="A < B")
        result = format_title_for_platform("telegram", data)
        assert "A &lt; B" in result

    def test_html_format(self):
        data = self._make_title_data()
        result = format_title_for_platform("html", data)
        assert "<a href=" in result

    def test_unknown_platform(self):
        data = self._make_title_data()
        result = format_title_for_platform("unknown", data)
        assert result == "Test Title"

    def test_count_greater_than_one(self):
        data = self._make_title_data(count=3)
        result = format_title_for_platform("dingtalk", data)
        assert "(3次)" in result


class TestLoadFrequencyWords:
    def test_load_valid_file(self):
        content = """
hello
world

foo
bar
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write(content)
            path = f.name

        try:
            groups, filters, globals_ = load_frequency_words(path)
            assert len(groups) == 2
            assert groups[0]["group_key"] == "hello world"
            assert groups[1]["group_key"] == "foo bar"
        finally:
            os.unlink(path)

    def test_load_with_required_and_filter(self):
        content = """
+must
hello
!exclude
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write(content)
            path = f.name

        try:
            groups, filters, globals_ = load_frequency_words(path)
            assert len(groups) == 1
            assert groups[0]["required"] == ["must"]
            assert groups[0]["normal"] == ["hello"]
            assert filters == ["exclude"]
        finally:
            os.unlink(path)

    def test_load_with_global_filter(self):
        content = """
[GLOBAL_FILTER]
spam

[WORD_GROUPS]
hello
world
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write(content)
            path = f.name

        try:
            groups, filters, globals_ = load_frequency_words(path)
            assert globals_ == ["spam"]
            assert len(groups) == 1
        finally:
            os.unlink(path)

    def test_load_with_max_count(self):
        content = """
hello
@5
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write(content)
            path = f.name

        try:
            groups, filters, globals_ = load_frequency_words(path)
            assert groups[0]["max_count"] == 5
        finally:
            os.unlink(path)

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_frequency_words("/nonexistent/path.txt")


class TestEnsureDirectoryExists:
    def test_creates_nested_directories(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "a", "b", "c")
            ensure_directory_exists(path)
            assert os.path.isdir(path)


class TestGetOutputPath:
    def test_returns_correct_path(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmpdir:
            monkeypatch.chdir(tmpdir)
            path = get_output_path("txt", "test.txt")
            assert path.endswith("test.txt")
            assert "txt" in path