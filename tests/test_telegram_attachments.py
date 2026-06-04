# coding=utf-8
"""Telegram HTML 报告附件测试。

覆盖：
  A. send_telegram_document 底层函数（成功 / 文件缺失 / 过大 / API 失败 / 网络异常 / 不限大小）
  B. resolve_report_attachment_path 的 group 映射
  C. dispatcher fan-out 接入（开关 / attach_on 闸 / 多 receiver / 失败不影响文本 /
     realtime 静默不附 / realtime 真实推送才附 / cooldown 不受影响 / flush 后才发附件）
  D. loader._load_telegram_attachments_config 默认值与归一化
"""

import importlib.util
import os
import sys
import tempfile
import types
import unittest
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


# 加载真实 senders + dispatcher（真实接线），不 stub senders。
_ensure_pkg("trendradar.notification")
_load_file("trendradar.core.config", "trendradar/core/config.py")
_load_file("trendradar.ai.alert_state", "trendradar/ai/alert_state.py")
_load_file("trendradar.notification.batch", "trendradar/notification/batch.py")
_load_file("trendradar.notification.formatters", "trendradar/notification/formatters.py")
SENDERS = _load_file("trendradar.notification.senders", "trendradar/notification/senders.py")
DISPATCHER = _load_file("trendradar.notification.dispatcher", "trendradar/notification/dispatcher.py")
NotificationDispatcher = DISPATCHER.NotificationDispatcher

# loader：按需 stub 缺失的可选依赖（slim 解释器无 yaml/pytz；完整环境则保留真实包）。
try:  # pragma: no cover - 依赖环境
    import yaml  # noqa: F401
except ImportError:
    sys.modules.setdefault("yaml", types.ModuleType("yaml"))
try:  # pragma: no cover - 依赖环境
    import pytz  # noqa: F401
except ImportError:
    sys.modules.setdefault("pytz", types.ModuleType("pytz"))
_ensure_pkg("trendradar.utils")
_load_file("trendradar.utils.time", "trendradar/utils/time.py")
LOADER = _load_file("trendradar.core.loader", "trendradar/core/loader.py")


def _fake_response(status_code=200, ok=True):
    resp = mock.Mock()
    resp.status_code = status_code
    resp.json.return_value = {"ok": ok, "description": "boom"}
    return resp


# ── A. send_telegram_document ───────────────────────────────────────────────


class TestSendTelegramDocument(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "full.html")
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("<html>x</html>")

    def tearDown(self):
        self.tmp.cleanup()

    def test_success_sends_document_multipart(self):
        with mock.patch.object(SENDERS, "requests") as req:
            req.post.return_value = _fake_response(200, ok=True)
            ok = SENDERS.send_telegram_document(
                "tok", "123", self.path, filename="trendradar-current.html"
            )
        self.assertTrue(ok)
        self.assertEqual(req.post.call_count, 1)
        _, kwargs = req.post.call_args
        self.assertEqual(kwargs["data"]["chat_id"], "123")
        self.assertIn("document", kwargs["files"])
        self.assertEqual(kwargs["files"]["document"][0], "trendradar-current.html")
        self.assertIn("/sendDocument", req.post.call_args[0][0])

    def test_missing_file_returns_false_without_request(self):
        with mock.patch.object(SENDERS, "requests") as req:
            ok = SENDERS.send_telegram_document(
                "tok", "123", os.path.join(self.tmp.name, "nope.html")
            )
        self.assertFalse(ok)
        req.post.assert_not_called()

    def test_oversize_file_skipped_without_request(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("x" * 2000)
        with mock.patch.object(SENDERS, "requests") as req:
            ok = SENDERS.send_telegram_document(
                "tok", "123", self.path, max_file_mb=0.0005  # limit ≈ 524 bytes
            )
        self.assertFalse(ok)
        req.post.assert_not_called()

    def test_api_not_ok_returns_false(self):
        with mock.patch.object(SENDERS, "requests") as req:
            req.post.return_value = _fake_response(200, ok=False)
            ok = SENDERS.send_telegram_document("tok", "123", self.path)
        self.assertFalse(ok)

    def test_network_exception_returns_false(self):
        with mock.patch.object(SENDERS, "requests") as req:
            req.post.side_effect = RuntimeError("net down")
            ok = SENDERS.send_telegram_document("tok", "123", self.path)
        self.assertFalse(ok)

    def test_max_file_mb_zero_disables_size_check(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("x" * 5000)
        with mock.patch.object(SENDERS, "requests") as req:
            req.post.return_value = _fake_response(200, ok=True)
            ok = SENDERS.send_telegram_document("tok", "123", self.path, max_file_mb=0)
        self.assertTrue(ok)
        req.post.assert_called_once()


# ── B. resolve_report_attachment_path ───────────────────────────────────────


class TestResolveAttachmentPath(unittest.TestCase):
    def test_group_mapping(self):
        join = os.path.join
        self.assertEqual(
            SENDERS.resolve_report_attachment_path("output", "daily"),
            join("output", "public", "daily", "full.html"),
        )
        for mode in ("current", "incremental"):
            self.assertEqual(
                SENDERS.resolve_report_attachment_path("output", mode),
                join("output", "public", "current", "full.html"),
            )

    def test_report_kind_other_falls_back_to_full(self):
        # 即便传入非 full，也只会拼出 full.html（绝不暴露 state.json/index.html）
        self.assertTrue(
            SENDERS.resolve_report_attachment_path("output", "daily", "state").endswith(
                os.path.join("daily", "full.html")
            )
        )


# ── C. dispatcher 接入 ──────────────────────────────────────────────────────


class _Backend:
    def __init__(self):
        self.data = {}

    def get_alert_state(self):
        return dict(self.data)

    def save_alert_state(self, state):
        self.data = dict(state)
        return True


class _AI:
    def __init__(self, report_style="environment"):
        self.report_style = report_style
        self.success = True


def _split(*args, **kwargs):
    return ["content"]


def _attach_cfg(**overrides):
    cfg = {
        "ENABLED": True,
        "ATTACH_ON": ["realtime_alert", "daily_digest"],
        "REPORT_KIND": "full",
        "MAX_FILE_MB": 8,
        "FAILURE_BEHAVIOR": "warn",
    }
    cfg.update(overrides)
    return cfg


def _config(receivers="111,222", attachments=None):
    return {
        "TELEGRAM_BOT_TOKEN": "token",
        "TELEGRAM_CHAT_ID": "",
        "TELEGRAM_OWNER_CHAT_IDS": "",
        "TELEGRAM_RECEIVER_CHAT_IDS": receivers,
        "TELEGRAM_ACCESS": build_telegram_access_config(
            {"TELEGRAM_RECEIVER_CHAT_IDS": receivers}
        ),
        "MESSAGE_BATCH_SIZE": 4000,
        "BATCH_SEND_INTERVAL": 0,
        "MAX_ACCOUNTS_PER_CHANNEL": 3,
        "ALERT": {"ENABLED": True, "STATE_TTL_DAYS": 14, "COOLDOWN_MINUTES": 180},
        "TELEGRAM_ATTACHMENTS": attachments if attachments is not None else _attach_cfg(),
    }


class TestDispatcherAttachments(unittest.TestCase):
    def _dispatcher(self, config, backend=None):
        return NotificationDispatcher(
            config,
            get_time_func=lambda: datetime(2026, 6, 4, 8, 0, 0),
            split_content_func=_split,
            storage_backend=backend,
            attachment_output_dir="output",
        )

    def _send_realtime(self, dispatcher, ai, mode="current"):
        return dispatcher._send_telegram(
            report_data={"stats": [], "failed_ids": [], "new_titles": [], "id_to_name": {}},
            report_type="当前榜单",
            update_info=None,
            proxy_url=None,
            mode=mode,
            ai_analysis=ai,
        )

    def _fake_text_send(self, *, commit=True, fail_chat=None):
        """模拟 send_to_telegram：可选择对某 chat 失败；真实推送时 commit 到 alert store。"""
        def fake(**kwargs):
            chat_id = kwargs.get("chat_id")
            if fail_chat is not None and chat_id == fail_chat:
                return False
            if commit:
                store = kwargs.get("alert_state_store")
                if store is not None:
                    now = kwargs["get_time_func"]() if kwargs.get("get_time_func") else None
                    store.commit([("cross_layer_verified", {"topic": f"t-{chat_id}"})], now)
            return True
        return fake

    def test_disabled_never_sends_attachment(self):
        cfg = _config(attachments=_attach_cfg(ENABLED=False))
        d = self._dispatcher(cfg, backend=_Backend())
        with mock.patch.object(DISPATCHER, "send_to_telegram", side_effect=self._fake_text_send()), \
             mock.patch.object(DISPATCHER, "send_telegram_document") as doc:
            ok = self._send_realtime(d, _AI())
        self.assertTrue(ok)
        doc.assert_not_called()

    def test_attach_on_filter_excludes_realtime(self):
        cfg = _config(attachments=_attach_cfg(ATTACH_ON=["daily_digest"]))
        d = self._dispatcher(cfg, backend=_Backend())
        with mock.patch.object(DISPATCHER, "send_to_telegram", side_effect=self._fake_text_send()), \
             mock.patch.object(DISPATCHER, "send_telegram_document") as doc:
            self._send_realtime(d, _AI())
        doc.assert_not_called()

    def test_classic_style_never_attaches(self):
        cfg = _config()
        d = self._dispatcher(cfg, backend=_Backend())
        with mock.patch.object(DISPATCHER, "send_to_telegram", side_effect=self._fake_text_send()), \
             mock.patch.object(DISPATCHER, "send_telegram_document") as doc:
            self._send_realtime(d, _AI(report_style="classic"))
        doc.assert_not_called()

    def test_realtime_current_no_longer_attaches(self):
        # 行为变更：current/incremental 改为轻量 dashboard、不再生成 full.html，
        # realtime_alert 的 HTML 附件已停用——即使真实推送（commit）也不附件。
        cfg = _config(receivers="111,222")
        d = self._dispatcher(cfg, backend=_Backend())
        with mock.patch.object(DISPATCHER, "send_to_telegram", side_effect=self._fake_text_send(commit=True)), \
             mock.patch.object(DISPATCHER, "send_telegram_document") as doc:
            ok = self._send_realtime(d, _AI())
        self.assertTrue(ok)
        doc.assert_not_called()

    def test_realtime_silent_round_does_not_attach(self):
        # 文本返回成功但未 commit（冷却/无候选静默成功）→ had_realtime_alert_items=False
        cfg = _config()
        d = self._dispatcher(cfg, backend=_Backend())
        with mock.patch.object(DISPATCHER, "send_to_telegram", side_effect=self._fake_text_send(commit=False)), \
             mock.patch.object(DISPATCHER, "send_telegram_document") as doc:
            ok = self._send_realtime(d, _AI())
        self.assertTrue(ok)
        doc.assert_not_called()

    def test_only_text_success_receivers_get_attachment(self):
        # daily_digest 附件：仅文本发送成功的 receiver 获得附件（daily 路径，current 已停用附件）。
        cfg = _config(receivers="111,222")
        d = self._dispatcher(cfg, backend=_Backend())
        fake = self._fake_text_send(commit=True, fail_chat="222")
        with mock.patch.object(DISPATCHER, "send_to_telegram", side_effect=fake), \
             mock.patch.object(DISPATCHER, "send_telegram_document", return_value=True) as doc:
            self._send_realtime(d, _AI(), mode="daily")
        self.assertEqual(doc.call_count, 1)
        self.assertEqual(doc.call_args_list[0].args[1], "111")
        self.assertEqual(doc.call_args_list[0].kwargs["filename"], "trendradar-daily-20260604-0800.html")

    def test_attachment_failure_does_not_fail_text_round(self):
        # daily_digest 附件失败不影响文本推送结果（current 已停用附件）。
        cfg = _config()
        d = self._dispatcher(cfg, backend=_Backend())
        with mock.patch.object(DISPATCHER, "send_to_telegram", side_effect=self._fake_text_send()), \
             mock.patch.object(DISPATCHER, "send_telegram_document", return_value=False):
            ok = self._send_realtime(d, _AI(), mode="daily")
        self.assertTrue(ok)

    def test_daily_digest_attaches_without_alert_store(self):
        cfg = _config()
        d = self._dispatcher(cfg, backend=_Backend())
        # daily 不走 realtime gate：deferred_store 为 None，但 daily_digest 仍可附
        with mock.patch.object(DISPATCHER, "send_to_telegram", side_effect=self._fake_text_send(commit=False)), \
             mock.patch.object(DISPATCHER, "send_telegram_document", return_value=True) as doc:
            d._send_telegram(
                report_data={"stats": [], "failed_ids": [], "new_titles": [], "id_to_name": {}},
                report_type="全天汇总",
                update_info=None,
                proxy_url=None,
                mode="daily",
                ai_analysis=_AI(),
            )
        self.assertEqual(doc.call_count, 2)
        for c in doc.call_args_list:
            self.assertEqual(c.kwargs["filename"], "trendradar-daily-20260604-0800.html")

    def test_attachments_do_not_add_cooldown_commits_and_run_after_flush(self):
        cfg = _config(receivers="111,222")
        backend = _Backend()
        d = self._dispatcher(cfg, backend=backend)

        events = []
        orig_flush = DISPATCHER._DeferredAlertStateStore.flush

        def spy_flush(self):
            events.append(("flush",))
            return orig_flush(self)

        def fake_text(**kwargs):
            chat_id = kwargs.get("chat_id")
            events.append(("text", chat_id))
            store = kwargs.get("alert_state_store")
            if store is not None:
                now = kwargs["get_time_func"]()
                store.commit([("cross_layer_verified", {"topic": f"t-{chat_id}"})], now)
            return True

        def fake_doc(token, chat_id, path, **kwargs):
            events.append(("doc", chat_id))
            return True

        with mock.patch.object(DISPATCHER._DeferredAlertStateStore, "flush", spy_flush), \
             mock.patch.object(DISPATCHER, "send_to_telegram", side_effect=fake_text), \
             mock.patch.object(DISPATCHER, "send_telegram_document", side_effect=fake_doc):
            self._send_realtime(d, _AI())

        # current 不再附件：文本两条 + 一次 flush，flush 之后没有 doc 事件。
        self.assertEqual(
            events,
            [("text", "111"), ("text", "222"), ("flush",)],
        )
        # 附件阶段没有产生额外的 cooldown commit：底层 store 只被两条文本各 commit 一次。
        flush_idx = events.index(("flush",))
        self.assertEqual([e for e in events[flush_idx:] if e[0] == "text"], [])


# ── D. loader ───────────────────────────────────────────────────────────────


class TestLoaderAttachmentsConfig(unittest.TestCase):
    def test_defaults(self):
        cfg = LOADER._load_telegram_attachments_config({})
        self.assertEqual(cfg["ENABLED"], False)
        self.assertEqual(cfg["ATTACH_ON"], ["realtime_alert", "daily_digest"])
        self.assertEqual(cfg["REPORT_KIND"], "full")
        self.assertEqual(cfg["MAX_FILE_MB"], 8.0)
        self.assertEqual(cfg["FAILURE_BEHAVIOR"], "warn")

    def test_report_kind_invalid_falls_back_to_full(self):
        cfg = LOADER._load_telegram_attachments_config(
            {"telegram_attachments": {"report_kind": "state"}}
        )
        self.assertEqual(cfg["REPORT_KIND"], "full")

    def test_attach_on_filters_unknown_values(self):
        cfg = LOADER._load_telegram_attachments_config(
            {"telegram_attachments": {"attach_on": ["realtime_alert", "bogus", "daily"]}}
        )
        self.assertEqual(cfg["ATTACH_ON"], ["realtime_alert"])

    def test_failure_behavior_invalid_falls_back_to_warn(self):
        cfg = LOADER._load_telegram_attachments_config(
            {"telegram_attachments": {"failure_behavior": "explode"}}
        )
        self.assertEqual(cfg["FAILURE_BEHAVIOR"], "warn")

    def test_env_override_enabled(self):
        with mock.patch.dict(os.environ, {"TELEGRAM_ATTACHMENTS_ENABLED": "true"}):
            cfg = LOADER._load_telegram_attachments_config(
                {"telegram_attachments": {"enabled": False}}
            )
        self.assertTrue(cfg["ENABLED"])


if __name__ == "__main__":
    unittest.main()
