# coding=utf-8
"""
newsletter 渲染器 _nl_ai_item_compact 正文 fallback / 风险去重测试。

回归 PR #18 引入的缺陷：daily full report 中 AI 异常条目仅读 summary 渲染
ai-body，AI 失败/返回空时缺失 ai-body。修复为 summary → analysis →
factual_boundary 三阶 fallback，并对风险提示做与正文的去重。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import _bootstrap  # noqa: E402

_bootstrap.load_all()  # 注册 trendradar.ai.evidence 等依赖到 sys.modules
_bootstrap._ensure_pkg("trendradar.report")
NL = _bootstrap._load_file(
    "trendradar.report.newsletter", "trendradar/report/newsletter.py"
)
_item = NL._nl_ai_item_compact


class TestNewsletterAiBodyFallback(unittest.TestCase):
    def test_summary_present_used_as_body(self):
        out = _item({"topic": "T", "summary": "正文摘要", "analysis": "分析",
                     "factual_boundary": "边界"})
        self.assertIn('<div class="ai-body">正文摘要</div>', out)

    def test_fallback_to_analysis_when_summary_empty(self):
        out = _item({"topic": "T", "summary": "", "analysis": "分析正文",
                     "factual_boundary": "边界"})
        self.assertIn('<div class="ai-body">分析正文</div>', out)

    def test_fallback_to_factual_boundary_when_summary_and_analysis_empty(self):
        # 核心 bug 场景：AI 失败/返回空，仅 factual_boundary 有值
        out = _item({"topic": "饭圈骂战", "summary": "", "analysis": "",
                     "factual_boundary": "仅情绪聚集，无事实核验"})
        self.assertIn('<div class="ai-body">仅情绪聚集，无事实核验</div>', out)

    def test_ai_body_always_present_with_factual_boundary(self):
        # factual_boundary 为程序常量始终有值 → ai-body 永不缺失
        out = _item({"topic": "T", "factual_boundary": "边界说明"})
        self.assertIn("ai-body", out)


class TestNewsletterRiskDedup(unittest.TestCase):
    def test_risk_skipped_when_equal_to_body(self):
        # 正文 fallback 到 factual_boundary，risk 也回退 factual_boundary → 去重
        out = _item({"topic": "T", "summary": "", "analysis": "",
                     "factual_boundary": "同一句话"})
        self.assertIn('<div class="ai-body">同一句话</div>', out)
        self.assertNotIn("ai-risk", out)

    def test_risk_rendered_when_different_from_body(self):
        out = _item({"topic": "T", "summary": "正文", "risk_note": "风险提示"})
        self.assertIn('<div class="ai-body">正文</div>', out)
        self.assertIn('<div class="ai-risk">风险提示</div>', out)

    def test_factual_boundary_as_risk_when_body_is_summary(self):
        # 正文取 summary，risk 回退 factual_boundary，二者不同 → 都渲染
        out = _item({"topic": "T", "summary": "正文", "factual_boundary": "边界"})
        self.assertIn('<div class="ai-body">正文</div>', out)
        self.assertIn('<div class="ai-risk">边界</div>', out)


class TestNewsletterStripAndEscape(unittest.TestCase):
    def test_strip_before_escape(self):
        out = _item({"topic": "T", "summary": "  含空白  "})
        self.assertIn('<div class="ai-body">含空白</div>', out)

    def test_body_html_escaped(self):
        out = _item({"topic": "T", "summary": "<b>x</b> & y"})
        self.assertIn("&lt;b&gt;x&lt;/b&gt; &amp; y", out)
        self.assertNotIn("<b>x</b>", out)

    def test_empty_item_renders_no_body_no_risk(self):
        out = _item({"topic": "T"})
        self.assertNotIn("ai-body", out)
        self.assertNotIn("ai-risk", out)
        self.assertIn('<div class="ai-topic">T</div>', out)


if __name__ == "__main__":
    unittest.main()
