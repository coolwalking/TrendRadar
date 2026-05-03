# coding=utf-8

import json
import tempfile
from pathlib import Path
from typing import Any, Dict

import pytest

from trendradar.records import PushRecordManager


class TestPushRecordManager:
    def _make_config(self, retention_days: int = 7) -> Dict[str, Any]:
        return {
            "PUSH_WINDOW": {
                "RECORD_RETENTION_DAYS": retention_days,
                "TIME_RANGE": {"START": "09:00", "END": "18:00"},
            }
        }

    def test_ensure_record_dir(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmpdir:
            monkeypatch.chdir(tmpdir)
            mgr = PushRecordManager(self._make_config())
            assert mgr.record_dir.exists()

    def test_has_pushed_today_false(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmpdir:
            monkeypatch.chdir(tmpdir)
            mgr = PushRecordManager(self._make_config())
            assert mgr.has_pushed_today() is False

    def test_record_push_and_check(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmpdir:
            monkeypatch.chdir(tmpdir)
            mgr = PushRecordManager(self._make_config())
            mgr.record_push("当日汇总")
            assert mgr.has_pushed_today() is True

    def test_record_content(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmpdir:
            monkeypatch.chdir(tmpdir)
            mgr = PushRecordManager(self._make_config())
            mgr.record_push("增量更新")
            record_file = mgr.get_today_record_file()
            with open(record_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            assert data["pushed"] is True
            assert data["report_type"] == "增量更新"
            assert "push_time" in data

    def test_cleanup_old_records(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmpdir:
            monkeypatch.chdir(tmpdir)
            mgr = PushRecordManager(self._make_config(retention_days=0))
            old_file = mgr.record_dir / "push_record_20000101.json"
            old_file.write_text('{"pushed": true}')
            mgr.cleanup_old_records()
            assert not old_file.exists()

    def test_is_in_time_range(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmpdir:
            monkeypatch.chdir(tmpdir)
            mgr = PushRecordManager(self._make_config())
            # We can't easily mock the current time, but we can test boundary logic
            assert mgr.is_in_time_range("00:00", "23:59") is True

    def test_normalize_time(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmpdir:
            monkeypatch.chdir(tmpdir)
            mgr = PushRecordManager(self._make_config())
            # Access via public method
            assert mgr.is_in_time_range("9:0", "18:0") is True
