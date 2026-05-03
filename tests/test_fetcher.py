# coding=utf-8

import json
from unittest.mock import MagicMock, patch

import pytest

from trendradar.fetcher import DataFetcher


class TestDataFetcher:
    def test_init_defaults(self):
        fetcher = DataFetcher()
        assert fetcher.request_interval == 1000
        assert fetcher.proxy_url is None

    def test_init_with_proxy(self):
        fetcher = DataFetcher(proxy_url="http://proxy:8080")
        assert fetcher.proxy_url == "http://proxy:8080"

    @patch("trendradar.fetcher.requests.get")
    def test_fetch_data_success(self, mock_get):
        mock_response = MagicMock()
        mock_response.text = '{"status": "success", "items": []}'
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        fetcher = DataFetcher()
        result, id_val, alias = fetcher.fetch_data("test_id")
        assert result is not None
        assert id_val == "test_id"
        assert alias == "test_id"

    @patch("trendradar.fetcher.requests.get")
    def test_fetch_data_with_alias(self, mock_get):
        mock_response = MagicMock()
        mock_response.text = '{"status": "success", "items": []}'
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        fetcher = DataFetcher()
        result, id_val, alias = fetcher.fetch_data(("test_id", "alias_name"))
        assert id_val == "test_id"
        assert alias == "alias_name"

    @patch("trendradar.fetcher.requests.get")
    def test_fetch_data_failure(self, mock_get):
        mock_get.side_effect = Exception("network error")

        fetcher = DataFetcher()
        result, id_val, alias = fetcher.fetch_data("test_id", max_retries=0)
        assert result is None

    @patch("trendradar.fetcher.requests.get")
    def test_crawl_websites_success(self, mock_get):
        mock_response = MagicMock()
        mock_response.text = json.dumps(
            {
                "status": "success",
                "items": [
                    {"title": "News 1", "url": "http://a.com", "mobileUrl": "http://m.a.com"},
                    {"title": "News 2", "url": "http://b.com", "mobileUrl": "http://m.b.com"},
                ],
            }
        )
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        fetcher = DataFetcher()
        results, id_to_name, failed_ids = fetcher.crawl_websites(["zhihu"])

        assert "zhihu" in results
        assert len(results["zhihu"]) == 2
        assert "News 1" in results["zhihu"]
        assert results["zhihu"]["News 1"]["ranks"] == [1]
        assert failed_ids == []

    @patch("trendradar.fetcher.requests.get")
    def test_crawl_websites_skips_invalid_titles(self, mock_get):
        mock_response = MagicMock()
        mock_response.text = json.dumps(
            {
                "status": "success",
                "items": [
                    {"title": None, "url": "", "mobileUrl": ""},
                    {"title": "   ", "url": "", "mobileUrl": ""},
                    {"title": 3.14, "url": "", "mobileUrl": ""},
                    {"title": "Valid Title", "url": "", "mobileUrl": ""},
                ],
            }
        )
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        fetcher = DataFetcher()
        results, _, failed_ids = fetcher.crawl_websites(["site1"])

        assert len(results["site1"]) == 1
        assert "Valid Title" in results["site1"]
        assert failed_ids == []

    @patch("trendradar.fetcher.requests.get")
    def test_crawl_websites_merge_duplicate_ranks(self, mock_get):
        mock_response = MagicMock()
        mock_response.text = json.dumps(
            {
                "status": "success",
                "items": [
                    {"title": "Same News", "url": "", "mobileUrl": ""},
                    {"title": "Same News", "url": "", "mobileUrl": ""},
                ],
            }
        )
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        fetcher = DataFetcher()
        results, _, _ = fetcher.crawl_websites(["site1"])

        assert results["site1"]["Same News"]["ranks"] == [1, 2]

    @patch("trendradar.fetcher.requests.get")
    def test_crawl_websites_failed_request(self, mock_get):
        mock_get.side_effect = Exception("timeout")

        fetcher = DataFetcher()
        results, id_to_name, failed_ids = fetcher.crawl_websites(["site1"])

        assert "site1" in id_to_name
        assert "site1" not in results
        assert failed_ids == ["site1"]

    @patch("trendradar.fetcher.requests.get")
    def test_crawl_websites_json_decode_error(self, mock_get):
        mock_response = MagicMock()
        mock_response.text = "not json"
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        fetcher = DataFetcher()
        results, _, failed_ids = fetcher.crawl_websites(["site1"])

        assert failed_ids == ["site1"]
