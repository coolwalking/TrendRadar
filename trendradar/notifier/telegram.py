# coding=utf-8

import time
from typing import Dict, Optional

import requests

from trendradar.notifier.base import BaseNotifier
from trendradar.notifier.batch import (
    add_batch_headers,
    split_content_into_batches,
    _get_max_batch_header_size,
)
from trendradar.logging_config import get_logger


logger = get_logger(__name__)

class TelegramNotifier(BaseNotifier):
    """Telegram 通知器"""

    def __init__(self, config: Dict, bot_token: str, chat_id: str):
        super().__init__(config)
        self.bot_token = bot_token
        self.chat_id = chat_id

    @property
    def name(self) -> str:
        return "Telegram"

    @property
    def batch_size_config_key(self) -> str:
        return "MESSAGE_BATCH_SIZE"

    @property
    def default_batch_size(self) -> int:
        return 4000

    def build_payload(self, batch_content: str, report_data: Dict, report_type: str) -> Dict:
        return {
            "chat_id": self.chat_id,
            "text": batch_content,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

    def is_success(self, response: requests.Response) -> bool:
        return response.status_code == 200 and response.json().get("ok")

    def get_error_message(self, response: requests.Response) -> str:
        if response.status_code != 200:
            return f"状态码：{response.status_code}"
        return response.json().get("description", "未知错误")

    def send(
        self,
        _webhook_url: str,
        report_data: Dict,
        report_type: str,
        update_info: Optional[Dict] = None,
        proxy_url: Optional[str] = None,
        mode: str = "daily",
        account_label: str = "",
    ) -> bool:
        """发送通知（支持分批）"""
        log_prefix = f"{self.name}{account_label}" if account_label else self.name
        proxies = self.get_proxies(proxy_url)
        batch_size = self.get_batch_size()
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"

        batches = split_content_into_batches(
            report_data,
            self.format_type,
            update_info,
            max_bytes=batch_size,
            mode=mode,
            config=self.config,
        )
        batches = add_batch_headers(batches, self.format_type, batch_size)

        logger.info(f"{log_prefix}消息分为 {len(batches)} 批次发送 [{report_type}]")

        for i, batch_content in enumerate(batches, 1):
            batch_bytes = len(batch_content.encode("utf-8"))
            logger.info(f"发送{log_prefix}第 {i}/{len(batches)} 批次（{batch_bytes} 字节）")

            payload = self.build_payload(batch_content, report_data, report_type)

            try:
                response = requests.post(
                    url,
                    headers=self.get_headers(),
                    json=payload,
                    proxies=proxies,
                    timeout=30,
                )
                if self.is_success(response):
                    logger.info(f"{log_prefix}第 {i}/{len(batches)} 批次发送成功 [{report_type}]")
                    if i < len(batches):
                        time.sleep(self.config.get("BATCH_SEND_INTERVAL", 3))
                else:
                    error_msg = self.get_error_message(response)
                    logger.error(f"{log_prefix}第 {i}/{len(batches)} 批次发送失败：{error_msg}")
                    return False
            except Exception as e:
                logger.exception(f"{log_prefix}第 {i}/{len(batches)} 批次发送出错 [{report_type}]：{e}")
                return False

        logger.info(f"{log_prefix}所有 {len(batches)} 批次发送完成 [{report_type}]")
        return True