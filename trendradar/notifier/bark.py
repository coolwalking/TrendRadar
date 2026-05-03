# coding=utf-8

import time
from typing import Dict, Optional
from urllib.parse import urlparse

import requests

from trendradar.notifier.base import BaseNotifier
from trendradar.notifier.batch import (
    add_batch_headers,
    split_content_into_batches,
    _get_max_batch_header_size,
)
from trendradar.logging_config import get_logger


logger = get_logger(__name__)

class BarkNotifier(BaseNotifier):
    """Bark 通知器"""

    @property
    def name(self) -> str:
        return "Bark"

    @property
    def batch_size_config_key(self) -> str:
        return "BARK_BATCH_SIZE"

    @property
    def default_batch_size(self) -> int:
        return 3600

    def build_payload(self, batch_content: str, report_data: Dict, report_type: str) -> Dict:
        """Bark payload 在 send 中动态构建（需要 device_key）。"""
        raise NotImplementedError("Bark payload is built in send()")

    def is_success(self, response: requests.Response) -> bool:
        if response.status_code != 200:
            return False
        try:
            return response.json().get("code") == 200
        except Exception:
            logger.exception("Bark响应解析失败")
            return False

    def get_error_message(self, response: requests.Response) -> str:
        if response.status_code != 200:
            return f"状态码：{response.status_code}"
        try:
            return response.json().get("message", "未知错误")
        except Exception:
            logger.exception("Bark错误消息解析失败")
            return "未知错误"

    def send(
        self,
        webhook_url: str,
        report_data: Dict,
        report_type: str,
        update_info: Optional[Dict] = None,
        proxy_url: Optional[str] = None,
        mode: str = "daily",
        account_label: str = "",
    ) -> bool:
        """发送通知（支持分批，反向顺序推送）"""
        log_prefix = f"{self.name}{account_label}" if account_label else self.name
        proxies = self.get_proxies(proxy_url)

        parsed_url = urlparse(webhook_url)
        device_key = parsed_url.path.strip("/").split("/")[0] if parsed_url.path else None
        if not device_key:
            logger.error(f"{log_prefix} URL 格式错误，无法提取 device_key: {webhook_url}")
            return False

        api_endpoint = f"{parsed_url.scheme}://{parsed_url.netloc}/push"

        batch_size = self.get_batch_size()
        batches = split_content_into_batches(
            report_data,
            self.format_type,
            update_info,
            max_bytes=batch_size,
            mode=mode,
            config=self.config,
        )
        batches = add_batch_headers(batches, self.format_type, batch_size)

        total_batches = len(batches)
        logger.info(f"{log_prefix}消息分为 {total_batches} 批次发送 [{report_type}]")

        reversed_batches = list(reversed(batches))
        logger.info(f"{log_prefix}将按反向顺序推送（最后批次先推送），确保客户端显示顺序正确")

        success_count = 0
        for idx, batch_content in enumerate(reversed_batches, 1):
            actual_batch_num = total_batches - idx + 1
            batch_bytes = len(batch_content.encode("utf-8"))
            logger.info(f"发送{log_prefix}第 {actual_batch_num}/{total_batches} 批次")

            if batch_bytes > 4096:
                logger.warning(f"警告：{log_prefix}第 {actual_batch_num}/{total_batches} ")

            payload = {
                "title": report_type,
                "markdown": batch_content,
                "device_key": device_key,
                "sound": "default",
                "group": "TrendRadar",
                "action": "none",
            }

            try:
                response = requests.post(
                    api_endpoint,
                    json=payload,
                    proxies=proxies,
                    timeout=30,
                )
                if self.is_success(response):
                    logger.info(f"{log_prefix}第 {actual_batch_num}/{total_batches} 批次发送成功 [{report_type}]")
                    success_count += 1
                    if idx < total_batches:
                        time.sleep(self.config.get("BATCH_SEND_INTERVAL", 3))
                else:
                    logger.error(f"{log_prefix}第 {actual_batch_num}/{total_batches} 批次发送失败 [{report_type}]，")
                    try:
                        logger.error(f"错误详情：{response.text}")
                    except Exception:
                        logger.exception("获取响应内容失败")

            except requests.exceptions.ConnectTimeout:
                logger.info(f"{log_prefix}第 {actual_batch_num}/{total_batches} 批次连接超时 [{report_type}]")
            except requests.exceptions.ReadTimeout:
                logger.info(f"{log_prefix}第 {actual_batch_num}/{total_batches} 批次读取超时 [{report_type}]")
            except requests.exceptions.ConnectionError as e:
                logger.error(f"{log_prefix}第 {actual_batch_num}/{total_batches} 批次连接错误 [{report_type}]：{e}")
            except Exception as e:
                logger.exception(f"{log_prefix}第 {actual_batch_num}/{total_batches} 批次发送异常 [{report_type}]：{e}")

        if success_count == total_batches:
            logger.info(f"{log_prefix}所有 {total_batches} 批次发送完成 [{report_type}]")
            return True
        elif success_count > 0:
            logger.info(f"{log_prefix}部分发送成功：{success_count}/{total_batches} 批次 [{report_type}]")
            return True
        else:
            logger.error(f"{log_prefix}发送完全失败 [{report_type}]")
            return False