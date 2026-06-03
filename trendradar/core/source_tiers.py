# coding=utf-8
"""
来源分层（Source Tier）解析模块

用于"信息环境异常监测 / 舆情雷达"：把平台和 RSS feed 映射到证据层级 A/B/C/D。

设计原则：
- 这是 source-level（来源层级）的证据解释标签，不是 content-level 的事实真伪判断。
- tier 只表达"这条信息来自哪种来源"，不代表内容已被证实。
- 缺失 tier 时一律返回 "unknown"，绝不抛异常、绝不阻断主流程。

层级含义：
    A  一手/官方来源（官方公告、公司博客、研究机构一手发布）
    B  国际媒体/背景源（国际通讯社、国际媒体、技术社区、财经背景源）
    C  中文相对严肃信息源（中文媒体、财经媒体、问答/搜索等相对结构化来源）
    D  高时效低可信传播平台（社交/热搜/社区平台，主要提供早期传播与情绪信号）
"""

from typing import Any, Dict, List, Optional

VALID_TIERS = ("A", "B", "C", "D")
UNKNOWN_TIER = "unknown"


class SourceTierResolver:
    """
    来源分层解析器

    结合 source_tiers.yaml（id -> tier/role）与 config.yaml 的
    platforms.sources / rss.feeds（id <-> 显示名），
    提供按 id 或显示名查询 tier / role 的能力。

    stats 数据里 source_name 是显示名（如"微博"），因此必须同时支持显示名查询。
    """

    def __init__(
        self,
        source_tiers: Optional[Dict[str, Any]] = None,
        platforms: Optional[List[Dict[str, Any]]] = None,
        rss_feeds: Optional[List[Dict[str, Any]]] = None,
    ):
        source_tiers = source_tiers or {}
        platforms = platforms or []
        rss_feeds = rss_feeds or []

        # id -> {"tier": "A".."D"|"unknown", "role": str}
        self._by_id: Dict[str, Dict[str, str]] = {}
        # 显示名 -> id（用于把 stats 里的 source_name 反查回 id）
        self._name_to_id: Dict[str, str] = {}

        platform_tiers = source_tiers.get("platforms", {}) or {}
        rss_tiers = source_tiers.get("rss_feeds", {}) or {}

        # 平台
        for item in platforms:
            self._register(item, platform_tiers)
        # RSS feed
        for item in rss_feeds:
            self._register(item, rss_tiers)

        # 同时把 source_tiers 中声明、但 config 未列出的 id 也纳入（容错）
        for source_id, info in {**platform_tiers, **rss_tiers}.items():
            if source_id not in self._by_id:
                self._by_id[source_id] = self._normalize(info)

        # tiers 描述（A/B/C/D -> {name, description}），仅用于展示
        self.tier_descriptions: Dict[str, Dict[str, str]] = source_tiers.get("tiers", {}) or {}

    def _register(self, item: Dict[str, Any], tier_table: Dict[str, Any]) -> None:
        source_id = item.get("id", "")
        if not source_id:
            return
        name = item.get("name", source_id)
        info = tier_table.get(source_id, {})
        self._by_id[source_id] = self._normalize(info)
        if name:
            self._name_to_id[name] = source_id
        # id 自身也作为别名，便于直接用 id 查询
        self._name_to_id.setdefault(source_id, source_id)

    @staticmethod
    def _normalize(info: Any) -> Dict[str, str]:
        if not isinstance(info, dict):
            return {"tier": UNKNOWN_TIER, "role": ""}
        tier = str(info.get("tier", "")).strip().upper()
        if tier not in VALID_TIERS:
            tier = UNKNOWN_TIER
        return {"tier": tier, "role": str(info.get("role", ""))}

    def _resolve_id(self, name_or_id: str) -> Optional[str]:
        if not name_or_id:
            return None
        if name_or_id in self._by_id:
            return name_or_id
        return self._name_to_id.get(name_or_id)

    def tier_of(self, name_or_id: str) -> str:
        """返回来源层级 A/B/C/D，未知返回 'unknown'。支持显示名或 id。"""
        source_id = self._resolve_id(name_or_id)
        if source_id is None:
            return UNKNOWN_TIER
        return self._by_id.get(source_id, {}).get("tier", UNKNOWN_TIER)

    def role_of(self, name_or_id: str) -> str:
        """返回来源 role 标签，未知返回空字符串。"""
        source_id = self._resolve_id(name_or_id)
        if source_id is None:
            return ""
        return self._by_id.get(source_id, {}).get("role", "")

    def tier_name(self, tier: str) -> str:
        """返回某层级的中文名称（如 A -> '一手/官方来源'），无配置时回退到层级字母。"""
        desc = self.tier_descriptions.get(tier, {})
        if isinstance(desc, dict):
            return desc.get("name", tier)
        return tier


def build_source_tier_resolver(config: Dict[str, Any]) -> SourceTierResolver:
    """
    从已加载的 config 字典构建 SourceTierResolver。

    依赖：
        config["_SOURCE_TIERS"]  来自 source_tiers.yaml
        config["PLATFORMS"]      平台列表（含 id/name）
        config["RSS"]["FEEDS"]   RSS feed 列表（含 id/name）
    """
    source_tiers = config.get("_SOURCE_TIERS", {}) or {}
    platforms = config.get("PLATFORMS", []) or []
    rss_feeds = (config.get("RSS", {}) or {}).get("FEEDS", []) or []
    return SourceTierResolver(
        source_tiers=source_tiers,
        platforms=platforms,
        rss_feeds=rss_feeds,
    )
