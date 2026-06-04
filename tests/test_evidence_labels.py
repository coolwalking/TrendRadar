# coding=utf-8
"""
evidence 证据摘要 + 程序化分栏规则测试（验收核心）。

覆盖 evidence_label 的全部规则及优先级、bucketize、overview_stats、
prompt 渲染，以及"高热待核实必带固定风险提示"。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import _bootstrap  # noqa: E402

B = _bootstrap.load_all()
EV = B.evidence
T = _bootstrap.make_title


def label_of(stats, rss_stats=None):
    r = _bootstrap.make_resolver(B)
    items = EV.build_evidence(stats, rss_stats, r)
    return {it["topic_group"]: it["label"] for it in items}, items


class TestAssignLabelRules(unittest.TestCase):
    """直接测试 assign_label 的布尔规则矩阵。"""

    def test_cross_layer_needs_D_plus_AB(self):
        # D + A
        self.assertEqual(
            EV.assign_label(has_A=True, has_B=False, has_C=False, has_D=True,
                            high_d=True, sentiment_flag=False),
            "cross_layer_verified",
        )
        # D + B
        self.assertEqual(
            EV.assign_label(has_A=False, has_B=True, has_C=True, has_D=True,
                            high_d=False, sentiment_flag=False),
            "cross_layer_verified",
        )

    def test_chinese_only_requires_D_and_C_no_AB(self):
        self.assertEqual(
            EV.assign_label(has_A=False, has_B=False, has_C=True, has_D=True,
                            high_d=False, sentiment_flag=False),
            "chinese_only_hot",
        )

    def test_C_only_without_D_is_background(self):
        # 验收：仅 C 层、无 D 热度 -> background（None），不进中文源独热
        self.assertIsNone(
            EV.assign_label(has_A=False, has_B=False, has_C=True, has_D=False,
                            high_d=False, sentiment_flag=False)
        )

    def test_pure_D_high_heat_is_high_heat_unverified(self):
        self.assertEqual(
            EV.assign_label(has_A=False, has_B=False, has_C=False, has_D=True,
                            high_d=True, sentiment_flag=False),
            "high_heat_unverified",
        )

    def test_pure_D_high_heat_with_emotion_still_high_heat(self):
        # 验收第 3 点：high_heat 优先于 sentiment，情绪只是二级信号
        self.assertEqual(
            EV.assign_label(has_A=False, has_B=False, has_C=False, has_D=True,
                            high_d=True, sentiment_flag=True),
            "high_heat_unverified",
        )

    def test_pure_D_low_heat_with_emotion_is_sentiment(self):
        self.assertEqual(
            EV.assign_label(has_A=False, has_B=False, has_C=False, has_D=True,
                            high_d=False, sentiment_flag=True),
            "sentiment_heavy",
        )

    def test_pure_D_low_heat_no_emotion_is_background(self):
        self.assertIsNone(
            EV.assign_label(has_A=False, has_B=False, has_C=False, has_D=True,
                            high_d=False, sentiment_flag=False)
        )

    def test_silence_gap_AB_without_D(self):
        self.assertEqual(
            EV.assign_label(has_A=True, has_B=False, has_C=False, has_D=False,
                            high_d=False, sentiment_flag=False),
            "silence_gap",
        )


class TestBuildEvidenceEndToEnd(unittest.TestCase):
    """从 stats 出发，验证完整分栏。"""

    def test_full_matrix(self):
        stats = [
            {"word": "AI前沿模型", "titles": [T("GPT传闻", "微博", 5)]},
            {"word": "某明星瓜", "titles": [T("某事", "微博", 3), T("吃瓜", "抖音", 6)]},
            {"word": "饭圈骂战", "titles": [T("粉丝骂战破防了", "微博", 45)]},
            {"word": "国产芯片", "titles": [T("国产芯片", "微博", 4), T("芯片解读", "澎湃新闻", 9)]},
            {"word": "财经观察", "titles": [T("财报", "华尔街见闻", 12)]},
            {"word": "国际政策", "titles": []},  # 仅 RSS 背景
        ]
        rss_stats = [
            {"word": "AI前沿模型", "titles": [T("OpenAI ships", "OpenAI News", 1)]},
            {"word": "国际政策", "titles": [T("policy", "BBC World", 1)]},
        ]
        labels, items = label_of(stats, rss_stats)
        by_topic = {it["topic_group"]: it for it in items}
        self.assertEqual(labels["AI前沿模型"], "cross_layer_verified")
        self.assertEqual(by_topic["AI前沿模型"]["platform_count"], 2)
        self.assertEqual(labels["某明星瓜"], "high_heat_unverified")
        self.assertEqual(labels["饭圈骂战"], "sentiment_heavy")
        self.assertEqual(labels["国产芯片"], "chinese_only_hot")
        self.assertIsNone(labels["财经观察"])     # 仅 C -> background
        self.assertEqual(labels["国际政策"], "silence_gap")

    def test_high_d_by_two_platforms(self):
        # 两个 D 平台即算高热（即使排名都不靠前）
        stats = [{"word": "X", "titles": [T("a", "微博", 30), T("b", "抖音", 28)]}]
        labels, _ = label_of(stats)
        self.assertEqual(labels["X"], "high_heat_unverified")

    def test_high_d_by_rank_threshold(self):
        # 单平台但排名 <=10 也算高热
        stats = [{"word": "X", "titles": [T("a", "微博", 8)]}]
        labels, _ = label_of(stats)
        self.assertEqual(labels["X"], "high_heat_unverified")

    def test_single_platform_low_rank_no_emotion_background(self):
        stats = [{"word": "X", "titles": [T("普通事件", "微博", 35)]}]
        labels, _ = label_of(stats)
        self.assertIsNone(labels["X"])

    def test_unknown_sources_do_not_create_layers(self):
        # 未在分层表中的来源 -> unknown，不应被当作 A/B/C/D
        stats = [{"word": "X", "titles": [T("a", "某野鸡站", 1)]}]
        labels, items = label_of(stats)
        self.assertIsNone(labels["X"])
        self.assertEqual(items[0]["source_layers"], "-")

    def test_sentiment_flag_recorded_on_high_heat(self):
        # 高热 + 情绪：归 high_heat，但 sentiment_flag 记为 True
        stats = [{"word": "X", "titles": [T("塌房了离谱", "微博", 2), T("塌房", "抖音", 3)]}]
        _, items = label_of(stats)
        it = items[0]
        self.assertEqual(it["label"], "high_heat_unverified")
        self.assertTrue(it["sentiment_flag"])

    def test_source_links_are_program_copied_not_prompted(self):
        stats = [{
            "word": "AI前沿模型",
            "titles": [T("GPT传闻", "微博", 5, url="https://example.com/weibo?q=1")],
        }]
        rss_stats = [{
            "word": "AI前沿模型",
            "titles": [T("OpenAI ships", "OpenAI News", 1, url="https://example.com/openai")],
        }]
        _, items = label_of(stats, rss_stats)
        item = items[0]
        links = item["source_links"]
        self.assertEqual(links[0]["url"], "https://example.com/openai")
        self.assertEqual(links[0]["tier"], "A")
        self.assertEqual(links[1]["url"], "https://example.com/weibo?q=1")

        buckets = EV.bucketize(items)
        prompt = EV.render_evidence_for_prompt(buckets, EV.build_overview_stats(buckets))
        self.assertNotIn("https://example.com/openai", prompt)
        self.assertNotIn("https://example.com/weibo", prompt)


class TestBucketizeAndOverview(unittest.TestCase):
    def _items(self):
        stats = [
            {"word": "G1", "titles": [T("a", "微博", 3), T("b", "抖音", 4)]},  # high_heat
            {"word": "G2", "titles": [T("c", "华尔街见闻", 11)]},               # background
        ]
        return EV.build_evidence(stats, None, _bootstrap.make_resolver(B))

    def test_bucketize_assigns_background_for_none_label(self):
        buckets = EV.bucketize(self._items())
        self.assertEqual(len(buckets["high_heat_unverified"]), 1)
        self.assertEqual(len(buckets["background"]), 1)
        # 所有声明的栏目 key 都存在
        for k in EV.BUCKET_ORDER:
            self.assertIn(k, buckets)

    def test_overview_stats_shape(self):
        buckets = EV.bucketize(self._items())
        stats = EV.build_overview_stats(buckets)
        self.assertEqual(stats["total_items"], 1)
        self.assertEqual(stats["background_count"], 1)
        self.assertEqual(stats["label_counts"]["high_heat_unverified"], 1)
        self.assertIn("layer_distribution", stats)
        self.assertEqual(stats["layer_distribution"]["D"], 1)

    def test_render_evidence_contains_risk_boundary_and_samples(self):
        buckets = EV.bucketize(self._items())
        ostats = EV.build_overview_stats(buckets)
        text = EV.render_evidence_for_prompt(buckets, ostats)
        self.assertIn("高热待核实", text)
        self.assertIn("代表性传播文本", text)
        # 高热栏目边界必须是固定风险提示
        self.assertIn(EV.RISK_NOTE_HIGH_HEAT, text)

    def test_render_empty_buckets_graceful(self):
        buckets = EV.bucketize([])
        ostats = EV.build_overview_stats(buckets)
        text = EV.render_evidence_for_prompt(buckets, ostats)
        self.assertTrue(text.strip())  # 不报错、非空


class TestPresentationLayer(unittest.TestCase):
    """呈现层重构：SECTION_ORDER 解耦、盘面读数、情绪降为属性并入已抑制。"""

    def _mixed_buckets(self):
        # cross_layer(D+A) / high_heat(纯D高) / 低热情绪(纯D低+情绪) / 仅C背景
        stats = [
            {"word": "AI模型", "titles": [T("传闻", "微博", 5)]},
            {"word": "明星瓜", "titles": [T("吃瓜", "微博", 3), T("再瓜", "抖音", 6)]},
            {"word": "饭圈骂战", "titles": [T("粉丝骂战破防了", "微博", 45)]},
            {"word": "财经观察", "titles": [T("财报", "华尔街见闻", 12)]},
        ]
        rss = [{"word": "AI模型", "titles": [T("OpenAI ships", "OpenAI News", 1)]}]
        items = EV.build_evidence(stats, rss, _bootstrap.make_resolver(B))
        return EV.bucketize(items)

    def test_section_order_excludes_sentiment(self):
        self.assertNotIn("sentiment_heavy", EV.SECTION_ORDER)
        self.assertIn("sentiment_heavy", EV.SUPPRESSED_BUCKETS)
        # 四个监测栏目按阅读动作排序
        self.assertEqual(
            EV.SECTION_ORDER,
            ["cross_layer_verified", "high_heat_unverified", "chinese_only_hot", "silence_gap"],
        )

    def test_derive_radar_readout_excludes_sentiment_from_anomaly(self):
        buckets = self._mixed_buckets()
        ostats = EV.build_overview_stats(buckets)
        r = EV.derive_radar_readout(ostats)
        # 异常仅含 cross_layer + high_heat（情绪不计入异常）
        self.assertEqual(r["anomaly"], 2)
        # 已抑制 = 背景(财经观察) + 低热情绪(饭圈骂战)
        self.assertEqual(r["suppressed"], 2)
        self.assertEqual(r["cross_layer"], 1)
        self.assertEqual(r["high_heat"], 1)

    def test_render_folds_sentiment_into_suppressed(self):
        buckets = self._mixed_buckets()
        ostats = EV.build_overview_stats(buckets)
        text = EV.render_evidence_for_prompt(buckets, ostats)
        # 不再有独立的"情绪型舆论/情绪聚集"分栏
        self.assertNotIn("情绪型舆论", text)
        self.assertNotIn("## [情绪聚集]", text)
        # 低热情绪项折叠进"已抑制"，且仍以属性标注
        self.assertIn("已抑制", text)
        self.assertIn("饭圈骂战", text)
        self.assertIn("含情绪信号", text)
        # 新名生效、旧名清除
        self.assertIn("跨层呼应", text)
        self.assertNotIn("跨层验证事件", text)

    def test_overview_stats_prompt_is_radar_framed(self):
        buckets = self._mixed_buckets()
        ostats = EV.build_overview_stats(buckets)
        line = EV.render_overview_stats_for_prompt(ostats)
        self.assertIn("今日盘面", line)
        self.assertIn("热度↔证据错位", line)
        self.assertIn("中外温差", line)


class TestConstants(unittest.TestCase):
    def test_risk_note_constant(self):
        self.assertEqual(
            EV.RISK_NOTE_HIGH_HEAT,
            "当前仅能确认传播正在发生，不能确认事件已经成立。",
        )

    def test_bucket_order_matches_labels(self):
        self.assertEqual(set(EV.BUCKET_ORDER), set(EV.LABELS.keys()))


if __name__ == "__main__":
    unittest.main()
