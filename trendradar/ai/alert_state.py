# coding=utf-8
"""
实时异常提醒的去重 / 冷却 / 升级再推（cooldown / dedup / upgrade）纯逻辑。

设计要点（与 formatter 的 selection / render 分离保持一致）：
- 本模块只做两件事：把候选议题归一为稳定的 topic_key，并决定"是否值得再次打断用户"。
  不发任何网络请求、不直接读写文件。
- 持久化通过 AlertStateStore 注入（背后是 storage backend 的 get/save_alert_state）。
- 一切异常一律 fail-open：宁可重复推送，也不要因状态丢失 / 解析失败而漏掉异常信号。

cooldown 语义：
1. 没有候选 → 不推（由上游 selection 决定，本模块不构造候选）。
2. 同一 topic_key 在 cooldown_minutes 内重复出现且无升级 → 丢弃（不重复推）。
3. 同一 topic_key 发生"信息结构升级"（label 升级 / 新增更上游来源层）→ 可突破 cooldown 再推。
4. high_heat_unverified 需额外通过 rank / 平台数 heat gate 才自动推（默认克制）。
"""

import re
import unicodedata
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

# 候选标签的"严重度"排序，仅用于 label 升级比较（值越大越值得打断用户）
_SEVERITY: Dict[str, int] = {
    "cross_layer_verified": 3,
    "chinese_only_hot": 2,
    "high_heat_unverified": 1,
}

_DT_FORMAT = "%Y-%m-%d %H:%M:%S"
_RANK_RE = re.compile(r"第\s*(\d+)\s*名")
_LAYER_SPLIT_RE = re.compile(r"[/、,\s]+")
_VALID_LAYERS = ("A", "B", "C", "D")


def severity_rank(label: Optional[str]) -> int:
    """候选标签严重度（未知标签视为 0）。"""
    return _SEVERITY.get(label or "", 0)


def topic_key(topic: str) -> str:
    """把议题名归一为稳定 key，用于判断"是不是同一个议题"。

    规范化策略（保守，绝不做同义词/分词归并）：
      NFKC（全角→半角、兼容字符折叠）→ 去首尾空白 → ASCII 大小写折叠 →
      去除所有空白 + 标点 + 符号，保留中文/字母/数字。

    rank / 平台轻微变化不影响 key；同议题的标点/空格/全半角变体归一到同 key。
    空串返回 ""（调用方应跳过，不入 state）。
    """
    if not topic:
        return ""
    s = unicodedata.normalize("NFKC", str(topic)).strip().lower()
    out: List[str] = []
    for ch in s:
        if ch.isspace():
            continue
        cat = unicodedata.category(ch)
        if cat.startswith("P") or cat.startswith("S"):  # 标点 / 符号
            continue
        out.append(ch)
    return "".join(out)


def layer_set(item: Dict[str, Any]) -> set:
    """从 item['source_layers']（形如 "A/B/D"）解析出 {"A","B","D"}。"""
    raw = str(item.get("source_layers", "") or "")
    return {p for p in _LAYER_SPLIT_RE.split(raw) if p in _VALID_LAYERS}


def _evidence_detail(item: Dict[str, Any]) -> Dict[str, Any]:
    detail = item.get("evidence_detail")
    return detail if isinstance(detail, dict) else {}


def parse_rank(item: Dict[str, Any]) -> Optional[int]:
    """提取该议题的最高 D 层名次。

    优先用结构化的 evidence_detail['highest_d_tier_rank']['rank']；
    回退正则解析 item['highest_heat']（形如 "微博 第3名"）。
    解析不到返回 None（fail-open：不因此阻止推送 / 误判升级）。
    """
    hd = _evidence_detail(item).get("highest_d_tier_rank")
    if isinstance(hd, dict):
        r = hd.get("rank")
        if isinstance(r, int) and r > 0:
            return r
    m = _RANK_RE.search(str(item.get("highest_heat", "") or ""))
    return int(m.group(1)) if m else None


def _d_platform_count(item: Dict[str, Any]) -> int:
    """D 层平台数；优先 evidence_detail，回退总平台数。"""
    v = _evidence_detail(item).get("d_tier_platform_count")
    if isinstance(v, int):
        return v
    v2 = item.get("platform_count")
    return v2 if isinstance(v2, int) else 0


def passes_heat_gate(label: str, item: Dict[str, Any], cfg: Dict[str, Any]) -> bool:
    """high_heat_unverified 的自动推门槛：D 层 rank≤min_rank 或 D 层平台数≥min_platforms。

    其它标签（cross_layer_verified / chinese_only_hot）不受 heat gate 约束。
    """
    if label != "high_heat_unverified":
        return True
    min_rank = cfg.get("high_heat_min_rank", 10)
    min_platforms = cfg.get("high_heat_min_platforms", 2)
    rank = parse_rank(item)
    if rank is not None and rank <= min_rank:
        return True
    if _d_platform_count(item) >= min_platforms:
        return True
    return False


def is_upgrade(prev: Dict[str, Any], label: str, item: Dict[str, Any]) -> bool:
    """判断本轮相对上次推送是否发生"信息结构升级"，可突破 cooldown 再推。

    MVP 仅取两类稳定升级（不含抖动型的名次变化 / 平台数增加）：
    1. label 升级：severity_rank 上跳。
    2. 来源层升级：相比上次新增了 A / B / C 中任一更上游来源层（D-only → C/D 或 A/B/D）。
    """
    if severity_rank(label) > int(prev.get("last_severity", 0) or 0):
        return True
    prev_layers = set(prev.get("last_source_layers", []) or [])
    gained = layer_set(item) - prev_layers
    return bool(gained & {"A", "B", "C"})


def _parse_dt(s: str) -> Optional[datetime]:
    try:
        return datetime.strptime(s, _DT_FORMAT)
    except (ValueError, TypeError):
        return None


def build_state_record(label: str, item: Dict[str, Any], now: datetime) -> Dict[str, Any]:
    """生成要写回 alert_state 的单议题 record（pushed_count 由 store.commit 累加）。"""
    return {
        "topic_key": topic_key(item.get("topic", "")),
        "topic": str(item.get("topic", "")).strip(),
        "last_pushed_at": now.strftime(_DT_FORMAT),
        "last_label": label,
        "last_severity": severity_rank(label),
        "last_source_layers": sorted(layer_set(item)),
        "last_highest_heat": str(item.get("highest_heat", "") or ""),
        "last_rank": parse_rank(item),
        "last_platform_count": _d_platform_count(item),
        "pushed_count": 1,
    }


def apply_alert_cooldown(
    items: List[Tuple[str, Dict[str, Any]]],
    store: Any,
    now: datetime,
    cfg: Dict[str, Any],
) -> List[Tuple[str, Dict[str, Any]]]:
    """对 selection 产出的候选 [(label, item), ...] 再加 heat gate + cooldown/dedup/upgrade 过滤。

    纯函数：只读 store.get(key)，不写状态（落盘由 store.commit 在 POST 成功后完成）。
    返回真正值得本轮推送的候选子列表（保序，截断到 max_items）。
    """
    cfg = cfg or {}
    cooldown = cfg.get("cooldown_minutes", 180)
    allow_upgrade = cfg.get("allow_upgrade_break_cooldown", True)
    max_items = cfg.get("max_items", 3)
    now_cmp = now.replace(tzinfo=None) if getattr(now, "tzinfo", None) else now

    kept: List[Tuple[str, Dict[str, Any]]] = []
    for label, item in items:
        key = topic_key(item.get("topic", ""))
        if not key:
            kept.append((label, item))  # 防御：无 key 不入 state，但仍推
            continue
        if not passes_heat_gate(label, item, cfg):
            continue
        prev = store.get(key) if store is not None else None
        if not isinstance(prev, dict):
            kept.append((label, item))  # 首见
            continue
        last_dt = _parse_dt(str(prev.get("last_pushed_at", "")))
        if last_dt is None:
            kept.append((label, item))  # 状态损坏：fail-open
            continue
        elapsed_min = (now_cmp - last_dt).total_seconds() / 60.0
        if elapsed_min >= cooldown:
            kept.append((label, item))  # 冷却已过
            continue
        if allow_upgrade and is_upgrade(prev, label, item):
            kept.append((label, item))  # 升级再推
            continue
        # 冷却内重复且无升级 → 丢弃

    if max_items and len(kept) > max_items:
        kept = kept[:max_items]
    return kept


class AlertStateStore:
    """topic 级 alert 状态：启动读入内存缓存，仅在 Telegram POST 成功后再落盘。

    背后注入 storage backend（实现 get_alert_state / save_alert_state）。
    backend 为 None（未注入）时退化为纯内存，便于测试与降级。
    """

    def __init__(self, backend: Any = None):
        self._backend = backend
        self._topics: Dict[str, Dict[str, Any]] = {}
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if self._backend is None:
            return
        try:
            data = self._backend.get_alert_state() or {}
            topics = data.get("topics") if isinstance(data, dict) else None
            if isinstance(topics, dict):
                self._topics = dict(topics)
        except Exception as e:  # fail-open：读不到就按空状态处理
            print(f"[Alert] 读取 alert_state 失败（按空状态处理）：{e}")
            self._topics = {}

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        self._load()
        return self._topics.get(key)

    def commit(self, pushed_items: List[Tuple[str, Dict[str, Any]]], now: datetime) -> bool:
        """记录本轮实际推送成功的候选，并落盘。"""
        self._load()
        for label, item in pushed_items:
            key = topic_key(item.get("topic", ""))
            if not key:
                continue
            rec = build_state_record(label, item, now)
            prev = self._topics.get(key)
            if isinstance(prev, dict):
                rec["pushed_count"] = int(prev.get("pushed_count", 0) or 0) + 1
            self._topics[key] = rec

        if self._backend is None:
            return False
        try:
            return bool(
                self._backend.save_alert_state({"version": 1, "topics": self._topics})
            )
        except Exception as e:
            print(f"[Alert] 保存 alert_state 失败：{e}")
            return False
