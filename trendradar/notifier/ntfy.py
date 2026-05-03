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

class NtfyNotifier(BaseNotifier):
    """ntfy 通知器"""

    def __init__(self, config: Dict, server_url: str, topic: str, token: Optional[str] = None):
        super().__init__(config)
        self.server_url = server_url
        self.topic = topic
        self.token = token

    @property
    def name(self) -> str:
        return "ntfy"

    @property
    def batch_size_config_key(self) -> str:
        return "NTFY_BATCH_SIZE"

    @property
    def default_batch_size(self) -> int:
        return 3800

    def build_payload(self, batch_content: str, report_data: Dict, report_type: str) -> Dict:
        """ntfy 使用原始 data 而非 JSON payload，此接口不适用。"""
        raise NotImplementedError("ntfy uses raw POST data, not JSON payload")

    def is_success(self, response: requests.Response) -> bool:
        return response.status_code == 200

    def get_error_message(self, response: requests.Response) -> str:
        return f"状态码：{response.status_code}"

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
        """发送通知（支持分批，反向顺序推送）"""
        log_prefix = f"{self.name}{account_label}" if account_label else self.name
        proxies = self.get_proxies(proxy_url)

        report_type_en_map = {
            "当日汇总": "Daily Summary",
            "当前榜单汇总": "Current Ranking",
            "增量更新": "Incremental Update",
            "实时增量": "Realtime Incremental",
            "实时当前榜单": "Realtime Current Ranking",
        }
        report_type_en = report_type_en_map.get(report_type, "News Report")

        headers = {
            "Content-Type": "text/plain; charset=utf-8",
            "Markdown": "yes",
            "Title": report_type_en,
            "Priority": "default",
            "Tags": "news",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        base_url = self.server_url.rstrip("/")
        if not base_url.startswith(("http://", "https://")):
            base_url = f"https://{base_url}"
        url = f"{base_url}/{self.topic}"

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
            logger.info(f"发送{log_prefix}第 {actual_batch_num}/{total_batches} 批次（推送顺序: {idx}/{total_batches}），")

            if batch_bytes > 4096:
                logger.warning(f"警告：{log_prefix}第 {actual_batch_num} 批次消息过大（{batch_bytes} 字节），可能被拒绝")

            current_headers = headers.copy()
            if total_batches > 1:
                current_headers["Title"] = f"{report_type_en} ({actual_batch_num}/{total_batches})"

            try:
                response = requests.post(
                    url,
                    headers=current_headers,
                    data=batch_content.encode("utf-8"),
                    proxies=proxies,
                    timeout=30,
                )

                if self.is_success(response):
                    logger.info(f"{log_prefix}第 {actual_batch_num}/{total_batches} 批次发送成功 [{report_type}]")
                    success_count += 1
                    if idx < total_batches:
                        interval = 2 if "ntfy.sh" in self.server_url else 1
                        time.sleep(interval)
                elif response.status_code == 429:
                    logger.info(f"{log_prefix}第 {actual_batch_num}/{total_batches} 批次触发速率限制，10秒后重试 [{report_type}]")
                    time.sleep(10)
                    retry_response = requests.post(
                        url,
                        headers=current_headers,
                        data=batch_content.encode("utf-8"),
                        proxies=proxies,
                        timeout=30,
                    )
                    if self.is_success(retry_response):
                        logger.info(f"{log_prefix}第 {actual_batch_num}/{total_batches} 批次重试成功 [{report_type}]")
                        success_count += 1
                    else:
                        logger.error(f"{log_prefix}第 {actual_batch_num}/{total_batches} 批次重试失败，")
                elif response.status_code == 413:
                    logger.info(f"{log_prefix}第 {actual_batch_num}/{total_batches} 批次消息过大被拒绝 [{report_type}]，")
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