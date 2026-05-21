"""
通用 article body 抓取 enricher (trafilatura-based)

2026-05-18 加(借鉴 RepoAgent / martinopiaggi-summarize / WorldMonitor)。

覆盖 4 个板块的"LLM 只看标题/摘要瞎编"问题:
- GitHub 开源生态: 抓项目页 (含 README 全文)
- Hacker News: 抓外部文章正文 (Show HN 跳过, 那是讨论本身)
- 国际局势: 抓 BBC/Guardian/Aljazeera/NPR 等正文
- 生物医疗工程: 抓 FierceBiotech/STAT News 等正文

只对每分类 top N 候选 enrich, 防止时间/成本爆炸。
"""

from __future__ import annotations
import time
import urllib.request
from typing import Optional

try:
    import trafilatura
except ImportError:
    trafilatura = None  # type: ignore


# 浏览器 UA, 大幅提升抓取成功率(BBC/NPR 等会挡空 UA)
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
TIMEOUT = 12
MAX_CHARS = 6000   # 单篇 ~1500 token, 30 篇 enrich = ~45k token 进 LLM
MIN_USEFUL_CHARS = 200  # 抓到 < 200 字算无效, 多半是 paywall/anti-bot 拦截页


def fetch_article(url: str, max_chars: int = MAX_CHARS) -> Optional[str]:
    """抓 URL 全文。失败/反爬/正文太短返回 None, 不让上游崩。"""
    if trafilatura is None or not url:
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            html = r.read().decode("utf-8", errors="replace")
        if len(html) < 500:
            return None  # 多半是 anti-bot 短返回页
        txt = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=False,
            no_fallback=False,  # 允许 fallback 提取器, 提升 BBC/NPR 这类站的成功率
        )
        if not txt or len(txt) < MIN_USEFUL_CHARS:
            return None
        return txt[:max_chars].strip()
    except Exception:
        return None


def _is_external_hn(it: dict) -> bool:
    """HN item 且 URL 不指向 news.ycombinator.com (即外部链接, 不是 Ask/Show HN 讨论本身)。"""
    src = (it.get("source") or "").lower()
    if not (src.startswith("hn-") or src == "hacker-news"):
        return False
    url = (it.get("url") or "").lower()
    if "news.ycombinator.com" in url:
        return False
    return True


def _default_match(it: dict) -> bool:
    """默认匹配规则: 5 大分类的 item 都 enrich (LLM 会再决定哪些进精选)。

    跳过:
    - 没 URL 的 item
    - 已经有 article_full 的 item (避免重复抓)
    - Ask/Show HN 讨论页 (没外部正文可抓)
    """
    if not it.get("url"):
        return False
    if it.get("article_full"):
        return False
    src = (it.get("source") or "").lower()
    if (src.startswith("hn-") or src == "hacker-news") and "news.ycombinator.com" in (it.get("url") or "").lower():
        return False
    return True


def enrich_items(
    items: list[dict],
    *,
    top_per_category: int = 6,
    sleep_between: float = 0.4,
    wall_budget_s: float = 150.0,
    match_fn=_default_match,
    verbose: bool = True,
) -> int:
    """
    对每分类 top N items (按 score 倒排) 抓 article_full。

    items 元素需含 category + score + url 字段。
    返回成功 enrich 的 item 数。
    """
    if trafilatura is None:
        if verbose:
            print("  [Art] trafilatura 未装, 跳过 article body enrich")
        return 0

    # 按分类分桶, 每桶 top N 按 score
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
        if not match_fn(it):
            continue
        if time.monotonic() > deadline:
            if verbose:
                print(f"  [Art] ⏱  已用 {wall_budget_s:.0f}s, 余下 {len(candidates) - idx} 项跳过")
            break
        article = fetch_article(it.get("url"))
        if article:
            it["article_full"] = article
            hit += 1
            if verbose:
                print(f"  [Art] ✅ {len(article):>4}字 · {it.get('title','')[:55]}")
        else:
            if verbose:
                print(f"  [Art] —     抓不到 · {(it.get('url') or '')[:60]}")
        time.sleep(sleep_between)
    return hit


# ============== 自测 ==============
if __name__ == "__main__":
    test_items = [
        {"category": "国际局势", "score": 0.9, "title": "Test BBC", "url": "https://www.bbc.com/news/world-us-canada-66907670"},
        {"category": "国际局势", "score": 0.8, "title": "Test Guardian", "url": "https://www.theguardian.com/world/europe-news"},
        {"category": "Hacker News", "score": 0.9, "title": "Test HN External", "url": "https://github.com/zakirullin/files.md", "source": "hn-best"},
        {"category": "Hacker News", "score": 0.8, "title": "Ask HN: skip me", "url": "https://news.ycombinator.com/item?id=999", "source": "hn-best"},
    ]
    print("=== article_body_enricher unit test ===")
    n = enrich_items(test_items, top_per_category=4, sleep_between=0.2)
    print(f"\nhit: {n}/{len(test_items)}")
    for it in test_items:
        af = it.get("article_full")
        print(f"  → {it['title'][:30]}: {len(af) if af else 0}字")
