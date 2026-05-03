import json
import os
import re
import time
from datetime import datetime
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import pytz
import requests

from trendradar import utils
from trendradar.config import SMTP_CONFIGS
from trendradar.notifier.batch import split_content_into_batches, add_batch_headers, _get_max_batch_header_size


def send_to_notifications(
    stats: List[Dict],
    failed_ids: Optional[List] = None,
    report_type: str = "当日汇总",
    new_titles: Optional[Dict] = None,
    id_to_name: Optional[Dict] = None,
    update_info: Optional[Dict] = None,
    proxy_url: Optional[str] = None,
    mode: str = "daily",
    html_file_path: Optional[str] = None,
) -> Dict[str, bool]:
    """发送数据到多个通知平台（支持多账号）"""
    results = {}
    max_accounts = CONFIG["MAX_ACCOUNTS_PER_CHANNEL"]

    if CONFIG["PUSH_WINDOW"]["ENABLED"]:
        push_manager = PushRecordManager(CONFIG)
        time_range_start = CONFIG["PUSH_WINDOW"]["TIME_RANGE"]["START"]
        time_range_end = CONFIG["PUSH_WINDOW"]["TIME_RANGE"]["END"]

        if not push_manager.is_in_time_range(time_range_start, time_range_end):
            now = utils.get_beijing_time()
            print(
                f"推送窗口控制：当前时间 {now.strftime('%H:%M')} 不在推送时间窗口 {time_range_start}-{time_range_end} 内，跳过推送"
            )
            return results

        if CONFIG["PUSH_WINDOW"]["ONCE_PER_DAY"]:
            if push_manager.has_pushed_today():
                print(f"推送窗口控制：今天已推送过，跳过本次推送")
                return results
            else:
                print(f"推送窗口控制：今天首次推送")

    report_data = prepare_report_data(stats, failed_ids, new_titles, id_to_name, mode)

    update_info_to_send = update_info if CONFIG["SHOW_VERSION_UPDATE"] else None

    # 发送到飞书（多账号）
    from trendradar.notifier.feishu import FeishuNotifier

    feishu_urls = utils.parse_multi_account_config(CONFIG["FEISHU_WEBHOOK_URL"])
    if feishu_urls:
        feishu_urls = utils.limit_accounts(feishu_urls, max_accounts, "飞书")
        feishu = FeishuNotifier(CONFIG)
        feishu_results = []
        for i, url in enumerate(feishu_urls):
            if url:  # 跳过空值
                account_label = f"账号{i+1}" if len(feishu_urls) > 1 else ""
                result = feishu.send(
                    url, report_data, report_type, update_info_to_send, proxy_url, mode, account_label
                )
                feishu_results.append(result)
        results["feishu"] = any(feishu_results) if feishu_results else False

    # 发送到钉钉（多账号）
    dingtalk_urls = utils.parse_multi_account_config(CONFIG["DINGTALK_WEBHOOK_URL"])
    if dingtalk_urls:
        dingtalk_urls = utils.limit_accounts(dingtalk_urls, max_accounts, "钉钉")
        dingtalk_results = []
        for i, url in enumerate(dingtalk_urls):
            if url:
                account_label = f"账号{i+1}" if len(dingtalk_urls) > 1 else ""
                result = send_to_dingtalk(
                    url, report_data, report_type, update_info_to_send, proxy_url, mode, account_label
                )
                dingtalk_results.append(result)
        results["dingtalk"] = any(dingtalk_results) if dingtalk_results else False

    # 发送到企业微信（多账号）
    wework_urls = utils.parse_multi_account_config(CONFIG["WEWORK_WEBHOOK_URL"])
    if wework_urls:
        wework_urls = utils.limit_accounts(wework_urls, max_accounts, "企业微信")
        wework_results = []
        for i, url in enumerate(wework_urls):
            if url:
                account_label = f"账号{i+1}" if len(wework_urls) > 1 else ""
                result = send_to_wework(
                    url, report_data, report_type, update_info_to_send, proxy_url, mode, account_label
                )
                wework_results.append(result)
        results["wework"] = any(wework_results) if wework_results else False

    # 发送到 Telegram（多账号，需验证配对）
    telegram_tokens = utils.parse_multi_account_config(CONFIG["TELEGRAM_BOT_TOKEN"])
    telegram_chat_ids = utils.parse_multi_account_config(CONFIG["TELEGRAM_CHAT_ID"])
    if telegram_tokens and telegram_chat_ids:
        valid, count = utils.validate_paired_configs(
            {"bot_token": telegram_tokens, "chat_id": telegram_chat_ids},
            "Telegram",
            required_keys=["bot_token", "chat_id"]
        )
        if valid and count > 0:
            telegram_tokens = utils.limit_accounts(telegram_tokens, max_accounts, "Telegram")
            telegram_chat_ids = telegram_chat_ids[:len(telegram_tokens)]  # 保持数量一致
            telegram_results = []
            for i in range(len(telegram_tokens)):
                token = telegram_tokens[i]
                chat_id = telegram_chat_ids[i]
                if token and chat_id:
                    account_label = f"账号{i+1}" if len(telegram_tokens) > 1 else ""
                    result = send_to_telegram(
                        token, chat_id, report_data, report_type,
                        update_info_to_send, proxy_url, mode, account_label
                    )
                    telegram_results.append(result)
            results["telegram"] = any(telegram_results) if telegram_results else False

    # 发送到 ntfy（多账号，需验证配对）
    ntfy_server_url = CONFIG["NTFY_SERVER_URL"]
    ntfy_topics = utils.parse_multi_account_config(CONFIG["NTFY_TOPIC"])
    ntfy_tokens = utils.parse_multi_account_config(CONFIG["NTFY_TOKEN"])
    if ntfy_server_url and ntfy_topics:
        # 验证 token 和 topic 数量一致（如果配置了 token）
        if ntfy_tokens and len(ntfy_tokens) != len(ntfy_topics):
            print(f"❌ ntfy 配置错误：topic 数量({len(ntfy_topics)})与 token 数量({len(ntfy_tokens)})不一致，跳过 ntfy 推送")
        else:
            ntfy_topics = utils.limit_accounts(ntfy_topics, max_accounts, "ntfy")
            if ntfy_tokens:
                ntfy_tokens = ntfy_tokens[:len(ntfy_topics)]
            ntfy_results = []
            for i, topic in enumerate(ntfy_topics):
                if topic:
                    token = utils.get_account_at_index(ntfy_tokens, i, "") if ntfy_tokens else ""
                    account_label = f"账号{i+1}" if len(ntfy_topics) > 1 else ""
                    result = send_to_ntfy(
                        ntfy_server_url, topic, token, report_data, report_type,
                        update_info_to_send, proxy_url, mode, account_label
                    )
                    ntfy_results.append(result)
            results["ntfy"] = any(ntfy_results) if ntfy_results else False

    # 发送到 Bark（多账号）
    bark_urls = utils.parse_multi_account_config(CONFIG["BARK_URL"])
    if bark_urls:
        bark_urls = utils.limit_accounts(bark_urls, max_accounts, "Bark")
        bark_results = []
        for i, url in enumerate(bark_urls):
            if url:
                account_label = f"账号{i+1}" if len(bark_urls) > 1 else ""
                result = send_to_bark(
                    url, report_data, report_type, update_info_to_send, proxy_url, mode, account_label
                )
                bark_results.append(result)
        results["bark"] = any(bark_results) if bark_results else False

    # 发送到 Slack（多账号）
    slack_urls = utils.parse_multi_account_config(CONFIG["SLACK_WEBHOOK_URL"])
    if slack_urls:
        slack_urls = utils.limit_accounts(slack_urls, max_accounts, "Slack")
        slack_results = []
        for i, url in enumerate(slack_urls):
            if url:
                account_label = f"账号{i+1}" if len(slack_urls) > 1 else ""
                result = send_to_slack(
                    url, report_data, report_type, update_info_to_send, proxy_url, mode, account_label
                )
                slack_results.append(result)
        results["slack"] = any(slack_results) if slack_results else False

    # 发送邮件（保持原有逻辑，已支持多收件人）
    email_from = CONFIG["EMAIL_FROM"]
    email_password = CONFIG["EMAIL_PASSWORD"]
    email_to = CONFIG["EMAIL_TO"]
    email_smtp_server = CONFIG.get("EMAIL_SMTP_SERVER", "")
    email_smtp_port = CONFIG.get("EMAIL_SMTP_PORT", "")
    if email_from and email_password and email_to:
        results["email"] = send_to_email(
            email_from,
            email_password,
            email_to,
            report_type,
            html_file_path,
            email_smtp_server,
            email_smtp_port,
        )

    if not results:
        print("未配置任何通知渠道，跳过通知发送")

    # 如果成功发送了任何通知，且启用了每天只推一次，则记录推送
    if (
        CONFIG["PUSH_WINDOW"]["ENABLED"]
        and CONFIG["PUSH_WINDOW"]["ONCE_PER_DAY"]
        and any(results.values())
    ):
        push_manager = PushRecordManager(CONFIG)
        push_manager.record_push(report_type)

    return results


def send_to_feishu(
    webhook_url: str,
    report_data: Dict,
    report_type: str,
    update_info: Optional[Dict] = None,
    proxy_url: Optional[str] = None,
    mode: str = "daily",
    account_label: str = "",
) -> bool:
    """发送到飞书（支持分批发送）"""
    headers = {"Content-Type": "application/json"}
    proxies = None
    if proxy_url:
        proxies = {"http": proxy_url, "https": proxy_url}

    # 日志前缀
    log_prefix = f"飞书{account_label}" if account_label else "飞书"

    # 获取分批内容，使用飞书专用的批次大小
    feishu_batch_size = CONFIG.get("FEISHU_BATCH_SIZE", 29000)
    # 预留批次头部空间，避免添加头部后超限
    header_reserve = _get_max_batch_header_size("feishu")
    batches = split_content_into_batches(
        report_data,
        "feishu",
        update_info,
        max_bytes=feishu_batch_size - header_reserve,
        mode=mode,
    )

    # 统一添加批次头部（已预留空间，不会超限）
    batches = add_batch_headers(batches, "feishu", feishu_batch_size)

    print(f"{log_prefix}消息分为 {len(batches)} 批次发送 [{report_type}]")

    # 逐批发送
    for i, batch_content in enumerate(batches, 1):
        batch_size = len(batch_content.encode("utf-8"))
        print(
            f"发送{log_prefix}第 {i}/{len(batches)} 批次，大小：{batch_size} 字节 [{report_type}]"
        )

        total_titles = sum(
            len(stat["titles"]) for stat in report_data["stats"] if stat["count"] > 0
        )
        now = utils.get_beijing_time()

        payload = {
            "msg_type": "text",
            "content": {
                "total_titles": total_titles,
                "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
                "report_type": report_type,
                "text": batch_content,
            },
        }

        try:
            response = requests.post(
                webhook_url, headers=headers, json=payload, proxies=proxies, timeout=30
            )
            if response.status_code == 200:
                result = response.json()
                # 检查飞书的响应状态
                if result.get("StatusCode") == 0 or result.get("code") == 0:
                    print(f"{log_prefix}第 {i}/{len(batches)} 批次发送成功 [{report_type}]")
                    # 批次间间隔
                    if i < len(batches):
                        time.sleep(CONFIG["BATCH_SEND_INTERVAL"])
                else:
                    error_msg = result.get("msg") or result.get("StatusMessage", "未知错误")
                    print(
                        f"{log_prefix}第 {i}/{len(batches)} 批次发送失败 [{report_type}]，错误：{error_msg}"
                    )
                    return False
            else:
                print(
                    f"{log_prefix}第 {i}/{len(batches)} 批次发送失败 [{report_type}]，状态码：{response.status_code}"
                )
                return False
        except Exception as e:
            print(f"{log_prefix}第 {i}/{len(batches)} 批次发送出错 [{report_type}]：{e}")
            return False

    print(f"{log_prefix}所有 {len(batches)} 批次发送完成 [{report_type}]")
    return True


def send_to_dingtalk(
    webhook_url: str,
    report_data: Dict,
    report_type: str,
    update_info: Optional[Dict] = None,
    proxy_url: Optional[str] = None,
    mode: str = "daily",
    account_label: str = "",
) -> bool:
    """发送到钉钉（支持分批发送）"""
    headers = {"Content-Type": "application/json"}
    proxies = None
    if proxy_url:
        proxies = {"http": proxy_url, "https": proxy_url}

    # 日志前缀
    log_prefix = f"钉钉{account_label}" if account_label else "钉钉"

    # 获取分批内容，使用钉钉专用的批次大小
    dingtalk_batch_size = CONFIG.get("DINGTALK_BATCH_SIZE", 20000)
    # 预留批次头部空间，避免添加头部后超限
    header_reserve = _get_max_batch_header_size("dingtalk")
    batches = split_content_into_batches(
        report_data,
        "dingtalk",
        update_info,
        max_bytes=dingtalk_batch_size - header_reserve,
        mode=mode,
    )

    # 统一添加批次头部（已预留空间，不会超限）
    batches = add_batch_headers(batches, "dingtalk", dingtalk_batch_size)

    print(f"{log_prefix}消息分为 {len(batches)} 批次发送 [{report_type}]")

    # 逐批发送
    for i, batch_content in enumerate(batches, 1):
        batch_size = len(batch_content.encode("utf-8"))
        print(
            f"发送{log_prefix}第 {i}/{len(batches)} 批次，大小：{batch_size} 字节 [{report_type}]"
        )

        payload = {
            "msgtype": "markdown",
            "markdown": {
                "title": f"TrendRadar 热点分析报告 - {report_type}",
                "text": batch_content,
            },
        }

        try:
            response = requests.post(
                webhook_url, headers=headers, json=payload, proxies=proxies, timeout=30
            )
            if response.status_code == 200:
                result = response.json()
                if result.get("errcode") == 0:
                    print(f"{log_prefix}第 {i}/{len(batches)} 批次发送成功 [{report_type}]")
                    # 批次间间隔
                    if i < len(batches):
                        time.sleep(CONFIG["BATCH_SEND_INTERVAL"])
                else:
                    print(
                        f"{log_prefix}第 {i}/{len(batches)} 批次发送失败 [{report_type}]，错误：{result.get('errmsg')}"
                    )
                    return False
            else:
                print(
                    f"{log_prefix}第 {i}/{len(batches)} 批次发送失败 [{report_type}]，状态码：{response.status_code}"
                )
                return False
        except Exception as e:
            print(f"{log_prefix}第 {i}/{len(batches)} 批次发送出错 [{report_type}]：{e}")
            return False

    print(f"{log_prefix}所有 {len(batches)} 批次发送完成 [{report_type}]")
    return True


def strip_markdown(text: str) -> str:
    """去除文本中的 markdown 语法格式，用于个人微信推送"""

    # 去除粗体 **text** 或 __text__
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'__(.+?)__', r'\1', text)

    # 去除斜体 *text* 或 _text_
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    text = re.sub(r'_(.+?)_', r'\1', text)

    # 去除删除线 ~~text~~
    text = re.sub(r'~~(.+?)~~', r'\1', text)

    # 转换链接 [text](url) -> text url（保留 URL）
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'\1 \2', text)
    # 如果不需要保留 URL，可以使用下面这行（只保留标题文本）：
    # text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)

    # 去除图片 ![alt](url) -> alt
    text = re.sub(r'!\[(.+?)\]\(.+?\)', r'\1', text)

    # 去除行内代码 `code`
    text = re.sub(r'`(.+?)`', r'\1', text)

    # 去除引用符号 >
    text = re.sub(r'^>\s*', '', text, flags=re.MULTILINE)

    # 去除标题符号 # ## ### 等
    text = re.sub(r'^#+\s*', '', text, flags=re.MULTILINE)

    # 去除水平分割线 --- 或 ***
    text = re.sub(r'^[\-\*]{3,}\s*$', '', text, flags=re.MULTILINE)

    # 去除 HTML 标签 <font color='xxx'>text</font> -> text
    text = re.sub(r'<font[^>]*>(.+?)</font>', r'\1', text)
    text = re.sub(r'<[^>]+>', '', text)

    # 清理多余的空行（保留最多两个连续空行）
    text = re.sub(r'\n{3,}', '\n\n', text)

    return text.strip()


def send_to_wework(
    webhook_url: str,
    report_data: Dict,
    report_type: str,
    update_info: Optional[Dict] = None,
    proxy_url: Optional[str] = None,
    mode: str = "daily",
    account_label: str = "",
) -> bool:
    """发送到企业微信（支持分批发送，支持 markdown 和 text 两种格式）"""
    headers = {"Content-Type": "application/json"}
    proxies = None
    if proxy_url:
        proxies = {"http": proxy_url, "https": proxy_url}

    # 日志前缀
    log_prefix = f"企业微信{account_label}" if account_label else "企业微信"

    # 获取消息类型配置（markdown 或 text）
    msg_type = CONFIG.get("WEWORK_MSG_TYPE", "markdown").lower()
    is_text_mode = msg_type == "text"

    if is_text_mode:
        print(f"{log_prefix}使用 text 格式（个人微信模式）[{report_type}]")
    else:
        print(f"{log_prefix}使用 markdown 格式（群机器人模式）[{report_type}]")

    # text 模式使用 wework_text，markdown 模式使用 wework
    header_format_type = "wework_text" if is_text_mode else "wework"

    # 获取分批内容，预留批次头部空间
    wework_batch_size = CONFIG.get("MESSAGE_BATCH_SIZE", 4000)
    header_reserve = _get_max_batch_header_size(header_format_type)
    batches = split_content_into_batches(
        report_data, "wework", update_info, max_bytes=wework_batch_size - header_reserve, mode=mode
    )

    # 统一添加批次头部（已预留空间，不会超限）
    batches = add_batch_headers(batches, header_format_type, wework_batch_size)

    print(f"{log_prefix}消息分为 {len(batches)} 批次发送 [{report_type}]")

    # 逐批发送
    for i, batch_content in enumerate(batches, 1):
        # 根据消息类型构建 payload
        if is_text_mode:
            # text 格式：去除 markdown 语法
            plain_content = strip_markdown(batch_content)
            payload = {"msgtype": "text", "text": {"content": plain_content}}
            batch_size = len(plain_content.encode("utf-8"))
        else:
            # markdown 格式：保持原样
            payload = {"msgtype": "markdown", "markdown": {"content": batch_content}}
            batch_size = len(batch_content.encode("utf-8"))

        print(
            f"发送{log_prefix}第 {i}/{len(batches)} 批次，大小：{batch_size} 字节 [{report_type}]"
        )

        try:
            response = requests.post(
                webhook_url, headers=headers, json=payload, proxies=proxies, timeout=30
            )
            if response.status_code == 200:
                result = response.json()
                if result.get("errcode") == 0:
                    print(f"{log_prefix}第 {i}/{len(batches)} 批次发送成功 [{report_type}]")
                    # 批次间间隔
                    if i < len(batches):
                        time.sleep(CONFIG["BATCH_SEND_INTERVAL"])
                else:
                    print(
                        f"{log_prefix}第 {i}/{len(batches)} 批次发送失败 [{report_type}]，错误：{result.get('errmsg')}"
                    )
                    return False
            else:
                print(
                    f"{log_prefix}第 {i}/{len(batches)} 批次发送失败 [{report_type}]，状态码：{response.status_code}"
                )
                return False
        except Exception as e:
            print(f"{log_prefix}第 {i}/{len(batches)} 批次发送出错 [{report_type}]：{e}")
            return False

    print(f"{log_prefix}所有 {len(batches)} 批次发送完成 [{report_type}]")
    return True


def send_to_telegram(
    bot_token: str,
    chat_id: str,
    report_data: Dict,
    report_type: str,
    update_info: Optional[Dict] = None,
    proxy_url: Optional[str] = None,
    mode: str = "daily",
    account_label: str = "",
) -> bool:
    """发送到Telegram（支持分批发送）"""
    headers = {"Content-Type": "application/json"}
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    proxies = None
    if proxy_url:
        proxies = {"http": proxy_url, "https": proxy_url}

    # 日志前缀
    log_prefix = f"Telegram{account_label}" if account_label else "Telegram"

    # 获取分批内容，预留批次头部空间
    telegram_batch_size = CONFIG.get("MESSAGE_BATCH_SIZE", 4000)
    header_reserve = _get_max_batch_header_size("telegram")
    batches = split_content_into_batches(
        report_data, "telegram", update_info, max_bytes=telegram_batch_size - header_reserve, mode=mode
    )

    # 统一添加批次头部（已预留空间，不会超限）
    batches = add_batch_headers(batches, "telegram", telegram_batch_size)

    print(f"{log_prefix}消息分为 {len(batches)} 批次发送 [{report_type}]")

    # 逐批发送
    for i, batch_content in enumerate(batches, 1):
        batch_size = len(batch_content.encode("utf-8"))
        print(
            f"发送{log_prefix}第 {i}/{len(batches)} 批次，大小：{batch_size} 字节 [{report_type}]"
        )

        payload = {
            "chat_id": chat_id,
            "text": batch_content,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

        try:
            response = requests.post(
                url, headers=headers, json=payload, proxies=proxies, timeout=30
            )
            if response.status_code == 200:
                result = response.json()
                if result.get("ok"):
                    print(f"{log_prefix}第 {i}/{len(batches)} 批次发送成功 [{report_type}]")
                    # 批次间间隔
                    if i < len(batches):
                        time.sleep(CONFIG["BATCH_SEND_INTERVAL"])
                else:
                    print(
                        f"{log_prefix}第 {i}/{len(batches)} 批次发送失败 [{report_type}]，错误：{result.get('description')}"
                    )
                    return False
            else:
                print(
                    f"{log_prefix}第 {i}/{len(batches)} 批次发送失败 [{report_type}]，状态码：{response.status_code}"
                )
                return False
        except Exception as e:
            print(f"{log_prefix}第 {i}/{len(batches)} 批次发送出错 [{report_type}]：{e}")
            return False

    print(f"{log_prefix}所有 {len(batches)} 批次发送完成 [{report_type}]")
    return True


def send_to_email(
    from_email: str,
    password: str,
    to_email: str,
    report_type: str,
    html_file_path: str,
    custom_smtp_server: Optional[str] = None,
    custom_smtp_port: Optional[int] = None,
) -> bool:
    """发送邮件通知"""
    try:
        if not html_file_path or not Path(html_file_path).exists():
            print(f"错误：HTML文件不存在或未提供: {html_file_path}")
            return False

        print(f"使用HTML文件: {html_file_path}")
        with open(html_file_path, "r", encoding="utf-8") as f:
            html_content = f.read()

        domain = from_email.split("@")[-1].lower()

        if custom_smtp_server and custom_smtp_port:
            # 使用自定义 SMTP 配置
            smtp_server = custom_smtp_server
            smtp_port = int(custom_smtp_port)
            # 根据端口判断加密方式：465=SSL, 587=TLS
            if smtp_port == 465:
                use_tls = False  # SSL 模式（SMTP_SSL）
            elif smtp_port == 587:
                use_tls = True   # TLS 模式（STARTTLS）
            else:
                # 其他端口优先尝试 TLS（更安全，更广泛支持）
                use_tls = True
        elif domain in SMTP_CONFIGS:
            # 使用预设配置
            smtp_config = SMTP_CONFIGS[domain]
            smtp_server = smtp_config["server"]
            smtp_port = smtp_config["port"]
            use_tls = smtp_config["encryption"] == "TLS"
        else:
            print(f"未识别的邮箱服务商: {domain}，使用通用 SMTP 配置")
            smtp_server = f"smtp.{domain}"
            smtp_port = 587
            use_tls = True

        msg = MIMEMultipart("alternative")

        # 严格按照 RFC 标准设置 From header
        sender_name = "TrendRadar"
        msg["From"] = formataddr((sender_name, from_email))

        # 设置收件人
        recipients = [addr.strip() for addr in to_email.split(",")]
        if len(recipients) == 1:
            msg["To"] = recipients[0]
        else:
            msg["To"] = ", ".join(recipients)

        # 设置邮件主题
        now = utils.get_beijing_time()
        subject = f"TrendRadar 热点分析报告 - {report_type} - {now.strftime('%m月%d日 %H:%M')}"
        msg["Subject"] = Header(subject, "utf-8")

        # 设置其他标准 header
        msg["MIME-Version"] = "1.0"
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid()

        # 添加纯文本部分（作为备选）
        text_content = f"""
TrendRadar 热点分析报告
========================
报告类型：{report_type}
生成时间：{now.strftime('%Y-%m-%d %H:%M:%S')}

请使用支持HTML的邮件客户端查看完整报告内容。
        """
        text_part = MIMEText(text_content, "plain", "utf-8")
        msg.attach(text_part)

        html_part = MIMEText(html_content, "html", "utf-8")
        msg.attach(html_part)

        print(f"正在发送邮件到 {to_email}...")
        print(f"SMTP 服务器: {smtp_server}:{smtp_port}")
        print(f"发件人: {from_email}")

        try:
            if use_tls:
                # TLS 模式
                server = smtplib.SMTP(smtp_server, smtp_port, timeout=30)
                server.set_debuglevel(0)  # 设为1可以查看详细调试信息
                server.ehlo()
                server.starttls()
                server.ehlo()
            else:
                # SSL 模式
                server = smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=30)
                server.set_debuglevel(0)
                server.ehlo()

            # 登录
            server.login(from_email, password)

            # 发送邮件
            server.send_message(msg)
            server.quit()

            print(f"邮件发送成功 [{report_type}] -> {to_email}")
            return True

        except smtplib.SMTPServerDisconnected:
            print(f"邮件发送失败：服务器意外断开连接，请检查网络或稍后重试")
            return False

    except smtplib.SMTPAuthenticationError as e:
        print(f"邮件发送失败：认证错误，请检查邮箱和密码/授权码")
        print(f"详细错误: {str(e)}")
        return False
    except smtplib.SMTPRecipientsRefused as e:
        print(f"邮件发送失败：收件人地址被拒绝 {e}")
        return False
    except smtplib.SMTPSenderRefused as e:
        print(f"邮件发送失败：发件人地址被拒绝 {e}")
        return False
    except smtplib.SMTPDataError as e:
        print(f"邮件发送失败：邮件数据错误 {e}")
        return False
    except smtplib.SMTPConnectError as e:
        print(f"邮件发送失败：无法连接到 SMTP 服务器 {smtp_server}:{smtp_port}")
        print(f"详细错误: {str(e)}")
        return False
    except Exception as e:
        print(f"邮件发送失败 [{report_type}]：{e}")
        import traceback

        traceback.print_exc()
        return False


def send_to_ntfy(
    server_url: str,
    topic: str,
    token: Optional[str],
    report_data: Dict,
    report_type: str,
    update_info: Optional[Dict] = None,
    proxy_url: Optional[str] = None,
    mode: str = "daily",
    account_label: str = "",
) -> bool:
    """发送到ntfy（支持分批发送，严格遵守4KB限制）"""
    # 日志前缀
    log_prefix = f"ntfy{account_label}" if account_label else "ntfy"

    # 避免 HTTP header 编码问题
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

    if token:
        headers["Authorization"] = f"Bearer {token}"

    # 构建完整URL，确保格式正确
    base_url = server_url.rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        base_url = f"https://{base_url}"
    url = f"{base_url}/{topic}"

    proxies = None
    if proxy_url:
        proxies = {"http": proxy_url, "https": proxy_url}

    # 获取分批内容，使用ntfy专用的4KB限制，预留批次头部空间
    ntfy_batch_size = 3800
    header_reserve = _get_max_batch_header_size("ntfy")
    batches = split_content_into_batches(
        report_data, "ntfy", update_info, max_bytes=ntfy_batch_size - header_reserve, mode=mode
    )

    # 统一添加批次头部（已预留空间，不会超限）
    batches = add_batch_headers(batches, "ntfy", ntfy_batch_size)

    total_batches = len(batches)
    print(f"{log_prefix}消息分为 {total_batches} 批次发送 [{report_type}]")

    # 反转批次顺序，使得在ntfy客户端显示时顺序正确
    # ntfy显示最新消息在上面，所以我们从最后一批开始推送
    reversed_batches = list(reversed(batches))

    print(f"{log_prefix}将按反向顺序推送（最后批次先推送），确保客户端显示顺序正确")

    # 逐批发送（反向顺序）
    success_count = 0
    for idx, batch_content in enumerate(reversed_batches, 1):
        # 计算正确的批次编号（用户视角的编号）
        actual_batch_num = total_batches - idx + 1

        batch_size = len(batch_content.encode("utf-8"))
        print(
            f"发送{log_prefix}第 {actual_batch_num}/{total_batches} 批次（推送顺序: {idx}/{total_batches}），大小：{batch_size} 字节 [{report_type}]"
        )

        # 检查消息大小，确保不超过4KB
        if batch_size > 4096:
            print(f"警告：{log_prefix}第 {actual_batch_num} 批次消息过大（{batch_size} 字节），可能被拒绝")

        # 更新 headers 的批次标识
        current_headers = headers.copy()
        if total_batches > 1:
            current_headers["Title"] = (
                f"{report_type_en} ({actual_batch_num}/{total_batches})"
            )

        try:
            response = requests.post(
                url,
                headers=current_headers,
                data=batch_content.encode("utf-8"),
                proxies=proxies,
                timeout=30,
            )

            if response.status_code == 200:
                print(f"{log_prefix}第 {actual_batch_num}/{total_batches} 批次发送成功 [{report_type}]")
                success_count += 1
                if idx < total_batches:
                    # 公共服务器建议 2-3 秒，自托管可以更短
                    interval = 2 if "ntfy.sh" in server_url else 1
                    time.sleep(interval)
            elif response.status_code == 429:
                print(
                    f"{log_prefix}第 {actual_batch_num}/{total_batches} 批次速率限制 [{report_type}]，等待后重试"
                )
                time.sleep(10)  # 等待10秒后重试
                # 重试一次
                retry_response = requests.post(
                    url,
                    headers=current_headers,
                    data=batch_content.encode("utf-8"),
                    proxies=proxies,
                    timeout=30,
                )
                if retry_response.status_code == 200:
                    print(f"{log_prefix}第 {actual_batch_num}/{total_batches} 批次重试成功 [{report_type}]")
                    success_count += 1
                else:
                    print(
                        f"{log_prefix}第 {actual_batch_num}/{total_batches} 批次重试失败，状态码：{retry_response.status_code}"
                    )
            elif response.status_code == 413:
                print(
                    f"{log_prefix}第 {actual_batch_num}/{total_batches} 批次消息过大被拒绝 [{report_type}]，消息大小：{batch_size} 字节"
                )
            else:
                print(
                    f"{log_prefix}第 {actual_batch_num}/{total_batches} 批次发送失败 [{report_type}]，状态码：{response.status_code}"
                )
                try:
                    print(f"错误详情：{response.text}")
                except:
                    pass

        except requests.exceptions.ConnectTimeout:
            print(f"{log_prefix}第 {actual_batch_num}/{total_batches} 批次连接超时 [{report_type}]")
        except requests.exceptions.ReadTimeout:
            print(f"{log_prefix}第 {actual_batch_num}/{total_batches} 批次读取超时 [{report_type}]")
        except requests.exceptions.ConnectionError as e:
            print(f"{log_prefix}第 {actual_batch_num}/{total_batches} 批次连接错误 [{report_type}]：{e}")
        except Exception as e:
            print(f"{log_prefix}第 {actual_batch_num}/{total_batches} 批次发送异常 [{report_type}]：{e}")

    # 判断整体发送是否成功
    if success_count == total_batches:
        print(f"{log_prefix}所有 {total_batches} 批次发送完成 [{report_type}]")
        return True
    elif success_count > 0:
        print(f"{log_prefix}部分发送成功：{success_count}/{total_batches} 批次 [{report_type}]")
        return True  # 部分成功也视为成功
    else:
        print(f"{log_prefix}发送完全失败 [{report_type}]")
        return False


def send_to_bark(
    bark_url: str,
    report_data: Dict,
    report_type: str,
    update_info: Optional[Dict] = None,
    proxy_url: Optional[str] = None,
    mode: str = "daily",
    account_label: str = "",
) -> bool:
    """发送到Bark（支持分批发送，使用 markdown 格式）"""
    # 日志前缀
    log_prefix = f"Bark{account_label}" if account_label else "Bark"

    proxies = None
    if proxy_url:
        proxies = {"http": proxy_url, "https": proxy_url}

    # 解析 Bark URL，提取 device_key 和 API 端点
    # Bark URL 格式: https://api.day.app/device_key 或 https://bark.day.app/device_key
    from urllib.parse import urlparse

    parsed_url = urlparse(bark_url)
    device_key = parsed_url.path.strip('/').split('/')[0] if parsed_url.path else None

    if not device_key:
        print(f"{log_prefix} URL 格式错误，无法提取 device_key: {bark_url}")
        return False

    # 构建正确的 API 端点
    api_endpoint = f"{parsed_url.scheme}://{parsed_url.netloc}/push"

    # 获取分批内容（Bark 限制为 3600 字节以避免 413 错误），预留批次头部空间
    bark_batch_size = CONFIG["BARK_BATCH_SIZE"]
    header_reserve = _get_max_batch_header_size("bark")
    batches = split_content_into_batches(
        report_data, "bark", update_info, max_bytes=bark_batch_size - header_reserve, mode=mode
    )

    # 统一添加批次头部（已预留空间，不会超限）
    batches = add_batch_headers(batches, "bark", bark_batch_size)

    total_batches = len(batches)
    print(f"{log_prefix}消息分为 {total_batches} 批次发送 [{report_type}]")

    # 反转批次顺序，使得在Bark客户端显示时顺序正确
    # Bark显示最新消息在上面，所以我们从最后一批开始推送
    reversed_batches = list(reversed(batches))

    print(f"{log_prefix}将按反向顺序推送（最后批次先推送），确保客户端显示顺序正确")

    # 逐批发送（反向顺序）
    success_count = 0
    for idx, batch_content in enumerate(reversed_batches, 1):
        # 计算正确的批次编号（用户视角的编号）
        actual_batch_num = total_batches - idx + 1

        batch_size = len(batch_content.encode("utf-8"))
        print(
            f"发送{log_prefix}第 {actual_batch_num}/{total_batches} 批次（推送顺序: {idx}/{total_batches}），大小：{batch_size} 字节 [{report_type}]"
        )

        # 检查消息大小（Bark使用APNs，限制4KB）
        if batch_size > 4096:
            print(
                f"警告：{log_prefix}第 {actual_batch_num}/{total_batches} 批次消息过大（{batch_size} 字节），可能被拒绝"
            )

        # 构建JSON payload
        payload = {
            "title": report_type,
            "markdown": batch_content,
            "device_key": device_key,
            "sound": "default",
            "group": "TrendRadar",
            "action": "none",  # 点击推送跳到 APP 不弹出弹框,方便阅读
        }

        try:
            response = requests.post(
                api_endpoint,
                json=payload,
                proxies=proxies,
                timeout=30,
            )

            if response.status_code == 200:
                result = response.json()
                if result.get("code") == 200:
                    print(f"{log_prefix}第 {actual_batch_num}/{total_batches} 批次发送成功 [{report_type}]")
                    success_count += 1
                    # 批次间间隔
                    if idx < total_batches:
                        time.sleep(CONFIG["BATCH_SEND_INTERVAL"])
                else:
                    print(
                        f"{log_prefix}第 {actual_batch_num}/{total_batches} 批次发送失败 [{report_type}]，错误：{result.get('message', '未知错误')}"
                    )
            else:
                print(
                    f"{log_prefix}第 {actual_batch_num}/{total_batches} 批次发送失败 [{report_type}]，状态码：{response.status_code}"
                )
                try:
                    print(f"错误详情：{response.text}")
                except:
                    pass

        except requests.exceptions.ConnectTimeout:
            print(f"{log_prefix}第 {actual_batch_num}/{total_batches} 批次连接超时 [{report_type}]")
        except requests.exceptions.ReadTimeout:
            print(f"{log_prefix}第 {actual_batch_num}/{total_batches} 批次读取超时 [{report_type}]")
        except requests.exceptions.ConnectionError as e:
            print(f"{log_prefix}第 {actual_batch_num}/{total_batches} 批次连接错误 [{report_type}]：{e}")
        except Exception as e:
            print(f"{log_prefix}第 {actual_batch_num}/{total_batches} 批次发送异常 [{report_type}]：{e}")

    # 判断整体发送是否成功
    if success_count == total_batches:
        print(f"{log_prefix}所有 {total_batches} 批次发送完成 [{report_type}]")
        return True
    elif success_count > 0:
        print(f"{log_prefix}部分发送成功：{success_count}/{total_batches} 批次 [{report_type}]")
        return True  # 部分成功也视为成功
    else:
        print(f"{log_prefix}发送完全失败 [{report_type}]")
        return False


def convert_markdown_to_mrkdwn(content: str) -> str:
    """
    将标准 Markdown 转换为 Slack 的 mrkdwn 格式

    转换规则：
    - **粗体** → *粗体*
    - [文本](url) → <url|文本>
    - 保留其他格式（代码块、列表等）
    """
    # 1. 转换链接格式: [文本](url) → <url|文本>
    content = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<\2|\1>', content)

    # 2. 转换粗体: **文本** → *文本*
    content = re.sub(r'\*\*([^*]+)\*\*', r'*\1*', content)

    return content


def send_to_slack(
    webhook_url: str,
    report_data: Dict,
    report_type: str,
    update_info: Optional[Dict] = None,
    proxy_url: Optional[str] = None,
    mode: str = "daily",
    account_label: str = "",
) -> bool:
    """发送到Slack（支持分批发送，使用 mrkdwn 格式）"""
    headers = {"Content-Type": "application/json"}
    proxies = None
    if proxy_url:
        proxies = {"http": proxy_url, "https": proxy_url}

    # 日志前缀
    log_prefix = f"Slack{account_label}" if account_label else "Slack"

    # 获取分批内容（使用 Slack 批次大小），预留批次头部空间
    slack_batch_size = CONFIG["SLACK_BATCH_SIZE"]
    header_reserve = _get_max_batch_header_size("slack")
    batches = split_content_into_batches(
        report_data, "slack", update_info, max_bytes=slack_batch_size - header_reserve, mode=mode
    )

    # 统一添加批次头部（已预留空间，不会超限）
    batches = add_batch_headers(batches, "slack", slack_batch_size)

    print(f"{log_prefix}消息分为 {len(batches)} 批次发送 [{report_type}]")

    # 逐批发送
    for i, batch_content in enumerate(batches, 1):
        # 转换 Markdown 到 mrkdwn 格式
        mrkdwn_content = convert_markdown_to_mrkdwn(batch_content)

        batch_size = len(mrkdwn_content.encode("utf-8"))
        print(
            f"发送{log_prefix}第 {i}/{len(batches)} 批次，大小：{batch_size} 字节 [{report_type}]"
        )

        # 构建 Slack payload（使用简单的 text 字段，支持 mrkdwn）
        payload = {
            "text": mrkdwn_content
        }

        try:
            response = requests.post(
                webhook_url, headers=headers, json=payload, proxies=proxies, timeout=30
            )

            # Slack Incoming Webhooks 成功时返回 "ok" 文本
            if response.status_code == 200 and response.text == "ok":
                print(f"{log_prefix}第 {i}/{len(batches)} 批次发送成功 [{report_type}]")
                # 批次间间隔
                if i < len(batches):
                    time.sleep(CONFIG["BATCH_SEND_INTERVAL"])
            else:
                error_msg = response.text if response.text else f"状态码：{response.status_code}"
                print(
                    f"{log_prefix}第 {i}/{len(batches)} 批次发送失败 [{report_type}]，错误：{error_msg}"
                )
                return False
        except Exception as e:
            print(f"{log_prefix}第 {i}/{len(batches)} 批次发送出错 [{report_type}]：{e}")
            return False

    print(f"{log_prefix}所有 {len(batches)} 批次发送完成 [{report_type}]")
    return True


# === 主分析器 ===
def prepare_report_data(
    stats: List[Dict],
    failed_ids: Optional[List] = None,
    new_titles: Optional[Dict] = None,
    id_to_name: Optional[Dict] = None,
    mode: str = "daily",
) -> Dict:
    """准备报告数据"""
    processed_new_titles = []

    # 在增量模式下隐藏新增新闻区域
    hide_new_section = mode == "incremental"

    # 只有在非隐藏模式下才处理新增新闻部分
    if not hide_new_section:
        filtered_new_titles = {}
        if new_titles and id_to_name:
            word_groups, filter_words, global_filters = load_frequency_words()
            for source_id, titles_data in new_titles.items():
                filtered_titles = {}
                for title, title_data in titles_data.items():
                    if matches_word_groups(title, word_groups, filter_words, global_filters):
                        filtered_titles[title] = title_data
                if filtered_titles:
                    filtered_new_titles[source_id] = filtered_titles

        if filtered_new_titles and id_to_name:
            for source_id, titles_data in filtered_new_titles.items():
                source_name = id_to_name.get(source_id, source_id)
                source_titles = []

                for title, title_data in titles_data.items():
                    url = title_data.get("url", "")
                    mobile_url = title_data.get("mobileUrl", "")
                    ranks = title_data.get("ranks", [])

                    processed_title = {
                        "title": title,
                        "source_name": source_name,
                        "time_display": "",
                        "count": 1,
                        "ranks": ranks,
                        "rank_threshold": CONFIG["RANK_THRESHOLD"],
                        "url": url,
                        "mobile_url": mobile_url,
                        "is_new": True,
                    }
                    source_titles.append(processed_title)

                if source_titles:
                    processed_new_titles.append(
                        {
                            "source_id": source_id,
                            "source_name": source_name,
                            "titles": source_titles,
                        }
                    )

    processed_stats = []
    for stat in stats:
        if stat["count"] <= 0:
            continue

        processed_titles = []
        for title_data in stat["titles"]:
            processed_title = {
                "title": title_data["title"],
                "source_name": title_data["source_name"],
                "time_display": title_data["time_display"],
                "count": title_data["count"],
                "ranks": title_data["ranks"],
                "rank_threshold": title_data["rank_threshold"],
                "url": title_data.get("url", ""),
                "mobile_url": title_data.get("mobileUrl", ""),
                "is_new": title_data.get("is_new", False),
            }
            processed_titles.append(processed_title)

        processed_stats.append(
            {
                "word": stat["word"],
                "count": stat["count"],
                "percentage": stat.get("percentage", 0),
                "titles": processed_titles,
            }
        )

    return {
        "stats": processed_stats,
        "new_titles": processed_new_titles,
        "failed_ids": failed_ids or [],
        "total_new_count": sum(
            len(source["titles"]) for source in processed_new_titles
        ),
    }


