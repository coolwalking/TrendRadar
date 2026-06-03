# coding=utf-8
"""
AIAnalyzer environment 流程集成测试（验收第 3/4/5 点）。

通过 monkeypatch _call_ai 注入"假 AI 响应"，在不联网/无 API Key 的情况下
验证完整的 evidence -> AI -> 程序组装 链路：
  - 程序定栏 + 定标签，AI 只写文字；
  - 高热待核实强制带固定风险提示；
  - AI 失败时仍输出程序事实（不崩）；
  - 无信号时 skipped；
  - 注入 AI 的 prompt 只含 evidence summary，不含 raw title 池。
"""

import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import _bootstrap  # noqa: E402

B = _bootstrap.load_all()
EV = B.evidence
T = _bootstrap.make_title


def _time_func():
    return datetime.datetime(2026, 6, 4, 20, 0, 0)


def make_analyzer(**analysis_overrides):
    ai_config = {"MODEL": "test/model", "API_KEY": "k", "TIMEOUT": 10, "MAX_TOKENS": 100}
    analysis_config = {
        "ENABLED": True,
        "REPORT_STYLE": "environment",
        "ENVIRONMENT_PROMPT_FILE": "ai_environment_report_prompt.txt",
        "LANGUAGE": "Chinese",
        "INCLUDE_RSS": True,
        "MAX_NEWS_FOR_ANALYSIS": 200,
    }
    analysis_config.update(analysis_overrides)
    return B.analyzer.AIAnalyzer(ai_config, analysis_config, _time_func, debug=False)


SAMPLE_STATS = [
    # cross_layer (D+A)
    {"word": "AI前沿模型", "titles": [T("GPT传闻", "微博", 5)]},
    # high_heat_unverified (pure D, high)
    {"word": "某明星瓜", "titles": [T("某事发生", "微博", 3), T("吃瓜", "抖音", 6)]},
    # background (C only)
    {"word": "财经观察", "titles": [T("财报", "华尔街见闻", 12)]},
]
SAMPLE_RSS = [
    {"word": "AI前沿模型", "titles": [T("OpenAI ships", "OpenAI News", 1)]},
]


class TestEnvironmentAssembly(unittest.TestCase):
    def setUp(self):
        self.az = make_analyzer()
        self.resolver = _bootstrap.make_resolver(B)
        self.captured = {}

        def fake_call(user_prompt):
            self.captured["prompt"] = user_prompt
            # AI 只返回文字，按议题名 key；故意混入垃圾字段验证被忽略
            return (
                '{"overview": "今日共 2 条异常信号，多为 D 层。",'
                ' "items": {'
                '   "AI前沿模型": {"summary": "关于新模型的讨论", "analysis": "A/B/D 多层呼应",'
                '                  "verification_status": "AI乱填的状态"},'
                '   "某明星瓜": {"summary": "出现关于某事的传播", "analysis": "仅 D 层"}'
                ' },'
                ' "background_notes": ["财经面平稳"]}'
            )

        self.az._call_ai = fake_call
        self.result = self.az.analyze(
            stats=SAMPLE_STATS, rss_stats=SAMPLE_RSS,
            source_tier_resolver=self.resolver,
        )

    def test_report_style_and_success(self):
        self.assertEqual(self.result.report_style, "environment")
        self.assertTrue(self.result.success)

    def test_program_owns_labels_not_AI(self):
        # 即使 AI 在 item 里塞了 verification_status，输出仍用程序写死的状态
        item = self.result.cross_layer_verified[0]
        self.assertEqual(item["topic"], "AI前沿模型")
        self.assertEqual(item["verification_status"], EV.LABELS["cross_layer_verified"]["verification_status"])
        self.assertNotEqual(item["verification_status"], "AI乱填的状态")

    def test_program_owns_bucketing(self):
        # 某明星瓜必须落在 high_heat_unverified，而不是别处
        topics_high = [it["topic"] for it in self.result.high_heat_unverified]
        self.assertIn("某明星瓜", topics_high)
        self.assertNotIn("某明星瓜", [it["topic"] for it in self.result.cross_layer_verified])

    def test_ai_prose_merged_by_topic(self):
        item = self.result.cross_layer_verified[0]
        self.assertEqual(item["summary"], "关于新模型的讨论")
        self.assertEqual(item["analysis"], "A/B/D 多层呼应")

    def test_high_heat_has_fixed_risk_note(self):
        for item in self.result.high_heat_unverified:
            self.assertEqual(item["risk_note"], EV.RISK_NOTE_HIGH_HEAT)

    def test_overview_from_ai(self):
        self.assertIn("异常信号", self.result.overview)

    def test_method_note_is_program_constant(self):
        self.assertEqual(self.result.method_note, EV.METHOD_NOTE)

    def test_background_notes_combine_program_and_ai(self):
        joined = " ".join(self.result.background_notes)
        self.assertIn("财经观察", joined)   # 程序事实
        self.assertIn("财经面平稳", joined)  # AI 文字

    def test_prompt_contains_evidence_not_raw_pool(self):
        prompt = self.captured["prompt"]
        self.assertIn("议题「", prompt)          # 注入了结构化证据
        self.assertIn("代表性传播文本", prompt)
        # 占位符已被替换
        self.assertNotIn("{evidence_summary}", prompt)
        self.assertNotIn("{overview_stats}", prompt)
        # 不应出现 classic 的 raw title 占位符
        self.assertNotIn("{news_content}", prompt)
        self.assertNotIn("{rss_content}", prompt)


class TestEnvironmentResilience(unittest.TestCase):
    def test_ai_failure_still_outputs_program_facts(self):
        az = make_analyzer()

        def boom(_):
            raise RuntimeError("network down")

        az._call_ai = boom
        result = az.analyze(stats=SAMPLE_STATS, rss_stats=SAMPLE_RSS,
                            source_tier_resolver=_bootstrap.make_resolver(B))
        # 不崩；success=True，仍有程序栏目；prose 为空；error 记录
        self.assertTrue(result.success)
        self.assertTrue(result.error)
        self.assertEqual(len(result.cross_layer_verified), 1)
        self.assertEqual(result.cross_layer_verified[0]["summary"], "")
        # 风险提示仍由程序补齐
        self.assertEqual(result.high_heat_unverified[0]["risk_note"], EV.RISK_NOTE_HIGH_HEAT)

    def test_bad_json_is_tolerated(self):
        az = make_analyzer()
        az._call_ai = lambda _: "这不是 JSON，只是一段废话"
        result = az.analyze(stats=SAMPLE_STATS, rss_stats=SAMPLE_RSS,
                            source_tier_resolver=_bootstrap.make_resolver(B))
        self.assertTrue(result.success)
        self.assertTrue(result.error)  # 解析有错，但不致命
        # 程序事实仍保留：AI前沿模型(D+A) 跨层、某明星瓜(纯D高热) 仍在
        self.assertEqual(len(result.cross_layer_verified), 1)
        self.assertEqual(result.cross_layer_verified[0]["summary"], "")  # AI 文字为空

    def test_code_fenced_json_parsed(self):
        az = make_analyzer()
        az._call_ai = lambda _: '```json\n{"overview":"OV","items":{},"background_notes":[]}\n```'
        result = az.analyze(stats=SAMPLE_STATS, source_tier_resolver=_bootstrap.make_resolver(B))
        self.assertTrue(result.success)
        self.assertEqual(result.overview, "OV")
        self.assertFalse(result.error)

    def test_no_signal_is_skipped(self):
        az = make_analyzer()
        az._call_ai = lambda _: "{}"  # 不应被调用
        empty = [{"word": "X", "titles": []}]
        result = az.analyze(stats=empty, source_tier_resolver=_bootstrap.make_resolver(B))
        self.assertFalse(result.success)
        self.assertTrue(result.skipped)

    def test_missing_resolver_falls_back_to_unknown(self):
        # 不传 resolver：内部回退到全 unknown，不应崩溃
        az = make_analyzer()
        az._call_ai = lambda _: '{"overview":"x","items":{},"background_notes":[]}'
        stats = [{"word": "X", "titles": [T("a", "微博", 2), T("b", "抖音", 3)]}]
        result = az.analyze(stats=stats)  # source_tier_resolver=None
        # 全 unknown -> 没有 D 层 -> 该组进 background -> 无异常信号 -> skipped
        self.assertTrue(result.skipped or result.success)


class TestEnvironmentConfigSemantics(unittest.TestCase):
    def test_include_rss_false_excludes_rss_from_evidence(self):
        az = make_analyzer(INCLUDE_RSS=False)
        az._call_ai = lambda _: '{"overview":"x","items":{},"background_notes":[]}'
        stats = [{"word": "AI前沿模型", "titles": [T("GPT传闻", "微博", 5)]}]
        rss_stats = [{"word": "AI前沿模型", "titles": [T("OpenAI ships", "OpenAI News", 1)]}]

        result = az.analyze(
            stats=stats,
            rss_stats=rss_stats,
            source_tier_resolver=_bootstrap.make_resolver(B),
        )

        self.assertTrue(result.success)
        self.assertFalse(result.include_rss)
        self.assertEqual(result.rss_count, 0)
        self.assertEqual(result.cross_layer_verified, [])
        self.assertEqual(len(result.high_heat_unverified), 1)
        self.assertEqual(result.high_heat_unverified[0]["topic"], "AI前沿模型")

    def test_max_news_limits_prompt_not_program_facts(self):
        az = make_analyzer(MAX_NEWS_FOR_ANALYSIS=1)
        captured = {}

        def fake_call(prompt):
            captured["prompt"] = prompt
            return '{"overview":"x","items":{},"background_notes":[]}'

        az._call_ai = fake_call
        stats = [
            {"word": "A议题", "titles": [T("a", "微博", 1)]},
            {"word": "B议题", "titles": [T("b", "抖音", 2)]},
        ]

        result = az.analyze(
            stats=stats,
            source_tier_resolver=_bootstrap.make_resolver(B),
        )

        self.assertTrue(result.success)
        self.assertEqual(len(result.high_heat_unverified), 2)
        self.assertIn("议题「A议题」", captured["prompt"])
        self.assertNotIn("议题「B议题」", captured["prompt"])


class TestClassicUnaffected(unittest.TestCase):
    def test_classic_style_uses_classic_prompt_and_parser(self):
        ai_config = {"MODEL": "test/model", "API_KEY": "k"}
        analysis_config = {"ENABLED": True, "REPORT_STYLE": "classic",
                           "PROMPT_FILE": "ai_analysis_prompt.txt", "LANGUAGE": "Chinese"}
        az = B.analyzer.AIAnalyzer(ai_config, analysis_config, _time_func, debug=False)
        az._call_ai = lambda _: '{"core_trends": "经典核心趋势"}'
        stats = [{"word": "X", "titles": [T("a", "微博", 2)]}]
        result = az.analyze(stats=stats, source_tier_resolver=_bootstrap.make_resolver(B))
        self.assertEqual(result.report_style, "classic")
        self.assertEqual(result.core_trends, "经典核心趋势")
        # 环境字段保持空
        self.assertEqual(result.high_heat_unverified, [])


if __name__ == "__main__":
    unittest.main()
