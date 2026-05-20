"""
Transcript enricher

针对 lex-fridman 源, 从 RSS summary 提取 lexfridman.com/{slug}-transcript URL,
抓 transcript 页面的 ts-segment 块, 拼成纯文本作为 transcript_full 字段。

2026-05-18 加: Lex 的 RSS 用的是 YouTube channel feed, 拿不到 transcript。
但 RSS description 文本里固定有一个 "Transcript: https://lexfridman.com/{slug}-transcript"
模式, 拿到这个 URL 抓页面就能拿到 human-verified 完整 transcript。

Lex transcript 页面 HTML 结构 (实测):
- 每段对话是 <div class="ts-segment"> 包含 speaker name + 时间戳 + dialogue
- 一集典型 800-1500 段
- 整页 HTML 580k 字符, 提取后纯文本 80-150k 字符

输出限到 8000 字符 (~2000 token), 截前 80% + 后 20%, 平衡开头和结尾内容。
"""

from __future__ import annotations
import html
import re
import time
import urllib.parse
import urllib.request
from typing import Optional


TIMEOUT = 15
UA = "TrendRadar/1.0 (https://github.com/sansan0/TrendRadar)"
LEX_TRANSCRIPT_PATTERN = re.compile(
    r"https?://lexfridman\.com/([A-Za-z0-9_-]+)-transcript/?",
    re.IGNORECASE,
)
ALLOWED_LEX_HOSTS = {"lexfridman.com", "www.lexfridman.com"}
MAX_TRANSCRIPT_CHARS = 8000  # ~2000 token, 留余地给其它 voices


def extract_lex_transcript_url(summary_text: str) -> Optional[str]:
    """从 RSS summary 提取 Lex transcript URL。无匹配返回 None。"""
    if not summary_text:
        return None
    m = LEX_TRANSCRIPT_PATTERN.search(summary_text)
    if not m:
        return None
    url = m.group(0).rstrip("/")
    # SSRF 防护: urlparse + hostname 白名单
    try:
        p = urllib.parse.urlparse(url)
    except Exception:
        return None
    if p.scheme not in ("http", "https"):
        return None
    if (p.hostname or "").lower() not in ALLOWED_LEX_HOSTS:
        return None
    return url


def fetch_lex_transcript(url: str) -> Optional[str]:
    """GET Lex transcript 页, 提取 ts-segment 块, 拼成纯文本。失败/异常返回 None。"""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            if resp.status != 200:
                return None
            page = resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None

    # 抓所有 ts-segment div
    segments = re.findall(
        r'<div[^>]*class="[^"]*ts-segment[^"]*"[^>]*>(.*?)</div>',
        page,
        re.DOTALL,
    )
    if not segments:
        # 后备: 整个 <article> 体
        m = re.search(r"<article[^>]*>(.*?)</article>", page, re.DOTALL)
        if not m:
            return None
        segments = [m.group(1)]

    parts: list[str] = []
    for seg in segments:
        # 剥 HTML tag, 解 entity, 折叠空白
        text = re.sub(r"<[^>]+>", " ", seg)
        text = html.unescape(text)
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            parts.append(text)
    if not parts:
        return None

    joined = " ".join(parts)
    # 截到 MAX_TRANSCRIPT_CHARS: 前 80% + 中间省略号 + 后 20%
    if len(joined) > MAX_TRANSCRIPT_CHARS:
        head_n = int(MAX_TRANSCRIPT_CHARS * 0.8)
        tail_n = MAX_TRANSCRIPT_CHARS - head_n - 30
        joined = joined[:head_n] + " ... [transcript 中段省略] ... " + joined[-tail_n:]
    return joined


def enrich_voices(
    voices: list[dict],
    *,
    sleep_between: float = 1.0,
    wall_budget_s: float = 60.0,
    verbose: bool = True,
) -> int:
    """
    对 source 为 lex-fridman 的 voice item 抓 transcript, 写入 it["transcript_full"]。

    其它 source (Dwarkesh 走 Substack feed 直接拿到 content:encoded, Latent Space 同理)
    不需要在这里 enrich, 它们的全文已经在 it["summary"] 里。

    返回成功 enrich 的 item 数。
    """
    hit = 0
    deadline = time.monotonic() + wall_budget_s
    for v in voices:
        if (v.get("source") or "").lower() != "lex-fridman":
            continue
        if time.monotonic() > deadline:
            if verbose:
                print(f"  [TR] ⏱  超 {wall_budget_s:.0f}s 预算, 剩下 Lex item 跳过")
            break
        url = extract_lex_transcript_url(v.get("summary") or "")
        if not url:
            if verbose:
                print(f"  [TR] —     {v.get('title','')[:55]} (summary 无 transcript URL)")
            continue
        transcript = fetch_lex_transcript(url)
        if transcript:
            v["transcript_full"] = transcript
            hit += 1
            if verbose:
                print(f"  [TR] ✅ {len(transcript)} 字 · {v.get('title','')[:50]}")
        else:
            if verbose:
                print(f"  [TR] —     抓失败 · {url[:60]}")
        time.sleep(sleep_between)
    return hit


# ============== 自测 ==============
if __name__ == "__main__":
    test_summary = (
        "FFmpeg episode summary blah blah See below for transcript "
        "Transcript: https://lexfridman.com/ffmpeg-transcript "
        "EPISODE LINKS blah"
    )
    url = extract_lex_transcript_url(test_summary)
    print(f"Extracted: {url}")
    if url:
        t = fetch_lex_transcript(url)
        if t:
            print(f"Transcript: {len(t)} chars")
            print(f"Head: {t[:300]}")
            print(f"Tail: {t[-200:]}")
        else:
            print("Fetch failed")
