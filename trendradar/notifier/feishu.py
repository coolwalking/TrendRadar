# coding=utf-8

from typing import Dict

import requests

from trendradar import utils
from trendradar.notifier.base import BaseNotifier
from trendradar.logging_config import get_logger


logger = get_logger(__name__)
class FeishuNotifier(BaseNotifier):
    """飞书通知器"""

    @property
    def name(self) -> str:
        return "飞书"

    @property
    def batch_size_config_key(self) -> str:
        return "FEISHU_BATCH_SIZE"

    @property
    def default_batch_size(self) -> int:
        return 29000

    def build_payload(self, batch_content: str, report_data: Dict, report_type: str) -> Dict:
        total_titles = sum(
            len(stat["titles"]) for stat in report_data["stats"] if stat["count"] > 0
        )
        now = utils.get_beijing_time()
        return {
            "msg_type": "text",
            "content": {
                "total_titles": total_titles,
                "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
                "report_type": report_type,
                "text": batch_content,
            },
        }

    def is_success(self, response: requests.Response) -> bool:
        if response.status_code != 200:
            return False
        result = response.json()
        return result.get("StatusCode") == 0 or result.get("code") == 0

    def get_error_message(self, response: requests.Response) -> str:
        if response.status_code != 200:
            return f"状态码：{response.status_code}"
        result = response.json()
        return result.get("msg") or result.get("StatusMessage", "未知错误")