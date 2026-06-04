# coding=utf-8
"""
Current Dashboard 测试。

覆盖（见 plan 测试计划）：
- render（environment / classic / None）
- 不受 cooldown / notify_labels 影响：栏目外标签（如 silence_gap）也进盘面
- state.json schema + 发布安全（无 source_links / sample_titles / evidence_detail / 原始 URL）
- 职责边界：write_dashboard 只写 index/state/landing，不写 full.html；
  generate_html_report 写 public/{group}/full.html
- 入口收敛：output/index.html 为跳转页，不再写仓库根 index.html
- 发布隔离：public/ 下无 *.db / alert_state.json / *.log
- 回归：archive 快照 + html/latest/{mode}.html 仍写出
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
import _bootstrap  # noqa: E402

B = _bootstrap.load_all()
AIAnalysisResult = B.analyzer.AIAnalysisResult

_bootstrap._ensure_pkg("trendradar.report")
DASH = _bootstrap._load_file(
    "trendradar.report.dashboard", "trendradar/report/dashboard.py"
)
GEN = _bootstrap._load_file(
    "trendradar.report.generator", "trendradar/report/generator.py"
)

NOW = datetime(2026, 6, 4, 15, 30)
META = {"hotlist_total": 12, "platform_total": 8, "rss_matched_count": 3}

# 敏感字段哨兵：以下绝不应出现在 state.json / 盘面页中
SECRET_URL = "https://evil.example.com/raw-link"
SECRET_TITLE = "原始抓取头条不应外泄"


def make_env_result():
    return AIAnalysisResult(
        report_style="environment",
        success=True,
        overview="今日 D 层独热为主、跨层呼应偏少。",
        overview_stats={
            "total_items": 3,
            "label_counts": {
                "cross_layer_verified": 1,
                "high_heat_unverified": 1,
                "sentiment_heavy": 1,
                "silence_gap": 1,
                "chinese_only_hot": 0,
            },
            "background_count": 2,
            "layer_distribution": {"A": 1, "B": 1, "C": 2, "D": 3},
        },
        cross_layer_verified=[
            {
                "topic": "某跨层事件",
                "summary": "多源同时在动",
                "analysis": "研判细节",
                "source_layers": "A/C/D",
                "platform_count": 3,
                "highest_heat": "微博 第2名",
                "sentiment_flag": False,
                # 故意塞入敏感字段，验证发布产物不泄漏
                "sample_titles": [{"title": SECRET_TITLE, "source": "x"}],
                "source_links": [SECRET_URL],
                "evidence_detail": {"sources_by_tier": {"A": ["内部源"]}},
            }
        ],
        high_heat_unverified=[
            {
                "topic": "高热待核实事件",
                "summary": "纯 D 层高热",
                "source_layers": "D",
                "platform_count": 2,
                "highest_heat": "抖音 第5名",
            }
        ],
        silence_gap=[
            {
                "topic": "沉默温差事件",
                "summary": "外热中静",
                "source_layers": "A/B",
                "platform_count": 1,
                "highest_heat": "-",
            }
        ],
        sentiment_heavy=[
            {"topic": "情绪聚集事件", "source_layers": "D", "sentiment_flag": True}
        ],
    )


class TestRenderEnvironment(unittest.TestCase):
    def setUp(self):
        self.html = DASH.render_current_dashboard_html(
            make_env_result(), META, NOW, mode="current"
        )

    def test_is_standalone_document(self):
        self.assertIn("<!DOCTYPE html>", self.html)
        self.assertIn("<style>", self.html)  # 内联 CSS
        self.assertNotIn("<script", self.html)  # 无外部脚本引用

    def test_has_radar_overview_and_link(self):
        self.assertIn("当前盘面", self.html)
        self.assertIn("信号密度", self.html)
        self.assertIn("层级覆盖", self.html)
        self.assertIn("异常 3", self.html)
        self.assertIn("已抑制 3", self.html)
        self.assertIn("今日 D 层独热为主", self.html)  # overview
        self.assertIn('href="full.html"', self.html)
        self.assertIn("2026-06-04 15:30", self.html)  # 生成时间

    def test_section_cards_present(self):
        self.assertIn("跨层呼应", self.html)
        self.assertIn("高热待核实", self.html)
        self.assertIn("某跨层事件", self.html)

    def test_not_filtered_by_notify_labels(self):
        # silence_gap 不在默认 notify_labels（cross_layer/high_heat/chinese_only），
        # 实时 alert 会过滤掉它；dashboard 不受影响，应照常呈现。
        self.assertIn("沉默温差", self.html)
        self.assertIn("沉默温差事件", self.html)

    def test_no_secret_leak_in_html(self):
        self.assertNotIn(SECRET_URL, self.html)
        self.assertNotIn(SECRET_TITLE, self.html)
        # 有意从严：盘面页当前不应含任何绝对 URL（外部引用/原始链接）。
        # 若将来确需加入合法绝对 URL，此断言会"响亮失败"，提示同步收紧泄漏检查。
        self.assertNotIn("https://", self.html)
        self.assertNotIn("http://", self.html)


class TestRenderDegraded(unittest.TestCase):
    def test_none_ai_analysis(self):
        html = DASH.render_current_dashboard_html(None, META, NOW, mode="current")
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn('href="full.html"', html)
        self.assertIn("12", html)  # hotlist_total 计数透出

    def test_classic_style(self):
        classic = AIAnalysisResult(report_style="classic", success=True)
        html = DASH.render_current_dashboard_html(classic, META, NOW, mode="daily")
        self.assertIn("每日盘面", html)
        self.assertIn('href="full.html"', html)

    def test_daily_title(self):
        html = DASH.render_current_dashboard_html(
            make_env_result(), META, NOW, mode="daily"
        )
        self.assertIn("每日盘面", html)


class TestBuildState(unittest.TestCase):
    def setUp(self):
        self.state = DASH.build_dashboard_state(
            make_env_result(), META, NOW, mode="current"
        )

    def test_schema(self):
        for key in (
            "schema_version",
            "mode",
            "generated_at",
            "overview",
            "radar",
            "top_items",
            "counts",
        ):
            self.assertIn(key, self.state)
        self.assertEqual(self.state["mode"], "current")
        self.assertEqual(self.state["group"], "current")
        self.assertEqual(self.state["generated_at"], NOW.isoformat())
        self.assertEqual(self.state["counts"]["hotlist_total"], 12)

    def test_json_serializable(self):
        # 不抛异常即通过
        json.dumps(self.state, ensure_ascii=False)

    def test_top_items_whitelisted(self):
        items = self.state["top_items"]
        self.assertTrue(items)
        topics = [it.get("topic") for it in items]
        self.assertIn("某跨层事件", topics)
        # silence_gap 也应纳入（不受 notify_labels 限制）
        self.assertIn("沉默温差事件", topics)
        for it in items:
            self.assertIn("label", it)
            # 敏感键绝不透出
            for bad in (
                "sample_titles",
                "source_links",
                "evidence_detail",
                "sources_by_tier",
            ):
                self.assertNotIn(bad, it)

    def test_state_publish_safe(self):
        blob = json.dumps(self.state, ensure_ascii=False)
        self.assertNotIn(SECRET_URL, blob)
        self.assertNotIn(SECRET_TITLE, blob)
        self.assertNotIn("sample_titles", blob)
        self.assertNotIn("source_links", blob)
        self.assertNotIn("evidence_detail", blob)
        self.assertNotIn("http://", blob)
        self.assertNotIn("https://", blob)

    def test_state_none_ai(self):
        state = DASH.build_dashboard_state(None, META, NOW, mode="current")
        self.assertEqual(state["top_items"], [])
        self.assertEqual(state["report_style"], "none")
        json.dumps(state, ensure_ascii=False)

    def test_daily_group_mapping(self):
        # 固化 mode→group 契约：daily 路径保留 mode='daily' 且归入 group='daily'。
        state = DASH.build_dashboard_state(make_env_result(), META, NOW, mode="daily")
        self.assertEqual(state["mode"], "daily")
        self.assertEqual(state["group"], "daily")

    def test_incremental_maps_to_current_group(self):
        # current/incremental 同归 current group，但保留各自原始 mode。
        state = DASH.build_dashboard_state(make_env_result(), META, NOW, mode="incremental")
        self.assertEqual(state["mode"], "incremental")
        self.assertEqual(state["group"], "current")


class TestWriteDashboardLayout(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))

    def _public(self, *parts):
        return os.path.join(self.tmp, "public", *parts)

    def test_current_writes_index_state_landing_not_full(self):
        DASH.write_dashboard(self.tmp, "current", make_env_result(), META, NOW)
        self.assertTrue(os.path.exists(self._public("current", "index.html")))
        self.assertTrue(os.path.exists(self._public("current", "state.json")))
        self.assertTrue(os.path.exists(self._public("index.html")))  # landing
        # write_dashboard 不负责 full.html
        self.assertFalse(os.path.exists(self._public("current", "full.html")))

    def test_incremental_maps_to_current_group(self):
        DASH.write_dashboard(self.tmp, "incremental", make_env_result(), META, NOW)
        self.assertTrue(os.path.exists(self._public("current", "index.html")))
        self.assertFalse(os.path.exists(self._public("daily", "index.html")))

    def test_daily_isolated_from_current(self):
        DASH.write_dashboard(self.tmp, "daily", make_env_result(), META, NOW)
        self.assertTrue(os.path.exists(self._public("daily", "index.html")))
        self.assertTrue(os.path.exists(self._public("daily", "state.json")))
        self.assertFalse(os.path.exists(self._public("current", "index.html")))

    def test_state_file_publish_safe(self):
        DASH.write_dashboard(self.tmp, "current", make_env_result(), META, NOW)
        with open(self._public("current", "state.json"), encoding="utf-8") as f:
            blob = f.read()
        self.assertNotIn(SECRET_URL, blob)
        self.assertNotIn(SECRET_TITLE, blob)

    def test_publish_dir_has_no_sensitive_files(self):
        DASH.write_dashboard(self.tmp, "current", make_env_result(), META, NOW)
        DASH.write_dashboard(self.tmp, "daily", make_env_result(), META, NOW)
        for root, _dirs, files in os.walk(self._public()):
            for name in files:
                self.assertFalse(name.endswith(".db"), name)
                self.assertFalse(name.endswith(".log"), name)
                self.assertNotEqual(name, "alert_state.json")


class TestGeneratorFullAndEntry(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.output = os.path.join(self.tmp, "output")
        self._prev_cwd = os.getcwd()
        os.chdir(self.tmp)  # 隔离：捕获任何 cwd 相对的 index.html 写入
        self.addCleanup(lambda: os.chdir(self._prev_cwd))

    def _run(self, mode):
        return GEN.generate_html_report(
            stats=[],
            total_titles=0,
            mode=mode,
            output_dir=self.output,
            date_folder="2026-06-04",
            time_filename="15-30",
            render_html_func=lambda *a, **k: "<html>FULL REPORT BODY</html>",
        )

    def test_full_html_written_to_public_group(self):
        self._run("current")
        full = os.path.join(self.output, "public", "current", "full.html")
        self.assertTrue(os.path.exists(full))
        with open(full, encoding="utf-8") as f:
            self.assertIn("FULL REPORT BODY", f.read())

    def test_daily_full_html_group(self):
        self._run("daily")
        self.assertTrue(
            os.path.exists(os.path.join(self.output, "public", "daily", "full.html"))
        )

    def test_output_index_is_redirect_not_full_report(self):
        self._run("current")
        idx = os.path.join(self.output, "index.html")
        self.assertTrue(os.path.exists(idx))
        with open(idx, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("public/index.html", content)
        self.assertIn("refresh", content)
        self.assertNotIn("FULL REPORT BODY", content)

    def test_no_root_index_written(self):
        self._run("current")
        # 收敛后不再写仓库根 index.html（此处 cwd 为 tmp）
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "index.html")))

    def test_regression_archive_and_latest(self):
        self._run("current")
        snapshot = os.path.join(self.output, "html", "2026-06-04", "15-30.html")
        latest = os.path.join(self.output, "html", "latest", "current.html")
        self.assertTrue(os.path.exists(snapshot))
        self.assertTrue(os.path.exists(latest))


if __name__ == "__main__":
    unittest.main()
