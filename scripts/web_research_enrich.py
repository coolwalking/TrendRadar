"""
Web research enrich

对 AI 筛选后的高分 items, DuckDuckGo 搜标题, 取 top 3 snippet 作为"网络补充背景",
塞进 LLM 上下文。专门解决 DeepSeek 训练 cutoff 后的新模型/新公司/新项目名让 insight
含糊或编造的问题。

2026-05-18 加 (借鉴 Horizon 的 enrich 阶段)。

策略:
- 只对每分类 top N 候选 (按 score) enrich, 默认 6 个 → 5 类 × 6 = 30 次搜索
- summary 已经够长 (> 300 字符) 的跳过, 已经够上下文了
- 异常 / 超时 / 空结果都 silent fallback (不写 web_context 字段), 不阻塞主流程
"""

from __future__ import annotations
import time
from typing import Optional

try:
    from ddgs import DDGS
except ImportError:
    DDGS = None  # type: ignore


def fetch_web_context(query: str, max_results: int = 3, timeout: int = 8) -> list[dict]:
    """单次 DuckDuckGo 文本搜索, 返回 [{title, body}, ...]。失败/超时返回 []。"""
    if DDGS is None or not query:
        return []
    try:
        results = list(DDGS().text(query, max_results=max_results, timelimit="m"))
        out = []
        for r in results:
            title = (r.get("title") or "").strip()
            body = (r.get("body") or "").strip()
            if title or body:
                out.append({"title": title[:120], "body": body[:300]})
        return out
    except Exception:
        return []


def _build_query(it: dict) -> str:
    """构造搜索 query: 用标题 + 适度上下文词。

    标题往往含特殊符号/引号, 适度清洗。
    """
    title = (it.get("title") or "").strip()
    if not title:
        return ""
    # 去掉过长的标题(DDG 对长 query 反应差)
    return title[:120]


def enrich_items(
    items: list[dict],
    *,
    top_per_category: int = 6,
    skip_if_summary_longer_than: int = 300,
    sleep_between: float = 0.6,
    wall_budget_s: float = 180.0,
    verbose: bool = True,
) -> int:
    """
    对每分类 top N items (按 score 倒排) 做 web 搜索补背景, 原地写 it["web_context"]。

    2026-05-18(codex P1): wall_budget_s 是整段 enrich 的硬时间预算。DDG 慢或被限频时,
    超预算就提前返回,防止把 generate_digest 10 分钟超时吃满。

    返回成功 enrich (拿到至少 1 条结果) 的 item 数。
    """
    if DDGS is None:
        if verbose:
            print("  [Web] ddgs 未安装, 跳过 web enrich")
        return 0

    # 按 category 分桶, 每桶按 score 取 top N
    by_cat: dict[str, list[dict]] = {}
    for it in items:
        cat = it.get("category") or "_"
        by_cat.setdefault(cat, []).append(it)
    candidates: list[dict] = []
    for cat, lst in by_cat.items():
        lst_sorted = sorted(lst, key=lambda x: x.get("score", 0), reverse=True)
        candidates.extend(lst_sorted[:top_per_category])

    hit = 0
    deadline = time.monotonic() + wall_budget_s
    for idx, it in enumerate(candidates):
        if time.monotonic() > deadline:
            if verbose:
                print(f"  [Web] ⏱  已用时 {wall_budget_s:.0f}s,余下 {len(candidates) - idx} 项跳过")
            break
        # 摘要够长就跳过 (已有足够上下文)
        if len(it.get("summary") or "") > skip_if_summary_longer_than:
            continue
        q = _build_query(it)
        if not q:
            continue
        results = fetch_web_context(q, max_results=3)
        if results:
            it["web_context"] = results
            hit += 1
            if verbose:
                print(f"  [Web] ✅ {len(results)} 条 · {q[:55]}")
        else:
            if verbose:
                print(f"  [Web] —     无结果 · {q[:55]}")
        time.sleep(sleep_between)  # DDG 限频, 防 429
    return hit


# ============== 自测 ==============
if __name__ == "__main__":
    test_items = [
        {"category": "AI 领域", "score": 0.9, "title": "DeepSeek V4-Flash beats GPT-5 on coding benchmark", "summary": ""},
        {"category": "AI 领域", "score": 0.7, "title": "Anthropic announces Claude Sonnet 4.7 with extended thinking", "summary": ""},
    ]
    print("=== Test: enrich 2 items ===")
    n = enrich_items(test_items, top_per_category=2)
    print(f"\n命中 {n}/2")
    for it in test_items:
        wc = it.get("web_context") or []
        print(f"\n→ {it['title'][:60]}: {len(wc)} web 结果")
        for w in wc:
            print(f"   • {w['title'][:60]}")
            print(f"     {w['body'][:120]}")
