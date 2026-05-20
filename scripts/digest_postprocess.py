"""
Digest 后处理 — 数字事实校验

读 latest.json,扫 summary / insight / title_zh 里的"具体数字 + 单位"模式,
如果数字不在该分类的真实数据池(ground truth)里,则:
  - HN 类、GitHub 类:替换为最近的真实值(因为我们有 metadata)
  - AI / 生医类:直接删掉整段(没有 metadata,无法校正,只能撤回)

设计原则:**保守删,不要保留任何编造数字**。

公开函数:
  postprocess_digest(latest_data: dict) -> dict   # 返回修正后的数据 + 写回 latest.json
"""

from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Any


# ===== 数字 + 单位的正则模式 =====
# 匹配:1325 点赞 / 711 comments / 12K stars / $700M / 200 亿 / etc.
NUMBER_UNIT_PATTERNS = [
    # HN 类
    (re.compile(r"(\d{1,6}(?:[,，]\d{3})*)\s*(?:点赞|赞|顶|票)\b", re.U), "vote"),
    (re.compile(r"(\d{1,6}(?:[,，]\d{3})*)\s*(?:points?|votes?)\b", re.I), "vote"),
    (re.compile(r"(\d{1,6}(?:[,，]\d{3})*)\s*(?:评论|条评论)\b", re.U), "comment"),
    (re.compile(r"(\d{1,6}(?:[,，]\d{3})*)\s*comments?\b", re.I), "comment"),
    # GitHub 类
    (re.compile(r"(\d{1,7}(?:[,，]\d{3})*)\s*(?:stars?|星标|★|颗星|个 star)\b", re.I | re.U), "star"),
    (re.compile(r"(\d{1,7}(?:[,，]\d{3})*)\s*(?:forks?|fork 数)\b", re.I | re.U), "fork"),
    # 通用大额(估值、融资、用户数等)
    (re.compile(r"\$(\d{1,4}(?:\.\d+)?)\s*([MB])\b"), "currency"),
    (re.compile(r"(\d{1,4}(?:\.\d+)?)\s*亿\s*(?:美元|元|人民币|RMB|USD)?", re.U), "currency_zh"),
    (re.compile(r"(\d{1,4}(?:\.\d+)?)\s*万\s*(?:美元|元|RMB|USD|用户|月活|DAU)?", re.U), "currency_zh"),
]


def _normalize_num(s: str) -> str:
    """去掉千分位逗号"""
    return s.replace(",", "").replace(",", "")


def _build_ground_truth(category: dict) -> dict[str, set[str]]:
    """
    给一个分类构建真实数据池。
    返回 {kind: set[数字字符串]}。
    kind: vote / comment / star / fork / currency / currency_zh / fulltext
    """
    gt: dict[str, set[str]] = {
        "vote": set(),
        "comment": set(),
        "star": set(),
        "fork": set(),
        "currency": set(),
        "currency_zh": set(),
        "fulltext": set(),  # 候选 title/summary 里出现过的所有数字
    }
    for it in category.get("items", []):
        # HN metadata
        m = it.get("hn_meta") or {}
        if m.get("points") is not None:
            gt["vote"].add(str(m["points"]))
        if m.get("num_comments") is not None:
            gt["comment"].add(str(m["num_comments"]))
        # GitHub metadata
        gm = it.get("gh_meta") or {}
        if gm.get("stars") is not None:
            gt["star"].add(str(gm["stars"]))
        if gm.get("forks") is not None:
            gt["fork"].add(str(gm["forks"]))
        # 候选 title / summary / insight 里出现过的数字
        for field in ("title", "title_zh", "insight", "summary"):
            text = str(it.get(field) or "")
            for n in re.findall(r"\d{1,7}(?:[,，]\d{3})*(?:\.\d+)?", text):
                gt["fulltext"].add(_normalize_num(n))
    return gt


def _check_text_against_gt(
    text: str,
    gt: dict[str, set[str]],
    *,
    delete_unverified: bool = True,
) -> tuple[str, list[str]]:
    """
    扫 text 里所有数字+单位模式,校验数字是否在对应 ground-truth set(或 fulltext)。
    校正策略:
      - 命中:保留
      - 不命中:整个匹配段(数字+单位)替换为占位符 "[数据待核实]" 或删除
    返回 (修正后的 text, 动作列表)
    """
    actions: list[str] = []
    out = text

    def make_replacer(kind):
        def replacer(m):
            raw = m.group(0)
            num_str = _normalize_num(m.group(1))
            allowed = gt.get(kind, set()) | gt["fulltext"]
            if num_str in allowed:
                return raw
            if delete_unverified:
                actions.append(f"❌ 删除 '{raw}' ({kind} 数字 {num_str} 不在真实数据)")
                return ""
            actions.append(f"⚠️  替换 '{raw}' → [数据待核实]")
            return "[数据待核实]"
        return replacer

    for pat, kind in NUMBER_UNIT_PATTERNS:
        out = pat.sub(make_replacer(kind), out)

    # 清理删除后的多余空格 / 标点
    out = re.sub(r"\s+([,，。、;;])", r"\1", out)
    out = re.sub(r"\(\s*\)", "", out)
    out = re.sub(r"（\s*）", "", out)
    out = re.sub(r"\s{2,}", " ", out)
    out = re.sub(r"，+", ",", out).replace(",", ",")
    out = re.sub(r",\s*。", "。", out)
    out = re.sub(r",\s*,", ",", out)

    return out.strip(), actions


def postprocess_category(cat: dict, *, delete_unverified: bool = True) -> tuple[dict, list[str]]:
    """对单个分类做事实校验,原地修改 summary 和每条 insight。"""
    gt = _build_ground_truth(cat)
    all_actions: list[str] = []

    # 修 summary
    if cat.get("summary"):
        new_sum, acts = _check_text_against_gt(cat["summary"], gt, delete_unverified=delete_unverified)
        if acts:
            all_actions.append(f"--- {cat['name']} summary ---")
            all_actions.extend(acts)
        cat["summary"] = new_sum

    # 修每条 insight
    for i, it in enumerate(cat.get("items", []), 1):
        if it.get("insight"):
            new_in, acts = _check_text_against_gt(it["insight"], gt, delete_unverified=delete_unverified)
            if acts:
                all_actions.append(f"--- {cat['name']} item {i}: {(it.get('title_zh') or it.get('title') or '')[:50]} ---")
                all_actions.extend(acts)
            it["insight"] = new_in

    return cat, all_actions


def postprocess_digest(latest_data: dict, *, delete_unverified: bool = True) -> tuple[dict, list[str]]:
    """对整份 digest 做事实校验,返回 (修正后的 data, 全部动作记录)"""
    all_actions: list[str] = []
    for cat in latest_data.get("report", {}).get("categories", []):
        _, acts = postprocess_category(cat, delete_unverified=delete_unverified)
        all_actions.extend(acts)
    return latest_data, all_actions


def main(json_path: Path):
    """从 json_path 读 → 校验 → 写回(同时备份原文件)"""
    data = json.loads(json_path.read_text(encoding="utf-8"))
    backup = json_path.with_suffix(".pre_postprocess.json")
    if not backup.exists():
        backup.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    fixed, actions = postprocess_digest(data, delete_unverified=True)

    json_path.write_text(json.dumps(fixed, ensure_ascii=False, indent=2), encoding="utf-8")
    # latest.json 也同步
    latest = json_path.parent / "latest.json"
    if latest.exists() and latest.resolve() != json_path.resolve():
        latest.write_text(json.dumps(fixed, ensure_ascii=False, indent=2), encoding="utf-8")

    if actions:
        print(f"[Postprocess] 共修正 {len(actions)} 处:")
        for a in actions:
            print(f"  {a}")
    else:
        print("[Postprocess] ✅ 没有发现需要修正的数字 — 全部数字都在真实数据里")
    print(f"[Postprocess] 备份原文件: {backup}")
    return fixed


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
    else:
        path = Path(__file__).parent.parent / "output" / "digest" / "latest.json"
    if not path.exists():
        sys.exit(f"❌ 找不到 {path}")
    main(path)
