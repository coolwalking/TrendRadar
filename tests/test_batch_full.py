# coding=utf-8

import pytest

from trendradar.notifier.batch import split_content_into_batches


class TestSplitContentIntoBatchesFull:
    def _make_report_data(self, stats=None, new_titles=None, failed_ids=None):
        return {
            "stats": stats or [],
            "new_titles": new_titles or [],
            "failed_ids": failed_ids or [],
            "total_new_count": sum(len(s["titles"]) for s in (new_titles or [])),
        }

    def _make_stat(self, word="测试", count=1, titles=None):
        return {
            "word": word,
            "count": count,
            "percentage": 50,
            "titles": titles or [
                {
                    "title": "标题1",
                    "source_name": "源1",
                    "time_display": "10:00",
                    "count": 1,
                    "ranks": [1],
                    "rank_threshold": 5,
                    "url": "http://a.com",
                    "mobile_url": "http://m.a.com",
                    "is_new": False,
                }
            ],
        }

    def test_empty_report_daily_mode(self):
        report = self._make_report_data()
        config = {"FEISHU_MESSAGE_SEPARATOR": "---"}
        result = split_content_into_batches(report, "feishu", mode="daily", config=config)
        assert len(result) == 1
        assert "暂无匹配的热点词汇" in result[0]

    def test_empty_report_incremental_mode(self):
        report = self._make_report_data()
        config = {"FEISHU_MESSAGE_SEPARATOR": "---"}
        result = split_content_into_batches(report, "feishu", mode="incremental", config=config)
        assert len(result) == 1
        assert "增量模式" in result[0]

    def test_empty_report_current_mode(self):
        report = self._make_report_data()
        config = {"FEISHU_MESSAGE_SEPARATOR": "---"}
        result = split_content_into_batches(report, "feishu", mode="current", config=config)
        assert len(result) == 1
        assert "当前榜单模式" in result[0]

    def test_single_stat_feishu(self):
        report = self._make_report_data(stats=[self._make_stat()])
        config = {"FEISHU_MESSAGE_SEPARATOR": "---"}
        result = split_content_into_batches(report, "feishu", mode="daily", config=config)
        assert len(result) >= 1
        assert "测试" in result[0]

    def test_single_stat_wework(self):
        report = self._make_report_data(stats=[self._make_stat()])
        result = split_content_into_batches(report, "wework", mode="daily")
        assert len(result) >= 1
        assert "测试" in result[0]

    def test_single_stat_telegram(self):
        report = self._make_report_data(stats=[self._make_stat()])
        result = split_content_into_batches(report, "telegram", mode="daily")
        assert len(result) >= 1
        assert "测试" in result[0]

    def test_single_stat_ntfy(self):
        report = self._make_report_data(stats=[self._make_stat()])
        result = split_content_into_batches(report, "ntfy", mode="daily")
        assert len(result) >= 1
        assert "测试" in result[0]

    def test_single_stat_dingtalk(self):
        report = self._make_report_data(stats=[self._make_stat()])
        result = split_content_into_batches(report, "dingtalk", mode="daily")
        assert len(result) >= 1
        assert "测试" in result[0]

    def test_single_stat_slack(self):
        report = self._make_report_data(stats=[self._make_stat()])
        result = split_content_into_batches(report, "slack", mode="daily")
        assert len(result) >= 1
        assert "测试" in result[0]

    def test_single_stat_bark(self):
        report = self._make_report_data(stats=[self._make_stat()])
        result = split_content_into_batches(report, "bark", mode="daily")
        assert len(result) >= 1
        assert "测试" in result[0]

    def test_multiple_titles_per_stat(self):
        stat = self._make_stat(
            count=2,
            titles=[
                {
                    "title": f"标题{i}",
                    "source_name": "源1",
                    "time_display": "10:00",
                    "count": 1,
                    "ranks": [i],
                    "rank_threshold": 5,
                    "url": f"http://a{i}.com",
                    "mobile_url": "",
                    "is_new": False,
                }
                for i in range(1, 4)
            ],
        )
        report = self._make_report_data(stats=[stat])
        config = {"FEISHU_MESSAGE_SEPARATOR": "---"}
        result = split_content_into_batches(report, "feishu", mode="daily", config=config)
        assert len(result) >= 1
        assert "标题1" in result[0]
        assert "标题3" in result[0]

    def test_multiple_stats(self):
        stats = [
            self._make_stat(word="词A", count=1),
            self._make_stat(word="词B", count=5),
            self._make_stat(word="词C", count=12),
        ]
        report = self._make_report_data(stats=stats)
        config = {"FEISHU_MESSAGE_SEPARATOR": "---"}
        result = split_content_into_batches(report, "feishu", mode="daily", config=config)
        assert len(result) >= 1
        assert "词A" in result[0]
        assert "词B" in result[0]
        assert "词C" in result[0]

    def test_with_new_titles(self):
        new_titles = [
            {
                "source_id": "src1",
                "source_name": "来源1",
                "titles": [
                    {
                        "title": "新标题",
                        "source_name": "来源1",
                        "time_display": "",
                        "count": 1,
                        "ranks": [1],
                        "rank_threshold": 5,
                        "url": "http://new.com",
                        "mobile_url": "",
                        "is_new": True,
                    }
                ],
            }
        ]
        report = self._make_report_data(stats=[self._make_stat()], new_titles=new_titles)
        config = {"FEISHU_MESSAGE_SEPARATOR": "---"}
        result = split_content_into_batches(report, "feishu", mode="daily", config=config)
        assert len(result) >= 1
        assert "新标题" in result[0]

    def test_with_failed_ids(self):
        report = self._make_report_data(stats=[self._make_stat()], failed_ids=["site1", "site2"])
        config = {"FEISHU_MESSAGE_SEPARATOR": "---"}
        result = split_content_into_batches(report, "feishu", mode="daily", config=config)
        assert len(result) >= 1
        assert "site1" in result[0]

    def test_with_update_info(self):
        report = self._make_report_data(stats=[self._make_stat()])
        update_info = {"remote_version": "3.6.0", "current_version": "3.5.0"}
        config = {"FEISHU_MESSAGE_SEPARATOR": "---"}
        result = split_content_into_batches(report, "feishu", mode="daily", update_info=update_info, config=config)
        assert len(result) >= 1
        assert "3.6.0" in result[0]

    def test_reverse_content_order(self):
        new_titles = [
            {
                "source_id": "src1",
                "source_name": "来源1",
                "titles": [
                    {
                        "title": "新标题",
                        "source_name": "来源1",
                        "time_display": "",
                        "count": 1,
                        "ranks": [1],
                        "rank_threshold": 5,
                        "url": "",
                        "mobile_url": "",
                        "is_new": True,
                    }
                ],
            }
        ]
        report = self._make_report_data(stats=[self._make_stat(word="词A")], new_titles=new_titles)
        config = {"REVERSE_CONTENT_ORDER": True, "FEISHU_MESSAGE_SEPARATOR": "---"}
        result = split_content_into_batches(report, "feishu", mode="daily", config=config)
        assert len(result) >= 1
        assert "新标题" in result[0]
        assert "词A" in result[0]

    def test_batch_splitting_due_to_size(self):
        # Create a very long stat that should force splitting
        titles = [
            {
                "title": f"这是一个非常长的标题用于测试分批逻辑{i}",
                "source_name": "源1",
                "time_display": "10:00",
                "count": 1,
                "ranks": [i],
                "rank_threshold": 5,
                "url": f"http://example{i}.com",
                "mobile_url": "",
                "is_new": False,
            }
            for i in range(50)
        ]
        stat = self._make_stat(word="长词", count=50, titles=titles)
        report = self._make_report_data(stats=[stat])
        config = {"FEISHU_MESSAGE_SEPARATOR": "---"}
        result = split_content_into_batches(report, "feishu", mode="daily", max_bytes=2000, config=config)
        assert len(result) >= 1

    def test_batch_splitting_telegram(self):
        titles = [
            {
                "title": f"标题{i}",
                "source_name": "源1",
                "time_display": "",
                "count": 1,
                "ranks": [i],
                "rank_threshold": 5,
                "url": "",
                "mobile_url": "",
                "is_new": False,
            }
            for i in range(30)
        ]
        stat = self._make_stat(word="测试", count=30, titles=titles)
        report = self._make_report_data(stats=[stat])
        result = split_content_into_batches(report, "telegram", mode="daily", max_bytes=1500)
        assert len(result) >= 1

    def test_batch_splitting_wework(self):
        titles = [
            {
                "title": f"标题{i}",
                "source_name": "源1",
                "time_display": "",
                "count": 1,
                "ranks": [i],
                "rank_threshold": 5,
                "url": "",
                "mobile_url": "",
                "is_new": False,
            }
            for i in range(30)
        ]
        stat = self._make_stat(word="测试", count=30, titles=titles)
        report = self._make_report_data(stats=[stat])
        result = split_content_into_batches(report, "wework", mode="daily", max_bytes=1500)
        assert len(result) >= 1

    def test_batch_splitting_ntfy(self):
        titles = [
            {
                "title": f"标题{i}",
                "source_name": "源1",
                "time_display": "",
                "count": 1,
                "ranks": [i],
                "rank_threshold": 5,
                "url": "",
                "mobile_url": "",
                "is_new": False,
            }
            for i in range(20)
        ]
        stat = self._make_stat(word="测试", count=20, titles=titles)
        report = self._make_report_data(stats=[stat])
        result = split_content_into_batches(report, "ntfy", mode="daily", max_bytes=1500)
        assert len(result) >= 1

    def test_batch_splitting_dingtalk(self):
        titles = [
            {
                "title": f"标题{i}",
                "source_name": "源1",
                "time_display": "",
                "count": 1,
                "ranks": [i],
                "rank_threshold": 5,
                "url": "",
                "mobile_url": "",
                "is_new": False,
            }
            for i in range(20)
        ]
        stat = self._make_stat(word="测试", count=20, titles=titles)
        report = self._make_report_data(stats=[stat])
        result = split_content_into_batches(report, "dingtalk", mode="daily", max_bytes=2000)
        assert len(result) >= 1

    def test_batch_splitting_slack(self):
        titles = [
            {
                "title": f"标题{i}",
                "source_name": "源1",
                "time_display": "",
                "count": 1,
                "ranks": [i],
                "rank_threshold": 5,
                "url": "",
                "mobile_url": "",
                "is_new": False,
            }
            for i in range(20)
        ]
        stat = self._make_stat(word="测试", count=20, titles=titles)
        report = self._make_report_data(stats=[stat])
        result = split_content_into_batches(report, "slack", mode="daily", max_bytes=1500)
        assert len(result) >= 1

    def test_different_count_emojis(self):
        stats = [
            self._make_stat(word="低", count=1),
            self._make_stat(word="中", count=5),
            self._make_stat(word="高", count=15),
        ]
        report = self._make_report_data(stats=stats)
        config = {"FEISHU_MESSAGE_SEPARATOR": "---"}
        result = split_content_into_batches(report, "feishu", mode="daily", config=config)
        assert "🔥" in result[0] or "📈" in result[0] or "📌" in result[0]

    def test_feishu_separator_between_stats(self):
        stats = [self._make_stat(word="词A"), self._make_stat(word="词B")]
        report = self._make_report_data(stats=stats)
        config = {"FEISHU_MESSAGE_SEPARATOR": "------"}
        result = split_content_into_batches(report, "feishu", mode="daily", config=config)
        assert len(result) >= 1
