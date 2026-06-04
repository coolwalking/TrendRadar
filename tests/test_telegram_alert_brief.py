# coding=utf-8
"""
Telegram environment 异常提醒 brief 测试。

覆盖：
- candidate selection（select_environment_alert_items）的 gate 规则
- alert brief renderer 只输出 alert layer（不含完整报告区块 / 证据 / source_links）
- send_to_telegram 在 environment 下走单条提醒路径、无候选静默跳过、classic 走原分批路径
- 超长摘要先截断单条，绝不拆成多批
"""

import importlib.util
import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import _bootstrap  # noqa: E402
from test_formatter_environment import make_env_result  # noqa: E402

B = _bootstrap.load_all()
FMT = B.formatter
EV = B.evidence
AIAnalysisResult = B.analyzer.AIAnalysisResult
ROOT = B.ROOT


def _load_senders():
    """按文件加载 trendradar.notification.senders 及其纯标准库依赖，避免触发重型 __init__。"""
    pkg = "trendradar.notification"
    if pkg not in sys.modules:
        mod = types.ModuleType(pkg)
        mod.__path__ = []
        sys.modules[pkg] = mod

    def _load(name, relpath):
        spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, relpath))
        m = importlib.util.module_from_spec(spec)
        sys.modules[name] = m
        spec.loader.exec_module(m)
        return m

    _load("trendradar.notification.batch", "trendradar/notification/batch.py")
    _load("trendradar.notification.formatters", "trendradar/notification/formatters.py")
    return _load("trendradar.notification.senders", "trendradar/notification/senders.py")


SENDERS = _load_senders()


def _load_alert_state():
    """加载纯标准库的 alert_state，并注册到 sys.modules 供 senders 惰性导入。"""
    name = "trendradar.ai.alert_state"
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(ROOT, "trendradar/ai/alert_state.py")
    )
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


AS = _load_alert_state()

ALERT_CFG = {
    "ENABLED": True,
    "COOLDOWN_MINUTES": 180,
    "STATE_TTL_DAYS": 14,
    "MAX_ITEMS": 3,
    "ALLOW_UPGRADE_BREAK_COOLDOWN": True,
    "HIGH_HEAT_MIN_RANK": 10,
    "HIGH_HEAT_MIN_PLATFORMS": 2,
    "NOTIFY_LABELS": ["cross_layer_verified", "high_heat_unverified", "chinese_only_hot"],
}


class _FakeBackend:
    """模拟跨 cron 运行持久化的存储后端（内存 dict），并记录读写次数。"""

    def __init__(self):
        self.data = {}
        self.get_calls = 0
        self.save_calls = 0

    def get_alert_state(self):
        self.get_calls += 1
        return dict(self.data)

    def save_alert_state(self, state):
        self.save_calls += 1
        self.data = dict(state)
        return True


class _SplitTracker:
    """记录 split_content_func 是否被调用（用于验证 environment 不走分批器）。"""

    def __init__(self):
        self.called = False

    def __call__(self, *args, **kwargs):
        self.called = True
        return ["分批内容"]


def _ok_response():
    resp = mock.Mock()
    resp.status_code = 200
    resp.json.return_value = {"ok": True}
    return resp


def _call_telegram(ai_analysis, split_func, mode="current", **extra):
    return SENDERS.send_to_telegram(
        bot_token="token",
        chat_id="chat",
        report_data={"stats": [], "failed_ids": [], "new_titles": [], "id_to_name": {}},
        report_type="当前榜单",
        mode=mode,
        split_content_func=split_func,
        ai_analysis=ai_analysis,
        **extra,
    )


# ════════════════════════════════════════════════════════════════
# selection gate
# ════════════════════════════════════════════════════════════════


class TestSelectAlertItems(unittest.TestCase):
    def test_priority_order_and_cap(self):
        result = make_env_result()
        items = FMT.select_environment_alert_items(result, max_items=3)
        labels = [label for label, _ in items]
        # cross_layer 优先于 high_heat；sentiment_heavy / background 不进
        self.assertEqual(labels[0], "cross_layer_verified")
        self.assertIn("high_heat_unverified", labels)
        self.assertNotIn("sentiment_heavy", labels)
        self.assertLessEqual(len(items), 3)

    def test_silence_gap_only_yields_no_candidate(self):
        result = AIAnalysisResult(
            report_style="environment", success=True,
            silence_gap=[{"topic": "外热中静议题", "source_layers": "B"}],
        )
        self.assertEqual(FMT.select_environment_alert_items(result), [])

    def test_sentiment_only_yields_no_candidate(self):
        result = AIAnalysisResult(
            report_style="environment", success=True,
            sentiment_heavy=[{"topic": "饭圈骂战", "source_layers": "D"}],
        )
        self.assertEqual(FMT.select_environment_alert_items(result), [])

    def test_item_without_topic_skipped(self):
        result = AIAnalysisResult(
            report_style="environment", success=True,
            cross_layer_verified=[{"topic": "  ", "source_layers": "A/D"}],
        )
        self.assertEqual(FMT.select_environment_alert_items(result), [])

    def test_classic_yields_no_candidate(self):
        result = AIAnalysisResult(report_style="classic", success=True, core_trends="x")
        self.assertEqual(FMT.select_environment_alert_items(result), [])

    def test_failed_result_yields_no_candidate(self):
        result = AIAnalysisResult(report_style="environment", success=False, skipped=True)
        self.assertEqual(FMT.select_environment_alert_items(result), [])

    # ── allowed_labels（notify_labels 语义）──

    def test_allowed_labels_only_chinese_only_hot(self):
        # notify_labels 只允许 chinese_only_hot 时，即使 cross_layer/high_heat 存在也跳过它们
        result = AIAnalysisResult(
            report_style="environment", success=True,
            cross_layer_verified=[{"topic": "跨层议题", "source_layers": "A/D"}],
            high_heat_unverified=[{"topic": "高热议题", "source_layers": "D"}],
            chinese_only_hot=[{"topic": "中文独热议题", "source_layers": "D"}],
        )
        items = FMT.select_environment_alert_items(result, allowed_labels=["chinese_only_hot"])
        labels = [lb for lb, _ in items]
        self.assertEqual(labels, ["chinese_only_hot"])

    def test_allowed_labels_only_cross_layer(self):
        # notify_labels 只允许 cross_layer_verified 时，不会选 high_heat_unverified
        result = make_env_result()
        items = FMT.select_environment_alert_items(
            result, max_items=3, allowed_labels=["cross_layer_verified"],
        )
        labels = [lb for lb, _ in items]
        self.assertIn("cross_layer_verified", labels)
        self.assertNotIn("high_heat_unverified", labels)

    def test_allowed_labels_none_uses_default_order(self):
        # allowed_labels=None → 使用默认 _ALERT_BUCKET_ORDER，行为不变
        result = make_env_result()
        items_default = FMT.select_environment_alert_items(result, max_items=3)
        items_none = FMT.select_environment_alert_items(result, max_items=3, allowed_labels=None)
        self.assertEqual(items_default, items_none)

    def test_allowed_labels_empty_list_yields_nothing(self):
        # allowed_labels=[] → 无桶可选 → 空列表
        result = make_env_result()
        items = FMT.select_environment_alert_items(result, max_items=3, allowed_labels=[])
        self.assertEqual(items, [])


# ════════════════════════════════════════════════════════════════
# renderer：只输出 alert layer
# ════════════════════════════════════════════════════════════════


class TestAlertBriefRenderer(unittest.TestCase):
    def setUp(self):
        self.result = make_env_result()
        self.items = FMT.select_environment_alert_items(self.result, max_items=3)
        self.now = __import__("datetime").datetime(2026, 6, 4, 8, 30, 0)
        self.out = FMT.render_environment_telegram_alert_brief(
            self.result, self.items,
            html_file_path="output/html/latest/current.html",
            now=self.now,
        )

    def test_contains_alert_layer_essentials(self):
        self.assertIn("TrendRadar｜异常提醒", self.out)
        self.assertIn("本轮发现", self.out)
        # topic
        self.assertIn("AI前沿模型", self.out)
        self.assertIn("某明星瓜", self.out)
        # 状态 / 层级 / 热度关键信息
        self.assertIn("跨层呼应", self.out)
        self.assertIn("高热待核实", self.out)
        self.assertIn("最高热度", self.out)
        # high_heat 风险边界
        self.assertIn(EV.RISK_NOTE_HIGH_HEAT, self.out)
        # 链接与时间戳
        self.assertIn("output/html/latest/current.html", self.out)
        self.assertIn("2026-06-04 08:30:00", self.out)
        # 标题用 <b>，配合 parse_mode=HTML
        self.assertIn("<b>", self.out)

    def test_excludes_full_report_blocks(self):
        for forbidden in [
            "热点词汇统计", "RSS", "抓取出处", "传播样本", "展开证据", "<details",
        ]:
            self.assertNotIn(forbidden, self.out, f"alert brief 不应包含 {forbidden}")
        # 不含 source_links URL / evidence URL
        self.assertNotIn("example.com", self.out)
        self.assertNotIn("http://", self.out)
        self.assertNotIn("https://", self.out)

    def test_html_file_path_is_escaped(self):
        out = FMT.render_environment_telegram_alert_brief(
            self.result, self.items,
            html_file_path="out/<x>&y.html",
            now=self.now,
        )
        self.assertIn("out/&lt;x&gt;&amp;y.html", out)
        self.assertNotIn("out/<x>&y.html", out)

    def test_omits_link_line_when_no_path(self):
        out = FMT.render_environment_telegram_alert_brief(self.result, self.items, now=self.now)
        self.assertNotIn("完整报告：", out)

    def test_long_summary_truncated(self):
        long_text = "传" * 5000
        result = AIAnalysisResult(
            report_style="environment", success=True,
            cross_layer_verified=[{
                "topic": "超长摘要议题", "summary": long_text,
                "source_layers": "A/D", "highest_heat": "微博 第1名",
                "verification_status": EV.LABELS["cross_layer_verified"]["verification_status"],
                "factual_boundary": EV.LABELS["cross_layer_verified"]["factual_boundary"],
            }],
        )
        items = FMT.select_environment_alert_items(result)
        out = FMT.render_environment_telegram_alert_brief(result, items, now=self.now)
        # 原始 summary 不应整体出现；被截断到上限内
        self.assertNotIn(long_text, out)
        self.assertIn("…", out)
        # 渲染结果远小于原始摘要
        self.assertLess(len(out), len(long_text))


# ════════════════════════════════════════════════════════════════
# renderer：每日简报 digest layer
# ════════════════════════════════════════════════════════════════


class TestDailyDigestRenderer(unittest.TestCase):
    def setUp(self):
        self.now = __import__("datetime").datetime(2026, 6, 4, 8, 30, 0)

    def test_contains_daily_digest_essentials(self):
        out = FMT.render_environment_telegram_daily_digest(
            make_env_result(),
            html_file_path="https://reports.example.com/latest.html",
            now=self.now,
        )
        self.assertIn("TrendRadar｜每日简报", out)
        self.assertIn("今日盘面", out)
        self.assertIn("今日重点", out)
        self.assertIn("异常 2｜已抑制 2", out)
        self.assertIn("跨层呼应 1｜高热待核实 1", out)
        self.assertIn("中文独热 0｜沉默温差 0", out)
        self.assertIn("AI前沿模型", out)
        self.assertIn("某明星瓜", out)
        self.assertIn("https://reports.example.com/latest.html", out)
        self.assertIn("2026-06-04 08:30:00", out)

    def test_excludes_evidence_fields_but_allows_report_url(self):
        out = FMT.render_environment_telegram_daily_digest(
            make_env_result(),
            html_file_path="https://reports.example.com/latest.html",
            now=self.now,
        )
        for forbidden in [
            "source_links", "sample_titles", "evidence_detail",
            "抓取出处", "传播样本", "展开证据", "example.com/openai",
        ]:
            self.assertNotIn(forbidden, out)
        self.assertIn("https://reports.example.com/latest.html", out)

    def test_top_n_cap_and_silence_gap_only_fills(self):
        result = AIAnalysisResult(
            report_style="environment", success=True,
            overview_stats={
                "label_counts": {
                    "cross_layer_verified": 5, "high_heat_unverified": 0,
                    "sentiment_heavy": 0, "silence_gap": 1, "chinese_only_hot": 0,
                },
                "background_count": 0,
                "layer_distribution": {"A": 5, "B": 0, "C": 0, "D": 5},
            },
            cross_layer_verified=[
                {"topic": f"主异常{i}", "summary": "摘要", "source_layers": "A/D"}
                for i in range(5)
            ],
            silence_gap=[{"topic": "补位沉默温差", "summary": "摘要", "source_layers": "B"}],
        )
        items = FMT.select_environment_daily_digest_items(result, max_items=5)
        self.assertEqual(len(items), 5)
        self.assertNotIn("补位沉默温差", [it["topic"] for it in items])

        result.cross_layer_verified = result.cross_layer_verified[:2]
        items = FMT.select_environment_daily_digest_items(result, max_items=5)
        self.assertIn("补位沉默温差", [it["topic"] for it in items])
        self.assertEqual(items[-1]["bucket"], "silence_gap")

    def test_deduplicates_topic_across_buckets(self):
        result = AIAnalysisResult(
            report_style="environment", success=True,
            cross_layer_verified=[{"topic": "AI 前沿模型！", "summary": "跨层", "source_layers": "A/D"}],
            high_heat_unverified=[{"topic": "ai前沿模型", "summary": "高热", "source_layers": "D"}],
        )
        items = FMT.select_environment_daily_digest_items(result, max_items=5)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["bucket"], "cross_layer_verified")

    def test_no_anomaly_still_renders_digest(self):
        result = AIAnalysisResult(
            report_style="environment", success=True,
            overview_stats={
                "label_counts": {
                    "cross_layer_verified": 0, "high_heat_unverified": 0,
                    "sentiment_heavy": 0, "silence_gap": 0, "chinese_only_hot": 0,
                },
                "background_count": 8,
                "layer_distribution": {"A": 0, "B": 0, "C": 0, "D": 0},
            },
        )
        out = FMT.render_environment_telegram_daily_digest(result, now=self.now)
        self.assertIn("异常 0｜已抑制 8", out)
        self.assertIn("今日未发现高优先级异常信号。", out)
        self.assertIn("低优先级观察项已收录在完整报告中。", out)

    def test_long_summaries_stay_within_single_message_budget(self):
        result = AIAnalysisResult(
            report_style="environment", success=True,
            cross_layer_verified=[
                {
                    "topic": f"超长摘要{i}",
                    "summary": "传" * 5000,
                    "source_layers": "A/D",
                    "highest_heat": "微博 第1名",
                }
                for i in range(5)
            ],
        )
        out = FMT.render_environment_telegram_daily_digest(result, now=self.now)
        self.assertLess(len(out), 3900)
        self.assertIn("…", out)


# ════════════════════════════════════════════════════════════════
# send_to_telegram 接入
# ════════════════════════════════════════════════════════════════


class TestSendToTelegramRouting(unittest.TestCase):
    def test_environment_no_candidate_skips_silently(self):
        result = AIAnalysisResult(
            report_style="environment", success=True,
            silence_gap=[{"topic": "外热中静议题", "source_layers": "B"}],
        )
        split = _SplitTracker()
        with mock.patch.object(SENDERS.requests, "post", return_value=_ok_response()) as post:
            ok = _call_telegram(result, split)
        self.assertTrue(ok)
        post.assert_not_called()
        self.assertFalse(split.called, "无候选时不应调用通用分批器")

    def test_environment_with_candidates_sends_single_message(self):
        result = make_env_result()
        split = _SplitTracker()
        with mock.patch.object(SENDERS.requests, "post", return_value=_ok_response()) as post:
            ok = _call_telegram(
                result, split, html_file_path="output/html/latest/current.html",
            )
        self.assertTrue(ok)
        self.assertEqual(post.call_count, 1, "environment 应只发一条消息")
        self.assertFalse(split.called, "environment 不应走分批器")
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["parse_mode"], "HTML")
        self.assertIn("TrendRadar｜异常提醒", payload["text"])

    def test_environment_long_summary_not_split(self):
        long_text = "传" * 5000
        result = AIAnalysisResult(
            report_style="environment", success=True,
            cross_layer_verified=[{
                "topic": "超长摘要议题", "summary": long_text,
                "source_layers": "A/D", "highest_heat": "微博 第1名",
                "verification_status": EV.LABELS["cross_layer_verified"]["verification_status"],
                "factual_boundary": EV.LABELS["cross_layer_verified"]["factual_boundary"],
            }],
        )
        split = _SplitTracker()
        with mock.patch.object(SENDERS.requests, "post", return_value=_ok_response()) as post:
            ok = _call_telegram(result, split)
        self.assertTrue(ok)
        self.assertEqual(post.call_count, 1, "超长摘要应截断而非拆成多批")
        self.assertFalse(split.called)
        text = post.call_args.kwargs["json"]["text"]
        self.assertNotIn("example.com", text)
        self.assertNotIn("<details", text)

    def test_classic_uses_split_path(self):
        result = AIAnalysisResult(
            report_style="classic", success=True, core_trends="核心趋势内容",
        )
        split = _SplitTracker()
        with mock.patch.object(SENDERS.requests, "post", return_value=_ok_response()) as post:
            ok = _call_telegram(result, split)
        self.assertTrue(ok)
        self.assertTrue(split.called, "classic 应走原 split_content_func 分批路径")
        self.assertGreaterEqual(post.call_count, 1)

    def test_environment_daily_sends_single_digest_without_split(self):
        result = make_env_result()
        split = _SplitTracker()
        store = AS.AlertStateStore(_FakeBackend())
        with mock.patch.object(SENDERS.requests, "post", return_value=_ok_response()) as post:
            ok = SENDERS.send_to_telegram(
                bot_token="token", chat_id="chat",
                report_data={"stats": [], "failed_ids": [], "new_titles": [], "id_to_name": {}},
                report_type="当日汇总", mode="daily",
                split_content_func=split,
                ai_analysis=result,
                html_file_path="output/html/latest/daily.html",
                alert_state_store=store,
                alert_config={**ALERT_CFG, "NOTIFY_LABELS": []},
            )
        self.assertTrue(ok)
        self.assertEqual(post.call_count, 1)
        self.assertFalse(split.called)
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["parse_mode"], "HTML")
        self.assertIn("TrendRadar｜每日简报", payload["text"])
        self.assertIn("AI前沿模型", payload["text"])
        self.assertIn("output/html/latest/daily.html", payload["text"])

    def test_environment_daily_uses_get_time_func_for_timestamp(self):
        from datetime import datetime as _dt

        fixed_time = _dt(2026, 1, 15, 9, 0, 0)
        result = make_env_result()
        split = _SplitTracker()
        with mock.patch.object(SENDERS.requests, "post", return_value=_ok_response()) as post:
            ok = SENDERS.send_to_telegram(
                bot_token="token", chat_id="chat",
                report_data={"stats": [], "failed_ids": [], "new_titles": [], "id_to_name": {}},
                report_type="当日汇总", mode="daily",
                split_content_func=split,
                ai_analysis=result,
                get_time_func=lambda: fixed_time,
            )
        self.assertTrue(ok)
        self.assertEqual(post.call_count, 1)
        self.assertFalse(split.called)
        text = post.call_args.kwargs["json"]["text"]
        self.assertIn("2026-01-15 09:00:00", text)

    def test_environment_daily_renderer_failure_falls_back_to_split(self):
        result = make_env_result()
        split = _SplitTracker()
        with mock.patch.object(
            FMT, "render_environment_telegram_daily_digest", side_effect=RuntimeError("boom")
        ), mock.patch.object(SENDERS.requests, "post", return_value=_ok_response()) as post:
            ok = SENDERS.send_to_telegram(
                bot_token="token", chat_id="chat",
                report_data={"stats": [], "failed_ids": [], "new_titles": [], "id_to_name": {}},
                report_type="当日汇总", mode="daily",
                split_content_func=split,
                ai_analysis=result,
            )
        self.assertTrue(ok)
        self.assertTrue(split.called, "daily digest 构造失败应回退完整分批路径")
        self.assertGreaterEqual(post.call_count, 1)


# ════════════════════════════════════════════════════════════════
# cooldown / 去重 / 升级再推（send_to_telegram 集成）
# ════════════════════════════════════════════════════════════════


def _one_item_result(label, topic, layers, heat, platform_count=2):
    item = {
        "topic": topic,
        "summary": "摘要",
        "source_layers": layers,
        "platforms": "微博",
        "platform_count": platform_count,
        "highest_heat": heat,
        "verification_status": EV.LABELS[label]["verification_status"],
        "factual_boundary": EV.LABELS[label]["factual_boundary"],
    }
    if label == "high_heat_unverified":
        item["risk_note"] = EV.RISK_NOTE_HIGH_HEAT
    return AIAnalysisResult(report_style="environment", success=True, **{label: [item]})


class TestCooldownIntegration(unittest.TestCase):
    def setUp(self):
        self.backend = _FakeBackend()
        self.T0 = __import__("datetime").datetime(2026, 6, 4, 8, 0, 0)

    def _send(self, result, now, mode="current"):
        """模拟一次 cron 运行：每次新建 store（共享同一 backend）。"""
        store = AS.AlertStateStore(
            self.backend,
            state_ttl_days=ALERT_CFG["STATE_TTL_DAYS"],
            cooldown_minutes=ALERT_CFG["COOLDOWN_MINUTES"],
        )
        split = _SplitTracker()
        with mock.patch.object(SENDERS.requests, "post", return_value=_ok_response()) as post:
            ok = SENDERS.send_to_telegram(
                bot_token="token", chat_id="chat",
                report_data={"stats": [], "failed_ids": [], "new_titles": [], "id_to_name": {}},
                report_type="当前榜单", mode=mode,
                split_content_func=split,
                ai_analysis=result,
                get_time_func=lambda: now,
                alert_state_store=store,
                alert_config=ALERT_CFG,
            )
        return ok, post, split

    def test_first_push_sends_and_persists(self):  # #1
        result = make_env_result()
        ok, post, _ = self._send(result, self.T0)
        self.assertTrue(ok)
        self.assertEqual(post.call_count, 1)
        # 状态已落盘：两个候选 topic_key 入库
        self.assertIn(AS.topic_key("AI前沿模型"), self.backend.data["topics"])
        self.assertIn(AS.topic_key("某明星瓜"), self.backend.data["topics"])

    def test_realtime_commit_prunes_expired_alert_state(self):
        import datetime as _dt

        old_key = AS.topic_key("长期旧状态")
        self.backend.data = {
            "version": 1,
            "topics": {
                old_key: AS.build_state_record(
                    "cross_layer_verified",
                    {"topic": "长期旧状态", "source_layers": "A/D"},
                    self.T0 - _dt.timedelta(days=30),
                ),
            },
        }

        ok, post, _ = self._send(make_env_result(), self.T0)

        self.assertTrue(ok)
        self.assertEqual(post.call_count, 1)
        self.assertNotIn(old_key, self.backend.data["topics"])
        self.assertIn(AS.topic_key("AI前沿模型"), self.backend.data["topics"])

    def test_within_cooldown_silent(self):  # #2
        import datetime as _dt
        result = make_env_result()
        self._send(result, self.T0)
        ok, post, _ = self._send(result, self.T0 + _dt.timedelta(minutes=60))
        self.assertTrue(ok)
        post.assert_not_called()  # 冷却内全部抑制 → 静默成功

    def test_after_cooldown_pushes_again(self):  # #3
        import datetime as _dt
        result = make_env_result()
        self._send(result, self.T0)
        ok, post, _ = self._send(result, self.T0 + _dt.timedelta(minutes=200))
        self.assertTrue(ok)
        self.assertEqual(post.call_count, 1)

    def test_label_upgrade_breaks_cooldown(self):  # #4
        import datetime as _dt
        r1 = _one_item_result("high_heat_unverified", "升级议题", "D", "微博 第3名")
        self._send(r1, self.T0)
        # 30 分钟后同议题升级为 cross_layer_verified（含 A 层）
        r2 = _one_item_result("cross_layer_verified", "升级议题", "A/D", "微博 第3名")
        ok, post, _ = self._send(r2, self.T0 + _dt.timedelta(minutes=30))
        self.assertTrue(ok)
        self.assertEqual(post.call_count, 1)

    def test_weak_high_heat_gated_out(self):  # #6（集成）
        # rank=40 且单平台 → 不满足 heat gate → 首见也静默
        result = _one_item_result("high_heat_unverified", "弱高热", "D", "微博 第40名", platform_count=1)
        ok, post, _ = self._send(result, self.T0)
        self.assertTrue(ok)
        post.assert_not_called()

    def test_does_not_mutate_ai_analysis(self):  # #9
        result = make_env_result()
        before_cross = list(result.cross_layer_verified)
        before_high = list(result.high_heat_unverified)
        self._send(result, self.T0)
        # cooldown 只过滤局部 items，不改 ai_analysis（HTML 仍读完整列表）
        self.assertEqual(result.cross_layer_verified, before_cross)
        self.assertEqual(result.high_heat_unverified, before_high)

    def test_store_none_behaves_like_previous_stage(self):  # #11
        result = make_env_result()
        split = _SplitTracker()
        with mock.patch.object(SENDERS.requests, "post", return_value=_ok_response()) as post:
            ok = SENDERS.send_to_telegram(
                bot_token="token", chat_id="chat",
                report_data={"stats": [], "failed_ids": [], "new_titles": [], "id_to_name": {}},
                report_type="当前榜单", mode="current",
                split_content_func=split,
                ai_analysis=result,
                alert_state_store=None,  # 未启用 → 无冷却
                alert_config=ALERT_CFG,
            )
        self.assertTrue(ok)
        self.assertEqual(post.call_count, 1)  # 直接推送，无门控

    # ── mode 作用域：cooldown 只作用于实时模式，daily 不受影响 ──

    def test_daily_mode_no_candidate_not_silenced(self):
        # environment + daily：即使没有 realtime alert candidate，也应发送 daily digest
        result = AIAnalysisResult(
            report_style="environment", success=True,
            silence_gap=[{"topic": "外热中静议题", "source_layers": "B"}],
        )
        ok, post, split = self._send(result, self.T0, mode="daily")
        self.assertTrue(ok)
        self.assertFalse(split.called, "daily 应走每日简报路径，而非完整 split 路径")
        self.assertEqual(post.call_count, 1, "daily 不应被静默")
        self.assertIn("TrendRadar｜每日简报", post.call_args.kwargs["json"]["text"])

    def test_daily_mode_does_not_touch_alert_state(self):
        # environment + daily：即使有候选，也不读写 alert_state（cooldown 不影响 daily）
        result = make_env_result()
        ok, post, split = self._send(result, self.T0, mode="daily")
        self.assertTrue(ok)
        self.assertFalse(split.called)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(self.backend.get_calls, 0, "daily 不应读取 alert_state")
        self.assertEqual(self.backend.save_calls, 0, "daily 不应写入 alert_state")
        self.assertEqual(self.backend.data, {})

    def test_daily_mode_no_anomaly_still_sends_digest(self):
        result = AIAnalysisResult(report_style="environment", success=True)
        ok, post, split = self._send(result, self.T0, mode="daily")
        self.assertTrue(ok)
        self.assertFalse(split.called)
        self.assertEqual(post.call_count, 1)
        self.assertIn("今日未发现高优先级异常信号", post.call_args.kwargs["json"]["text"])

    def test_incremental_mode_applies_cooldown(self):
        import datetime as _dt
        result = make_env_result()
        self._send(result, self.T0, mode="incremental")
        ok, post, _ = self._send(
            result, self.T0 + _dt.timedelta(minutes=60), mode="incremental"
        )
        self.assertTrue(ok)
        post.assert_not_called()  # incremental 同样受 cooldown 约束

    def test_current_mode_applies_cooldown(self):
        import datetime as _dt
        result = make_env_result()
        self._send(result, self.T0, mode="current")
        ok, post, _ = self._send(result, self.T0 + _dt.timedelta(minutes=60), mode="current")
        self.assertTrue(ok)
        post.assert_not_called()

    def test_manual_trigger_bypasses_gate(self):
        # 未来手动 /now：绕过 realtime alert gate，直接走完整渲染，且不读写 alert_state
        result = make_env_result()
        store = AS.AlertStateStore(self.backend)
        split = _SplitTracker()
        with mock.patch.object(SENDERS.requests, "post", return_value=_ok_response()) as post:
            ok = SENDERS.send_to_telegram(
                bot_token="token", chat_id="chat",
                report_data={"stats": [], "failed_ids": [], "new_titles": [], "id_to_name": {}},
                report_type="当前榜单", mode="current",
                split_content_func=split,
                ai_analysis=result,
                get_time_func=lambda: self.T0,
                alert_state_store=store,
                alert_config=ALERT_CFG,
                manual_trigger=True,
            )
        self.assertTrue(ok)
        self.assertTrue(split.called, "manual /now 应走完整渲染路径，而非 alert gate")
        self.assertEqual(self.backend.get_calls, 0, "manual /now 不应读取 alert_state")
        self.assertEqual(self.backend.save_calls, 0, "manual /now 不应写入 alert_state")


class TestRealtimeAlertGateScope(unittest.TestCase):
    """realtime alert gate 作用域规则（唯一来源 should_apply_realtime_alert_gate）。"""

    def test_environment_current_applies(self):
        self.assertTrue(SENDERS.should_apply_realtime_alert_gate("environment", "current"))

    def test_environment_incremental_applies(self):
        self.assertTrue(SENDERS.should_apply_realtime_alert_gate("environment", "incremental"))

    def test_environment_daily_excluded(self):
        self.assertFalse(SENDERS.should_apply_realtime_alert_gate("environment", "daily"))

    def test_manual_trigger_excluded(self):
        self.assertFalse(
            SENDERS.should_apply_realtime_alert_gate("environment", "current", manual_trigger=True)
        )

    def test_classic_excluded(self):
        self.assertFalse(SENDERS.should_apply_realtime_alert_gate("classic", "current"))

    def test_unknown_mode_excluded(self):
        # 默认从严：未知 / 缺省 mode 不施加 gate
        self.assertFalse(SENDERS.should_apply_realtime_alert_gate("environment", "daily_brief"))
        self.assertFalse(SENDERS.should_apply_realtime_alert_gate("environment", ""))


if __name__ == "__main__":
    unittest.main()
