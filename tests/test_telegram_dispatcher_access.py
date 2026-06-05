# coding=utf-8

import unittest
import importlib.util
import os
import sys
import types
from datetime import datetime
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import _bootstrap

B = _bootstrap.load_all()
ROOT = B.ROOT
build_telegram_access_config = B.access.build_telegram_access_config


def _ensure_pkg(name):
    if name not in sys.modules:
        mod = types.ModuleType(name)
        mod.__path__ = []
        sys.modules[name] = mod
    return sys.modules[name]


def _load_file(name, relpath):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, relpath))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_ensure_pkg("trendradar.notification")
_load_file("trendradar.core.config", "trendradar/core/config.py")
_load_file("trendradar.ai.alert_state", "trendradar/ai/alert_state.py")

senders_stub = types.ModuleType("trendradar.notification.senders")
for _name in [
    "send_to_bark",
    "send_to_dingtalk",
    "send_to_email",
    "send_to_feishu",
    "send_to_ntfy",
    "send_to_slack",
    "send_to_telegram",
    "send_to_wework",
    "send_to_generic_webhook",
    "send_telegram_document",
]:
    setattr(senders_stub, _name, lambda *args, **kwargs: True)


senders_stub.resolve_report_attachment_path = (
    lambda output_dir, mode, report_kind="full": "path"
)
senders_stub.resolve_attachment_kind_for_event = (
    lambda cfg, event_name: "full"
)


def _should_apply_realtime_alert_gate(report_style, mode, manual_trigger=False):
    return report_style == "environment" and mode in ("current", "incremental") and not manual_trigger


senders_stub.should_apply_realtime_alert_gate = _should_apply_realtime_alert_gate
sys.modules["trendradar.notification.senders"] = senders_stub

DISPATCHER = _load_file("trendradar.notification.dispatcher", "trendradar/notification/dispatcher.py")
NotificationDispatcher = DISPATCHER.NotificationDispatcher
DeferredAlertStateStore = DISPATCHER._DeferredAlertStateStore


class _FakeBackend:
    def __init__(self):
        self.data = {}
        self.save_calls = 0

    def get_alert_state(self):
        return dict(self.data)

    def save_alert_state(self, state):
        self.save_calls += 1
        self.data = dict(state)
        return True


class _FakeAlertStore:
    def __init__(self):
        self.commits = []

    def get(self, key):
        return None

    def commit(self, items, now):
        self.commits.append((items, now))
        return True


class _AIResult:
    report_style = "environment"
    success = True


def _split(*args, **kwargs):
    return ["content"]


def _base_config(**overrides):
    config = {
        "TELEGRAM_BOT_TOKEN": "token",
        "TELEGRAM_CHAT_ID": "",
        "TELEGRAM_OWNER_CHAT_IDS": "",
        "TELEGRAM_RECEIVER_CHAT_IDS": "",
        "TELEGRAM_COMMAND_CHAT_IDS": "",
        "TELEGRAM_ACCESS": build_telegram_access_config({}),
        "MESSAGE_BATCH_SIZE": 4000,
        "BATCH_SEND_INTERVAL": 0,
        "MAX_ACCOUNTS_PER_CHANNEL": 3,
        "ALERT": {
            "ENABLED": True,
            "STATE_TTL_DAYS": 14,
            "COOLDOWN_MINUTES": 180,
        },
    }
    config.update(overrides)
    return config


class TestTelegramDispatcherAccess(unittest.TestCase):
    def _dispatcher(self, config, backend=None):
        return NotificationDispatcher(
            config,
            get_time_func=lambda: datetime(2026, 6, 4, 8, 0, 0),
            split_content_func=_split,
            storage_backend=backend,
        )

    def test_multiple_receivers_are_sent_individually(self):
        access = build_telegram_access_config({"TELEGRAM_RECEIVER_CHAT_IDS": "111,222"})
        config = _base_config(
            TELEGRAM_RECEIVER_CHAT_IDS="111,222",
            TELEGRAM_ACCESS=access,
        )
        dispatcher = self._dispatcher(config)

        calls = []

        def fake_send(**kwargs):
            calls.append(kwargs["chat_id"])
            return True

        with mock.patch("trendradar.notification.dispatcher.send_to_telegram", side_effect=fake_send):
            ok = dispatcher._send_telegram({}, "当前榜单", None, None, "daily")

        self.assertTrue(ok)
        self.assertEqual(calls, ["111", "222"])

    def test_legacy_chat_id_path_still_uses_token_chat_pairs(self):
        access = build_telegram_access_config({"TELEGRAM_CHAT_ID": "111;222"})
        config = _base_config(
            TELEGRAM_BOT_TOKEN="token1;token2",
            TELEGRAM_CHAT_ID="111;222",
            TELEGRAM_ACCESS=access,
        )
        dispatcher = self._dispatcher(config)

        calls = []

        def fake_send(**kwargs):
            calls.append((kwargs["bot_token"], kwargs["chat_id"]))
            return True

        with mock.patch("trendradar.notification.dispatcher.send_to_telegram", side_effect=fake_send):
            ok = dispatcher._send_telegram({}, "当前榜单", None, None, "daily")

        self.assertTrue(ok)
        self.assertEqual(calls, [("token1", "111"), ("token2", "222")])

    def test_command_whitelist_does_not_change_legacy_outbound_pairs(self):
        access = build_telegram_access_config(
            {"TELEGRAM_CHAT_ID": "111;222", "TELEGRAM_COMMAND_CHAT_IDS": "111"}
        )
        config = _base_config(
            TELEGRAM_BOT_TOKEN="token1;token2",
            TELEGRAM_CHAT_ID="111;222",
            TELEGRAM_COMMAND_CHAT_IDS="111",
            TELEGRAM_ACCESS=access,
        )
        dispatcher = self._dispatcher(config)

        calls = []

        def fake_send(**kwargs):
            calls.append((kwargs["bot_token"], kwargs["chat_id"]))
            return True

        with mock.patch("trendradar.notification.dispatcher.send_to_telegram", side_effect=fake_send):
            ok = dispatcher._send_telegram({}, "当前榜单", None, None, "daily")

        self.assertTrue(ok)
        self.assertEqual(calls, [("token1", "111"), ("token2", "222")])

    def test_mixed_legacy_and_receiver_config_warns_and_uses_first_token_fanout(self):
        access = build_telegram_access_config(
            {"TELEGRAM_CHAT_ID": "111;222", "TELEGRAM_RECEIVER_CHAT_IDS": "333"}
        )
        config = _base_config(
            TELEGRAM_BOT_TOKEN="token1;token2",
            TELEGRAM_CHAT_ID="111;222",
            TELEGRAM_RECEIVER_CHAT_IDS="333",
            TELEGRAM_ACCESS=access,
        )
        dispatcher = self._dispatcher(config)

        calls = []

        def fake_send(**kwargs):
            calls.append((kwargs["bot_token"], kwargs["chat_id"]))
            return True

        with mock.patch("trendradar.notification.dispatcher.send_to_telegram", side_effect=fake_send), \
                mock.patch("builtins.print") as print_mock:
            ok = dispatcher._send_telegram({}, "当前榜单", None, None, "daily")

        self.assertTrue(ok)
        self.assertEqual(calls, [("token1", "111"), ("token1", "222"), ("token1", "333")])
        printed = "\n".join(str(call.args[0]) for call in print_mock.call_args_list)
        self.assertIn("第一个 bot_token", printed)
        self.assertIn("同时配置了旧 TELEGRAM_CHAT_ID", printed)

    def test_receiver_failure_does_not_block_later_receivers(self):
        access = build_telegram_access_config({"TELEGRAM_RECEIVER_CHAT_IDS": "111,222,333"})
        config = _base_config(
            TELEGRAM_RECEIVER_CHAT_IDS="111,222,333",
            TELEGRAM_ACCESS=access,
        )
        dispatcher = self._dispatcher(config)

        calls = []

        def fake_send(**kwargs):
            calls.append(kwargs["chat_id"])
            return kwargs["chat_id"] == "222"

        with mock.patch("trendradar.notification.dispatcher.send_to_telegram", side_effect=fake_send):
            ok = dispatcher._send_telegram({}, "当前榜单", None, None, "daily")

        self.assertTrue(ok)
        self.assertEqual(calls, ["111", "222", "333"])

    def test_all_receiver_failures_return_false(self):
        access = build_telegram_access_config({"TELEGRAM_RECEIVER_CHAT_IDS": "111,222"})
        config = _base_config(
            TELEGRAM_RECEIVER_CHAT_IDS="111,222",
            TELEGRAM_ACCESS=access,
        )
        dispatcher = self._dispatcher(config)

        with mock.patch("trendradar.notification.dispatcher.send_to_telegram", return_value=False):
            ok = dispatcher._send_telegram({}, "当前榜单", None, None, "daily")

        self.assertFalse(ok)

    def test_realtime_alert_state_commit_is_deferred_until_after_receiver_loop(self):
        access = build_telegram_access_config({"TELEGRAM_RECEIVER_CHAT_IDS": "111,222"})
        config = _base_config(
            TELEGRAM_RECEIVER_CHAT_IDS="111,222",
            TELEGRAM_ACCESS=access,
        )
        backend = _FakeBackend()
        dispatcher = self._dispatcher(config, backend=backend)
        save_calls_during_send = []

        def fake_send(**kwargs):
            save_calls_during_send.append(backend.save_calls)
            kwargs["alert_state_store"].commit(
                [("cross_layer_verified", {"topic": "AI前沿模型"})],
                datetime(2026, 6, 4, 8, 0, 0),
            )
            return True

        with mock.patch("trendradar.notification.dispatcher.send_to_telegram", side_effect=fake_send):
            ok = dispatcher._send_telegram({}, "当前榜单", None, None, "current", ai_analysis=_AIResult())

        self.assertTrue(ok)
        self.assertEqual(save_calls_during_send, [0, 0])
        self.assertEqual(backend.save_calls, 1)

    def test_deferred_store_empty_flush_returns_false(self):
        store = _FakeAlertStore()
        deferred = DeferredAlertStateStore(store)

        self.assertFalse(deferred.flush())
        self.assertEqual(store.commits, [])

    def test_deferred_store_flush_dedupes_topic_commits(self):
        store = _FakeAlertStore()
        deferred = DeferredAlertStateStore(store)
        now = datetime(2026, 6, 4, 8, 0, 0)

        deferred.commit([("cross_layer_verified", {"topic": "AI前沿模型"})], now)
        deferred.commit([("cross_layer_verified", {"topic": "AI前沿模型"})], now)

        self.assertTrue(deferred.flush())
        self.assertEqual(len(store.commits), 1)
        items, commit_time = store.commits[0]
        self.assertEqual(items, [("cross_layer_verified", {"topic": "AI前沿模型"})])
        self.assertEqual(commit_time, now)


if __name__ == "__main__":
    unittest.main()
