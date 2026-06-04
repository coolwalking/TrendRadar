# coding=utf-8
"""
测试引导模块

TrendRadar 主包的 __init__ 会导入 litellm 等重依赖（pyproject 要求 Python 3.12）。
本引导在不触发这些 __init__ 的前提下，按文件路径单独加载"信息环境异常监测"涉及的
纯逻辑模块，并仅 stub 掉 litellm 客户端（trendradar.ai.client）。

这样测试可以在：
  - 用户当前的精简解释器（python3，无第三方依赖）下运行；
  - 也可以在装好依赖的 3.12 环境（pytest）下运行，结果一致且不联网。

加载的真实模块：
  trendradar.core.source_tiers   （纯标准库）
  trendradar.ai.evidence         （纯标准库）
  trendradar.ai.prompt_loader    （纯标准库，真实读取 config/ 下的 prompt 文件）
  trendradar.ai.analyzer         （仅 stub 掉 client）
  trendradar.ai.formatter        （依赖 analyzer + evidence）
"""

import importlib.util
import os
import sys
import types
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CACHE = None


def _ensure_pkg(name):
    if name not in sys.modules:
        mod = types.ModuleType(name)
        mod.__path__ = []  # 标记为包，但不指向真实 __init__
        sys.modules[name] = mod
    return sys.modules[name]


def _load_file(name, relpath):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, relpath))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _install_client_stub():
    """stub trendradar.ai.client，避免导入 litellm。"""
    client_mod = types.ModuleType("trendradar.ai.client")

    class AIClient:
        def __init__(self, config=None):
            config = config or {}
            self.api_key = config.get("API_KEY", "") or "test-key"
            self.config = config

        def validate_config(self):
            return True, ""

        def chat(self, messages):  # 测试中通常通过 monkeypatch _call_ai 绕过
            return ""

    client_mod.AIClient = AIClient
    sys.modules["trendradar.ai.client"] = client_mod


def load_all():
    """加载并返回所有受测模块（带缓存，保证类对象单例）。"""
    global _CACHE
    if _CACHE is not None:
        return _CACHE

    _ensure_pkg("trendradar")
    _ensure_pkg("trendradar.ai")
    _ensure_pkg("trendradar.core")
    _ensure_pkg("trendradar.telegram_bot")

    _install_client_stub()
    # 真实 prompt_loader（纯标准库，会实际读取 config/ 下的 prompt 文件）
    _load_file("trendradar.ai.prompt_loader", "trendradar/ai/prompt_loader.py")

    source_tiers = _load_file("trendradar.core.source_tiers", "trendradar/core/source_tiers.py")
    access = _load_file("trendradar.telegram_bot.access", "trendradar/telegram_bot/access.py")
    evidence = _load_file("trendradar.ai.evidence", "trendradar/ai/evidence.py")
    analyzer = _load_file("trendradar.ai.analyzer", "trendradar/ai/analyzer.py")
    formatter = _load_file("trendradar.ai.formatter", "trendradar/ai/formatter.py")

    _CACHE = SimpleNamespace(
        ROOT=ROOT,
        source_tiers=source_tiers,
        access=access,
        evidence=evidence,
        analyzer=analyzer,
        formatter=formatter,
    )
    return _CACHE


def make_title(title, source_name, rank, count=1, **extra):
    """构造一条匹配标题（mimic count_word_frequency 的 titles 结构）。"""
    d = {
        "title": title,
        "source_name": source_name,
        "ranks": [rank] if isinstance(rank, int) else list(rank),
        "count": count,
        "first_time": "09:30",
        "last_time": "12:00",
    }
    d.update(extra)
    return d


def make_resolver(bootstrap=None):
    """构造一个与默认 config 对齐的 SourceTierResolver。"""
    b = bootstrap or load_all()
    platforms = [
        {"id": "weibo", "name": "微博"},
        {"id": "douyin", "name": "抖音"},
        {"id": "bilibili-hot-search", "name": "bilibili 热搜"},
        {"id": "tieba", "name": "贴吧"},
        {"id": "zhihu", "name": "知乎"},
        {"id": "thepaper", "name": "澎湃新闻"},
        {"id": "wallstreetcn-hot", "name": "华尔街见闻"},
        {"id": "toutiao", "name": "今日头条"},
    ]
    rss = [
        {"id": "openai-news", "name": "OpenAI News"},
        {"id": "hacker-news", "name": "Hacker News"},
        {"id": "bbc-world", "name": "BBC World"},
    ]
    tiers = {
        "tiers": {"A": {"name": "一手/官方"}, "D": {"name": "社交"}},
        "platforms": {
            "weibo": {"tier": "D", "role": "social_hotlist"},
            "douyin": {"tier": "D"},
            "bilibili-hot-search": {"tier": "D"},
            "tieba": {"tier": "D"},
            "zhihu": {"tier": "C"},
            "thepaper": {"tier": "C"},
            "wallstreetcn-hot": {"tier": "C"},
            "toutiao": {"tier": "C"},
        },
        "rss_feeds": {
            "openai-news": {"tier": "A"},
            "hacker-news": {"tier": "B"},
            "bbc-world": {"tier": "B"},
        },
    }
    return b.source_tiers.SourceTierResolver(tiers, platforms, rss)
