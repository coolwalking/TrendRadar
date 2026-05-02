# coding=utf-8

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

import pytz
import requests


# === 工具函数 ===
def get_beijing_time():
    """获取北京时间"""
    return datetime.now(pytz.timezone("Asia/Shanghai"))


def format_date_folder():
    """格式化日期文件夹"""
    return get_beijing_time().strftime("%Y年%m月%d日")


def format_time_filename():
    """格式化时间文件名"""
    return get_beijing_time().strftime("%H时%M分")


def clean_title(title: str) -> str:
    """清理标题中的特殊字符"""
    if not isinstance(title, str):
        title = str(title)
    cleaned_title = title.replace("\n", " ").replace("\r", " ")
    cleaned_title = re.sub(r"\s+", " ", cleaned_title)
    cleaned_title = cleaned_title.strip()
    return cleaned_title


def ensure_directory_exists(directory: str):
    """确保目录存在"""
    Path(directory).mkdir(parents=True, exist_ok=True)


def get_output_path(subfolder: str, filename: str) -> str:
    """获取输出路径"""
    date_folder = format_date_folder()
    output_dir = Path("output") / date_folder / subfolder
    ensure_directory_exists(str(output_dir))
    return str(output_dir / filename)


def check_version_update(
    current_version: str, version_url: str, proxy_url: Optional[str] = None
) -> Tuple[bool, Optional[str]]:
    """检查版本更新"""
    try:
        proxies = None
        if proxy_url:
            proxies = {"http": proxy_url, "https": proxy_url}

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/plain, */*",
            "Cache-Control": "no-cache",
        }

        response = requests.get(
            version_url, proxies=proxies, headers=headers, timeout=10
        )
        response.raise_for_status()

        remote_version = response.text.strip()
        print(f"当前版本: {current_version}, 远程版本: {remote_version}")

        # 比较版本
        def parse_version(version_str):
            try:
                parts = version_str.strip().split(".")
                if len(parts) != 3:
                    raise ValueError("版本号格式不正确")
                return int(parts[0]), int(parts[1]), int(parts[2])
            except:
                return 0, 0, 0

        current_tuple = parse_version(current_version)
        remote_tuple = parse_version(remote_version)

        need_update = current_tuple < remote_tuple
        return need_update, remote_version if need_update else None

    except Exception as e:
        print(f"版本检查失败: {e}")
        return False, None


def is_first_crawl_today() -> bool:
    """检测是否是当天第一次爬取"""
    date_folder = format_date_folder()
    txt_dir = Path("output") / date_folder / "txt"

    if not txt_dir.exists():
        return True

    files = sorted([f for f in txt_dir.iterdir() if f.suffix == ".txt"])
    return len(files) <= 1


def html_escape(text: str) -> str:
    """HTML转义"""
    if not isinstance(text, str):
        text = str(text)

    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )
def matches_word_groups(
    title: str, word_groups: List[Dict], filter_words: List[str], global_filters: Optional[List[str]] = None
) -> bool:
    """检查标题是否匹配词组规则"""
    # 防御性类型检查：确保 title 是有效字符串
    if not isinstance(title, str):
        title = str(title) if title is not None else ""
    if not title.strip():
        return False

    title_lower = title.lower()

    # 全局过滤检查（优先级最高）
    if global_filters:
        if any(global_word.lower() in title_lower for global_word in global_filters):
            return False

    # 如果没有配置词组，则匹配所有标题（支持显示全部新闻）
    if not word_groups:
        return True

    # 过滤词检查
    if any(filter_word.lower() in title_lower for filter_word in filter_words):
        return False

    # 词组匹配检查
    for group in word_groups:
        required_words = group["required"]
        normal_words = group["normal"]

        # 必须词检查
        if required_words:
            all_required_present = all(
                req_word.lower() in title_lower for req_word in required_words
            )
            if not all_required_present:
                continue

        # 普通词检查
        if normal_words:
            any_normal_present = any(
                normal_word.lower() in title_lower for normal_word in normal_words
            )
            if not any_normal_present:
                continue

        return True

    return False


def load_frequency_words(
    frequency_file: Optional[str] = None,
) -> Tuple[List[Dict], List[str], List[str]]:
    """
    加载频率词配置

    Returns:
        (词组列表, 词组内过滤词, 全局过滤词)
    """
    if frequency_file is None:
        frequency_file = os.environ.get(
            "FREQUENCY_WORDS_PATH", "config/frequency_words.txt"
        )

    frequency_path = Path(frequency_file)
    if not frequency_path.exists():
        raise FileNotFoundError(f"频率词文件 {frequency_file} 不存在")

    with open(frequency_path, "r", encoding="utf-8") as f:
        content = f.read()

    word_groups = [group.strip() for group in content.split("\n\n") if group.strip()]

    processed_groups = []
    filter_words = []
    global_filters = []  # 新增：全局过滤词列表

    # 默认区域（向后兼容）
    current_section = "WORD_GROUPS"

    for group in word_groups:
        lines = [line.strip() for line in group.split("\n") if line.strip()]

        if not lines:
            continue

        # 检查是否为区域标记
        if lines[0].startswith("[") and lines[0].endswith("]"):
            section_name = lines[0][1:-1].upper()
            if section_name in ("GLOBAL_FILTER", "WORD_GROUPS"):
                current_section = section_name
                lines = lines[1:]  # 移除标记行

        # 处理全局过滤区域
        if current_section == "GLOBAL_FILTER":
            # 直接添加所有非空行到全局过滤列表
            for line in lines:
                # 忽略特殊语法前缀，只提取纯文本
                if line.startswith(("!", "+", "@")):
                    continue  # 全局过滤区不支持特殊语法
                if line:
                    global_filters.append(line)
            continue

        # 处理词组区域（保持现有逻辑）
        words = lines

        group_required_words = []
        group_normal_words = []
        group_filter_words = []
        group_max_count = 0  # 默认不限制

        for word in words:
            if word.startswith("@"):
                # 解析最大显示数量（只接受正整数）
                try:
                    count = int(word[1:])
                    if count > 0:
                        group_max_count = count
                except (ValueError, IndexError):
                    pass  # 忽略无效的@数字格式
            elif word.startswith("!"):
                filter_words.append(word[1:])
                group_filter_words.append(word[1:])
            elif word.startswith("+"):
                group_required_words.append(word[1:])
            else:
                group_normal_words.append(word)

        if group_required_words or group_normal_words:
            if group_normal_words:
                group_key = " ".join(group_normal_words)
            else:
                group_key = " ".join(group_required_words)

            processed_groups.append(
                {
                    "required": group_required_words,
                    "normal": group_normal_words,
                    "group_key": group_key,
                    "max_count": group_max_count,  # 新增字段
                }
            )

    return processed_groups, filter_words, global_filters


