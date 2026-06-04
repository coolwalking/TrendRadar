# coding=utf-8
"""
证据摘要构建模块（信息环境异常监测）

在 AI 介入之前，先把当天按主题组匹配到的结果整理成结构化的 evidence summary，
并用**纯程序规则**给每个主题组算出唯一的 evidence_label（验证状态）。

核心边界：
- 程序负责证据状态：来源层级、平台数量、最高排名、跨层呼应、情绪信号等。
- 程序唯一裁定 evidence_label，并把每个主题组分到唯一栏目（bucket）。
- AI 不允许改标签、不允许移动栏目；AI 只为已分栏的议题补写文字。
- tier 是来源层级标签，不是事实真伪判断。
- sample_titles 只是代表性"传播文本"，不是事实来源。
"""

import re
from typing import Any, Callable, Dict, List, Optional

# ─────────────────────────────────────────────────────────────
# 栏目（label）定义：key -> 中文名 / 验证状态文案 / 固定边界提示
# ─────────────────────────────────────────────────────────────
RISK_NOTE_HIGH_HEAT = "当前仅能确认传播正在发生，不能确认事件已经成立。"

LABELS: Dict[str, Dict[str, str]] = {
    "cross_layer_verified": {
        "title": "跨层呼应",
        "verification_status": "跨层有呼应",
        "factual_boundary": "存在 A/B 一手/背景源呼应，但不代表所有 D 层说法均被证实。",
    },
    "high_heat_unverified": {
        "title": "高热待核实",
        "verification_status": "高热待核实",
        "factual_boundary": RISK_NOTE_HIGH_HEAT,
    },
    "sentiment_heavy": {
        "title": "情绪聚集",
        "verification_status": "情绪聚集",
        "factual_boundary": "主要是情绪聚集，不代表事实增量。",
    },
    "silence_gap": {
        "title": "沉默温差",
        "verification_status": "沉默温差",
        "factual_boundary": "背景源有信息，但中文社交平台未明显响应。",
    },
    "chinese_only_hot": {
        "title": "中文独热",
        "verification_status": "中文源呼应(缺A/B背景)",
        "factual_boundary": "仅有中文相对严肃源(C)呼应，缺少 A/B 一手/国际背景源；中文信息环境内部升温，不宜直接视为事实性重大事件。",
    },
}

# 数据层：程序裁定 label / 分桶用的全集顺序。assign_label / bucketize / 统计依赖它，勿轻改。
BUCKET_ORDER: List[str] = [
    "cross_layer_verified",
    "high_heat_unverified",
    "sentiment_heavy",
    "silence_gap",
    "chinese_only_hot",
]

# 呈现层：作为独立监测栏目展示的桶，按"用户该怎么对待它"的阅读动作排序。
# 与数据层 BUCKET_ORDER 解耦：sentiment_heavy 不在此列——情绪降为议题属性，不单独成栏。
SECTION_ORDER: List[str] = [
    "cross_layer_verified",  # 优先看：多源同时在动
    "high_heat_unverified",  # 隔离看：纯 D 层高热、无严肃源呼应
    "chinese_only_hot",      # 中文独热：中热缺外
    "silence_gap",           # 沉默温差：外热中静
]

# 计算进"已抑制"但不单独成节的桶；其 item 折叠进背景/已抑制，情绪以属性标注。
SUPPRESSED_BUCKETS: List[str] = [
    "sentiment_heavy",
]

METHOD_NOTE = (
    "社交平台热度只表示传播强度，不代表事实成立；验证状态由程序化规则根据来源层级、"
    "平台数量、排名变化和跨层呼应生成；AI 仅用于摘要与解释，不直接判断事实真伪。"
)

# 强情绪词（命中即置 sentiment_flag，作为二级信号）
_EMOTION_WORDS = [
    "饭圈", "围观", "骂战", "破防", "炸了", "塌房", "抵制", "离谱",
    "翻车", "冲上热搜", "怒怼", "diss", "互撕", "玻璃心", "爆哭", "泪目",
    "气炸", "震怒", "群嘲", "嘲讽",
]
_EMOTION_RE = re.compile("|".join(re.escape(w) for w in _EMOTION_WORDS), re.IGNORECASE)

# D 层平台数 >= 此值视为"高热"之一
_HIGH_D_PLATFORM_COUNT = 2
# 任一 D 层 rank <= 此值视为"高热"之一
_HIGH_D_RANK = 10


def _min_rank(title: Dict[str, Any]) -> Optional[int]:
    ranks = title.get("ranks", []) or []
    valid = [r for r in ranks if isinstance(r, int) and r > 0]
    return min(valid) if valid else None


def _rank_trend(title: Dict[str, Any]) -> str:
    """根据 ranks 列表做极简趋势判定（升/稳/退），仅供参考，不做强判断。"""
    ranks = [r for r in (title.get("ranks", []) or []) if isinstance(r, int) and r > 0]
    if len(ranks) < 2:
        return "稳定"
    first, last = ranks[0], ranks[-1]
    # rank 数值越小越热
    if last <= first - 3:
        return "升温"
    if last >= first + 3:
        return "降温"
    return "稳定"


def build_evidence(
    stats: Optional[List[Dict]],
    rss_stats: Optional[List[Dict]],
    resolver,
    include_rank_timeline: bool = False,
) -> List[Dict[str, Any]]:
    """
    按主题组（word）合并 hotlist + rss 条目，构建 evidence items。

    Args:
        stats: 热榜统计（List[{word, titles:[...]}]）
        rss_stats: RSS 统计（结构同上）
        resolver: SourceTierResolver，按显示名/ID 返回 tier
        include_rank_timeline: 是否在 sample 中附带轨迹（暂仅影响 sample 文本）

    Returns:
        evidence item 列表，每项含分层证据 + 程序裁定的 label
    """
    # topic -> {"hotlist": [titles], "rss": [titles]}
    groups: Dict[str, Dict[str, List[Dict]]] = {}

    def _ingest(source_stats, kind):
        if not source_stats:
            return
        for stat in source_stats:
            word = stat.get("word", "")
            titles = stat.get("titles", []) or []
            if not word or not titles:
                continue
            bucket = groups.setdefault(word, {"hotlist": [], "rss": []})
            bucket[kind].extend(t for t in titles if isinstance(t, dict) and t.get("title"))

    _ingest(stats, "hotlist")
    _ingest(rss_stats, "rss")

    items: List[Dict[str, Any]] = []
    for topic, parts in groups.items():
        item = _build_one(topic, parts["hotlist"], parts["rss"], resolver)
        if item:
            items.append(item)
    return items


def _build_one(
    topic: str,
    hotlist_titles: List[Dict],
    rss_titles: List[Dict],
    resolver,
) -> Optional[Dict[str, Any]]:
    all_titles = hotlist_titles + rss_titles
    if not all_titles:
        return None

    # 按层级归集来源显示名
    sources_by_tier: Dict[str, List[str]] = {"A": [], "B": [], "C": [], "D": [], "unknown": []}
    d_platforms = set()
    source_names = set()
    rss_background_count = 0

    for t in hotlist_titles:
        name = t.get("source_name", t.get("source", "")) or ""
        tier = resolver.tier_of(name) if name else "unknown"
        if name and name not in sources_by_tier.get(tier, []):
            sources_by_tier.setdefault(tier, []).append(name)
        if name:
            source_names.add(name)
        if tier == "D" and name:
            d_platforms.add(name)
        if tier in ("A", "B"):
            rss_background_count += 1

    for t in rss_titles:
        name = t.get("source_name", t.get("feed_name", "")) or ""
        tier = resolver.tier_of(name) if name else "unknown"
        if name and name not in sources_by_tier.get(tier, []):
            sources_by_tier.setdefault(tier, []).append(name)
        if name:
            source_names.add(name)
        if tier in ("A", "B"):
            rss_background_count += 1

    has_A = bool(sources_by_tier["A"])
    has_B = bool(sources_by_tier["B"])
    has_C = bool(sources_by_tier["C"])
    has_D = bool(sources_by_tier["D"])

    # D 层最高排名
    highest_d_tier_rank = None
    for t in hotlist_titles:
        name = t.get("source_name", "") or ""
        if resolver.tier_of(name) != "D":
            continue
        mr = _min_rank(t)
        if mr is None:
            continue
        if highest_d_tier_rank is None or mr < highest_d_tier_rank["rank"]:
            highest_d_tier_rank = {"platform": name, "rank": mr}

    d_tier_platform_count = len(d_platforms)
    high_d = (d_tier_platform_count >= _HIGH_D_PLATFORM_COUNT) or (
        highest_d_tier_rank is not None and highest_d_tier_rank["rank"] <= _HIGH_D_RANK
    )

    # 情绪信号（二级）：基于标题文本中的强情绪词
    joined = " ".join(t.get("title", "") for t in all_titles)
    sentiment_flag = bool(_EMOTION_RE.search(joined))

    # 代表性传播文本（按热度排序，最多 3 条）—— 不是事实来源
    sample_titles = _pick_samples(all_titles, resolver, limit=3)
    # 抓取出处链接（仅供 HTML 证据展开；不进入 AI prompt）
    source_links = _pick_source_links(all_titles, resolver, limit=5)

    # 来源数（去重的热榜平台 + RSS 源；字段名沿用 platform_count 以兼容渲染层）
    platform_count = len(source_names)

    # 程序唯一裁定 label / 栏目
    label = assign_label(
        has_A=has_A, has_B=has_B, has_C=has_C, has_D=has_D,
        high_d=high_d, sentiment_flag=sentiment_flag,
    )

    source_layers = "/".join(tier for tier in ("A", "B", "C", "D") if sources_by_tier[tier])

    highest_heat = ""
    if highest_d_tier_rank:
        highest_heat = f"{highest_d_tier_rank['platform']} 第{highest_d_tier_rank['rank']}名"
    else:
        # 无 D 层时，用任一 hotlist 最高排名做参考
        best = None
        for t in hotlist_titles:
            mr = _min_rank(t)
            if mr is not None and (best is None or mr < best["rank"]):
                best = {"platform": t.get("source_name", ""), "rank": mr}
        if best:
            highest_heat = f"{best['platform']} 第{best['rank']}名"

    return {
        "topic_group": topic,
        "label": label,  # None 表示进 background_notes
        "source_tiers_present": [tier for tier in ("A", "B", "C", "D") if sources_by_tier[tier]],
        "sources_by_tier": {k: v for k, v in sources_by_tier.items() if v},
        "source_layers": source_layers or "-",
        "platform_count": platform_count,
        "d_tier_platform_count": d_tier_platform_count,
        "highest_d_tier_rank": highest_d_tier_rank,
        "highest_heat": highest_heat or "-",
        "rss_background_count": rss_background_count,
        "sentiment_flag": sentiment_flag,
        "sample_titles": sample_titles,
        "source_links": source_links,
    }


def _pick_samples(titles: List[Dict], resolver, limit: int = 3) -> List[Dict[str, str]]:
    def sort_key(t):
        mr = _min_rank(t)
        return mr if mr is not None else 9999

    seen = set()
    result = []
    for t in sorted(titles, key=sort_key):
        title = t.get("title", "")
        if not title or title in seen:
            continue
        seen.add(title)
        name = t.get("source_name", t.get("feed_name", "")) or ""
        result.append({
            "title": title,
            "source": name,
            "tier": resolver.tier_of(name) if name else "unknown",
            "trend": _rank_trend(t),
        })
        if len(result) >= limit:
            break
    return result


def _title_url(title: Dict[str, Any]) -> str:
    """从热榜/RSS 条目中取已有抓取链接，不补全、不生成。"""
    return (
        title.get("url")
        or title.get("mobile_url")
        or title.get("mobileUrl")
        or ""
    )


def _title_time(title: Dict[str, Any]) -> str:
    """返回已有展示时间字段；没有则留空。"""
    return (
        title.get("time_display")
        or title.get("published_at")
        or title.get("first_time")
        or title.get("last_time")
        or ""
    )


def _pick_source_links(titles: List[Dict], resolver, limit: int = 5) -> List[Dict[str, Any]]:
    """挑选带 URL 的抓取出处，供 HTML 展开证据使用。

    该字段只复制程序已抓到的链接和元数据；AI prompt 渲染不读取它，避免链接进入 AI 生成路径。
    """
    def sort_key(t):
        mr = _min_rank(t)
        return mr if mr is not None else 9999

    seen = set()
    result = []
    for t in sorted(titles, key=sort_key):
        title = t.get("title", "")
        url = _title_url(t)
        if not title or not url:
            continue
        key = url or f"{t.get('source_name', t.get('feed_name', ''))}:{title}"
        if key in seen:
            continue
        seen.add(key)

        name = t.get("source_name", t.get("feed_name", t.get("source", ""))) or ""
        rank = _min_rank(t)
        item = {
            "title": title,
            "url": url,
            "source": name,
            "tier": resolver.tier_of(name) if name else "unknown",
            "rank": rank,
            "time": _title_time(t),
        }
        result.append(item)
        if len(result) >= limit:
            break
    return result


def assign_label(
    has_A: bool,
    has_B: bool,
    has_C: bool,
    has_D: bool,
    high_d: bool,
    sentiment_flag: bool,
) -> Optional[str]:
    """
    程序唯一裁定 evidence_label。优先级从上到下。
    返回 None 表示该主题组不构成异常信号，进 background_notes。
    """
    ab_support = has_A or has_B

    # 1. 跨层有呼应：D 层有热度 且 A/B 背景源呼应
    if has_D and ab_support:
        return "cross_layer_verified"

    # 2. 中文源独热：D 层有热度 且 有 C 严肃源 但缺 A/B 背景（必须 has_D）
    if has_D and has_C and not ab_support:
        return "chinese_only_hot"

    # 3. 纯 D 层（无 A/B/C 任何呼应）
    if has_D and not (has_A or has_B or has_C):
        if high_d:
            # 高热待核实优先；情绪只作为二级 sentiment_flag 记录在 item 上
            return "high_heat_unverified"
        if sentiment_flag:
            # 低热但明显情绪聚集，才进情绪聚集桶（呈现层并入"已抑制"，情绪降为属性）
            return "sentiment_heavy"
        return None  # 低热无情绪 -> background_notes

    # 4. 沉默温差：背景源有信息，但 D 层无热度
    if ab_support and not has_D:
        return "silence_gap"

    # 5. 仅 C 层、无 D、无 A/B -> background_notes（点2：无 D 热度不进中文源独热）
    # 6. 其余 -> background_notes
    return None


def bucketize(items: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """把 evidence items 分到各栏目；label 为 None 的进 background。"""
    buckets: Dict[str, List[Dict[str, Any]]] = {k: [] for k in BUCKET_ORDER}
    buckets["background"] = []
    for item in items:
        label = item.get("label")
        if label in buckets:
            buckets[label].append(item)
        else:
            buckets["background"].append(item)
    return buckets


def build_overview_stats(buckets: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    """程序统计骨架，供 AI 写 overview（AI 不得自行概括社会趋势）。"""
    label_counts = {k: len(buckets.get(k, [])) for k in BUCKET_ORDER}
    total_items = sum(label_counts.values())

    # 层级分布：统计各层级出现在多少个主题组里
    layer_distribution = {"A": 0, "B": 0, "C": 0, "D": 0}
    for k in list(BUCKET_ORDER) + ["background"]:
        for item in buckets.get(k, []):
            for tier in item.get("source_tiers_present", []):
                if tier in layer_distribution:
                    layer_distribution[tier] += 1

    return {
        "total_items": total_items,
        "label_counts": label_counts,
        "background_count": len(buckets.get("background", [])),
        "layer_distribution": layer_distribution,
    }


def derive_radar_readout(overview_stats: Dict[str, Any]) -> Dict[str, Any]:
    """
    从 overview_stats 派生"今日盘面"展示数字（呈现层用，不改数据层计数）。

    - anomaly：真正的异常信号数（仅 SECTION_ORDER 四类，已排除情绪聚集）。
    - suppressed：已抑制数（背景 + 低热情绪聚集，情绪降为属性后并入此处）。
    其余键为各栏目计数与层级覆盖，供盘面"密度 / 热度↔证据错位 / 中外温差 / 层级覆盖"四行使用。
    """
    lc = overview_stats.get("label_counts", {}) or {}
    ld = overview_stats.get("layer_distribution", {}) or {}
    sentiment = lc.get("sentiment_heavy", 0)
    return {
        "anomaly": sum(lc.get(k, 0) for k in SECTION_ORDER),
        "suppressed": overview_stats.get("background_count", 0) + sentiment,
        "cross_layer": lc.get("cross_layer_verified", 0),
        "high_heat": lc.get("high_heat_unverified", 0),
        "silence_gap": lc.get("silence_gap", 0),
        "chinese_only": lc.get("chinese_only_hot", 0),
        "layers": {t: ld.get(t, 0) for t in ("A", "B", "C", "D")},
    }


def _format_sample_line(sample: Dict[str, str]) -> str:
    src = sample.get("source", "")
    tier = sample.get("tier", "")
    tier_tag = f"[{tier}]" if tier and tier != "unknown" else ""
    return f"    · {tier_tag}[{src}] {sample.get('title', '')}（{sample.get('trend', '')}）"


def render_evidence_for_prompt(
    buckets: Dict[str, List[Dict[str, Any]]],
    overview_stats: Dict[str, Any],
) -> str:
    """把已分栏的证据渲染为紧凑文本，注入 AI prompt 的 {evidence_summary}。

    呈现与用户端对齐：只把 SECTION_ORDER 四类作为独立监测栏目；情绪聚集等
    SUPPRESSED_BUCKETS 与背景项一起折叠进"已抑制"段，情绪仅以属性标注。
    """
    lines: List[str] = []

    for label in SECTION_ORDER:
        items = buckets.get(label, [])
        if not items:
            continue
        meta = LABELS[label]
        lines.append(f"\n## [{meta['title']}] 验证状态={meta['verification_status']}（共{len(items)}条）")
        lines.append(f"（边界：{meta['factual_boundary']}）")
        for item in items:
            lines.append(
                f"- 议题「{item['topic_group']}」 | 来源层级:{item['source_layers']} | "
                f"平台数:{item['platform_count']} | 最高热度:{item['highest_heat']}"
                + ("（含情绪信号）" if item.get("sentiment_flag") else "")
            )
            if item.get("sample_titles"):
                lines.append("  代表性传播文本（非事实来源，勿当事实复述）：")
                for s in item["sample_titles"]:
                    lines.append(_format_sample_line(s))

    # 已抑制（未达异常阈值）：背景项 + 低热情绪聚集项；情绪降为属性，禁止展开成新闻
    suppressed: List[Dict[str, Any]] = list(buckets.get("background", []))
    for sb in SUPPRESSED_BUCKETS:
        suppressed.extend(buckets.get(sb, []))
    if suppressed:
        lines.append(
            f"\n## [已抑制/未达异常阈值]（共{len(suppressed)}条，仅供盘面计数，勿展开成新闻条目）"
        )
        for item in suppressed:
            flag = "（含情绪信号）" if item.get("sentiment_flag") else ""
            lines.append(
                f"- 议题「{item['topic_group']}」 | 来源层级:{item['source_layers']} | "
                f"平台数:{item['platform_count']}{flag}"
            )

    if not lines:
        return "（今日无可分栏的异常信号）"
    return "\n".join(lines)


def render_overview_stats_for_prompt(overview_stats: Dict[str, Any]) -> str:
    """把盘面骨架渲染为文本，注入 {overview_stats}。数字由程序给定，AI 不得自造。"""
    r = derive_radar_readout(overview_stats)
    ly = r["layers"]
    return (
        "【今日盘面】"
        f"信号密度：异常信号 {r['anomaly']} 条 ｜ 已抑制 {r['suppressed']} 条；"
        f"热度↔证据错位：跨层呼应 {r['cross_layer']} 条 ｜ 高热待核实(无呼应) {r['high_heat']} 条；"
        f"中外温差：沉默温差(外热中静) {r['silence_gap']} 条 ｜ 中文独热(中热缺外) {r['chinese_only']} 条；"
        f"层级覆盖(组数)：A={ly['A']} B={ly['B']} C={ly['C']} D={ly['D']}"
    )
