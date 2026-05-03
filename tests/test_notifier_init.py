# coding=utf-8

from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

from trendradar.notifier import prepare_report_data, send_to_notifications


class TestPrepareReportData:
    def _make_config(self) -> Dict[str, Any]:
        return {"RANK_THRESHOLD": 5}

    def test_empty_stats(self):
        result = prepare_report_data(self._make_config(), [])
        assert result["stats"] == []
        assert result["new_titles"] == []
        assert result["failed_ids"] == []
        assert result["total_new_count"] == 0

    def test_with_stats(self):
        stats = [
            {
                "word": "测试",
                "count": 2,
                "percentage": 50,
                "titles": [
                    {
                        "title": "标题1",
                        "source_name": "源1",
                        "time_display": "10:00",
                        "count": 1,
                        "ranks": [1],
                        "rank_threshold": 5,
                        "url": "http://a.com",
                        "mobileUrl": "http://m.a.com",
                        "is_new": False,
                    }
                ],
            }
        ]
        result = prepare_report_data(self._make_config(), stats)
        assert len(result["stats"]) == 1
        assert result["stats"][0]["word"] == "测试"
        assert result["stats"][0]["titles"][0]["url"] == "http://a.com"

    def test_skips_zero_count_stats(self):
        stats = [
            {"word": "测试", "count": 0, "titles": []},
            {"word": "有效", "count": 1, "titles": [{"title": "T", "source_name": "S", "time_display": "", "count": 1, "ranks": [1], "rank_threshold": 5}]},
        ]
        result = prepare_report_data(self._make_config(), stats)
        assert len(result["stats"]) == 1
        assert result["stats"][0]["word"] == "有效"

    def test_incremental_mode_hides_new_section(self):
        new_titles = {"src1": {"标题1": {"url": "", "mobileUrl": "", "ranks": [1]}}}
        id_to_name = {"src1": "来源1"}
        result = prepare_report_data(
            self._make_config(), [], new_titles=new_titles, id_to_name=id_to_name, mode="incremental"
        )
        assert result["new_titles"] == []

    def test_daily_mode_with_new_titles(self):
        config = self._make_config()
        with patch("trendradar.notifier.load_frequency_words") as mock_load:
            mock_load.return_value = ({}, {}, [])
            new_titles = {"src1": {"标题1": {"url": "http://a.com", "mobileUrl": "", "ranks": [1]}}}
            id_to_name = {"src1": "来源1"}
            result = prepare_report_data(config, [], new_titles=new_titles, id_to_name=id_to_name, mode="daily")
            assert len(result["new_titles"]) == 1
            assert result["new_titles"][0]["source_name"] == "来源1"
            assert result["total_new_count"] == 1

    def test_with_failed_ids(self):
        result = prepare_report_data(self._make_config(), [], failed_ids=["site1", "site2"])
        assert result["failed_ids"] == ["site1", "site2"]


class TestSendToNotifications:
    def _make_config(self, **overrides: Any) -> Dict[str, Any]:
        base = {
            "MAX_ACCOUNTS_PER_CHANNEL": 3,
            "PUSH_WINDOW": {
                "ENABLED": False,
                "ONCE_PER_DAY": False,
                "TIME_RANGE": {"START": "00:00", "END": "23:59"},
                "RECORD_RETENTION_DAYS": 7,
            },
            "SHOW_VERSION_UPDATE": False,
            "MESSAGE_BATCH_SIZE": 4000,
            "DINGTALK_BATCH_SIZE": 20000,
            "FEISHU_BATCH_SIZE": 29000,
            "BARK_BATCH_SIZE": 3600,
            "SLACK_BATCH_SIZE": 4000,
            "BATCH_SEND_INTERVAL": 0,
            "FEISHU_MESSAGE_SEPARATOR": "---",
            "FEISHU_WEBHOOK_URL": "",
            "DINGTALK_WEBHOOK_URL": "",
            "WEWORK_WEBHOOK_URL": "",
            "WEWORK_MSG_TYPE": "markdown",
            "TELEGRAM_BOT_TOKEN": "",
            "TELEGRAM_CHAT_ID": "",
            "NTFY_SERVER_URL": "",
            "NTFY_TOPIC": "",
            "NTFY_TOKEN": "",
            "BARK_URL": "",
            "SLACK_WEBHOOK_URL": "",
            "EMAIL_FROM": "",
            "EMAIL_PASSWORD": "",
            "EMAIL_TO": "",
            "EMAIL_SMTP_SERVER": "",
            "EMAIL_SMTP_PORT": "",
        }
        base.update(overrides)
        return base

    def test_no_channels_configured(self):
        config = self._make_config()
        result = send_to_notifications(config, [])
        assert result == {}

    @patch("trendradar.notifier.FeishuNotifier")
    def test_feishu_success(self, mock_cls):
        mock_instance = MagicMock()
        mock_instance.send.return_value = True
        mock_cls.return_value = mock_instance

        config = self._make_config(FEISHU_WEBHOOK_URL="https://open.feishu.cn/hook/xxx")
        result = send_to_notifications(config, [])
        assert result.get("feishu") is True
        assert mock_instance.send.called

    @patch("trendradar.notifier.DingTalkNotifier")
    def test_dingtalk_success(self, mock_cls):
        mock_instance = MagicMock()
        mock_instance.send.return_value = True
        mock_cls.return_value = mock_instance

        config = self._make_config(DINGTALK_WEBHOOK_URL="https://oapi.dingtalk.com/robot/send?access_token=xxx")
        result = send_to_notifications(config, [])
        assert result.get("dingtalk") is True

    @patch("trendradar.notifier.WeComNotifier")
    def test_wework_success(self, mock_cls):
        mock_instance = MagicMock()
        mock_instance.send.return_value = True
        mock_cls.return_value = mock_instance

        config = self._make_config(WEWORK_WEBHOOK_URL="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx")
        result = send_to_notifications(config, [])
        assert result.get("wework") is True

    @patch("trendradar.notifier.TelegramNotifier")
    def test_telegram_success(self, mock_cls):
        mock_instance = MagicMock()
        mock_instance.send.return_value = True
        mock_cls.return_value = mock_instance

        config = self._make_config(
            TELEGRAM_BOT_TOKEN="token123",
            TELEGRAM_CHAT_ID="chat456",
        )
        result = send_to_notifications(config, [])
        assert result.get("telegram") is True

    @patch("trendradar.notifier.NtfyNotifier")
    def test_ntfy_success(self, mock_cls):
        mock_instance = MagicMock()
        mock_instance.send.return_value = True
        mock_cls.return_value = mock_instance

        config = self._make_config(
            NTFY_SERVER_URL="https://ntfy.sh",
            NTFY_TOPIC="test-topic",
        )
        result = send_to_notifications(config, [])
        assert result.get("ntfy") is True

    @patch("trendradar.notifier.BarkNotifier")
    def test_bark_success(self, mock_cls):
        mock_instance = MagicMock()
        mock_instance.send.return_value = True
        mock_cls.return_value = mock_instance

        config = self._make_config(BARK_URL="https://api.day.app/testkey")
        result = send_to_notifications(config, [])
        assert result.get("bark") is True

    @patch("trendradar.notifier.SlackNotifier")
    def test_slack_success(self, mock_cls):
        mock_instance = MagicMock()
        mock_instance.send.return_value = True
        mock_cls.return_value = mock_instance

        config = self._make_config(SLACK_WEBHOOK_URL="https://hooks.slack.com/services/xxx")
        result = send_to_notifications(config, [])
        assert result.get("slack") is True

    @patch("trendradar.notifier.EmailNotifier")
    def test_email_success(self, mock_cls):
        mock_instance = MagicMock()
        mock_instance.send.return_value = True
        mock_cls.return_value = mock_instance

        config = self._make_config(
            EMAIL_FROM="from@test.com",
            EMAIL_PASSWORD="pass",
            EMAIL_TO="to@test.com",
        )
        result = send_to_notifications(config, [])
        assert result.get("email") is True

    @patch("trendradar.notifier.PushRecordManager")
    def test_push_window_outside_range(self, mock_mgr_cls):
        mock_mgr = MagicMock()
        mock_mgr.is_in_time_range.return_value = False
        mock_mgr_cls.return_value = mock_mgr

        config = self._make_config(
            PUSH_WINDOW={
                "ENABLED": True,
                "ONCE_PER_DAY": False,
                "TIME_RANGE": {"START": "09:00", "END": "18:00"},
                "RECORD_RETENTION_DAYS": 7,
            }
        )
        result = send_to_notifications(config, [])
        assert result == {}

    @patch("trendradar.notifier.PushRecordManager")
    def test_push_window_once_per_day_already_pushed(self, mock_mgr_cls):
        mock_mgr = MagicMock()
        mock_mgr.is_in_time_range.return_value = True
        mock_mgr.has_pushed_today.return_value = True
        mock_mgr_cls.return_value = mock_mgr

        config = self._make_config(
            PUSH_WINDOW={
                "ENABLED": True,
                "ONCE_PER_DAY": True,
                "TIME_RANGE": {"START": "00:00", "END": "23:59"},
                "RECORD_RETENTION_DAYS": 7,
            }
        )
        result = send_to_notifications(config, [])
        assert result == {}

    @patch("trendradar.notifier.PushRecordManager")
    @patch("trendradar.notifier.FeishuNotifier")
    def test_push_window_records_push(self, mock_feishu, mock_mgr_cls):
        mock_mgr = MagicMock()
        mock_mgr.is_in_time_range.return_value = True
        mock_mgr.has_pushed_today.return_value = False
        mock_mgr_cls.return_value = mock_mgr

        mock_instance = MagicMock()
        mock_instance.send.return_value = True
        mock_feishu.return_value = mock_instance

        config = self._make_config(
            FEISHU_WEBHOOK_URL="https://open.feishu.cn/hook/xxx",
            PUSH_WINDOW={
                "ENABLED": True,
                "ONCE_PER_DAY": True,
                "TIME_RANGE": {"START": "00:00", "END": "23:59"},
                "RECORD_RETENTION_DAYS": 7,
            }
        )
        result = send_to_notifications(config, [])
        assert result.get("feishu") is True
        assert mock_mgr.record_push.called

    @patch("trendradar.notifier.FeishuNotifier")
    def test_multi_account(self, mock_cls):
        mock_instance = MagicMock()
        mock_instance.send.return_value = True
        mock_cls.return_value = mock_instance

        config = self._make_config(FEISHU_WEBHOOK_URL="url1;url2")
        result = send_to_notifications(config, [])
        assert result.get("feishu") is True
        assert mock_instance.send.call_count == 2

    @patch("trendradar.notifier.TelegramNotifier")
    def test_telegram_mismatched_config(self, mock_cls):
        config = self._make_config(
            TELEGRAM_BOT_TOKEN="token1;token2",
            TELEGRAM_CHAT_ID="chat1",
        )
        result = send_to_notifications(config, [])
        assert "telegram" not in result

    @patch("trendradar.notifier.NtfyNotifier")
    def test_ntfy_mismatched_token_topic(self, mock_cls):
        config = self._make_config(
            NTFY_SERVER_URL="https://ntfy.sh",
            NTFY_TOPIC="t1;t2",
            NTFY_TOKEN="tok1",
        )
        result = send_to_notifications(config, [])
        assert "ntfy" not in result
