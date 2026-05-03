# coding=utf-8

import os
import tempfile
from pathlib import Path

import pytest
import yaml

from trendradar.config import load_config


class TestLoadConfig:
    def _make_config_data(self):
        return {
            "app": {
                "version_check_url": "https://example.com/version",
                "show_version_update": True,
            },
            "crawler": {
                "request_interval": 1000,
                "use_proxy": False,
                "default_proxy": "",
                "enable_crawler": True,
            },
            "report": {
                "mode": "daily",
                "rank_threshold": 5,
                "sort_by_position_first": False,
                "max_news_per_keyword": 0,
                "reverse_content_order": False,
            },
            "notification": {
                "enable_notification": True,
                "message_batch_size": 4000,
                "dingtalk_batch_size": 20000,
                "feishu_batch_size": 29000,
                "bark_batch_size": 3600,
                "slack_batch_size": 4000,
                "batch_send_interval": 3,
                "feishu_message_separator": "---",
                "max_accounts_per_channel": 3,
                "push_window": {
                    "enabled": False,
                    "time_range": {"start": "08:00", "end": "22:00"},
                    "once_per_day": True,
                    "push_record_retention_days": 7,
                },
                "webhooks": {
                    "feishu_url": "",
                    "dingtalk_url": "",
                    "wework_url": "",
                    "wework_msg_type": "markdown",
                    "telegram_bot_token": "",
                    "telegram_chat_id": "",
                    "email_from": "",
                    "email_password": "",
                    "email_to": "",
                    "email_smtp_server": "",
                    "email_smtp_port": "",
                    "ntfy_server_url": "",
                    "ntfy_topic": "",
                    "ntfy_token": "",
                    "bark_url": "",
                    "slack_webhook_url": "",
                },
            },
            "weight": {
                "rank_weight": 0.4,
                "frequency_weight": 0.3,
                "hotness_weight": 0.3,
            },
            "platforms": ["zhihu", "weibo"],
        }

    def test_load_config_success(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text(yaml.safe_dump(self._make_config_data()), encoding="utf-8")
            monkeypatch.setenv("CONFIG_PATH", str(config_path))
            config = load_config()
            assert config["RANK_THRESHOLD"] == 5
            assert config["REQUEST_INTERVAL"] == 1000
            assert config["PLATFORMS"] == ["zhihu", "weibo"]

    def test_load_config_file_not_found(self, monkeypatch):
        monkeypatch.setenv("CONFIG_PATH", "/nonexistent/config.yaml")
        with pytest.raises(FileNotFoundError):
            load_config()

    def test_env_override_report_mode(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text(yaml.safe_dump(self._make_config_data()), encoding="utf-8")
            monkeypatch.setenv("CONFIG_PATH", str(config_path))
            monkeypatch.setenv("REPORT_MODE", "incremental")
            config = load_config()
            assert config["REPORT_MODE"] == "incremental"

    def test_env_override_enable_crawler(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text(yaml.safe_dump(self._make_config_data()), encoding="utf-8")
            monkeypatch.setenv("CONFIG_PATH", str(config_path))
            monkeypatch.setenv("ENABLE_CRAWLER", "false")
            config = load_config()
            assert config["ENABLE_CRAWLER"] is False

    def test_env_override_enable_notification(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text(yaml.safe_dump(self._make_config_data()), encoding="utf-8")
            monkeypatch.setenv("CONFIG_PATH", str(config_path))
            monkeypatch.setenv("ENABLE_NOTIFICATION", "false")
            config = load_config()
            assert config["ENABLE_NOTIFICATION"] is False

    def test_env_override_webhook_url(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text(yaml.safe_dump(self._make_config_data()), encoding="utf-8")
            monkeypatch.setenv("CONFIG_PATH", str(config_path))
            monkeypatch.setenv("FEISHU_WEBHOOK_URL", "https://feishu.env/hook")
            config = load_config()
            assert config["FEISHU_WEBHOOK_URL"] == "https://feishu.env/hook"

    def test_push_window_env_overrides(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text(yaml.safe_dump(self._make_config_data()), encoding="utf-8")
            monkeypatch.setenv("CONFIG_PATH", str(config_path))
            monkeypatch.setenv("PUSH_WINDOW_ENABLED", "true")
            monkeypatch.setenv("PUSH_WINDOW_START", "09:00")
            monkeypatch.setenv("PUSH_WINDOW_END", "18:00")
            monkeypatch.setenv("PUSH_WINDOW_ONCE_PER_DAY", "false")
            config = load_config()
            assert config["PUSH_WINDOW"]["ENABLED"] is True
            assert config["PUSH_WINDOW"]["TIME_RANGE"]["START"] == "09:00"
            assert config["PUSH_WINDOW"]["TIME_RANGE"]["END"] == "18:00"
            assert config["PUSH_WINDOW"]["ONCE_PER_DAY"] is False

    def test_notification_sources_logged(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmpdir:
            data = self._make_config_data()
            data["notification"]["webhooks"]["feishu_url"] = "https://feishu.config/hook"
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text(yaml.safe_dump(data), encoding="utf-8")
            monkeypatch.setenv("CONFIG_PATH", str(config_path))
            config = load_config()
            assert config["FEISHU_WEBHOOK_URL"] == "https://feishu.config/hook"

    def test_ntfy_default_server(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text(yaml.safe_dump(self._make_config_data()), encoding="utf-8")
            monkeypatch.setenv("CONFIG_PATH", str(config_path))
            config = load_config()
            assert config["NTFY_SERVER_URL"] == "https://ntfy.sh"
