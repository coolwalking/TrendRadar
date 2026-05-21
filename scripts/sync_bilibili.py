"""
Sync B 站 AI 一手访谈账号 → 本地 RSS XML,供 trendradar 主管道消费

2026-05-12 加: 让 trend radar 把水球泡推荐的国内 AI 一手访谈源纳入

每天跑一次(在 daily_digest.sh Step 0 调用)。
依赖 bilibili-api-python(已实现 WBI 签名),无需登录。

输出: output/synced/bilibili/<account_id>.xml
"""
from __future__ import annotations

import sys
import time
import random
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

from bilibili_api import user, sync

# 水球泡视频里推荐的 3 个 B 站账号
# 格式: account_id(对应 config.yaml 里 feed id), uid, display_name
ACCOUNTS = [
    ("web3-sky-city", 351754674, "web3 天空之城"),
    ("zhangxiaojun-bili", 280780745, "张小珺 (B 站)"),
    ("laoluo-shizilukou", 538596213, "老罗的十字路口 (B 站)"),
]

OUT_DIR = Path(__file__).resolve().parent.parent / "output" / "synced" / "bilibili"


def fetch_user_videos(uid: int, n: int = 15, max_retries: int = 3) -> list[dict]:
    """拉 user 最近 N 条视频。bilibili-api 已处理 WBI 签名。

    遇 412 风控时退避重试(等 10/20/40 秒)。
    """
    u = user.User(uid=uid)
    last_err = None
    for attempt in range(max_retries):
        try:
            resp = sync(u.get_videos(pn=1, ps=n))
            return resp.get("list", {}).get("vlist", []) or []
        except Exception as e:
            last_err = e
            err_str = str(e)
            if "412" in err_str or "风控" in err_str:
                wait = (2 ** attempt) * 10 + random.uniform(0, 5)
                print(f"    412 风控,等 {wait:.0f}s 重试 ({attempt+1}/{max_retries})", flush=True)
                time.sleep(wait)
            else:
                # 非风控错误立即抛
                raise
    # 重试用尽
    raise last_err if last_err else RuntimeError("fetch failed")


def videos_to_rss(account_id: str, display_name: str, uid: int, videos: list[dict]) -> str:
    """B 站视频列表 → RSS 2.0 XML"""
    now_str = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    entries = []
    for v in videos:
        bvid = v.get("bvid") or ""
        title = (v.get("title") or "").strip() or "(no title)"
        desc = (v.get("description") or "").strip()
        created_ts = v.get("created") or 0
        # B 站时间戳是本地秒(中国时间),转 UTC
        try:
            pub_date = datetime.fromtimestamp(created_ts, tz=timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
        except Exception:
            pub_date = now_str
        url = f"https://www.bilibili.com/video/{bvid}" if bvid else f"https://space.bilibili.com/{uid}"
        entries.append(f"""
    <item>
      <title>{escape(title)}</title>
      <link>{escape(url)}</link>
      <guid isPermaLink="true">{escape(url)}</guid>
      <pubDate>{pub_date}</pubDate>
      <description>{escape(desc)}</description>
    </item>""")
    body = "".join(entries)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>{escape(display_name)}</title>
    <link>https://space.bilibili.com/{uid}</link>
    <description>Synced by sync_bilibili.py</description>
    <lastBuildDate>{now_str}</lastBuildDate>{body}
  </channel>
</rss>
"""


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[sync_bilibili] 开始同步 {len(ACCOUNTS)} 个 B 站账号", flush=True)

    fail_count = 0
    for i, (account_id, uid, display_name) in enumerate(ACCOUNTS):
        # 账号之间 sleep 防风控(第一个不 sleep)
        if i > 0:
            wait = random.uniform(5, 8)
            print(f"  ... 间隔 {wait:.1f}s 防风控", flush=True)
            time.sleep(wait)
        try:
            print(f"  [{account_id}] uid={uid} 调用 B 站 API...", flush=True)
            videos = fetch_user_videos(uid)
            xml = videos_to_rss(account_id, display_name, uid, videos)
            out = OUT_DIR / f"{account_id}.xml"
            out.write_text(xml, encoding="utf-8")
            print(f"  [{account_id}] 拿到 {len(videos)} 条 → {out.name} ({out.stat().st_size} bytes)", flush=True)
        except Exception as e:
            print(f"  [{account_id}] 失败: {e}", file=sys.stderr, flush=True)
            fail_count += 1

    if fail_count == len(ACCOUNTS):
        print("[sync_bilibili] 全部失败", file=sys.stderr)
        return 1
    print(f"[sync_bilibili] 完成 ({len(ACCOUNTS) - fail_count}/{len(ACCOUNTS)} 成功)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
