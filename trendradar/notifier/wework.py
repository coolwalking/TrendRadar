# coding=utf-8

import re
from typing import Dict

import requests

from trendradar.notifier.base import BaseNotifier
from trendradar.logging_config import get_logger


logger = get_logger(__name__)
def _strip_markdown(text: str) -> str:
    """去除文本中的 markdown 语法格式，用于个人微信推送"""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"_(.+?)_", r"\1", text)
    text = re.sub(r"~~(.+?)~~", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 \2", text)
    text = re.sub(r"!\[(.+?)\]\(.+?\)", r"\1", text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    text = re.sub(r"^>\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^#+\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^[\-\*]{3,}\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"<font[^>]*>(.+?)</font>", r"\1", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


class WeComNotifier(BaseNotifier):
    """企业微信通知器"""

    def __init__(self, config: Dict):
        super().__init__(config)
        self.msg_type = config.get("WEWORK_MSG_TYPE", "markdown").lower()
        self.is_text_mode = self.msg_type == "text"

    @property
    def name(self) -> str:
        return "企业微信"

    @property
    def batch_size_config_key(self) -> str:
        return "MESSAGE_BATCH_SIZE"

    @property
    def default_batch_size(self) -> int:
        return 4000

    @property
    def format_type(self) -> str:
        return "wework_text" if self.is_text_mode else "wework"

    def build_payload(self, batch_content: str, report_data: Dict, report_type: str) -> Dict:
        if self.is_text_mode:
            return {"msgtype": "text", "text": {"content": _strip_markdown(batch_content)}}
        return {"msgtype": "markdown", "markdown": {"content": batch_content}}

    def is_success(self, response: requests.Response) -> bool:
        return response.status_code == 200 and response.json().get("errcode") == 0

    def get_error_message(self, response: requests.Response) -> str:
        if response.status_code != 200:
            return f"状态码：{response.status_code}"
        return response.json().get("errmsg", "未知错误")