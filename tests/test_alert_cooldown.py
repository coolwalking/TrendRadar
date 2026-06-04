# coding=utf-8
"""
实时异常提醒 cooldown / 去重 / 升级再推 纯逻辑测试。

覆盖（plan 七）：
- #2 cooldown 内重复同 topic_key → 不推
- #3 cooldown 后重复 → 可推
- #4 cooldown 内 label 升级 → 可推
- #5 来源层升级（D-only → A/B/D / C/D）→ 可推
- #6 high_heat_unverified 不满足 rank/platform gate → 不推
- #7 high_heat_unverified 满足 gate → 推
- #12 topic_key 规范化
- #13 parse_rank 解析失败 → fail-open
- #14 topic_key 为空 → 保留且不入 state
- AlertStateStore 内存往返 / commit 累加 pushed_count
"""

import datetime
import importlib.util
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, relpath):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, relpath))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# alert_state 是纯标准库模块，可直接按文件加载（不触发重型 __init__）
AS = _load("trendradar.ai.alert_state", "trendradar/ai/alert_state.py")

DEFAULT_CFG = {
    "cooldown_minutes": 180,
    "max_items": 3,
    "allow_upgrade_break_cooldown": True,
    "high_heat_min_rank": 10,
    "high_heat_min_platforms": 2,
}


def item(topic, label="cross_layer_verified", layers="A/D", heat="微博 第5名",
         d_platforms=2, platform_count=2):
    """构造一条 environment 候选 item（mimic analyzer 渲染产物）。"""
    return {
        "topic": topic,
        "source_layers": layers,
        "highest_heat": heat,
        "platform_count": platform_count,
        "evidence_detail": {
            "d_tier_platform_count": d_platforms,
            "highest_d_tier_rank": {"platform": "微博", "rank": _rank_from_heat(heat)},
        },
    }


def _rank_from_heat(heat):
    import re
    m = re.search(r"第\s*(\d+)\s*名", heat or "")
    return int(m.group(1)) if m else None


def state_from(label, it, when):
    """模拟一条已落盘的 state record。"""
    return AS.build_state_record(label, it, when)


T0 = datetime.datetime(2026, 6, 4, 8, 0, 0)


class TestTopicKey(unittest.TestCase):
    def test_normalization_merges_variants(self):  # #12
        # 全角标点 / 空格 / 大小写 归一到同 key；保留中文
        self.assertEqual(AS.topic_key("AI 前沿模型！"), AS.topic_key("ai前沿模型"))
        self.assertEqual(AS.topic_key("某·明星  瓜"), AS.topic_key("某明星瓜"))

    def test_distinct_topics_not_merged(self):
        self.assertNotEqual(AS.topic_key("某明星瓜"), AS.topic_key("某明星塌房"))

    def test_empty_topic(self):  # #14 (key 层面)
        self.assertEqual(AS.topic_key("   "), "")
        self.assertEqual(AS.topic_key(""), "")


class TestParseRank(unittest.TestCase):
    def test_structured_rank(self):
        self.assertEqual(AS.parse_rank(item("x", heat="微博 第3名")), 3)

    def test_fallback_regex(self):
        it = {"topic": "x", "highest_heat": "抖音 第7名", "evidence_detail": {}}
        self.assertEqual(AS.parse_rank(it), 7)

    def test_unparseable_returns_none(self):  # #13
        it = {"topic": "x", "highest_heat": "-", "evidence_detail": {}}
        self.assertIsNone(AS.parse_rank(it))


class TestHeatGate(unittest.TestCase):
    def test_high_heat_blocked_when_weak(self):  # #6
        it = item("弱高热", label="high_heat_unverified", layers="D",
                  heat="微博 第40名", d_platforms=1, platform_count=1)
        self.assertFalse(AS.passes_heat_gate("high_heat_unverified", it, DEFAULT_CFG))

    def test_high_heat_passes_by_rank(self):  # #7
        it = item("强高热", label="high_heat_unverified", layers="D",
                  heat="微博 第3名", d_platforms=1, platform_count=1)
        self.assertTrue(AS.passes_heat_gate("high_heat_unverified", it, DEFAULT_CFG))

    def test_high_heat_passes_by_platforms(self):  # #7
        it = item("多平台高热", label="high_heat_unverified", layers="D",
                  heat="微博 第40名", d_platforms=2, platform_count=2)
        self.assertTrue(AS.passes_heat_gate("high_heat_unverified", it, DEFAULT_CFG))

    def test_other_labels_not_gated(self):
        it = item("跨层", label="cross_layer_verified", layers="A/D",
                  heat="微博 第40名", d_platforms=1, platform_count=1)
        self.assertTrue(AS.passes_heat_gate("cross_layer_verified", it, DEFAULT_CFG))


class TestCooldown(unittest.TestCase):
    def test_first_seen_kept(self):  # #1 (纯逻辑层)
        items = [("cross_layer_verified", item("新议题"))]
        kept = AS.apply_alert_cooldown(items, store={}, now=T0, cfg=DEFAULT_CFG)
        self.assertEqual(len(kept), 1)

    def test_within_cooldown_dropped(self):  # #2
        it = item("热议题", label="high_heat_unverified", layers="D",
                  heat="微博 第3名")
        store = {AS.topic_key("热议题"): state_from("high_heat_unverified", it, T0)}
        now = T0 + datetime.timedelta(minutes=60)
        kept = AS.apply_alert_cooldown([("high_heat_unverified", it)], store, now, DEFAULT_CFG)
        self.assertEqual(kept, [])

    def test_after_cooldown_kept(self):  # #3
        it = item("热议题", label="high_heat_unverified", layers="D", heat="微博 第3名")
        store = {AS.topic_key("热议题"): state_from("high_heat_unverified", it, T0)}
        now = T0 + datetime.timedelta(minutes=200)
        kept = AS.apply_alert_cooldown([("high_heat_unverified", it)], store, now, DEFAULT_CFG)
        self.assertEqual(len(kept), 1)

    def test_label_upgrade_breaks_cooldown(self):  # #4
        prev_it = item("升级议题", label="high_heat_unverified", layers="D", heat="微博 第3名")
        store = {AS.topic_key("升级议题"): state_from("high_heat_unverified", prev_it, T0)}
        # 同议题这次变成 cross_layer_verified（含 A 层）
        now_it = item("升级议题", label="cross_layer_verified", layers="A/D", heat="微博 第3名")
        now = T0 + datetime.timedelta(minutes=30)
        kept = AS.apply_alert_cooldown([("cross_layer_verified", now_it)], store, now, DEFAULT_CFG)
        self.assertEqual(len(kept), 1)

    def test_layer_upgrade_breaks_cooldown(self):  # #5
        prev_it = item("层升级", label="high_heat_unverified", layers="D", heat="微博 第3名")
        store = {AS.topic_key("层升级"): state_from("high_heat_unverified", prev_it, T0)}
        # label 不变，但新增 C 层（D-only → C/D）
        now_it = item("层升级", label="high_heat_unverified", layers="C/D", heat="微博 第3名")
        now = T0 + datetime.timedelta(minutes=30)
        kept = AS.apply_alert_cooldown([("high_heat_unverified", now_it)], store, now, DEFAULT_CFG)
        self.assertEqual(len(kept), 1)

    def test_no_upgrade_when_only_d_rank_changes(self):
        # 名次变化不算升级：仍在 cooldown 内被丢弃
        prev_it = item("纯波动", label="high_heat_unverified", layers="D", heat="微博 第8名")
        store = {AS.topic_key("纯波动"): state_from("high_heat_unverified", prev_it, T0)}
        now_it = item("纯波动", label="high_heat_unverified", layers="D", heat="微博 第2名")
        now = T0 + datetime.timedelta(minutes=30)
        kept = AS.apply_alert_cooldown([("high_heat_unverified", now_it)], store, now, DEFAULT_CFG)
        self.assertEqual(kept, [])

    def test_heat_gate_drops_weak_high_heat_even_first_seen(self):  # #6
        it = item("弱高热", label="high_heat_unverified", layers="D",
                  heat="微博 第40名", d_platforms=1, platform_count=1)
        kept = AS.apply_alert_cooldown([("high_heat_unverified", it)], store={}, now=T0, cfg=DEFAULT_CFG)
        self.assertEqual(kept, [])

    def test_empty_topic_kept_not_stored(self):  # #14
        it = item("   ")
        store = {}
        kept = AS.apply_alert_cooldown([("cross_layer_verified", it)], store, T0, DEFAULT_CFG)
        self.assertEqual(len(kept), 1)  # 仍推
        self.assertEqual(store, {})     # 但不入 state

    def test_corrupt_timestamp_fail_open(self):  # fail-open
        store = {AS.topic_key("坏状态"): {"last_pushed_at": "not-a-date", "last_severity": 1}}
        it = item("坏状态")
        kept = AS.apply_alert_cooldown([("cross_layer_verified", it)], store, T0, DEFAULT_CFG)
        self.assertEqual(len(kept), 1)

    def test_max_items_truncation(self):
        items = [("cross_layer_verified", item(f"议题{i}")) for i in range(5)]
        kept = AS.apply_alert_cooldown(items, store={}, now=T0, cfg={**DEFAULT_CFG, "max_items": 3})
        self.assertEqual(len(kept), 3)

    def test_tzaware_now_does_not_crash(self):
        # 生产 get_time_func 返回 tz-aware；与 naive 落盘时间比较不应抛错
        try:
            import zoneinfo
            tz = zoneinfo.ZoneInfo("Asia/Shanghai")
        except Exception:
            self.skipTest("zoneinfo 不可用")
        it = item("时区议题", label="high_heat_unverified", layers="D", heat="微博 第3名")
        store = {AS.topic_key("时区议题"): state_from("high_heat_unverified", it, T0)}
        now = datetime.datetime(2026, 6, 4, 9, 0, 0, tzinfo=tz)
        kept = AS.apply_alert_cooldown([("high_heat_unverified", it)], store, now, DEFAULT_CFG)
        self.assertEqual(kept, [])  # 60 min 内仍冷却


class TestAlertStateStore(unittest.TestCase):
    def test_commit_and_get_roundtrip(self):
        store = AS.AlertStateStore(backend=None)
        it = item("记忆议题", label="high_heat_unverified", layers="D", heat="微博 第3名")
        self.assertIsNone(store.get(AS.topic_key("记忆议题")))
        store.commit([("high_heat_unverified", it)], T0)
        rec = store.get(AS.topic_key("记忆议题"))
        self.assertIsNotNone(rec)
        self.assertEqual(rec["pushed_count"], 1)
        self.assertEqual(rec["last_label"], "high_heat_unverified")

    def test_commit_increments_pushed_count(self):
        store = AS.AlertStateStore(backend=None)
        it = item("累计议题")
        store.commit([("cross_layer_verified", it)], T0)
        store.commit([("cross_layer_verified", it)], T0 + datetime.timedelta(hours=4))
        rec = store.get(AS.topic_key("累计议题"))
        self.assertEqual(rec["pushed_count"], 2)

    def test_backend_load_failure_fail_open(self):
        class BoomBackend:
            def get_alert_state(self):
                raise RuntimeError("boom")

        store = AS.AlertStateStore(backend=BoomBackend())
        self.assertIsNone(store.get("anything"))  # 不抛错，按空状态

    def test_backend_save_called(self):
        saved = {}

        class FakeBackend:
            def get_alert_state(self):
                return {}

            def save_alert_state(self, data):
                saved.update(data)
                return True

        store = AS.AlertStateStore(backend=FakeBackend())
        it = item("落盘议题")
        ok = store.commit([("cross_layer_verified", it)], T0)
        self.assertTrue(ok)
        self.assertEqual(saved.get("version"), 1)
        self.assertIn(AS.topic_key("落盘议题"), saved["topics"])

    def test_commit_prunes_expired_valid_records_before_save(self):
        saved = {}
        fresh = item("新鲜旧议题")
        old = item("过期旧议题")

        class FakeBackend:
            def get_alert_state(self):
                return {
                    "version": 1,
                    "topics": {
                        AS.topic_key("新鲜旧议题"): state_from(
                            "cross_layer_verified",
                            fresh,
                            T0 - datetime.timedelta(days=13),
                        ),
                        AS.topic_key("过期旧议题"): state_from(
                            "cross_layer_verified",
                            old,
                            T0 - datetime.timedelta(days=15),
                        ),
                        "bad-ts": {"last_pushed_at": "not-a-date"},
                        "legacy-list": ["legacy", "malformed"],
                    },
                }

            def save_alert_state(self, data):
                saved.update(data)
                return True

        store = AS.AlertStateStore(
            backend=FakeBackend(), state_ttl_days=14, cooldown_minutes=180,
        )
        store.commit([("cross_layer_verified", item("本轮新议题"))], T0)

        topics = saved["topics"]
        self.assertIn(AS.topic_key("新鲜旧议题"), topics)
        self.assertNotIn(AS.topic_key("过期旧议题"), topics)
        self.assertIn(AS.topic_key("本轮新议题"), topics)
        self.assertIn("bad-ts", topics)       # malformed records fail-open 保留
        self.assertIn("legacy-list", topics)  # legacy malformed records 保留

    def test_state_ttl_zero_disables_prune(self):
        saved = {}
        old = item("很旧议题")

        class FakeBackend:
            def get_alert_state(self):
                return {
                    "topics": {
                        AS.topic_key("很旧议题"): state_from(
                            "cross_layer_verified",
                            old,
                            T0 - datetime.timedelta(days=365),
                        ),
                    },
                }

            def save_alert_state(self, data):
                saved.update(data)
                return True

        store = AS.AlertStateStore(
            backend=FakeBackend(), state_ttl_days=0, cooldown_minutes=180,
        )
        store.commit([], T0)

        self.assertIn(AS.topic_key("很旧议题"), saved["topics"])

    def test_effective_ttl_not_shorter_than_cooldown_keeps_topic_still_in_cooldown(self):
        # effective TTL = max(TTL, cooldown) 只防止过短 TTL 破坏 cooldown 语义；
        # cooldown 是否抑制推送仍由 apply_alert_cooldown 单独判断。
        saved = {}
        it = item("长冷却议题")

        class FakeBackend:
            def get_alert_state(self):
                return {
                    "topics": {
                        AS.topic_key("长冷却议题"): state_from(
                            "cross_layer_verified",
                            it,
                            T0 - datetime.timedelta(hours=36),
                        ),
                    },
                }

            def save_alert_state(self, data):
                saved.update(data)
                return True

        store = AS.AlertStateStore(
            backend=FakeBackend(),
            state_ttl_days=1,
            cooldown_minutes=48 * 60,
        )
        store.commit([], T0)

        self.assertIn(AS.topic_key("长冷却议题"), saved["topics"])

    def test_expired_same_topic_starts_fresh_pushed_count(self):
        saved = {}
        it = item("重新出现议题")
        expired = state_from(
            "cross_layer_verified",
            it,
            T0 - datetime.timedelta(days=30),
        )
        expired["pushed_count"] = 9

        class FakeBackend:
            def get_alert_state(self):
                return {"topics": {AS.topic_key("重新出现议题"): expired}}

            def save_alert_state(self, data):
                saved.update(data)
                return True

        store = AS.AlertStateStore(
            backend=FakeBackend(), state_ttl_days=14, cooldown_minutes=180,
        )
        store.commit([("cross_layer_verified", it)], T0)

        rec = saved["topics"][AS.topic_key("重新出现议题")]
        self.assertEqual(rec["pushed_count"], 1)


class TestAlertStateStoreDefaults(unittest.TestCase):
    """AlertStateStore 构造函数默认值与非法值回退测试。"""

    def test_default_state_ttl_days_is_14(self):
        store = AS.AlertStateStore()
        self.assertEqual(store._state_ttl_days, 14)

    def test_default_cooldown_minutes_is_180(self):
        store = AS.AlertStateStore()
        self.assertEqual(store._cooldown_minutes, 180)

    def test_invalid_state_ttl_days_falls_back_to_14(self):
        store = AS.AlertStateStore(state_ttl_days="bad")
        self.assertEqual(store._state_ttl_days, 14)

    def test_invalid_cooldown_minutes_falls_back_to_180(self):
        store = AS.AlertStateStore(cooldown_minutes="bad")
        self.assertEqual(store._cooldown_minutes, 180)

    def test_state_ttl_days_zero_disables(self):
        store = AS.AlertStateStore(state_ttl_days=0)
        self.assertEqual(store._state_ttl_days, 0)

    def test_state_ttl_days_negative_disables(self):
        store = AS.AlertStateStore(state_ttl_days=-5)
        self.assertEqual(store._state_ttl_days, 0)

    def test_cooldown_minutes_zero_disables(self):
        store = AS.AlertStateStore(cooldown_minutes=0)
        self.assertEqual(store._cooldown_minutes, 0)

    def test_cooldown_minutes_negative_disables(self):
        store = AS.AlertStateStore(cooldown_minutes=-5)
        self.assertEqual(store._cooldown_minutes, 0)

    def test_none_state_ttl_days_falls_back_to_14(self):
        store = AS.AlertStateStore(state_ttl_days=None)
        self.assertEqual(store._state_ttl_days, 14)

    def test_none_cooldown_minutes_falls_back_to_180(self):
        store = AS.AlertStateStore(cooldown_minutes=None)
        self.assertEqual(store._cooldown_minutes, 180)


class TestToUtcNaive(unittest.TestCase):
    """_to_utc_naive helper 行为测试。"""

    def test_naive_passthrough(self):
        dt = datetime.datetime(2026, 6, 4, 8, 0, 0)
        self.assertEqual(AS._to_utc_naive(dt), dt)
        self.assertIsNone(AS._to_utc_naive(dt).tzinfo)

    def test_aware_utc_converted(self):
        import datetime as _dt
        utc_dt = _dt.datetime(2026, 6, 4, 8, 0, 0, tzinfo=_dt.timezone.utc)
        result = AS._to_utc_naive(utc_dt)
        self.assertEqual(result, _dt.datetime(2026, 6, 4, 8, 0, 0))
        self.assertIsNone(result.tzinfo)

    def test_aware_non_utc_converted_to_utc(self):
        import datetime as _dt
        shanghai = _dt.timezone(_dt.timedelta(hours=8))
        local_dt = _dt.datetime(2026, 6, 4, 16, 0, 0, tzinfo=shanghai)  # UTC+8 16:00
        result = AS._to_utc_naive(local_dt)
        self.assertEqual(result, _dt.datetime(2026, 6, 4, 8, 0, 0))  # UTC 08:00
        self.assertIsNone(result.tzinfo)

    def test_none_returns_none(self):
        self.assertIsNone(AS._to_utc_naive(None))


class TestPruneExpiredWithAwareDatetime(unittest.TestCase):
    """_prune_expired 使用 aware now 时不应误判过期。"""

    def test_aware_now_does_not_prematurely_prune(self):
        import datetime as _dt
        saved = {}
        shanghai = _dt.timezone(_dt.timedelta(hours=8))

        # topic 在 naive T0 推送，TTL=14 天
        # aware_now = T0 + 13 天（UTC+8），转 UTC naive 后仍在 TTL 内
        fresh = item("近期议题")
        naive_pushed = T0  # 2026-06-04 08:00:00
        aware_now = _dt.datetime(2026, 6, 17, 16, 0, 0, tzinfo=shanghai)  # +13d, UTC+8

        backend_data = {
            "topics": {
                AS.topic_key("近期议题"): state_from(
                    "cross_layer_verified",
                    fresh,
                    naive_pushed,
                ),
            },
        }

        class FakeBackend:
            def get_alert_state(self):
                return dict(backend_data)

            def save_alert_state(self, data):
                saved.update(data)
                return True

        store = AS.AlertStateStore(
            backend=FakeBackend(), state_ttl_days=14, cooldown_minutes=180,
        )
        store.commit([], aware_now)

        self.assertIn(AS.topic_key("近期议题"), saved["topics"])


if __name__ == "__main__":
    unittest.main()
