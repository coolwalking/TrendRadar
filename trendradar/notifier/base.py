# coding=utf-8

import time
from abc import ABC, abstractmethod
from typing import Dict, List, Optional

import requests

from trendradar import utils
from trendradar.notifier.batch import split_content_into_batches, add_batch_headers, _get_max_batch_header_size


class BaseNotifier(ABC):
    """通知渠道基类"""

    def __init__(self, config: Dict):
        self.config = config

    @property
    @abstractmethod
    def name(self) -> str:
        """渠道名称"""
        ...

    @property
    @abstractmethod
    def batch_size_config_key(self) -> str:
        """批次大小配置键"""
        ...

    @property
    def default_batch_size(self) -> int:
        """默认批次大小（字节）"""
        return 4000

    @property
    def format_type(self) -> str:
        """内容格式类型"""
        return self.name.lower()

    def get_batch_size(self) -> int:
        """获取实际批次大小"""
        key = self.batch_size_config_key
        size = self.config.get(key, self.default_batch_size)
        header_reserve = _get_max_batch_header_size(self.format_type)
        return size - header_reserve

    def get_proxies(self, proxy_url: Optional[str]) -> Optional[Dict]:
        """获取代理配置"""
        if proxy_url:
            return {"http": proxy_url, "https": proxy_url}
        return None

    @abstractmethod
    def build_payload(self, batch_content: str, report_data: Dict, report_type: str) -> Dict:
        """构建请求体"""
        ...

    @abstractmethod
    def is_success(self, response: requests.Response) -> bool:
        """判断响应是否成功"""
        ...

    @abstractmethod
    def get_error_message(self, response: requests.Response) -> str:
        """获取错误信息"""
        ...

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
        """发送通知（支持分批）"""
        log_prefix = f"{self.name}{account_label}" if account_label else self.name
        proxies = self.get_proxies(proxy_url)
        batch_size = self.get_batch_size()

        batches = split_content_into_batches(
            report_data,
            self.format_type,
            update_info,
            max_bytes=batch_size,
            mode=mode,
        )
        batches = add_batch_headers(batches, self.format_type, batch_size)

        print(f"{log_prefix}消息分为 {len(batches)} 批次发送 [{report_type}]")

        for i, batch_content in enumerate(batches, 1):
            batch_bytes = len(batch_content.encode("utf-8"))
            print(
                f"发送{log_prefix}第 {i}/{len(batches)} 批次，大小：{batch_bytes} 字节 [{report_type}]"
            )

            payload = self.build_payload(batch_content, report_data, report_type)

            try:
                response = requests.post(
                    webhook_url,
                    headers=self.get_headers(),
                    json=payload,
                    proxies=proxies,
                    timeout=30,
                )
                if self.is_success(response):
                    print(f"{log_prefix}第 {i}/{len(batches)} 批次发送成功 [{report_type}]")
                    if i < len(batches):
                        time.sleep(self.config.get("BATCH_SEND_INTERVAL", 3))
                else:
                    error_msg = self.get_error_message(response)
                    print(
                        f"{log_prefix}第 {i}/{len(batches)} 批次发送失败 [{report_type}]，错误：{error_msg}"
                    )
                    return False
            except Exception as e:
                print(f"{log_prefix}第 {i}/{len(batches)} 批次发送出错 [{report_type}]：{e}")
                return False

        print(f"{log_prefix}所有 {len(batches)} 批次发送完成 [{report_type}]")
        return True

    def get_headers(self) -> Dict:
        """获取请求头"""
        return {"Content-Type": "application/json"}
