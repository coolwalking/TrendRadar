# coding=utf-8
"""SourceTierResolver 来源分层解析器测试。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import _bootstrap  # noqa: E402

B = _bootstrap.load_all()
ST = B.source_tiers


class TestSourceTierResolver(unittest.TestCase):
    def setUp(self):
        self.r = _bootstrap.make_resolver(B)

    def test_lookup_by_display_name(self):
        self.assertEqual(self.r.tier_of("微博"), "D")
        self.assertEqual(self.r.tier_of("知乎"), "C")
        self.assertEqual(self.r.tier_of("OpenAI News"), "A")
        self.assertEqual(self.r.tier_of("BBC World"), "B")

    def test_lookup_by_id(self):
        # stats 里是显示名，但 resolver 也应支持 id 直接查询
        self.assertEqual(self.r.tier_of("weibo"), "D")
        self.assertEqual(self.r.tier_of("openai-news"), "A")

    def test_unknown_source_returns_unknown(self):
        self.assertEqual(self.r.tier_of("某不存在平台"), "unknown")
        self.assertEqual(self.r.tier_of(""), "unknown")

    def test_role_lookup(self):
        self.assertEqual(self.r.role_of("微博"), "social_hotlist")
        self.assertEqual(self.r.role_of("不存在"), "")

    def test_tier_normalization(self):
        # 小写 tier 归一化为大写；非法 tier -> unknown
        r = ST.SourceTierResolver(
            {"platforms": {"x": {"tier": "d"}, "y": {"tier": "Z"}, "z": {}}},
            [{"id": "x", "name": "X"}, {"id": "y", "name": "Y"}, {"id": "z", "name": "Z"}],
            [],
        )
        self.assertEqual(r.tier_of("X"), "D")       # 小写归一
        self.assertEqual(r.tier_of("Y"), "unknown")  # 非法层级
        self.assertEqual(r.tier_of("Z"), "unknown")  # 缺 tier 字段

    def test_empty_resolver_all_unknown(self):
        # 模拟 source_tiers.yaml 缺失：全部返回 unknown，不报错（验收第 2 点）
        r = ST.SourceTierResolver()
        self.assertEqual(r.tier_of("微博"), "unknown")
        self.assertEqual(r.tier_of("anything"), "unknown")

    def test_build_from_config_dict(self):
        config = {
            "_SOURCE_TIERS": {
                "platforms": {"weibo": {"tier": "D"}},
                "rss_feeds": {"openai-news": {"tier": "A"}},
            },
            "PLATFORMS": [{"id": "weibo", "name": "微博"}],
            "RSS": {"FEEDS": [{"id": "openai-news", "name": "OpenAI News"}]},
        }
        r = ST.build_source_tier_resolver(config)
        self.assertEqual(r.tier_of("微博"), "D")
        self.assertEqual(r.tier_of("OpenAI News"), "A")

    def test_build_from_config_missing_keys(self):
        # config 缺少相关键时不应崩溃
        r = ST.build_source_tier_resolver({})
        self.assertEqual(r.tier_of("微博"), "unknown")


if __name__ == "__main__":
    unittest.main()
