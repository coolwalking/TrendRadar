# coding=utf-8
"""
Focused daily digest for western news and social RSS sources.

This module is intentionally separate from the original hot-list crawler:
it uses explicit date windows, source links, and a switchable model backend.
"""

from __future__ import annotations

import argparse
import calendar
import html
import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import time
from dataclasses import dataclass, asdict
from datetime import datetime, time as dt_time, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import feedparser
import requests
import yaml


DEFAULT_CONFIG_PATH = Path("config/daily_digest.yaml")
DEFAULT_MAIN_CONFIG_PATH = Path("config/config.yaml")
USER_AGENT = "TrendRadar-DailyDigest/1.0 (+https://github.com/sansan0/TrendRadar)"


@dataclass
class DigestItem:
    title: str
    source_id: str
    source_name: str
    source_category: str
    url: str
    published_at: str
    summary: str
    matched_topics: List[str]
    score: int


def load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def parse_target_date(date_value: Optional[str], tz: ZoneInfo) -> datetime.date:
    if date_value:
        return datetime.strptime(date_value, "%Y-%m-%d").date()
    return datetime.now(tz).date()


def day_window(target_date, tz: ZoneInfo) -> Tuple[datetime, datetime]:
    start = datetime.combine(target_date, dt_time.min, tzinfo=tz)
    end = datetime.combine(target_date, dt_time.max, tzinfo=tz)
    return start, end


def entry_datetime(entry: Any, tz: ZoneInfo) -> Optional[datetime]:
    parsed = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if not parsed:
        return None
    utc_dt = datetime.fromtimestamp(calendar.timegm(parsed), tz=timezone.utc)
    return utc_dt.astimezone(tz)


def strip_markup(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value or "")
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def normalize_url(url: str) -> str:
    return (url or "").strip()


def keyword_matches(text: str, keyword: str) -> bool:
    keyword_text = keyword.lower().strip()
    if not keyword_text:
        return False

    # Single-token keywords such as "ai", "nato", or "meta" must match as
    # full tokens, not inside words or URLs like "claiming" or "ycombinator".
    if keyword_text.replace("-", "").isalnum():
        pattern = rf"(?<![a-z0-9]){re.escape(keyword_text)}(?![a-z0-9])"
        return re.search(pattern, text) is not None

    return keyword_text in text


def score_item(text: str, topics: List[Dict[str, Any]]) -> Tuple[List[str], int]:
    lowered = text.lower()
    matched = []
    score = 0
    for topic in topics:
        topic_name = topic.get("name", "")
        keywords = topic.get("keywords", []) or []
        hits = 0
        for keyword in keywords:
            if keyword_matches(lowered, str(keyword)):
                hits += 1
        if hits:
            matched.append(topic_name)
            score += hits
    return matched, score


def topic_priority_map(topics: List[Dict[str, Any]]) -> Dict[str, int]:
    priorities: Dict[str, int] = {}
    for index, topic in enumerate(topics, 1):
        name = topic.get("name", "")
        if not name:
            continue
        priorities[name] = int(topic.get("priority", index))
    return priorities


def item_topic_priority(item: DigestItem, priorities: Dict[str, int]) -> int:
    matched = [priorities.get(topic, 999) for topic in item.matched_topics]
    return min(matched or [999])


def item_timestamp(item: DigestItem) -> float:
    if not item.published_at:
        return 0
    try:
        return datetime.fromisoformat(item.published_at).timestamp()
    except ValueError:
        return 0


def sort_digest_items(items: List[DigestItem], topics: List[Dict[str, Any]]) -> List[DigestItem]:
    priorities = topic_priority_map(topics)
    category_bonus = {"github": 4, "tech": 2, "news": 1, "social": 1, "aggregator": 0}
    return sorted(
        items,
        key=lambda item: (
            item_topic_priority(item, priorities),
            -(item.score + category_bonus.get(item.source_category, 0)),
            -item.score,
            -item_timestamp(item),
        ),
    )


def fetch_feed(source: Dict[str, Any], timeout: int, retry_count: int = 1) -> Any:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml, text/xml, */*"}
    last_error = None
    for attempt in range(retry_count + 1):
        try:
            response = requests.get(source["url"], headers=headers, timeout=timeout)
            response.raise_for_status()
            return feedparser.parse(response.content)
        except Exception as exc:
            last_error = exc
            if attempt < retry_count:
                time.sleep(1 + attempt)
    raise last_error


def fetch_github_trending(source: Dict[str, Any], timeout: int, retry_count: int = 1) -> str:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    last_error = None
    for attempt in range(retry_count + 1):
        try:
            response = requests.get(source["url"], headers=headers, timeout=timeout)
            response.raise_for_status()
            return response.text
        except Exception as exc:
            last_error = exc
            if attempt < retry_count:
                time.sleep(1 + attempt)
    raise last_error


def parse_github_trending_html(
    raw_html: str,
    source: Dict[str, Any],
    target_date,
    tz: ZoneInfo,
    max_items: int,
) -> List[DigestItem]:
    articles = re.findall(r"<article\b.*?</article>", raw_html, flags=re.DOTALL | re.IGNORECASE)
    published = datetime.combine(target_date, dt_time(hour=12), tzinfo=tz).isoformat()
    items: List[DigestItem] = []

    for article in articles:
        if len(items) >= max_items:
            break

        link_match = re.search(r'href="/([^"/\s]+/[^"#?\s]+)"', article)
        if not link_match:
            continue
        repo_path = strip_markup(link_match.group(1)).strip("/")
        if not repo_path or "/" not in repo_path:
            continue
        owner, repo = repo_path.split("/", 1)
        if owner in {"sponsors", "features", "topics", "collections", "marketplace", "explore"} or not repo:
            continue

        description_match = re.search(
            r"<p\b[^>]*color-fg-muted[^>]*>(.*?)</p>",
            article,
            flags=re.DOTALL | re.IGNORECASE,
        )
        language_match = re.search(
            r'itemprop="programmingLanguage"[^>]*>(.*?)</[^>]+>',
            article,
            flags=re.DOTALL | re.IGNORECASE,
        )
        stars_today_match = re.search(r"([\d,.]+)\s+stars?\s+today", strip_markup(article), flags=re.IGNORECASE)

        description = strip_markup(description_match.group(1)) if description_match else ""
        language = strip_markup(language_match.group(1)) if language_match else ""
        stars_today = f"{stars_today_match.group(1)} stars today" if stars_today_match else ""
        summary_parts = []
        if description:
            summary_parts.append(f"Description: {description}")
        if language:
            summary_parts.append(f"Language: {language}")
        if stars_today:
            summary_parts.append(f"Trending signal: {stars_today}")

        items.append(
            DigestItem(
                title=repo_path,
                source_id=source.get("id", ""),
                source_name=source.get("name", source.get("id", "")),
                source_category=source.get("category", "github"),
                url=f"https://github.com/{repo_path}",
                published_at=published,
                summary=" | ".join(summary_parts) or "GitHub Trending did not provide a description.",
                matched_topics=["GitHub 热点项目"],
                score=5 + len(summary_parts),
            )
        )

    return items


def collect_items(config: Dict[str, Any], target_date, tz: ZoneInfo) -> Tuple[List[DigestItem], List[str]]:
    fetch_config = config.get("fetch", {})
    topics = config.get("topics", []) or []
    include_undated = bool(fetch_config.get("include_undated_items", False))
    max_per_source = int(fetch_config.get("max_items_per_source", 25))
    max_total = int(fetch_config.get("max_total_items", 80))
    timeout = int(fetch_config.get("request_timeout", 20))
    retry_count = int(fetch_config.get("retry_count", 1))
    start, end = day_window(target_date, tz)

    items: List[DigestItem] = []
    errors: List[str] = []
    seen: set[str] = set()

    for source in config.get("sources", []) or []:
        if not source.get("enabled", True):
            continue
        source_type = source.get("type", "rss")
        if source_type == "github_trending":
            try:
                raw_html = fetch_github_trending(source, timeout, retry_count)
                for item in parse_github_trending_html(raw_html, source, target_date, tz, max_per_source):
                    dedupe_key = item.url or item.title.lower()
                    if dedupe_key in seen:
                        continue
                    seen.add(dedupe_key)
                    items.append(item)
            except Exception as exc:
                errors.append(f"{source.get('name', source.get('id'))}: {exc}")
            continue

        if source_type != "rss":
            errors.append(f"Skipped unsupported source type: {source.get('id')}")
            continue

        try:
            parsed = fetch_feed(source, timeout, retry_count)
        except Exception as exc:
            errors.append(f"{source.get('name', source.get('id'))}: {exc}")
            continue

        source_count = 0
        for entry in parsed.entries:
            if source_count >= max_per_source:
                break

            published = entry_datetime(entry, tz)
            if published is None and not include_undated:
                continue
            if published is not None and not (start <= published <= end):
                continue

            title = strip_markup(getattr(entry, "title", ""))
            url = normalize_url(getattr(entry, "link", ""))
            summary = strip_markup(getattr(entry, "summary", "") or getattr(entry, "description", ""))
            if not title or not url:
                continue

            dedupe_key = url or title.lower()
            if dedupe_key in seen:
                continue

            text = f"{title}\n{summary}"
            matched_topics, score = score_item(text, topics)
            if topics and not matched_topics:
                continue

            seen.add(dedupe_key)
            source_count += 1
            items.append(
                DigestItem(
                    title=title,
                    source_id=source.get("id", ""),
                    source_name=source.get("name", source.get("id", "")),
                    source_category=source.get("category", "news"),
                    url=url,
                    published_at=published.isoformat() if published else "",
                    summary=summary[:700],
                    matched_topics=matched_topics,
                    score=score,
                )
            )

    return sort_digest_items(items, topics)[:max_total], errors


def item_payload(items: List[DigestItem]) -> List[Dict[str, Any]]:
    return [asdict(item) for item in items]


def select_items_for_prompt(config: Dict[str, Any], items: List[DigestItem]) -> List[DigestItem]:
    ai_config = config.get("ai", {})
    max_per_topic = int(ai_config.get("max_prompt_items_per_topic", 12))
    topics = [topic.get("name", "") for topic in config.get("topics", []) or [] if topic.get("name")]
    selected: List[DigestItem] = []
    seen: set[str] = set()

    for topic in topics:
        count = 0
        for item in items:
            if topic not in item.matched_topics:
                continue
            key = item.url or item.title
            if key in seen:
                continue
            selected.append(item)
            seen.add(key)
            count += 1
            if count >= max_per_topic:
                break

    return selected


def build_prompt(config: Dict[str, Any], items: List[DigestItem], target_date, tz: ZoneInfo) -> List[Dict[str, str]]:
    language = config.get("language", "Chinese")
    start, end = day_window(target_date, tz)
    payload = item_payload(select_items_for_prompt(config, items))

    topic_order = [topic.get("name", "") for topic in config.get("topics", []) or [] if topic.get("name")]
    system = (
        "你是一名谨慎的中文研究编辑。你的任务是把英文/社区/聚合条目整理成中文简报。"
        "只能基于提供的 Items JSON 写作，禁止补充外部知识，禁止编造事实、原因、引语、日期、数字、融资金额、版本号或影响范围。"
        "如果标题或摘要没有给出足够细节，必须写“原始摘要不足，需要打开原文确认”，不能自行推断。"
        "每个判断都要能回到 title、summary、source_name、matched_topics 或 url。"
        "GitHub 项目只能根据 repo 名、description、language、stars today 描述用途和热度，不得编造 stars、license、作者背景或商业化信息。"
    )
    user = f"""
请用{language}生成一份专项每日热点简报。

日期窗口：
{start.isoformat()} to {end.isoformat()}

主题优先级：
{json.dumps(topic_order, ensure_ascii=False)}

格式要求：
- 标题使用中文，例如「每日专项热点 - YYYY-MM-DD」。
- 开头必须写明完整日期窗口，不要让读者猜“今天”是哪一天。
- 先写「3分钟速读」：用 5 条以内 bullet，严格按主题优先级概括。
- 再按主题优先级写四个分区：GitHub 热点项目、AI 与前沿模型、生物医疗工程、国际局势。
- 每个分区选择最多 6 条；没有足够条目就写“今日来源池不足”。
- 每条的小标题必须尽量使用中文标题；GitHub 项目可以保留 repo 名。
- 每条必须包含这些小标题：
  - 「一句话总结」：中文，不超过 60 字。
  - 「我的理解」：只解释该条为什么值得看，不能加入 Items JSON 没有的新事实。
  - 「原始内容」：用中文翻译/转述原始英文标题和摘要中的关键信息；不要扩写成新事实。
  - 「证据」：写来源名称、发布时间、链接。
  - 「可信度」：高 / 中 / 低。社区、聚合、摘要很薄、单一来源默认中或低，不要给高。
  - 「下一步看什么」：只能提出需要确认的问题，例如“打开原文确认具体数据/发布方/影响范围”。
- GitHub 项目优先解释“它看起来解决什么问题”和“热度信号来自哪里”；没有 description 就明确说不足。
- 主流媒体、GitHub、社区、Google News 聚合源要明确区分。
- 不要使用没有证据的推测。如果摘要太薄，就写「细节不足，建议打开原文确认」。
- 结尾给「值得继续追踪」和「来源列表」。

Items JSON:
{json.dumps(payload, ensure_ascii=False, indent=2)}
"""
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": textwrap.dedent(user).strip()},
    ]


def call_ollama(ai_config: Dict[str, Any], messages: List[Dict[str, str]]) -> str:
    api_base = ai_config.get("api_base", "http://localhost:11434").rstrip("/")
    payload = {
        "model": ai_config.get("model", "qwen2.5:7b"),
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": ai_config.get("temperature", 0.2),
            "num_predict": ai_config.get("max_tokens", 2200),
        },
    }
    response = requests.post(
        f"{api_base}/api/chat",
        json=payload,
        timeout=int(ai_config.get("timeout", 180)),
    )
    response.raise_for_status()
    data = response.json()
    return data.get("message", {}).get("content", "").strip()


def call_litellm(ai_config: Dict[str, Any], messages: List[Dict[str, str]]) -> str:
    from trendradar.ai.client import AIClient

    api_key = ai_config.get("api_key", "")
    api_key_env = ai_config.get("api_key_env", "AI_API_KEY")
    if not api_key and api_key_env:
        api_key = os.environ.get(api_key_env, "")

    client_config = {
        "MODEL": ai_config.get("model", ""),
        "API_KEY": api_key,
        "API_BASE": ai_config.get("api_base", ""),
        "TEMPERATURE": ai_config.get("temperature", 0.2),
        "MAX_TOKENS": ai_config.get("max_tokens", 2200),
        "TIMEOUT": ai_config.get("timeout", 180),
        "NUM_RETRIES": ai_config.get("num_retries", 1),
        "FALLBACK_MODELS": ai_config.get("fallback_models", []),
    }
    return AIClient(client_config).chat(messages)


def call_minimax(ai_config: Dict[str, Any], messages: List[Dict[str, str]]) -> str:
    api_key = ai_config.get("api_key", "")
    api_key_env = ai_config.get("api_key_env", "MINIMAX_API_KEY")
    if not api_key and api_key_env:
        api_key = os.environ.get(api_key_env, "")
    if not api_key:
        raise ValueError(f"Missing MiniMax API key. Set {api_key_env} or ai.api_key in daily_digest.yaml.")

    api_base = ai_config.get("api_base", "https://api.minimax.io/v1").rstrip("/")
    payload = {
        "model": ai_config.get("model", "MiniMax-M2.7"),
        "messages": messages,
        "temperature": ai_config.get("temperature", 1.0),
        "max_tokens": ai_config.get("max_tokens", 2200),
        "stream": False,
        "reasoning_split": True,
    }
    response = requests.post(
        f"{api_base}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=int(ai_config.get("timeout", 180)),
    )
    response.raise_for_status()
    data = response.json()
    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    return re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()


def call_openai_compatible(ai_config: Dict[str, Any], messages: List[Dict[str, str]]) -> str:
    api_key = ai_config.get("api_key", "")
    api_key_env = ai_config.get("api_key_env", "AI_API_KEY")
    if not api_key and api_key_env:
        api_key = os.environ.get(api_key_env, "")
    if not api_key:
        raise ValueError(f"Missing API key. Set {api_key_env} or ai.api_key in daily_digest.yaml.")

    api_base = ai_config.get("api_base", "").rstrip("/")
    if not api_base:
        raise ValueError("Missing ai.api_base in daily_digest.yaml.")

    payload = {
        "model": ai_config.get("model", ""),
        "messages": messages,
        "temperature": ai_config.get("temperature", 1.0),
        "max_tokens": ai_config.get("max_tokens", 2200),
        "stream": False,
    }

    response = requests.post(
        f"{api_base}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=int(ai_config.get("timeout", 180)),
    )
    response.raise_for_status()
    data = response.json()
    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    return re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()


def generate_ai_digest(config: Dict[str, Any], items: List[DigestItem], target_date, tz: ZoneInfo) -> str:
    ai_config = config.get("ai", {})
    provider = ai_config.get("provider", "ollama").lower()
    messages = build_prompt(config, items, target_date, tz)
    if provider in {"deepseek", "openai_compatible"}:
        return call_openai_compatible(ai_config, messages)
    if provider == "minimax":
        return call_minimax(ai_config, messages)
    if provider == "ollama":
        return call_ollama(ai_config, messages)
    if provider in {"litellm", "gemini"}:
        return call_litellm(ai_config, messages)
    raise ValueError(f"Unsupported AI provider: {provider}")


def extractive_digest(config: Dict[str, Any], items: List[DigestItem], target_date, tz: ZoneInfo, errors: List[str]) -> str:
    start, end = day_window(target_date, tz)
    lines = [
        f"# 每日专项热点 - {target_date.isoformat()}",
        "",
        f"日期窗口：`{start.isoformat()}` 至 `{end.isoformat()}`",
        "",
    ]
    if not items:
        lines.append("没有找到匹配当前主题和来源的新闻。")
    else:
        grouped: Dict[str, List[DigestItem]] = {}
        for item in items:
            topic_names = item.matched_topics or ["未分类"]
            for topic in topic_names:
                grouped.setdefault(topic, []).append(item)

        topic_names = [topic.get("name", "") for topic in config.get("topics", []) or [] if topic.get("name")]
        for topic in topic_names + [name for name in grouped if name not in topic_names]:
            topic_items = grouped.get(topic, [])
            if not topic_items:
                lines.append(f"## {topic}")
                lines.append("今日来源池不足。")
                lines.append("")
                continue
            lines.append(f"## {topic}")
            for item in topic_items[:8]:
                summary = item.summary or "该来源未提供摘要，建议打开原文确认。"
                lines.append(f"- [{item.title}]({item.url})")
                lines.append(f"  来源：{item.source_name} | 发布时间：{item.published_at or '未知'}")
                lines.append(f"  一句话总结：该条来自原始来源池，未调用 AI 时保留原文信息。")
                lines.append(f"  我的理解：需要调用配置模型后生成；当前只做证据整理，避免无依据推断。")
                lines.append(f"  原始内容：{summary[:360]}")
                lines.append(f"  可信度：{'中' if item.source_category in {'news', 'tech', 'github'} else '低'}")
                lines.append(f"  下一步看什么：打开原文确认具体细节、数据和影响范围。")
            lines.append("")

    if errors:
        lines.append("## 运行警告")
        for error in errors[:12]:
            lines.append(f"- {error}")
        lines.append("")

    lines.append("## 来源列表")
    for item in items[:30]:
        lines.append(f"- {item.source_name}: [{item.title}]({item.url})")
    return "\n".join(lines).strip() + "\n"


def markdown_inline(value: str) -> str:
    escaped = html.escape(value)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    return re.sub(
        r"\[([^\]]+)\]\((https?://[^)]+)\)",
        lambda m: f'<a href="{html.escape(m.group(2), quote=True)}" target="_blank" rel="noreferrer">{m.group(1)}</a>',
        escaped,
    )


def markdown_to_body_html(markdown: str) -> str:
    parts = []
    in_list = False
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line or line == "---":
            if in_list:
                parts.append("</ul>")
                in_list = False
            continue
        if line.startswith("# "):
            if in_list:
                parts.append("</ul>")
                in_list = False
            parts.append(f"<h1>{markdown_inline(line[2:])}</h1>")
        elif line.startswith("## "):
            if in_list:
                parts.append("</ul>")
                in_list = False
            parts.append(f"<h2>{markdown_inline(line[3:])}</h2>")
        elif line.startswith("- "):
            if not in_list:
                parts.append("<ul>")
                in_list = True
            parts.append(f"<li>{markdown_inline(line[2:])}</li>")
        else:
            if in_list:
                parts.append("</ul>")
                in_list = False
            parts.append(f"<p>{markdown_inline(line)}</p>")
    if in_list:
        parts.append("</ul>")
    return "\n".join(parts)


def render_interactive_html(
    markdown: str,
    items: List[DigestItem],
    config: Dict[str, Any],
    target_date,
    tz: ZoneInfo,
    errors: List[str],
) -> str:
    start, end = day_window(target_date, tz)
    payload = json.dumps(item_payload(items), ensure_ascii=False).replace("</", "<\\/")
    topics = []
    for topic in config.get("topics", []) or []:
        name = topic.get("name", "")
        if name:
            topics.append(name)
    categories = sorted({item.source_category for item in items})
    source_count = len({item.source_id for item in items})
    social_count = sum(1 for item in items if item.source_category in {"social", "aggregator"})
    top_items = items[:6]

    topic_buttons = "".join(
        f'<button class="filter-btn" data-topic="{html.escape(topic, quote=True)}">{html.escape(topic)}</button>'
        for topic in topics
    )
    category_buttons = "".join(
        f'<button class="filter-btn ghost" data-category="{html.escape(category, quote=True)}">{html.escape(category)}</button>'
        for category in categories
    )
    top_links = "".join(
        f'<li><a href="{html.escape(item.url, quote=True)}" target="_blank" rel="noreferrer">{html.escape(item.title)}</a><span>{html.escape(item.source_name)}</span></li>'
        for item in top_items
    )
    warning_html = ""
    if errors:
        warning_html = "<div class=\"warnings\"><strong>运行警告</strong>" + "".join(
            f"<p>{html.escape(error)}</p>" for error in errors[:6]
        ) + "</div>"

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>每日专项热点 - {target_date.isoformat()}</title>
  <style>
    :root {{
      --ink: #161616;
      --muted: #61645f;
      --line: #d8d4c8;
      --paper: #f7f2e8;
      --panel: #fffdf7;
      --accent: #b23a24;
      --accent-2: #1f6f68;
      --gold: #b9862b;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--ink);
      background: var(--paper);
      font-family: Georgia, "Songti SC", "STSong", serif;
      line-height: 1.55;
    }}
    a {{ color: var(--accent-2); text-decoration-thickness: 1px; text-underline-offset: 3px; }}
    .masthead {{
      border-bottom: 1px solid var(--line);
      background: #fbf7ee;
    }}
    .masthead-inner {{
      max-width: 1240px;
      margin: 0 auto;
      padding: 28px 22px 22px;
      display: grid;
      grid-template-columns: minmax(0, 1.5fr) minmax(280px, .8fr);
      gap: 24px;
      align-items: end;
    }}
    .kicker {{
      font: 700 12px/1.2 ui-monospace, SFMono-Regular, Menlo, monospace;
      text-transform: uppercase;
      color: var(--accent);
      margin-bottom: 10px;
    }}
    h1 {{
      margin: 0;
      font-size: clamp(30px, 5vw, 68px);
      line-height: .95;
      letter-spacing: 0;
      max-width: 820px;
    }}
    .date-window {{
      margin-top: 16px;
      color: var(--muted);
      font: 13px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace;
    }}
    .stats {{
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      border: 1px solid var(--line);
      background: var(--panel);
    }}
    .stat {{
      padding: 14px;
      border-right: 1px solid var(--line);
    }}
    .stat:last-child {{ border-right: 0; }}
    .stat b {{ display: block; font-size: 28px; line-height: 1; }}
    .stat span {{ color: var(--muted); font-size: 13px; }}
    .layout {{
      max-width: 1240px;
      margin: 0 auto;
      padding: 22px;
      display: grid;
      grid-template-columns: 280px minmax(0, 1fr);
      gap: 22px;
    }}
    .sidebar {{
      position: sticky;
      top: 14px;
      align-self: start;
      border: 1px solid var(--line);
      background: var(--panel);
      padding: 16px;
    }}
    .sidebar h2 {{
      margin: 0 0 10px;
      font-size: 17px;
    }}
    .search {{
      width: 100%;
      min-height: 38px;
      border: 1px solid var(--line);
      background: #fff;
      color: var(--ink);
      padding: 9px 10px;
      font: 14px/1.2 ui-sans-serif, system-ui, sans-serif;
    }}
    .filters {{
      display: grid;
      gap: 8px;
      margin: 14px 0 18px;
    }}
    .filter-btn {{
      min-height: 36px;
      border: 1px solid var(--ink);
      background: var(--ink);
      color: #fff;
      cursor: pointer;
      text-align: left;
      padding: 8px 10px;
      font: 700 13px/1.2 ui-sans-serif, system-ui, sans-serif;
    }}
    .filter-btn.ghost {{
      border-color: var(--line);
      color: var(--ink);
      background: #fff;
    }}
    .filter-btn.active {{
      outline: 2px solid var(--gold);
      outline-offset: 1px;
    }}
    .top-list {{
      padding-left: 18px;
      margin: 0;
      display: grid;
      gap: 10px;
      font-size: 14px;
    }}
    .top-list span {{
      display: block;
      color: var(--muted);
      font-size: 12px;
      margin-top: 2px;
    }}
    .content {{
      display: grid;
      gap: 18px;
    }}
    .digest-panel, .items-panel {{
      border: 1px solid var(--line);
      background: var(--panel);
      padding: 18px;
    }}
    .digest-panel h1 {{
      font-size: 30px;
      line-height: 1.1;
      margin-bottom: 10px;
    }}
    .digest-panel h2 {{
      border-top: 1px solid var(--line);
      padding-top: 16px;
      margin-top: 22px;
      font-size: 21px;
    }}
    .digest-panel p, .digest-panel li {{
      font-size: 16px;
    }}
    .toolbar {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: center;
      border-bottom: 1px solid var(--line);
      padding-bottom: 12px;
      margin-bottom: 14px;
    }}
    .toolbar h2 {{
      margin: 0;
      font-size: 22px;
    }}
    .count {{
      color: var(--muted);
      font: 13px/1 ui-monospace, SFMono-Regular, Menlo, monospace;
    }}
    .cards {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 12px;
    }}
    .card {{
      border: 1px solid var(--line);
      background: #fff;
      padding: 14px;
      min-height: 245px;
      display: grid;
      gap: 10px;
      align-content: start;
    }}
    .card h3 {{
      margin: 0;
      font-size: 18px;
      line-height: 1.25;
    }}
    .meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      color: var(--muted);
      font: 12px/1.2 ui-monospace, SFMono-Regular, Menlo, monospace;
    }}
    .pill {{
      border: 1px solid var(--line);
      padding: 4px 6px;
      background: #faf6ee;
    }}
    .summary {{
      margin: 0;
      color: #30312e;
      font-size: 14px;
    }}
    details {{
      border-top: 1px solid var(--line);
      padding-top: 8px;
    }}
    summary {{
      cursor: pointer;
      color: var(--accent);
      font: 700 13px/1.2 ui-sans-serif, system-ui, sans-serif;
    }}
    .warnings {{
      border: 1px solid #d79a46;
      background: #fff4dc;
      padding: 12px;
      margin-bottom: 18px;
    }}
    .warnings p {{
      margin: 6px 0 0;
      color: #6b4216;
      font-size: 13px;
    }}
    @media (max-width: 860px) {{
      .masthead-inner, .layout {{
        grid-template-columns: 1fr;
      }}
      .sidebar {{
        position: static;
      }}
      .stats {{
        grid-template-columns: 1fr;
      }}
      .stat {{
        border-right: 0;
        border-bottom: 1px solid var(--line);
      }}
      .stat:last-child {{ border-bottom: 0; }}
    }}
  </style>
</head>
<body>
  <header class="masthead">
    <div class="masthead-inner">
      <div>
        <div class="kicker">TrendRadar Briefing Desk</div>
        <h1>每日专项热点</h1>
        <div class="date-window">{html.escape(start.isoformat())} 至 {html.escape(end.isoformat())}</div>
      </div>
      <div class="stats">
        <div class="stat"><b>{len(items)}</b><span>匹配条目</span></div>
        <div class="stat"><b>{source_count}</b><span>来源</span></div>
        <div class="stat"><b>{social_count}</b><span>社区/聚合信号</span></div>
      </div>
    </div>
  </header>
  <main class="layout">
    <aside class="sidebar">
      <h2>筛选阅读</h2>
      <input class="search" id="search" placeholder="搜索标题、来源、主题">
      <div class="filters">
        <button class="filter-btn active" id="showAll">全部热点</button>
        {topic_buttons}
        {category_buttons}
      </div>
      <h2>先看这几条</h2>
      <ol class="top-list">{top_links}</ol>
    </aside>
    <section class="content">
      {warning_html}
      <article class="digest-panel">
        {markdown_to_body_html(markdown)}
      </article>
      <section class="items-panel">
        <div class="toolbar">
          <h2>证据池</h2>
          <span class="count" id="visibleCount"></span>
        </div>
        <div class="cards" id="cards"></div>
      </section>
    </section>
  </main>
  <script id="items-data" type="application/json">{payload}</script>
  <script>
    const items = JSON.parse(document.getElementById('items-data').textContent);
    const cards = document.getElementById('cards');
    const visibleCount = document.getElementById('visibleCount');
    const search = document.getElementById('search');
    const buttons = Array.from(document.querySelectorAll('.filter-btn'));
    let filter = {{ type: 'all', value: '' }};

    function esc(value) {{
      return String(value || '').replace(/[&<>"']/g, ch => ({{
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
      }}[ch]));
    }}
    function card(item) {{
      const topics = (item.matched_topics || []).map(t => `<span class="pill">${{esc(t)}}</span>`).join('');
      const summary = item.summary || '该来源未提供摘要，建议打开原文确认。';
      return `<article class="card" data-topics="${{esc((item.matched_topics || []).join('|'))}}" data-category="${{esc(item.source_category)}}">`
        + `<div class="meta"><span class="pill">${{esc(item.source_category)}}</span><span class="pill">score ${{esc(item.score)}}</span>${{topics}}</div>`
        + `<h3><a href="${{esc(item.url)}}" target="_blank" rel="noreferrer">${{esc(item.title)}}</a></h3>`
        + `<div class="meta"><span>${{esc(item.source_name)}}</span><span>${{esc(item.published_at || '未知时间')}}</span></div>`
        + `<p class="summary">${{esc(summary.slice(0, 220))}}</p>`
        + `<details><summary>展开原始内容</summary><p class="summary">${{esc(summary)}}</p></details>`
        + `</article>`;
    }}
    function matches(item) {{
      const q = search.value.trim().toLowerCase();
      const haystack = [item.title, item.source_name, item.source_category, (item.matched_topics || []).join(' '), item.summary].join(' ').toLowerCase();
      if (q && !haystack.includes(q)) return false;
      if (filter.type === 'topic') return (item.matched_topics || []).includes(filter.value);
      if (filter.type === 'category') return item.source_category === filter.value;
      return true;
    }}
    function render() {{
      const visible = items.filter(matches);
      cards.innerHTML = visible.map(card).join('');
      visibleCount.textContent = `${{visible.length}} / ${{items.length}} 条`;
    }}
    search.addEventListener('input', render);
    buttons.forEach(button => {{
      button.addEventListener('click', () => {{
        buttons.forEach(btn => btn.classList.remove('active'));
        button.classList.add('active');
        if (button.id === 'showAll') filter = {{ type: 'all', value: '' }};
        else if (button.dataset.topic) filter = {{ type: 'topic', value: button.dataset.topic }};
        else filter = {{ type: 'category', value: button.dataset.category }};
        render();
      }});
    }});
    render();
  </script>
</body>
</html>"""


def load_reportlab():
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.pdfgen import canvas

        return colors, A4, pdfmetrics, TTFont, canvas
    except ImportError:
        bundled = (
            Path.home()
            / ".cache/codex-runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/site-packages"
        )
        if bundled.exists() and str(bundled) not in sys.path:
            sys.path.append(str(bundled))
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.pdfgen import canvas

        return colors, A4, pdfmetrics, TTFont, canvas


def register_pdf_fonts(pdfmetrics, TTFont) -> Tuple[str, str]:
    regular = "Helvetica"
    bold = "Helvetica-Bold"
    candidates = [
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/Supplemental/Songti.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    ]
    for path in candidates:
        if not Path(path).exists():
            continue
        try:
            pdfmetrics.registerFont(TTFont("DigestCJK", path))
            regular = "DigestCJK"
            bold = "DigestCJK"
            break
        except Exception:
            continue
    return regular, bold


def wrap_pdf_text(pdfmetrics, text: str, font_name: str, font_size: int, max_width: float) -> List[str]:
    words = re.split(r"(\s+)", strip_markup(text))
    lines: List[str] = []
    line = ""
    for word in words:
        candidate = f"{line}{word}"
        if pdfmetrics.stringWidth(candidate, font_name, font_size) <= max_width:
            line = candidate
            continue
        if line.strip():
            lines.append(line.strip())
        if pdfmetrics.stringWidth(word, font_name, font_size) <= max_width:
            line = word
        else:
            chunk = ""
            for char in word:
                if pdfmetrics.stringWidth(chunk + char, font_name, font_size) <= max_width:
                    chunk += char
                else:
                    if chunk:
                        lines.append(chunk)
                    chunk = char
            line = chunk
    if line.strip():
        lines.append(line.strip())
    return lines


def draw_wrapped_pdf_text(
    pdf,
    pdfmetrics,
    text: str,
    x: float,
    y: float,
    max_width: float,
    font_name: str,
    font_size: int,
    leading: int,
    max_lines: int = 0,
) -> float:
    lines = wrap_pdf_text(pdfmetrics, text, font_name, font_size, max_width)
    if max_lines:
        lines = lines[:max_lines]
    pdf.setFont(font_name, font_size)
    for line in lines:
        pdf.drawString(x, y, line)
        y -= leading
    return y


def markdown_to_pdf_rows(markdown: str) -> List[Tuple[str, str]]:
    rows: List[Tuple[str, str]] = []
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line or line == "---":
            continue
        kind = "p"
        if line.startswith("# "):
            kind = "h1"
            line = line[2:].strip()
        elif line.startswith("## "):
            kind = "h2"
            line = line[3:].strip()
        elif line.startswith("### "):
            kind = "h3"
            line = line[4:].strip()
        elif line.startswith("- "):
            kind = "li"
            line = "• " + line[2:].strip()
        elif re.match(r"^\d+\.\s+", line):
            kind = "li"

        line = re.sub(r"\*\*([^*]+)\*\*", r"\1", line)
        line = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1（\2）", line)
        if line.startswith("• 原始内容："):
            continue
        if "http://" in line or "https://" in line:
            line = re.sub(r"https?://\S+", "链接见 HTML/Markdown 原文", line)
        if kind == "h3" and not re.search(r"[\u4e00-\u9fff]", line) and "/" not in line:
            number_match = re.match(r"^(\d+)\.", line)
            line = f"{number_match.group(1)}. 热点条目" if number_match else "热点条目"
        rows.append((kind, line))
    return rows


def draw_markdown_pdf_pages(
    pdf,
    pdfmetrics,
    colors,
    markdown: str,
    new_page,
    margin: float,
    width: float,
    font_regular: str,
    font_bold: str,
) -> None:
    y = new_page("中文简报")
    for kind, text in markdown_to_pdf_rows(markdown):
        if kind == "h1":
            font_name, font_size, leading, gap = font_bold, 20, 25, 8
        elif kind == "h2":
            font_name, font_size, leading, gap = font_bold, 15, 20, 7
        elif kind == "h3":
            font_name, font_size, leading, gap = font_bold, 12, 16, 4
        elif kind == "li":
            font_name, font_size, leading, gap = font_regular, 10, 14, 3
        else:
            font_name, font_size, leading, gap = font_regular, 10, 14, 3

        estimated_lines = max(1, len(wrap_pdf_text(pdfmetrics, text, font_name, font_size, width - margin * 2)))
        if y - estimated_lines * leading < margin + 20:
            y = new_page("中文简报")

        pdf.setFillColor(colors.HexColor("#161616"))
        y = draw_wrapped_pdf_text(
            pdf,
            pdfmetrics,
            text,
            margin,
            y,
            width - margin * 2,
            font_name,
            font_size,
            leading,
        )
        y -= gap


def render_pdf_report(
    pdf_path: Path,
    markdown: str,
    items: List[DigestItem],
    config: Dict[str, Any],
    target_date,
    tz: ZoneInfo,
    errors: List[str],
) -> None:
    colors, A4, pdfmetrics, TTFont, canvas = load_reportlab()
    font_regular, font_bold = register_pdf_fonts(pdfmetrics, TTFont)
    width, height = A4
    margin = 42
    pdf = canvas.Canvas(str(pdf_path), pagesize=A4)
    pdf.setTitle(f"每日专项热点 - {target_date.isoformat()}")

    start, end = day_window(target_date, tz)
    categories = sorted({item.source_category for item in items})
    source_count = len({item.source_id for item in items})
    social_count = sum(1 for item in items if item.source_category in {"social", "aggregator"})

    def new_page(title: str = ""):
        pdf.showPage()
        pdf.setFillColor(colors.HexColor("#f7f2e8"))
        pdf.rect(0, 0, width, height, fill=1, stroke=0)
        pdf.setFillColor(colors.HexColor("#161616"))
        if title:
            pdf.setFont(font_bold, 17)
            pdf.drawString(margin, height - margin, title)
            pdf.setStrokeColor(colors.HexColor("#d8d4c8"))
            pdf.line(margin, height - margin - 12, width - margin, height - margin - 12)
            return height - margin - 34
        return height - margin

    pdf.setFillColor(colors.HexColor("#f7f2e8"))
    pdf.rect(0, 0, width, height, fill=1, stroke=0)
    pdf.setFillColor(colors.HexColor("#b23a24"))
    pdf.rect(0, height - 110, width, 110, fill=1, stroke=0)
    pdf.setFillColor(colors.white)
    pdf.setFont(font_bold, 13)
    pdf.drawString(margin, height - 42, "TrendRadar Briefing Desk")
    pdf.setFont(font_bold, 36)
    pdf.drawString(margin, height - 82, "每日专项热点")
    pdf.setFont(font_regular, 11)
    pdf.drawString(margin, height - 102, f"{start.isoformat()} 至 {end.isoformat()}")

    y = height - 152
    stat_width = (width - margin * 2 - 18) / 3
    stats = [("匹配条目", len(items)), ("来源", source_count), ("社区/聚合", social_count)]
    for i, (label, value) in enumerate(stats):
        x = margin + i * (stat_width + 9)
        pdf.setFillColor(colors.HexColor("#fffdf7"))
        pdf.rect(x, y - 54, stat_width, 54, fill=1, stroke=0)
        pdf.setFillColor(colors.HexColor("#161616"))
        pdf.setFont(font_bold, 24)
        pdf.drawString(x + 12, y - 26, str(value))
        pdf.setFont(font_regular, 10)
        pdf.setFillColor(colors.HexColor("#61645f"))
        pdf.drawString(x + 12, y - 43, label)

    y -= 92
    pdf.setFillColor(colors.HexColor("#161616"))
    pdf.setFont(font_bold, 18)
    pdf.drawString(margin, y, "阅读路径")
    y -= 24
    pdf.setFont(font_regular, 11)
    guide = "先看主题色条判断当天重心，再读重点卡片；社区和聚合源只作为信号，不当作最终事实。"
    y = draw_wrapped_pdf_text(pdf, pdfmetrics, guide, margin, y, width - margin * 2, font_regular, 11, 16)
    y -= 18

    topic_counts = {}
    for item in items:
        for topic in item.matched_topics:
            topic_counts[topic] = topic_counts.get(topic, 0) + 1
    max_count = max(topic_counts.values() or [1])
    for topic, count in sorted(topic_counts.items(), key=lambda pair: pair[1], reverse=True):
        pdf.setFillColor(colors.HexColor("#1f6f68"))
        pdf.rect(margin, y - 8, (width - margin * 2) * count / max_count, 10, fill=1, stroke=0)
        pdf.setFillColor(colors.HexColor("#161616"))
        pdf.setFont(font_regular, 10)
        pdf.drawString(margin, y + 6, f"{topic} / {count}")
        y -= 30

    draw_markdown_pdf_pages(pdf, pdfmetrics, colors, markdown, new_page, margin, width, font_regular, font_bold)

    if errors:
        y = new_page("运行警告")
        for error in errors[:8]:
            y = draw_wrapped_pdf_text(pdf, pdfmetrics, f"- {error}", margin, y, width - margin * 2, font_regular, 10, 14)
            y -= 4

    pdf.save()


def render_pdf_report_with_bundled_python(
    pdf_path: Path,
    markdown: str,
    items: List[DigestItem],
    config: Dict[str, Any],
    target_date,
    tz: ZoneInfo,
    errors: List[str],
) -> None:
    runtime = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
    if not runtime.exists():
        raise RuntimeError("reportlab is unavailable and bundled Python runtime was not found")

    start, end = day_window(target_date, tz)
    payload = {
        "pdf_path": str(pdf_path),
        "markdown": markdown,
        "items": item_payload(items),
        "date": target_date.isoformat(),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "errors": errors[:8],
    }
    script = r'''
import json, re
from pathlib import Path
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

payload = json.load(open("__PAYLOAD__", encoding="utf-8"))
pdf_path = payload["pdf_path"]
markdown = payload["markdown"]
items = payload["items"]
width, height = A4
margin = 42

def clean(value):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", str(value or ""))).strip()

def register_font():
    for path in ["/System/Library/Fonts/STHeiti Light.ttc", "/System/Library/Fonts/Supplemental/Songti.ttc"]:
        if Path(path).exists():
            try:
                pdfmetrics.registerFont(TTFont("DigestCJK", path))
                return "DigestCJK", "DigestCJK"
            except Exception:
                pass
    return "Helvetica", "Helvetica-Bold"

font_regular, font_bold = register_font()

def wrap(text, font, size, max_width):
    text = clean(text)
    words = re.split(r"(\s+)", text)
    lines, line = [], ""
    for word in words:
        candidate = line + word
        if pdfmetrics.stringWidth(candidate, font, size) <= max_width:
            line = candidate
            continue
        if line.strip():
            lines.append(line.strip())
        if pdfmetrics.stringWidth(word, font, size) <= max_width:
            line = word
        else:
            chunk = ""
            for char in word:
                if pdfmetrics.stringWidth(chunk + char, font, size) <= max_width:
                    chunk += char
                else:
                    if chunk:
                        lines.append(chunk)
                    chunk = char
            line = chunk
    if line.strip():
        lines.append(line.strip())
    return lines

def draw_text(pdf, text, x, y, max_width, font, size, leading, max_lines=0):
    lines = wrap(text, font, size, max_width)
    if max_lines:
        lines = lines[:max_lines]
    pdf.setFont(font, size)
    for line in lines:
        pdf.drawString(x, y, line)
        y -= leading
    return y

def markdown_rows(markdown):
    rows = []
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line or line == "---":
            continue
        kind = "p"
        if line.startswith("# "):
            kind, line = "h1", line[2:].strip()
        elif line.startswith("## "):
            kind, line = "h2", line[3:].strip()
        elif line.startswith("### "):
            kind, line = "h3", line[4:].strip()
        elif line.startswith("- "):
            kind, line = "li", "• " + line[2:].strip()
        elif re.match(r"^\d+\.\s+", line):
            kind = "li"
        line = re.sub(r"\*\*([^*]+)\*\*", r"\1", line)
        line = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1（\2）", line)
        if line.startswith("• 原始内容："):
            continue
        if "http://" in line or "https://" in line:
            line = re.sub(r"https?://\S+", "链接见 HTML/Markdown 原文", line)
        if kind == "h3" and not re.search(r"[\u4e00-\u9fff]", line) and "/" not in line:
            number_match = re.match(r"^(\d+)\.", line)
            line = f"{number_match.group(1)}. 热点条目" if number_match else "热点条目"
        rows.append((kind, line))
    return rows

def draw_markdown(pdf, markdown, start_y):
    y = start_y
    for kind, text in markdown_rows(markdown):
        if kind == "h1":
            font, size, leading, gap = font_bold, 20, 25, 8
        elif kind == "h2":
            font, size, leading, gap = font_bold, 15, 20, 7
        elif kind == "h3":
            font, size, leading, gap = font_bold, 12, 16, 4
        else:
            font, size, leading, gap = font_regular, 10, 14, 3
        estimated = max(1, len(wrap(text, font, size, width - margin * 2)))
        if y - estimated * leading < margin + 20:
            y = new_page("中文简报")
        pdf.setFillColor(colors.HexColor("#161616"))
        y = draw_text(pdf, text, margin, y, width - margin * 2, font, size, leading)
        y -= gap
    return y

pdf = canvas.Canvas(pdf_path, pagesize=A4)
pdf.setTitle("每日专项热点 - " + payload["date"])
pdf.setFillColor(colors.HexColor("#f7f2e8"))
pdf.rect(0, 0, width, height, fill=1, stroke=0)
pdf.setFillColor(colors.HexColor("#b23a24"))
pdf.rect(0, height - 112, width, 112, fill=1, stroke=0)
pdf.setFillColor(colors.white)
pdf.setFont(font_bold, 13)
pdf.drawString(margin, height - 42, "TrendRadar Briefing Desk")
pdf.setFont(font_bold, 34)
pdf.drawString(margin, height - 82, "每日专项热点")
pdf.setFont(font_regular, 10)
pdf.drawString(margin, height - 102, payload["start"] + " 至 " + payload["end"])

source_count = len({item["source_id"] for item in items})
social_count = sum(1 for item in items if item["source_category"] in ("social", "aggregator"))
stats = [("匹配条目", len(items)), ("来源", source_count), ("社区/聚合", social_count)]
y = height - 154
stat_width = (width - margin * 2 - 18) / 3
for idx, (label, value) in enumerate(stats):
    x = margin + idx * (stat_width + 9)
    pdf.setFillColor(colors.HexColor("#fffdf7"))
    pdf.rect(x, y - 54, stat_width, 54, fill=1, stroke=0)
    pdf.setFillColor(colors.HexColor("#161616"))
    pdf.setFont(font_bold, 24)
    pdf.drawString(x + 12, y - 26, str(value))
    pdf.setFont(font_regular, 10)
    pdf.setFillColor(colors.HexColor("#61645f"))
    pdf.drawString(x + 12, y - 43, label)

y -= 92
pdf.setFillColor(colors.HexColor("#161616"))
pdf.setFont(font_bold, 18)
pdf.drawString(margin, y, "主题热度")
y -= 28
topic_counts = {}
for item in items:
    for topic in item.get("matched_topics", []):
        topic_counts[topic] = topic_counts.get(topic, 0) + 1
max_count = max(topic_counts.values() or [1])
for topic, count in sorted(topic_counts.items(), key=lambda pair: pair[1], reverse=True):
    pdf.setFillColor(colors.HexColor("#1f6f68"))
    pdf.rect(margin, y - 8, (width - margin * 2) * count / max_count, 10, fill=1, stroke=0)
    pdf.setFillColor(colors.HexColor("#161616"))
    pdf.setFont(font_regular, 10)
    pdf.drawString(margin, y + 6, f"{topic} / {count}")
    y -= 30

def new_page(title):
    pdf.showPage()
    pdf.setFillColor(colors.HexColor("#f7f2e8"))
    pdf.rect(0, 0, width, height, fill=1, stroke=0)
    pdf.setFillColor(colors.HexColor("#161616"))
    pdf.setFont(font_bold, 17)
    pdf.drawString(margin, height - margin, title)
    pdf.setStrokeColor(colors.HexColor("#d8d4c8"))
    pdf.line(margin, height - margin - 12, width - margin, height - margin - 12)
    return height - margin - 36

draw_markdown(pdf, markdown, new_page("中文简报"))

if payload["errors"]:
    y = new_page("运行警告")
    for error in payload["errors"]:
        y = draw_text(pdf, "- " + error, margin, y, width - margin * 2, font_regular, 10, 14)
        y -= 4
pdf.save()
'''.strip()

    with tempfile.TemporaryDirectory() as tmp_dir:
        payload_path = Path(tmp_dir) / "payload.json"
        script_path = Path(tmp_dir) / "make_pdf.py"
        payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        script_path.write_text(script.replace("__PAYLOAD__", str(payload_path)), encoding="utf-8")
        subprocess.run([str(runtime), str(script_path)], check=True, cwd=str(Path.cwd()))


def save_outputs(
    markdown: str,
    items: List[DigestItem],
    target_date,
    config: Dict[str, Any],
    tz: ZoneInfo,
    errors: List[str],
) -> Tuple[Path, Path, Path, Path]:
    output_dir = Path("output/daily_digest") / target_date.isoformat()
    output_dir.mkdir(parents=True, exist_ok=True)
    md_path = output_dir / "digest.md"
    html_path = output_dir / "digest.html"
    json_path = output_dir / "items.json"
    pdf_path = output_dir / "digest.pdf"
    md_path.write_text(markdown, encoding="utf-8")
    html_path.write_text(render_interactive_html(markdown, items, config, target_date, tz, errors), encoding="utf-8")
    json_path.write_text(json.dumps(item_payload(items), ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        render_pdf_report(pdf_path, markdown, items, config, target_date, tz, errors)
    except Exception as exc:
        try:
            render_pdf_report_with_bundled_python(pdf_path, markdown, items, config, target_date, tz, errors)
        except Exception as fallback_exc:
            print(f"[daily_digest] warning: PDF generation failed: {exc}; fallback failed: {fallback_exc}")
    return md_path, html_path, json_path, pdf_path


def split_text_by_bytes(text: str, max_bytes: int) -> List[str]:
    chunks = []
    current = ""
    for line in text.splitlines():
        candidate = f"{current}\n{line}" if current else line
        if len(candidate.encode("utf-8")) <= max_bytes:
            current = candidate
            continue
        if current:
            chunks.append(current)
        current = line
    if current:
        chunks.append(current)
    return chunks or [text[:max_bytes]]


def post_ntfy(channel_config: Dict[str, Any], title: str, content: str) -> bool:
    server_url = channel_config.get("server_url", "https://ntfy.sh").rstrip("/")
    topic = channel_config.get("topic", "")
    token = channel_config.get("token", "")
    if not topic:
        return False
    headers = {
        "Title": "Daily Digest",
        "Markdown": "yes",
        "Tags": "news",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    ok = True
    chunks = split_text_by_bytes(content, 3800)
    for idx, chunk in enumerate(reversed(chunks), 1):
        label = len(chunks) - idx + 1
        current_headers = dict(headers)
        if len(chunks) > 1:
            current_headers["Title"] = f"Daily Digest ({label}/{len(chunks)})"
        response = requests.post(
            f"{server_url}/{topic}",
            headers=current_headers,
            data=chunk.encode("utf-8"),
            timeout=30,
        )
        ok = ok and response.status_code == 200
        if idx < len(chunks):
            time.sleep(1)
    return ok


def post_telegram(channel_config: Dict[str, Any], content: str) -> bool:
    token = channel_config.get("bot_token", "")
    chat_id = channel_config.get("chat_id", "")
    if not token or not chat_id:
        return False
    ok = True
    for chunk in split_text_by_bytes(content, 3800):
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": chunk, "disable_web_page_preview": True},
            timeout=30,
        )
        ok = ok and response.status_code == 200 and response.json().get("ok", False)
        time.sleep(1)
    return ok


def post_generic_webhook(channel_config: Dict[str, Any], title: str, content: str) -> bool:
    webhook_url = channel_config.get("webhook_url", "")
    if not webhook_url:
        return False
    template = channel_config.get("payload_template", "")
    if template:
        payload = json.loads(template.replace("{title}", title).replace("{content}", content))
    else:
        payload = {"title": title, "content": content}
    response = requests.post(webhook_url, json=payload, timeout=30)
    return 200 <= response.status_code < 300


def notification_channels(main_config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    notification = main_config.get("notification", {})
    return notification.get("channels", {}) or {}


def send_notifications(config: Dict[str, Any], main_config: Dict[str, Any], markdown: str, target_date) -> Dict[str, bool]:
    wanted = config.get("notification", {}).get("channels", [])
    channels = notification_channels(main_config)
    title = f"每日专项热点 {target_date.isoformat()}"
    results: Dict[str, bool] = {}
    for channel in wanted:
        try:
            if channel == "ntfy":
                results[channel] = post_ntfy(channels.get("ntfy", {}), title, markdown)
            elif channel == "telegram":
                results[channel] = post_telegram(channels.get("telegram", {}), markdown)
            elif channel == "generic_webhook":
                results[channel] = post_generic_webhook(channels.get("generic_webhook", {}), title, markdown)
            else:
                results[channel] = False
        except Exception as exc:
            print(f"[notification] {channel} failed: {exc}")
            results[channel] = False
    return results


def build_digest(config: Dict[str, Any], target_date, tz: ZoneInfo, no_ai: bool) -> Tuple[str, List[DigestItem], List[str]]:
    items, errors = collect_items(config, target_date, tz)
    if not items:
        return extractive_digest(config, items, target_date, tz, errors), items, errors

    if no_ai:
        return extractive_digest(config, items, target_date, tz, errors), items, errors

    try:
        digest = generate_ai_digest(config, items, target_date, tz)
        if digest:
            return digest.strip() + "\n", items, errors
    except Exception as exc:
        errors.append(f"AI generation failed: {exc}")
        if not config.get("ai", {}).get("fallback_to_extractive", True):
            raise

    return extractive_digest(config, items, target_date, tz, errors), items, errors


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a focused daily digest from western RSS/social sources.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to daily digest config.")
    parser.add_argument("--main-config", default=str(DEFAULT_MAIN_CONFIG_PATH), help="Path to TrendRadar config.yaml for notification secrets.")
    parser.add_argument("--date", help="Target date in YYYY-MM-DD. Defaults to today in configured timezone.")
    parser.add_argument("--no-ai", action="store_true", help="Skip model generation and create an extractive digest.")
    parser.add_argument("--send", action="store_true", help="Force notification sending.")
    parser.add_argument("--no-send", action="store_true", help="Disable notification sending.")
    parser.add_argument("--print", action="store_true", help="Print digest markdown to stdout.")
    args = parser.parse_args(argv)

    config = load_yaml(Path(args.config))
    tz = ZoneInfo(config.get("timezone", "America/Los_Angeles"))
    target_date = parse_target_date(args.date, tz)
    markdown, items, errors = build_digest(config, target_date, tz, args.no_ai)
    md_path, html_path, json_path, pdf_path = save_outputs(markdown, items, target_date, config, tz, errors)

    print(f"[daily_digest] matched items: {len(items)}")
    print(f"[daily_digest] markdown: {md_path}")
    print(f"[daily_digest] html: {html_path}")
    print(f"[daily_digest] json: {json_path}")
    if pdf_path.exists() and pdf_path.stat().st_size > 0:
        print(f"[daily_digest] pdf: {pdf_path}")
    if errors:
        print(f"[daily_digest] warnings: {len(errors)}")
        for error in errors[:5]:
            print(f"[daily_digest] warning: {error}")

    should_send = bool(config.get("notification", {}).get("enabled", False))
    if args.send:
        should_send = True
    if args.no_send:
        should_send = False

    if should_send:
        main_config = load_yaml(Path(args.main_config))
        results = send_notifications(config, main_config, markdown, target_date)
        print(f"[daily_digest] notification results: {results}")

    if args.print:
        print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
