# coding=utf-8
"""
formatter 各渠道 environment 渲染测试 + classic 回归。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import _bootstrap  # noqa: E402

B = _bootstrap.load_all()
FMT = B.formatter
EV = B.evidence
AIAnalysisResult = B.analyzer.AIAnalysisResult


def make_env_result():
    return AIAnalysisResult(
        report_style="environment",
        success=True,
        overview="今日 D 层独热为主、跨层呼应偏少。",
        overview_stats={
            "total_items": 3, "background_count": 1,
            "label_counts": {
                "cross_layer_verified": 1, "high_heat_unverified": 1,
                "sentiment_heavy": 1, "silence_gap": 0, "chinese_only_hot": 0,
            },
            "layer_distribution": {"A": 1, "B": 0, "C": 1, "D": 3},
        },
        cross_layer_verified=[{
            "topic": "AI前沿模型", "summary": "关于新模型的讨论", "analysis": "多层呼应",
            "source_layers": "A/D", "platforms": "微博、OpenAI News", "platform_count": 2,
            "highest_heat": "微博 第5名",
            "verification_status": EV.LABELS["cross_layer_verified"]["verification_status"],
            "factual_boundary": EV.LABELS["cross_layer_verified"]["factual_boundary"],
            "sentiment_flag": False,
        }],
        high_heat_unverified=[{
            "topic": "某明星瓜", "summary": "出现关于某事的传播", "analysis": "仅 D 层",
            "source_layers": "D", "platforms": "微博、抖音", "platform_count": 2,
            "highest_heat": "微博 第3名",
            "verification_status": EV.LABELS["high_heat_unverified"]["verification_status"],
            "factual_boundary": EV.LABELS["high_heat_unverified"]["factual_boundary"],
            "risk_note": EV.RISK_NOTE_HIGH_HEAT,
            "sentiment_flag": True,
        }],
        # 情绪降为属性：低热情绪项不单独成栏，呈现层折叠进"已抑制"
        sentiment_heavy=[{
            "topic": "饭圈骂战", "summary": "", "analysis": "",
            "source_layers": "D", "platforms": "微博", "platform_count": 1,
            "highest_heat": "微博 第45名",
            "verification_status": EV.LABELS["sentiment_heavy"]["verification_status"],
            "factual_boundary": EV.LABELS["sentiment_heavy"]["factual_boundary"],
            "sentiment_flag": True,
        }],
        background_notes=["财经观察（C）", "财经面平稳"],
        method_note=EV.METHOD_NOTE,
    )


ALL_RENDERERS = [
    "render_ai_analysis_markdown",
    "render_ai_analysis_feishu",
    "render_ai_analysis_dingtalk",
    "render_ai_analysis_plain",
    "render_ai_analysis_telegram",
    "render_ai_analysis_html_rich",
]


class TestEnvironmentRendering(unittest.TestCase):
    def setUp(self):
        self.result = make_env_result()

    def test_all_channels_render_core_sections(self):
        for name in ALL_RENDERERS:
            out = getattr(FMT, name)(self.result)
            self.assertTrue(out, f"{name} 返回空")
            self.assertIn("信息环境异常监测日报", out, f"{name} 缺标题")
            self.assertIn("今日盘面", out, f"{name} 缺盘面")
            self.assertIn("跨层呼应", out, f"{name} 缺跨层栏目")
            self.assertIn("高热待核实", out, f"{name} 缺高热栏目")
            # 高热条目必须带固定风险提示
            self.assertIn(EV.RISK_NOTE_HIGH_HEAT, out, f"{name} 缺风险提示")
            self.assertIn("方法说明", out, f"{name} 缺方法说明")
            # 旧措辞已清除（防 newsletter 回流 + 事实确认感）
            self.assertNotIn("跨层验证事件", out, f"{name} 残留旧措辞")
            self.assertNotIn("情绪型舆论", out, f"{name} 残留情绪栏目")

    def test_radar_header_uses_program_counts(self):
        # 盘面四行由程序统计驱动（异常排除情绪、已抑制含情绪折叠）
        out = FMT.render_ai_analysis_plain(self.result)
        self.assertIn("信号密度", out)
        self.assertIn("热度↔证据", out)
        self.assertIn("中外温差", out)
        self.assertIn("层级覆盖", out)

    def test_sentiment_demoted_to_suppressed_attribute(self):
        # 情绪低热项不单独成栏，折叠进"已抑制"，且仍以"含情绪信号"标注
        out = FMT.render_ai_analysis_plain(self.result)
        self.assertIn("已抑制", out)
        self.assertIn("饭圈骂战", out)
        self.assertIn("含情绪信号", out)
        # 高热项中的情绪也以属性出现（sentiment_flag=True）
        self.assertIn("某明星瓜", out)

    def test_html_uses_card_structure(self):
        out = FMT.render_ai_analysis_html_rich(self.result)
        self.assertIn('class="ai-section"', out)
        self.assertIn('class="ai-block"', out)

    def test_telegram_escapes_html(self):
        # telegram 渲染应对正文做 HTML 转义（不抛异常且产出文本）
        out = FMT.render_ai_analysis_telegram(self.result)
        self.assertIn("<b>", out)

    def test_verification_status_present(self):
        out = FMT.render_ai_analysis_markdown(self.result)
        self.assertIn("高热待核实", out)
        self.assertIn("跨层有呼应", out)

    def test_router_returns_callable_per_channel(self):
        for ch in ["feishu", "dingtalk", "wework", "telegram", "ntfy", "bark", "slack", "email"]:
            fn = FMT.get_ai_analysis_renderer(ch)
            self.assertTrue(callable(fn))
            out = fn(self.result)
            self.assertIn("信息环境异常监测日报", out)


class TestSkippedAndFailed(unittest.TestCase):
    def test_skipped_shows_info(self):
        res = AIAnalysisResult(report_style="environment", success=False, skipped=True,
                               error="本轮无可分栏的异常信号")
        out = FMT.render_ai_analysis_markdown(res)
        self.assertIn("本轮无可分栏", out)

    def test_failed_shows_warning(self):
        res = AIAnalysisResult(report_style="environment", success=False, skipped=False,
                               error="AI 调用失败")
        out = FMT.render_ai_analysis_markdown(res)
        self.assertIn("失败", out)


class TestClassicRegression(unittest.TestCase):
    def test_classic_path_unchanged(self):
        res = AIAnalysisResult(
            report_style="classic", success=True,
            core_trends="核心趋势内容", sentiment_controversy="争议",
        )
        out = FMT.render_ai_analysis_markdown(res)
        self.assertIn("AI 热点分析", out)
        self.assertIn("核心热点态势", out)
        self.assertNotIn("信息环境异常监测日报", out)

    def test_default_report_style_is_classic_on_dataclass(self):
        # 未显式设置时，默认 classic（向后兼容旧调用方）
        res = AIAnalysisResult(success=True, core_trends="x")
        self.assertEqual(res.report_style, "classic")


if __name__ == "__main__":
    unittest.main()
