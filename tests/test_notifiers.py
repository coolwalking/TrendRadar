# coding=utf-8

from typing import Dict
from unittest.mock import MagicMock, PropertyMock, patch

import pytest
import requests

from trendradar.notifier.bark import BarkNotifier
from trendradar.notifier.dingtalk import DingTalkNotifier
from trendradar.notifier.feishu import FeishuNotifier
from trendradar.notifier.ntfy import NtfyNotifier
from trendradar.notifier.slack import SlackNotifier
from trendradar.notifier.telegram import TelegramNotifier
from trendradar.notifier.wework import WeComNotifier


class TestNotifierChannels:
    def _make_config(self) -> Dict:
        return {
            "BATCH_SEND_INTERVAL": 0,
            "BARK_BATCH_SIZE": 3600,
            "DINGTALK_BATCH_SIZE": 20000,
            "FEISHU_BATCH_SIZE": 29000,
            "NTFY_BATCH_SIZE": 3800,
            "SLACK_BATCH_SIZE": 4000,
            "TELEGRAM_BATCH_SIZE": 4000,
            "WEWORK_BATCH_SIZE": 4000,
            "MESSAGE_BATCH_SIZE": 4000,
        }

    def _make_report_data(self) -> Dict:
        return {
            "stats": [
                {
                    "word": "测试",
                    "count": 1,
                    "titles": [
                        {
                            "title": "测试标题",
                            "source_name": "测试源",
                            "time_display": "10:00",
                            "count": 1,
                            "ranks": [1],
                            "rank_threshold": 5,
                            "url": "http://example.com",
                            "mobile_url": "",
                            "is_new": False,
                        }
                    ],
                }
            ],
            "new_titles": [],
            "failed_ids": [],
        }

    @patch("trendradar.notifier.bark.requests.post")
    def test_bark_success(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"code": 200}
        mock_post.return_value = mock_response

        notifier = BarkNotifier(self._make_config())
        result = notifier.send(
            "https://api.day.app/testkey",
            self._make_report_data(),
            "测试",
        )
        assert result is True

    @patch("trendradar.notifier.bark.requests.post")
    def test_bark_invalid_url(self, mock_post):
        notifier = BarkNotifier(self._make_config())
        result = notifier.send(
            "https://api.day.app/",
            self._make_report_data(),
            "测试",
        )
        assert result is False

    @patch("trendradar.notifier.dingtalk.requests.post")
    def test_dingtalk_success(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"errcode": 0}
        mock_post.return_value = mock_response

        notifier = DingTalkNotifier(self._make_config())
        result = notifier.send(
            "https://oapi.dingtalk.com/robot/send?access_token=xxx",
            self._make_report_data(),
            "测试",
        )
        assert result is True

    @patch("trendradar.notifier.feishu.requests.post")
    def test_feishu_success(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"code": 0}
        mock_post.return_value = mock_response

        notifier = FeishuNotifier(self._make_config())
        result = notifier.send(
            "https://open.feishu.cn/open-apis/bot/v2/hook/xxx",
            self._make_report_data(),
            "测试",
        )
        assert result is True

    @patch("trendradar.notifier.ntfy.requests.post")
    def test_ntfy_success(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        notifier = NtfyNotifier(self._make_config(), "ntfy.sh", "test-topic")
        result = notifier.send(
            "",
            self._make_report_data(),
            "测试",
        )
        assert result is True

    @patch("trendradar.notifier.slack.requests.post")
    def test_slack_success(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "ok"
        mock_post.return_value = mock_response

        notifier = SlackNotifier(self._make_config())
        result = notifier.send(
            "https://hooks.slack.com/services/xxx",
            self._make_report_data(),
            "测试",
        )
        assert result is True

    @patch("trendradar.notifier.telegram.requests.post")
    def test_telegram_success(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"ok": True}
        mock_post.return_value = mock_response

        notifier = TelegramNotifier(self._make_config(), "token", "chat_id")
        result = notifier.send(
            "",
            self._make_report_data(),
            "测试",
        )
        assert result is True

    @patch("trendradar.notifier.wework.requests.post")
    def test_wework_success(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"errcode": 0}
        mock_post.return_value = mock_response

        notifier = WeComNotifier(self._make_config())
        result = notifier.send(
            "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx",
            self._make_report_data(),
            "测试",
        )
        assert result is True


class TestNotifierProperties:
    def test_bark_properties(self):
        n = BarkNotifier({})
        assert n.name == "Bark"
        assert n.batch_size_config_key == "BARK_BATCH_SIZE"
        assert n.default_batch_size == 3600

    def test_dingtalk_properties(self):
        n = DingTalkNotifier({})
        assert n.name == "钉钉"
        assert n.batch_size_config_key == "DINGTALK_BATCH_SIZE"

    def test_feishu_properties(self):
        n = FeishuNotifier({})
        assert n.name == "飞书"
        assert n.batch_size_config_key == "FEISHU_BATCH_SIZE"

    def test_ntfy_properties(self):
        n = NtfyNotifier({}, "https://ntfy.sh", "topic")
        assert n.name == "ntfy"
        assert n.batch_size_config_key == "NTFY_BATCH_SIZE"

    def test_slack_properties(self):
        n = SlackNotifier({})
        assert n.name == "Slack"
        assert n.batch_size_config_key == "SLACK_BATCH_SIZE"

    def test_telegram_properties(self):
        n = TelegramNotifier({}, "token", "chat_id")
        assert n.name == "Telegram"
        assert n.batch_size_config_key == "MESSAGE_BATCH_SIZE"

    def test_wework_properties(self):
        n = WeComNotifier({})
        assert n.name == "企业微信"
        assert n.batch_size_config_key == "MESSAGE_BATCH_SIZE"


class TestNotifierSendScenarios:
    def _make_config(self) -> Dict:
        return {
            "BATCH_SEND_INTERVAL": 0,
            "BARK_BATCH_SIZE": 3600,
            "NTFY_BATCH_SIZE": 3800,
            "MESSAGE_BATCH_SIZE": 4000,
            "FEISHU_MESSAGE_SEPARATOR": "---",
        }

    def _make_report_data(self) -> Dict:
        return {
            "stats": [
                {
                    "word": "测试",
                    "count": 1,
                    "titles": [
                        {
                            "title": "测试标题",
                            "source_name": "测试源",
                            "time_display": "10:00",
                            "count": 1,
                            "ranks": [1],
                            "rank_threshold": 5,
                            "url": "http://example.com",
                            "mobile_url": "",
                            "is_new": False,
                        }
                    ],
                }
            ],
            "new_titles": [],
            "failed_ids": [],
        }

    @patch("trendradar.notifier.bark.requests.post")
    def test_bark_single_batch_failure(self, mock_post):
        mock_fail = MagicMock()
        mock_fail.status_code = 500
        mock_post.return_value = mock_fail

        notifier = BarkNotifier(self._make_config())
        result = notifier.send(
            "https://api.day.app/testkey",
            self._make_report_data(),
            "测试",
        )
        assert result is False

    @patch("trendradar.notifier.bark.requests.post")
    def test_bark_connect_timeout(self, mock_post):
        mock_post.side_effect = requests.exceptions.ConnectTimeout("timeout")

        notifier = BarkNotifier(self._make_config())
        result = notifier.send(
            "https://api.day.app/testkey",
            self._make_report_data(),
            "测试",
        )
        assert result is False

    @patch("trendradar.notifier.bark.requests.post")
    def test_bark_read_timeout(self, mock_post):
        mock_post.side_effect = requests.exceptions.ReadTimeout("timeout")

        notifier = BarkNotifier(self._make_config())
        result = notifier.send(
            "https://api.day.app/testkey",
            self._make_report_data(),
            "测试",
        )
        assert result is False

    @patch("trendradar.notifier.bark.requests.post")
    def test_bark_connection_error(self, mock_post):
        mock_post.side_effect = requests.exceptions.ConnectionError("conn error")

        notifier = BarkNotifier(self._make_config())
        result = notifier.send(
            "https://api.day.app/testkey",
            self._make_report_data(),
            "测试",
        )
        assert result is False

    @patch("trendradar.notifier.ntfy.requests.post")
    def test_ntfy_rate_limit_retry_success(self, mock_post):
        mock_429 = MagicMock()
        mock_429.status_code = 429
        mock_success = MagicMock()
        mock_success.status_code = 200
        mock_post.side_effect = [mock_429, mock_success]

        notifier = NtfyNotifier(self._make_config(), "ntfy.sh", "topic")
        result = notifier.send(
            "",
            self._make_report_data(),
            "测试",
        )
        assert result is True

    @patch("trendradar.notifier.ntfy.requests.post")
    def test_ntfy_413_payload_too_large(self, mock_post):
        mock_413 = MagicMock()
        mock_413.status_code = 413
        mock_post.return_value = mock_413

        notifier = NtfyNotifier(self._make_config(), "ntfy.sh", "topic")
        result = notifier.send(
            "",
            self._make_report_data(),
            "测试",
        )
        assert result is False

    @patch("trendradar.notifier.ntfy.requests.post")
    def test_ntfy_connect_timeout(self, mock_post):
        mock_post.side_effect = requests.exceptions.ConnectTimeout("timeout")

        notifier = NtfyNotifier(self._make_config(), "ntfy.sh", "topic")
        result = notifier.send(
            "",
            self._make_report_data(),
            "测试",
        )
        assert result is False

    @patch("trendradar.notifier.ntfy.requests.post")
    def test_ntfy_with_token(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        notifier = NtfyNotifier(self._make_config(), "ntfy.sh", "topic", "mytoken")
        result = notifier.send(
            "",
            self._make_report_data(),
            "测试",
        )
        assert result is True
        call_headers = mock_post.call_args[1]["headers"]
        assert call_headers["Authorization"] == "Bearer mytoken"

    @patch("trendradar.notifier.wework.requests.post")
    def test_wework_text_mode(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"errcode": 0}
        mock_post.return_value = mock_response

        notifier = WeComNotifier({"WEWORK_MSG_TYPE": "text"})
        result = notifier.send(
            "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx",
            self._make_report_data(),
            "测试",
        )
        assert result is True
        payload = mock_post.call_args[1]["json"]
        assert payload["msgtype"] == "text"

    @patch("trendradar.notifier.wework.requests.post")
    def test_wework_markdown_mode(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"errcode": 0}
        mock_post.return_value = mock_response

        notifier = WeComNotifier({"WEWORK_MSG_TYPE": "markdown"})
        result = notifier.send(
            "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx",
            self._make_report_data(),
            "测试",
        )
        assert result is True
        payload = mock_post.call_args[1]["json"]
        assert payload["msgtype"] == "markdown"

    def test_wework_strip_markdown(self):
        from trendradar.notifier.wework import _strip_markdown

        text = "**bold** _italic_ [link](http://a.com) `code` \n\n\n extra"
        result = _strip_markdown(text)
        assert "**" not in result
        assert "`" not in result
        assert "link http://a.com" in result


class TestBarkEdgeCases:
    def test_is_success_json_exception(self):
        notifier = BarkNotifier({})
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.side_effect = ValueError("bad json")
        assert notifier.is_success(mock_response) is False

    def test_get_error_message_json_exception(self):
        notifier = BarkNotifier({})
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.side_effect = ValueError("bad json")
        assert notifier.get_error_message(mock_response) == "未知错误"

    @patch("trendradar.notifier.bark.requests.post")
    def test_bark_response_text_exception(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = PropertyMock(side_effect=RuntimeError("text error"))
        mock_post.return_value = mock_response

        notifier = BarkNotifier({"BATCH_SEND_INTERVAL": 0})
        result = notifier.send(
            "https://api.day.app/testkey",
            {"stats": [], "new_titles": [], "failed_ids": []},
            "测试",
        )
        assert result is False

    @patch("trendradar.notifier.bark.requests.post")
    def test_bark_generic_exception(self, mock_post):
        mock_post.side_effect = RuntimeError("unexpected")

        notifier = BarkNotifier({"BATCH_SEND_INTERVAL": 0})
        result = notifier.send(
            "https://api.day.app/testkey",
            {"stats": [], "new_titles": [], "failed_ids": []},
            "测试",
        )
        assert result is False


class TestNtfyEdgeCases:
    @patch("trendradar.notifier.ntfy.requests.post")
    def test_ntfy_connect_timeout(self, mock_post):
        mock_post.side_effect = requests.exceptions.ConnectTimeout("timeout")

        notifier = NtfyNotifier({"BATCH_SEND_INTERVAL": 0}, "ntfy.sh", "topic")
        result = notifier.send(
            "",
            {"stats": [], "new_titles": [], "failed_ids": []},
            "测试",
        )
        assert result is False

    @patch("trendradar.notifier.ntfy.requests.post")
    def test_ntfy_read_timeout(self, mock_post):
        mock_post.side_effect = requests.exceptions.ReadTimeout("timeout")

        notifier = NtfyNotifier({"BATCH_SEND_INTERVAL": 0}, "ntfy.sh", "topic")
        result = notifier.send(
            "",
            {"stats": [], "new_titles": [], "failed_ids": []},
            "测试",
        )
        assert result is False

    @patch("trendradar.notifier.ntfy.requests.post")
    def test_ntfy_connection_error(self, mock_post):
        mock_post.side_effect = requests.exceptions.ConnectionError("conn error")

        notifier = NtfyNotifier({"BATCH_SEND_INTERVAL": 0}, "ntfy.sh", "topic")
        result = notifier.send(
            "",
            {"stats": [], "new_titles": [], "failed_ids": []},
            "测试",
        )
        assert result is False

    @patch("trendradar.notifier.ntfy.requests.post")
    def test_ntfy_response_text_exception(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 500
        type(mock_response).text = PropertyMock(side_effect=RuntimeError("text error"))
        mock_post.return_value = mock_response

        notifier = NtfyNotifier({"BATCH_SEND_INTERVAL": 0}, "ntfy.sh", "topic")
        result = notifier.send(
            "",
            {"stats": [], "new_titles": [], "failed_ids": []},
            "测试",
        )
        assert result is False

    @patch("trendradar.notifier.ntfy.requests.post")
    def test_ntfy_generic_exception(self, mock_post):
        mock_post.side_effect = RuntimeError("unexpected")

        notifier = NtfyNotifier({"BATCH_SEND_INTERVAL": 0}, "ntfy.sh", "topic")
        result = notifier.send(
            "",
            {"stats": [], "new_titles": [], "failed_ids": []},
            "测试",
        )
        assert result is False

    @patch("trendradar.notifier.ntfy.requests.post")
    def test_ntfy_partial_success(self, mock_post):
        # Need 2 batches: first succeeds, second fails
        mock_success = MagicMock()
        mock_success.status_code = 200
        mock_fail = MagicMock()
        mock_fail.status_code = 500
        # With tiny max_bytes, we should get multiple batches
        mock_post.side_effect = [mock_success, mock_fail]

        config = {"NTFY_BATCH_SIZE": 100, "BATCH_SEND_INTERVAL": 0}
        notifier = NtfyNotifier(config, "ntfy.sh", "topic")
        report_data = {
            "stats": [
                {
                    "word": "测试词A",
                    "count": 1,
                    "titles": [
                        {"title": "标题1", "source_name": "源1", "time_display": "", "count": 1, "ranks": [1], "rank_threshold": 5, "url": "", "mobile_url": ""}
                    ],
                },
                {
                    "word": "测试词B",
                    "count": 1,
                    "titles": [
                        {"title": "标题2", "source_name": "源2", "time_display": "", "count": 1, "ranks": [1], "rank_threshold": 5, "url": "", "mobile_url": ""}
                    ],
                },
            ],
            "new_titles": [],
            "failed_ids": [],
        }
        result = notifier.send("", report_data, "测试")
        assert result is True
