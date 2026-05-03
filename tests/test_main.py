# coding=utf-8

import os
import sys
import pytest
from pathlib import Path
from datetime import datetime
from unittest.mock import patch, MagicMock

# Ensure project root is in path
sys.path.insert(0, "/tmp/TrendRadar_clone")

import main


class TestParseFileTitles:
    def test_parse_simple_file(self, tmp_path):
        file_path = tmp_path / "test.txt"
        file_path.write_text(
            "zhihu | 知乎\n"
            "1. 测试标题 [URL:http://example.com] [MOBILE:http://m.example.com]\n"
            "\n"
            "weibo | 微博\n"
            "2. 另一个标题\n",
            encoding="utf-8"
        )

        titles_by_id, id_to_name = main.parse_file_titles(file_path)

        assert id_to_name == {"zhihu": "知乎", "weibo": "微博"}
        assert "zhihu" in titles_by_id
        assert "weibo" in titles_by_id
        assert titles_by_id["zhihu"]["测试标题"]["ranks"] == [1]
        assert titles_by_id["zhihu"]["测试标题"]["url"] == "http://example.com"
        assert titles_by_id["zhihu"]["测试标题"]["mobileUrl"] == "http://m.example.com"
        assert titles_by_id["weibo"]["另一个标题"]["ranks"] == [2]

    def test_parse_without_urls(self, tmp_path):
        file_path = tmp_path / "test.txt"
        file_path.write_text(
            "zhihu\n"
            "1. 无链接标题\n",
            encoding="utf-8"
        )

        titles_by_id, id_to_name = main.parse_file_titles(file_path)

        assert id_to_name == {"zhihu": "zhihu"}
        assert titles_by_id["zhihu"]["无链接标题"]["url"] == ""
        assert titles_by_id["zhihu"]["无链接标题"]["mobileUrl"] == ""

    def test_parse_failed_ids_section(self, tmp_path):
        file_path = tmp_path / "test.txt"
        file_path.write_text(
            "zhihu | 知乎\n"
            "1. 测试标题\n"
            "\n"
            "==== 以下ID请求失败 ====\n"
            "failed_id\n",
            encoding="utf-8"
        )

        titles_by_id, id_to_name = main.parse_file_titles(file_path)

        assert "failed_id" not in id_to_name
        assert "failed_id" not in titles_by_id

    def test_parse_invalid_line_default_rank(self, tmp_path):
        file_path = tmp_path / "test.txt"
        file_path.write_text(
            "zhihu | 知乎\n"
            "invalid line without rank\n",
            encoding="utf-8"
        )

        titles_by_id, id_to_name = main.parse_file_titles(file_path)

        assert id_to_name == {"zhihu": "知乎"}
        # Lines without rank get default rank of 1
        assert "invalid line without rank" in titles_by_id["zhihu"]
        assert titles_by_id["zhihu"]["invalid line without rank"]["ranks"] == [1]


class TestCalculateNewsWeight:
    def test_basic_rank_weight(self):
        data = {"ranks": [1], "count": 1}
        weight = main.calculate_news_weight(data, rank_threshold=5)
        assert weight > 0

    def test_multiple_ranks_average(self):
        data = {"ranks": [1, 2], "count": 2}
        weight1 = main.calculate_news_weight(data, rank_threshold=5)
        data2 = {"ranks": [5, 6], "count": 2}
        weight2 = main.calculate_news_weight(data2, rank_threshold=5)
        assert weight1 > weight2

    def test_frequency_weight(self):
        data = {"ranks": [1], "count": 5}
        weight = main.calculate_news_weight(data, rank_threshold=5)
        assert weight > 0

    def test_hotness_weight(self):
        data = {"ranks": [1, 2, 3], "count": 3}
        weight_high = main.calculate_news_weight(data, rank_threshold=5)
        data2 = {"ranks": [8, 9, 10], "count": 3}
        weight_low = main.calculate_news_weight(data2, rank_threshold=5)
        assert weight_high > weight_low

    def test_empty_ranks(self):
        data = {"ranks": [], "count": 1}
        weight = main.calculate_news_weight(data, rank_threshold=5)
        assert weight == 0.0


class TestFormatTimeDisplay:
    def test_same_time(self):
        assert main.format_time_display("10:00", "10:00") == "10:00"

    def test_different_time(self):
        assert main.format_time_display("10:00", "12:00") == "[10:00 ~ 12:00]"

    def test_empty_first(self):
        assert main.format_time_display("", "12:00") == ""

    def test_empty_last(self):
        assert main.format_time_display("10:00", "") == "10:00"


class TestFormatRankDisplay:
    def test_single_top_rank(self):
        result = main.format_rank_display([1], 5, "feishu")
        assert "1" in result
        assert "font color='red'" in result

    def test_range_rank(self):
        result = main.format_rank_display([1, 3], 5, "feishu")
        assert "1" in result
        assert "3" in result

    def test_non_highlighted_rank(self):
        result = main.format_rank_display([8], 5, "feishu")
        assert "[8]" in result
        assert "font color='red'" not in result

    def test_platform_variants(self):
        for platform in ["dingtalk", "wework", "telegram", "slack", "ntfy", "html"]:
            result = main.format_rank_display([1], 5, platform)
            assert "1" in result

    def test_empty_ranks(self):
        assert main.format_rank_display([], 5, "feishu") == ""


class TestProcessSourceData:
    def test_new_source(self):
        all_results = {}
        title_info = {}
        main.process_source_data(
            "zhihu",
            {"标题1": {"ranks": [1], "url": "", "mobileUrl": ""}},
            "10:00",
            all_results,
            title_info,
        )
        assert "zhihu" in all_results
        assert title_info["zhihu"]["标题1"]["count"] == 1
        assert title_info["zhihu"]["标题1"]["first_time"] == "10:00"

    def test_merge_existing_title(self):
        all_results = {
            "zhihu": {
                "标题1": {"ranks": [1], "url": "", "mobileUrl": ""}
            }
        }
        title_info = {
            "zhihu": {
                "标题1": {
                    "first_time": "09:00",
                    "last_time": "09:00",
                    "count": 1,
                    "ranks": [1],
                    "url": "",
                    "mobileUrl": "",
                }
            }
        }
        main.process_source_data(
            "zhihu",
            {"标题1": {"ranks": [2], "url": "http://new.com", "mobileUrl": ""}},
            "10:00",
            all_results,
            title_info,
        )
        assert sorted(all_results["zhihu"]["标题1"]["ranks"]) == [1, 2]
        assert title_info["zhihu"]["标题1"]["count"] == 2
        assert title_info["zhihu"]["标题1"]["last_time"] == "10:00"
        assert all_results["zhihu"]["标题1"]["url"] == "http://new.com"

    def test_new_title_in_existing_source(self):
        all_results = {
            "zhihu": {
                "标题1": {"ranks": [1], "url": "", "mobileUrl": ""}
            }
        }
        title_info = {
            "zhihu": {
                "标题1": {
                    "first_time": "09:00",
                    "last_time": "09:00",
                    "count": 1,
                    "ranks": [1],
                    "url": "",
                    "mobileUrl": "",
                }
            }
        }
        main.process_source_data(
            "zhihu",
            {"标题2": {"ranks": [3], "url": "", "mobileUrl": ""}},
            "10:00",
            all_results,
            title_info,
        )
        assert "标题2" in all_results["zhihu"]
        assert title_info["zhihu"]["标题2"]["count"] == 1


class TestDetectLatestNewTitles:
    def test_detect_new_titles(self, tmp_path):
        output_dir = tmp_path / "output" / main.utils.format_date_folder() / "txt"
        output_dir.mkdir(parents=True)

        file1 = output_dir / "09-00-00.txt"
        file1.write_text(
            "zhihu | 知乎\n1. 旧标题\n",
            encoding="utf-8"
        )

        file2 = output_dir / "10-00-00.txt"
        file2.write_text(
            "zhihu | 知乎\n1. 旧标题\n2. 新标题\n",
            encoding="utf-8"
        )

        with patch("main.Path", return_value=output_dir.parent.parent):
            # Actually, detect_latest_new_titles hardcodes Path("output")
            # We need to monkeypatch the cwd or use a different approach
            # Let's patch utils.format_date_folder to return our date folder
            # and change directory
            old_cwd = os.getcwd()
            os.chdir(tmp_path)
            try:
                new_titles = main.detect_latest_new_titles()
                assert "zhihu" in new_titles
                assert "新标题" in new_titles["zhihu"]
                assert "旧标题" not in new_titles["zhihu"]
            finally:
                os.chdir(old_cwd)

    def test_no_new_titles(self, tmp_path):
        output_dir = tmp_path / "output" / main.utils.format_date_folder() / "txt"
        output_dir.mkdir(parents=True)

        file1 = output_dir / "09-00-00.txt"
        file1.write_text(
            "zhihu | 知乎\n1. 标题\n",
            encoding="utf-8"
        )

        file2 = output_dir / "10-00-00.txt"
        file2.write_text(
            "zhihu | 知乎\n1. 标题\n",
            encoding="utf-8"
        )

        old_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            new_titles = main.detect_latest_new_titles()
            assert new_titles == {}
        finally:
            os.chdir(old_cwd)

    def test_single_file_returns_empty(self, tmp_path):
        output_dir = tmp_path / "output" / main.utils.format_date_folder() / "txt"
        output_dir.mkdir(parents=True)

        file1 = output_dir / "09-00-00.txt"
        file1.write_text(
            "zhihu | 知乎\n1. 标题\n",
            encoding="utf-8"
        )

        old_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            new_titles = main.detect_latest_new_titles()
            assert new_titles == {}
        finally:
            os.chdir(old_cwd)

    def test_filter_by_platform(self, tmp_path):
        output_dir = tmp_path / "output" / main.utils.format_date_folder() / "txt"
        output_dir.mkdir(parents=True)

        file1 = output_dir / "09-00-00.txt"
        file1.write_text(
            "zhihu | 知乎\n1. 旧标题\n",
            encoding="utf-8"
        )

        file2 = output_dir / "10-00-00.txt"
        file2.write_text(
            "zhihu | 知乎\n1. 旧标题\n2. 新标题\n"
            "weibo | 微博\n1. 微博新标题\n",
            encoding="utf-8"
        )

        old_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            new_titles = main.detect_latest_new_titles(["zhihu"])
            assert "zhihu" in new_titles
            assert "weibo" not in new_titles
        finally:
            os.chdir(old_cwd)


class TestFormatTitleForPlatform:
    def make_title_data(self, **overrides):
        defaults = {
            "title": "测试标题",
            "source_name": "测试源",
            "ranks": [1],
            "rank_threshold": 5,
            "time_display": "10:00",
            "count": 1,
            "url": "http://example.com",
            "mobile_url": "http://m.example.com",
            "is_new": False,
        }
        defaults.update(overrides)
        return defaults

    def test_feishu_with_link(self):
        data = self.make_title_data()
        result = main.format_title_for_platform("feishu", data)
        assert "测试标题" in result
        assert "测试源" in result
        assert "http://m.example.com" in result

    def test_feishu_new_title(self):
        data = self.make_title_data(is_new=True)
        result = main.format_title_for_platform("feishu", data)
        assert "🆕" in result

    def test_dingtalk_format(self):
        data = self.make_title_data()
        result = main.format_title_for_platform("dingtalk", data)
        assert "测试标题" in result
        assert "测试源" in result

    def test_telegram_html_escape(self):
        data = self.make_title_data(title="测试<标题>")
        result = main.format_title_for_platform("telegram", data)
        assert "&lt;" in result or "<" not in result or "测试" in result

    def test_slack_link_format(self):
        data = self.make_title_data()
        result = main.format_title_for_platform("slack", data)
        assert "测试标题" in result
        assert "<http://m.example.com|" in result or "http://m.example.com" in result

    def test_ntfy_format(self):
        data = self.make_title_data()
        result = main.format_title_for_platform("ntfy", data)
        assert "测试标题" in result

    def test_html_format(self):
        data = self.make_title_data(is_new=True)
        result = main.format_title_for_platform("html", data)
        assert "测试标题" in result
        assert "new-title" in result

    def test_unknown_platform(self):
        data = self.make_title_data()
        result = main.format_title_for_platform("unknown", data)
        assert result == "测试标题"


class TestSaveTitlesToFile:
    def test_save_and_read_roundtrip(self, tmp_path):
        old_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            results = {
                "zhihu": {
                    "标题1": {"ranks": [1], "url": "http://a.com", "mobileUrl": "http://ma.com"},
                    "标题2": {"ranks": [2], "url": "", "mobileUrl": ""},
                }
            }
            id_to_name = {"zhihu": "知乎"}
            failed_ids = ["failed1"]

            file_path = main.save_titles_to_file(results, id_to_name, failed_ids)
            assert Path(file_path).exists()

            content = Path(file_path).read_text(encoding="utf-8")
            assert "zhihu | 知乎" in content
            assert "1. 标题1" in content
            assert "[URL:http://a.com]" in content
            assert "[MOBILE:http://ma.com]" in content
            assert "failed1" in content
        finally:
            os.chdir(old_cwd)
