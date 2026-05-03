# coding=utf-8

from typing import Dict

import requests

from trendradar.notifier.base import BaseNotifier
from trendradar.logging_config import get_logger


logger = get_logger(__name__)
class DingTalkNotifier(BaseNotifier):
    """钉钉通知器"""

    @property
    def name(self) -> str:
        return "钉钉"

    @property
    def batch_size_config_key(self) -> str:
        return "DINGTALK_BATCH_SIZE"

    @property
    def default_batch_size(self) -> int:
        return 20000

    def build_payload(self, batch_content: str, report_data: Dict, report_type: str) -> Dict:
        return {
            "msgtype": "markdown",
            "markdown": {
                "title": f"TrendRadar 热点分析报告 - {report_type}",
                "text": batch_content,
            },
        }

    def is_success(self, response: requests.Response) -> bool:
        return response.status_code == 200 and response.json().get("errcode") == 0

    def get_error_message(self, response: requests.Response) -> str:
        if response.status_code != 200:
            return f"状态码：{response.status_code}"
        return response.json().get("errmsg", "未知错误")