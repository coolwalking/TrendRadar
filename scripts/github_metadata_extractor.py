"""
GitHub repo metadata extractor

针对 github-trending-* 来源的 items,通过 GitHub REST API 拿到:
  - stars         真实 stargazer 数
  - forks         真实 fork 数
  - language      主要语言
  - description   官方简介
  - pushed_at     最后 push 时间(用于时效性)
  - created_at    repo 创建时间

公开 API,无 token 限 60 次/小时(GH_TOKEN 在环境变量里则 5000 次/小时)。
我们一次只 30 条,无需 token。

公开函数:
  fetch_github_metadata(url: str) -> dict | None
  enrich_items(items: list[dict]) -> int
"""

from __future__ import annotations
import json
import os
import re
import time
import urllib.parse
import urllib.request
from typing import Optional


API_BASE = "https://api.github.com/repos"
TIMEOUT = 8

# repo URL 解析:支持
#   https://github.com/owner/repo
#   https://github.com/owner/repo/
#   https://github.com/owner/repo/tree/main/...
#   github.com/owner/repo
_REPO_RE = re.compile(r"github\.com/([^/\s?#]+)/([^/\s?#]+)", re.I)


def _http_get_json(url: str, timeout: int = TIMEOUT) -> Optional[dict]:
    try:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "TrendRadar GH Metadata/1.0",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        # 如果环境里有 GH_TOKEN,带上(限流从 60 提到 5000)
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def parse_repo_path(url: str) -> Optional[tuple[str, str]]:
    """从 URL 解析 (owner, repo)"""
    if not url:
        return None
    m = _REPO_RE.search(url)
    if not m:
        return None
    owner = m.group(1).strip()
    repo = m.group(2).strip().rstrip(".git")
    # 排除非 repo 路径:settings, marketplace, topics 等
    if owner.lower() in {"settings", "marketplace", "topics", "trending", "explore", "features"}:
        return None
    return owner, repo


def fetch_github_metadata(url: str) -> Optional[dict]:
    """根据 GitHub URL 拿 repo 真实 metadata。返回 dict 或 None。"""
    parsed = parse_repo_path(url)
    if not parsed:
        return None
    owner, repo = parsed
    data = _http_get_json(f"{API_BASE}/{owner}/{repo}")
    if not data or not isinstance(data, dict):
        return None
    return {
        "owner": owner,
        "repo": repo,
        "full_name": data.get("full_name") or f"{owner}/{repo}",
        "stars": data.get("stargazers_count"),
        "forks": data.get("forks_count"),
        "language": data.get("language") or "",
        "description": data.get("description") or "",
        "pushed_at": data.get("pushed_at"),
        "created_at": data.get("created_at"),
        "open_issues": data.get("open_issues_count"),
        "license": (data.get("license") or {}).get("spdx_id") or "",
        "topics": data.get("topics") or [],
        "homepage": data.get("homepage") or "",
        "html_url": data.get("html_url") or f"https://github.com/{owner}/{repo}",
    }


def enrich_items(items: list[dict], *, sleep_between: float = 0.2) -> int:
    """对所有 source 以 github-trending- 开头的 items 加 gh_meta 字段(原地)"""
    hit = 0
    seen: dict[str, dict] = {}  # 同一 repo URL 多个候选共享一次 API 调用
    for it in items:
        src = (it.get("source") or "").lower()
        if not src.startswith("github-trending"):
            continue
        url = it.get("url", "")
        cache_key = url
        if cache_key in seen:
            it["gh_meta"] = seen[cache_key]
            hit += 1
            continue
        meta = fetch_github_metadata(url)
        if meta:
            it["gh_meta"] = meta
            seen[cache_key] = meta
            hit += 1
            stars = meta.get("stars", 0)
            print(f"  [GH] ✅ {stars:>6} ★ · {meta['full_name']}")
        else:
            print(f"  [GH] —     无元数据 · {url[:60]}")
        time.sleep(sleep_between)
    return hit


# ============== 自测 ==============
if __name__ == "__main__":
    print("=== Test 1: 解析 URL ===")
    for u in [
        "https://github.com/openai/gpt-oss",
        "https://github.com/microsoft/TRELLIS",
        "https://github.com/microsoft/TRELLIS/tree/main",
        "github.com/foo/bar",
        "https://news.ycombinator.com/item?id=123",
    ]:
        print(f"  {u} → {parse_repo_path(u)}")

    print("\n=== Test 2: 抓 microsoft/TRELLIS 真实元数据 ===")
    m = fetch_github_metadata("https://github.com/microsoft/TRELLIS")
    if m:
        print(json.dumps({
            "full_name": m["full_name"],
            "stars": m["stars"],
            "forks": m["forks"],
            "language": m["language"],
            "pushed_at": m["pushed_at"],
        }, ensure_ascii=False, indent=2))
    else:
        print("FAIL")

    print("\n=== Test 3: enrich latest.json GitHub 类前 5 条 ===")
    from pathlib import Path
    digest_path = Path(__file__).parent.parent / "output" / "digest" / "latest.json"
    if digest_path.exists():
        data = json.loads(digest_path.read_text(encoding="utf-8"))
        gh_cat = next(
            (c for c in data["report"]["categories"] if c["name"] == "GitHub 开源生态"),
            None,
        )
        if gh_cat:
            items = [{"source": "github-trending-python", "url": it["url"], "title": it["title"]}
                     for it in gh_cat["items"][:5]]
            hit = enrich_items(items, sleep_between=0.3)
            print(f"\n命中 {hit}/{len(items)}")
        else:
            print("没有 GitHub 类")
