"""
Hacker News metadata extractor

针对 hn-* / hacker-news 来源的 items,通过 HN Algolia API 拿到:
  - points         真实点赞数
  - num_comments   评论数
  - hn_id          HN 帖子 ID(用于构造 news.ycombinator.com link)
  - hn_url         HN 帖子 URL
  - created_at     发布时间(ISO)

不依赖任何 token,Algolia API 免费公开。
设计为容错:任何异常返回 None,不会让上游崩溃。

公开函数:
  fetch_hn_metadata(title: str, url: str | None) -> dict | None
"""

from __future__ import annotations
import html
import json
import time
import urllib.parse
import urllib.request
import re
from typing import Optional


SEARCH_API = "https://hn.algolia.com/api/v1/search"
ITEM_API = "https://hn.algolia.com/api/v1/items"
TIMEOUT = 8


def _http_get_json(url: str, timeout: int = TIMEOUT) -> Optional[dict]:
    """GET JSON,失败返回 None"""
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "TrendRadar HN Metadata/1.0"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def _extract_hn_id_from_url(url: str) -> Optional[str]:
    """如果 url 是 hnrss 或 news.ycombinator.com 的链接,直接从中提取 HN id"""
    if not url:
        return None
    # 直接是 HN item 链接
    m = re.search(r"news\.ycombinator\.com/item\?id=(\d+)", url)
    if m:
        return m.group(1)
    # hnrss 输出的 link 字段往往就是 HN item 链接
    m = re.search(r"/item\?id=(\d+)", url)
    if m:
        return m.group(1)
    return None


def fetch_by_id(hn_id: str) -> Optional[dict]:
    """已知 HN id 直接查 item API 拿元数据"""
    data = _http_get_json(f"{ITEM_API}/{hn_id}")
    if not data:
        return None
    return {
        "points": data.get("points"),
        "num_comments": _count_comments(data),
        "hn_id": str(data.get("id", hn_id)),
        "hn_url": f"https://news.ycombinator.com/item?id={data.get('id', hn_id)}",
        "created_at": data.get("created_at"),
        "title": data.get("title") or "",
    }


def _count_comments(item: dict) -> int:
    """item API 返回嵌套 children 树,实际 num_comments 字段在 search API 才有。
    这里递归数一下子节点近似(不准确但够用)。"""
    n = 0
    for child in item.get("children") or []:
        n += 1 + _count_comments(child)
    return n


def fetch_by_title_search(title: str) -> Optional[dict]:
    """根据标题模糊搜索 HN 帖子,取最匹配的一个"""
    if not title:
        return None
    # 标题清洗:移除引号 / 多余空格,长度限制(Algolia 截 512)
    clean = re.sub(r"[\"'`]", "", title).strip()[:200]
    if not clean:
        return None
    q = urllib.parse.quote_plus(clean)
    data = _http_get_json(f"{SEARCH_API}?query={q}&tags=story&hitsPerPage=3")
    if not data or not data.get("hits"):
        return None
    # 取第一个 hit(Algolia 已按相关度排序)
    h = data["hits"][0]
    return {
        "points": h.get("points") or 0,
        "num_comments": h.get("num_comments") or 0,
        "hn_id": str(h.get("objectID", "")),
        "hn_url": f"https://news.ycombinator.com/item?id={h.get('objectID', '')}",
        "created_at": h.get("created_at"),
        "title": h.get("title") or "",
    }


def fetch_hn_metadata(title: str, url: Optional[str]) -> Optional[dict]:
    """
    综合入口:优先用 url 里的 HN id 直查(快+准),失败 fallback 标题搜索。

    返回 dict 或 None。
    """
    # 路径 1:URL 里直接带 HN id
    hn_id = _extract_hn_id_from_url(url) if url else None
    if hn_id:
        meta = fetch_by_id(hn_id)
        if meta and meta.get("points") is not None:
            return meta
        # item API 拿到但没 points → fallback 搜索同帖
    # 路径 2:标题搜索
    return fetch_by_title_search(title)


def fetch_top_comments(hn_id: str, n: int = 5) -> list[str]:
    """拉一个 HN 帖子的前 N 条顶层评论。

    2026-05-18 加: 给 LLM 提供"社区争议/共识"信号,补足只看 points/comments 数字的不足。
    每条评论截到 400 字。Algolia item API 返回的 text 带 HTML tag,这里简易剥离。
    异常返回 []。
    """
    data = _http_get_json(f"{ITEM_API}/{hn_id}")
    if not data:
        return []
    out: list[str] = []
    for child in (data.get("children") or [])[: n * 3]:  # 多取一些,过滤空/dead/太短
        text = child.get("text") or ""
        if not text or child.get("dead") or child.get("deleted"):
            continue
        clean = re.sub(r"<[^>]+>", " ", text)
        clean = html.unescape(clean)  # &#x27; → ' 等 HTML 实体
        clean = re.sub(r"\s+", " ", clean).strip()
        if len(clean) < 30:  # 跳掉"+1"/"this"这种水帖
            continue
        out.append(clean[:400])
        if len(out) >= n:
            break
    return out


def enrich_items(
    items: list[dict],
    *,
    sleep_between: float = 0.15,
    fetch_comments_threshold: int = 50,
    comments_per_item: int = 5,
) -> int:
    """
    给一组 items 批量加 hn_meta 字段(原地修改)。
    只对 source 以 hn- 开头或等于 hacker-news 的 items 处理。

    2026-05-18 加: points >= fetch_comments_threshold 的高热度帖额外拉 top 评论,
    写入 it["hn_meta"]["top_comments"] (list[str])。低热度帖不拉,省 API。

    返回成功拿到 metadata 的命中数。
    """
    hit = 0
    for it in items:
        src = (it.get("source") or "").lower()
        if not (src.startswith("hn-") or src == "hacker-news"):
            continue
        meta = fetch_hn_metadata(it.get("title", ""), it.get("url"))
        if meta and meta.get("points") is not None:
            it["hn_meta"] = meta
            hit += 1
            comm_note = ""
            if meta.get("points", 0) >= fetch_comments_threshold and meta.get("hn_id"):
                comments = fetch_top_comments(meta["hn_id"], n=comments_per_item)
                if comments:
                    meta["top_comments"] = comments
                    comm_note = f" · 拉 {len(comments)} 评论"
                time.sleep(sleep_between)  # 评论 API 也限速
            print(f"  [HN] ✅ {meta.get('points', 0):>4} pts · {meta.get('num_comments', 0):>3} comm · {it.get('title', '')[:55]}{comm_note}")
        else:
            print(f"  [HN] —     无元数据 · {it.get('title', '')[:60]}")
        time.sleep(sleep_between)  # API 限速防御
    return hit


# ============== 自测 ==============
if __name__ == "__main__":
    # 用之前出过问题的"OpenClaw"那条做单元验证
    print("=== Test 1: 标题精确搜索(OpenClaw 那条) ===")
    m = fetch_by_title_search("Claude Code refuses requests or charges extra if your commits mention OpenClaw")
    if m:
        print(json.dumps(m, ensure_ascii=False, indent=2))
    else:
        print("FAIL")

    # 自测 enrich
    print("\n=== Test 2: enrich_items 批量(取 latest.json HN 类前 5 条) ===")
    from pathlib import Path
    digest_path = Path(__file__).parent.parent / "output" / "digest" / "latest.json"
    if digest_path.exists():
        data = json.loads(digest_path.read_text(encoding="utf-8"))
        hn_cat = next(
            (c for c in data["report"]["categories"] if c["name"] == "Hacker News"),
            None,
        )
        if hn_cat:
            items = hn_cat["items"][:5]
            hit = enrich_items(items, sleep_between=0.2)
            print(f"\n命中 {hit}/{len(items)}")
        else:
            print("没有 Hacker News 类")
    else:
        print("没有 latest.json")
