"""
Reddit comment extractor

针对 source 以 reddit- 开头的 items,拉 top N 评论作为社区信号给 LLM。

Reddit 公开 JSON 端点(不需要 OAuth):
  https://www.reddit.com/r/<sub>/comments/<id>/.json
必须带 User-Agent,否则返回 429。

2026-05-18 加(配合 HN 评论增强,统一"社区信号"层)。
"""

from __future__ import annotations
import json
import re
import time
import urllib.parse
import urllib.request
from typing import Optional


TIMEOUT = 10
UA = "TrendRadar/1.0 (https://github.com/sansan0/TrendRadar)"
# 2026-05-18 加(codex review P2): SSRF 防护 — 只允许真实 reddit hostname
ALLOWED_REDDIT_HOSTS = {"www.reddit.com", "reddit.com", "old.reddit.com", "new.reddit.com"}


def _http_get_json(url: str, timeout: int = TIMEOUT) -> Optional[list | dict]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def _ensure_json_url(url: str) -> Optional[str]:
    """把 Reddit 帖子 URL 变成 .json 端点。

    输入示例:
      https://www.reddit.com/r/LocalLLaMA/comments/abc123/title_slug/

    2026-05-18 加(codex review P2): urlparse 严格校验 scheme + hostname,
    防止字符串包含匹配被构造 URL 绕过(例如 http://attacker.com/?x=reddit.com/comments/x)。
    """
    if not url:
        return None
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return None
    if parsed.scheme not in ("http", "https"):
        return None
    if (parsed.hostname or "").lower() not in ALLOWED_REDDIT_HOSTS:
        return None
    if "/comments/" not in parsed.path:
        return None
    base = url.rstrip("/")
    if base.endswith(".json"):
        return base
    return f"{base}.json?limit=20&depth=1"


def fetch_top_comments(post_url: str, n: int = 5) -> list[str]:
    """拉 Reddit 帖子的 top N 顶层评论,按 score 倒排截断到 400 字。

    Reddit JSON 结构:[{post}, {data:{children:[{kind:'t1', data:{body, score, ...}}]}}]
    2026-05-18 加(codex review P2): 整段 try/except,任何结构异常返回 [] 不让上游崩。
    """
    json_url = _ensure_json_url(post_url)
    if not json_url:
        return []
    data = _http_get_json(json_url)
    if not isinstance(data, list) or len(data) < 2:
        return []
    try:
        second = data[1] if isinstance(data[1], dict) else {}
        inner = second.get("data") if isinstance(second, dict) else None
        children = inner.get("children") if isinstance(inner, dict) else []
        if not isinstance(children, list):
            return []
        candidates: list[tuple[int, str]] = []
        for c in children:
            if not isinstance(c, dict) or c.get("kind") != "t1":
                continue
            d = c.get("data") if isinstance(c.get("data"), dict) else {}
            body = d.get("body") or ""
            try:
                score = int(d.get("score") or 0)
            except (TypeError, ValueError):
                score = 0
            if not body or d.get("removed") or d.get("collapsed"):
                continue
            # 简易剥 markdown link / 多空白
            clean = re.sub(r"\[([^\]]+)\]\(https?://[^\)]+\)", r"\1", body)
            clean = re.sub(r"\s+", " ", clean).strip()
            if len(clean) < 30:
                continue
            candidates.append((score, clean[:400]))
        candidates.sort(key=lambda x: x[0], reverse=True)
        return [text for _, text in candidates[:n]]
    except Exception:
        return []


def enrich_items(
    items: list[dict],
    *,
    sleep_between: float = 0.5,
    max_enrich: int = 15,
    comments_per_item: int = 5,
    wall_budget_s: float = 90.0,
) -> int:
    """
    给 source 以 reddit- 开头的 items 批量加 reddit_meta.top_comments(原地修改)。

    2026-05-18(codex P1): wall_budget_s 是整段 enrich 的硬时间预算。超了就提前返回,
    防止 Reddit 限频/慢响应把 generate_digest 10 分钟超时吃满。

    返回拉到至少 1 条评论的 item 数。
    """
    hit = 0
    seen = 0
    deadline = time.monotonic() + wall_budget_s
    for it in items:
        src = (it.get("source") or "").lower()
        if not src.startswith("reddit-"):
            continue
        seen += 1
        if seen > max_enrich:
            break
        if time.monotonic() > deadline:
            print(f"  [RD] ⏱  已用时 {wall_budget_s:.0f}s,余下 {max_enrich - seen + 1} 个 reddit item 跳过")
            break
        url = it.get("url") or ""
        comments = fetch_top_comments(url, n=comments_per_item)
        if comments:
            it["reddit_meta"] = {"top_comments": comments}
            hit += 1
            print(f"  [RD] ✅ {len(comments)} 评论 · {it.get('title', '')[:60]}")
        else:
            print(f"  [RD] —     无评论 · {it.get('title', '')[:60]}")
        time.sleep(sleep_between)
    return hit


# ============== 自测 ==============
if __name__ == "__main__":
    sample = "https://www.reddit.com/r/LocalLLaMA/comments/1d8j7e1/qwen3_is_out/"
    print("=== Test: fetch_top_comments sample ===")
    cs = fetch_top_comments(sample, n=3)
    if cs:
        for i, c in enumerate(cs, 1):
            print(f"  [{i}] {c[:120]}...")
    else:
        print("  (无返回,帖子可能已删/网络问题)")
