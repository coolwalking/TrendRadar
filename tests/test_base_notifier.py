# coding=utf-8

from typing import Dict, Optional
from unittest.mock import MagicMock, patch

import pytest
import requests

from trendradar.notifier.base import BaseNotifier


class TestNotifier(BaseNotifier):
    @property
    def name(self) -> str:
        return "Test"

    @property
    def batch_size_config_key(self) -> str:
        return "TEST_BATCH_SIZE"

    @property
    def default_batch_size(self) -> int:
        return 1000

    def build_payload(self, batch_content: str, report_data: Dict, report_type: str) -> Dict:
        return {"content": batch_content}

    def is_success(self, response: requests.Response) -> bool:
        return response.status_code == 200

    def get_error_message(self, response: requests.Response) -> str:
        return f"Error {response.status_code}"


class TestBaseNotifier:
    def test_get_batch_size(self):
        notifier = TestNotifier({"TEST_BATCH_SIZE": 5000})
        size = notifier.get_batch_size()
        assert size > 0
        assert size < 5000  # header reserve subtracted

    def test_get_batch_size_fallback(self):
        notifier = TestNotifier({})
        size = notifier.get_batch_size()
        assert size > 0

    def test_get_proxies_with_url(self):
        notifier = TestNotifier({})
        proxies = notifier.get_proxies("http://proxy:8080")
        assert proxies == {"http": "http://proxy:8080", "https": "http://proxy:8080"}

    def test_get_proxies_without_url(self):
        notifier = TestNotifier({})
        assert notifier.get_proxies(None) is None

    def test_format_type(self):
        notifier = TestNotifier({})
        assert notifier.format_type == "test"

    def test_get_headers(self):
        notifier = TestNotifier({})
        assert notifier.get_headers() == {"Content-Type": "application/json"}

    @patch("trendradar.notifier.base.requests.post")
    def test_send_success(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        notifier = TestNotifier({"BATCH_SEND_INTERVAL": 0})
        result = notifier.send(
            "http://example.com",
            {"stats": [], "new_titles": [], "failed_ids": []},
            "测试报告",
        )
        assert result is True
        assert mock_post.called

    @patch("trendradar.notifier.base.requests.post")
    def test_send_failure(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_post.return_value = mock_response

        notifier = TestNotifier({})
        result = notifier.send(
            "http://example.com",
            {"stats": [], "new_titles": [], "failed_ids": []},
            "测试报告",
        )
        assert result is False

    @patch("trendradar.notifier.base.requests.post")
    def test_send_exception(self, mock_post):
        mock_post.side_effect = Exception("connection error")

        notifier = TestNotifier({})
        result = notifier.send(
            "http://example.com",
            {"stats": [], "new_titles": [], "failed_ids": []},
            "测试报告",
        )
        assert result is False
