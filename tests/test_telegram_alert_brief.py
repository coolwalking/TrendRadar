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


def _call_telegram(ai_analysis, split_func, **extra):
    return SENDERS.send_to_telegram(
        bot_token="token",
        chat_id="chat",
        report_data={"stats": [], "failed_ids": [], "new_titles": [], "id_to_name": {}},
        report_type="当前榜单",
        mode="current",
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


if __name__ == "__main__":
    unittest.main()
