# coding=utf-8
"""
静态文件验收：新 prompt、source_tiers.yaml、config.yaml 的关键内容。
不依赖第三方库（纯文本断言），保证在任意 Python 下可运行。
"""

import os
import sys
import importlib.util
import types
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import _bootstrap  # noqa: E402

ROOT = _bootstrap.ROOT


def read(relpath):
    with open(os.path.join(ROOT, relpath), "r", encoding="utf-8") as f:
        return f.read()


def load_file(name, relpath):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, relpath))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


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

    def test_alert_state_ttl_documented(self):
        self.assertIn("state_ttl_days: 14", self.text)
        self.assertIn("缺省/非法=14", self.text)
        self.assertIn("0 或负数=禁用 TTL 清理", self.text)

    def test_anthropic_feeds_use_official_html_fallback(self):
        self.assertIn('id: "anthropic-news-openrss"', self.text)
        self.assertIn('id: "anthropic-research-openrss"', self.text)

        news_start = self.text.index('id: "anthropic-news-openrss"')
        news_block = self.text[news_start:self.text.find("\n\n", news_start)]
        self.assertIn('url: "https://www.anthropic.com/news"', news_block)
        self.assertIn('source_type: "anthropic_html"', news_block)
        self.assertIn('link_prefixes: ["/news/"]', news_block)

        research_start = self.text.index('id: "anthropic-research-openrss"')
        research_block = self.text[research_start:self.text.find("\n\n", research_start)]
        self.assertIn('url: "https://www.anthropic.com/research"', research_block)
        self.assertIn('source_type: "anthropic_html"', research_block)
        self.assertIn('link_prefixes: ["/research/", "/news/"]', research_block)


class TestAlertConfigLoader(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _bootstrap._ensure_pkg("trendradar")
        _bootstrap._ensure_pkg("trendradar.core")
        _bootstrap._ensure_pkg("trendradar.utils")
        load_file("trendradar.core.config", "trendradar/core/config.py")
        if "trendradar.utils.time" not in sys.modules:
            time_mod = types.ModuleType("trendradar.utils.time")
            time_mod.DEFAULT_TIMEZONE = "Asia/Shanghai"
            sys.modules["trendradar.utils.time"] = time_mod
        if "yaml" not in sys.modules and importlib.util.find_spec("yaml") is None:
            yaml_mod = types.ModuleType("yaml")
            yaml_mod.safe_load = lambda *_args, **_kwargs: {}
            sys.modules["yaml"] = yaml_mod
        cls.loader = load_file("trendradar.core.loader", "trendradar/core/loader.py")

    def test_state_ttl_default_is_14(self):
        cfg = self.loader._load_alert_config({})
        self.assertEqual(cfg["STATE_TTL_DAYS"], 14)

    def test_state_ttl_positive_integer_loaded(self):
        cfg = self.loader._load_alert_config({"alert": {"state_ttl_days": 30}})
        self.assertEqual(cfg["STATE_TTL_DAYS"], 30)

    def test_state_ttl_invalid_falls_back_to_14(self):
        cfg = self.loader._load_alert_config({"alert": {"state_ttl_days": "bad"}})
        self.assertEqual(cfg["STATE_TTL_DAYS"], 14)

    def test_state_ttl_zero_and_negative_disable_ttl(self):
        zero = self.loader._load_alert_config({"alert": {"state_ttl_days": 0}})
        negative = self.loader._load_alert_config({"alert": {"state_ttl_days": -3}})
        self.assertEqual(zero["STATE_TTL_DAYS"], 0)
        self.assertEqual(negative["STATE_TTL_DAYS"], 0)

    def test_cooldown_minutes_default_is_180(self):
        cfg = self.loader._load_alert_config({})
        self.assertEqual(cfg["COOLDOWN_MINUTES"], 180)

    def test_cooldown_minutes_positive_integer_loaded(self):
        cfg = self.loader._load_alert_config({"alert": {"cooldown_minutes": 60}})
        self.assertEqual(cfg["COOLDOWN_MINUTES"], 60)

    def test_cooldown_minutes_invalid_falls_back_to_180(self):
        cfg = self.loader._load_alert_config({"alert": {"cooldown_minutes": "bad"}})
        self.assertEqual(cfg["COOLDOWN_MINUTES"], 180)

    def test_cooldown_minutes_negative_disables(self):
        cfg = self.loader._load_alert_config({"alert": {"cooldown_minutes": -5}})
        self.assertEqual(cfg["COOLDOWN_MINUTES"], 0)


class TestMainPipelineSource(unittest.TestCase):
    def test_ai_analysis_runs_when_only_rss_has_content(self):
        text = read("trendradar/__main__.py")
        self.assertIn('if ai_config.get("ENABLED", False) and (stats or rss_items):', text)


if __name__ == "__main__":
    unittest.main()
