# coding=utf-8
"""
静态文件验收：新 prompt、source_tiers.yaml、config.yaml 的关键内容。
不依赖第三方库（纯文本断言），保证在任意 Python 下可运行。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import _bootstrap  # noqa: E402

ROOT = _bootstrap.ROOT


def read(relpath):
    with open(os.path.join(ROOT, relpath), "r", encoding="utf-8") as f:
        return f.read()


class TestEnvironmentPromptFile(unittest.TestCase):
    def setUp(self):
        self.text = read("config/ai_environment_report_prompt.txt")

    def test_has_system_and_user_sections(self):
        self.assertIn("[system]", self.text)
        self.assertIn("[user]", self.text)

    def test_injects_evidence_not_raw_titles(self):
        # 注入结构化证据 + 统计骨架
        self.assertIn("{evidence_summary}", self.text)
        self.assertIn("{overview_stats}", self.text)
        self.assertIn("{current_time}", self.text)
        self.assertIn("{language}", self.text)
        # 不得注入 classic 的 raw title 池
        self.assertNotIn("{news_content}", self.text)
        self.assertNotIn("{rss_content}", self.text)

    def test_role_is_monitor_editor_not_intelligence_analyst(self):
        # 用真实 loader 取出 [system] 段，确保 AI 角色不是旧版"高级情报分析师"
        import trendradar.ai.prompt_loader as pl
        system, _ = pl.load_prompt_template("ai_environment_report_prompt.txt", label="AI")
        self.assertIn("信息环境异常监测报告编辑", system)
        # 旧版 classic system 的标志句不应出现在新角色定义中
        self.assertNotIn("你是一名高级情报分析师", system)

    def test_forbids_changing_verification_status(self):
        self.assertIn("verification_status", self.text)
        # 明确约束 sample_titles 不得当事实复述（验收第 4 点）
        self.assertIn("sample_titles", self.text)

    def test_real_prompt_parses_into_system_user(self):
        # 用真实 prompt_loader 解析，确认能拆出非空 system / user
        import trendradar.ai.prompt_loader as pl
        system, user = pl.load_prompt_template("ai_environment_report_prompt.txt", label="AI")
        self.assertTrue(system.strip())
        self.assertTrue(user.strip())
        self.assertIn("{evidence_summary}", user)


class TestSourceTiersYaml(unittest.TestCase):
    def setUp(self):
        self.text = read("config/source_tiers.yaml")

    def test_has_tier_sections(self):
        for key in ["tiers:", "platforms:", "rss_feeds:"]:
            self.assertIn(key, self.text)

    def test_known_platform_tiers_declared(self):
        # 抽查几个关键映射
        self.assertIn("weibo:", self.text)
        self.assertIn("toutiao:", self.text)
        self.assertIn("openai-news:", self.text)
        self.assertIn("bbc-world:", self.text)
        self.assertIn("ruanyifeng:", self.text)


class TestConfigYaml(unittest.TestCase):
    def setUp(self):
        self.text = read("config/config.yaml")

    def test_report_style_default_environment(self):
        self.assertIn("report_style:", self.text)
        self.assertIn('report_style: "environment"', self.text)

    def test_environment_prompt_file_configured(self):
        self.assertIn("environment_prompt_file:", self.text)
        self.assertIn("ai_environment_report_prompt.txt", self.text)


class TestMainPipelineSource(unittest.TestCase):
    def test_ai_analysis_runs_when_only_rss_has_content(self):
        text = read("trendradar/__main__.py")
        self.assertIn('if ai_config.get("ENABLED", False) and (stats or rss_items):', text)


if __name__ == "__main__":
    unittest.main()
