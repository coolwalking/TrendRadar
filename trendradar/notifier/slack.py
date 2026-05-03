# coding=utf-8

import re
from typing import Dict

import requests

from trendradar.notifier.base import BaseNotifier
from trendradar.logging_config import get_logger


logger = get_logger(__name__)
def _convert_markdown_to_mrkdwn(content: str) -> str:
    """将标准 Markdown 转换为 Slack 的 mrkdwn 格式"""
    content = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"<\2|\1>", content)
    content = re.sub(r"\*\*([^*]+)\*\*", r"*\1*", content)
    return content


class SlackNotifier(BaseNotifier):
    """Slack 通知器"""

    @property
    def name(self) -> str:
        return "Slack"

    @property
    def batch_size_config_key(self) -> str:
        return "SLACK_BATCH_SIZE"

    @property
    def default_batch_size(self) -> int:
        return 4000

    def build_payload(self, batch_content: str, report_data: Dict, report_type: str) -> Dict:
        mrkdwn_content = _convert_markdown_to_mrkdwn(batch_content)
        return {"text": mrkdwn_content}

    def is_success(self, response: requests.Response) -> bool:
        return response.status_code == 200 and response.text == "ok"

    def get_error_message(self, response: requests.Response) -> str:
        return response.text if response.text else f"状态码：{response.status_code}"