# coding=utf-8

from typing import Any, Dict, List, Optional, Union

from trendradar import config, utils
from trendradar.config import (
    get_account_at_index,
    limit_accounts,
    parse_multi_account_config,
    validate_paired_configs,
)
from trendradar.records import PushRecordManager
from trendradar.utils import load_frequency_words, matches_word_groups
from trendradar.logging_config import get_logger

logger = get_logger(__name__)

from trendradar.notifier.bark import BarkNotifier
from trendradar.notifier.dingtalk import DingTalkNotifier
from trendradar.notifier.email import EmailNotifier
from trendradar.notifier.feishu import FeishuNotifier
from trendradar.notifier.ntfy import NtfyNotifier
from trendradar.notifier.slack import SlackNotifier
from trendradar.notifier.telegram import TelegramNotifier
from trendradar.notifier.wework import WeComNotifier


def send_to_notifications(
    config: Union[Dict[str, Any], Any],
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
    results: Dict[str, bool] = {}
    max_accounts = config["MAX_ACCOUNTS_PER_CHANNEL"]

    if config["PUSH_WINDOW"]["ENABLED"]:
        push_manager = PushRecordManager(config)
        time_range_start = config["PUSH_WINDOW"]["TIME_RANGE"]["START"]
        time_range_end = config["PUSH_WINDOW"]["TIME_RANGE"]["END"]

        if not push_manager.is_in_time_range(time_range_start, time_range_end):
            now = utils.get_beijing_time()
            logger.info(f"推送窗口控制：当前时间 {now.strftime('%H:%M')} 不在推送时间窗口 ")
            return results

        if config["PUSH_WINDOW"]["ONCE_PER_DAY"]:
            if push_manager.has_pushed_today():
                logger.info("推送窗口控制：今天已推送过，跳过本次推送")
                return results
            else:
                logger.info("推送窗口控制：今天首次推送")

    report_data = prepare_report_data(config, stats, failed_ids, new_titles, id_to_name, mode)

    update_info_to_send = update_info if config["SHOW_VERSION_UPDATE"] else None

    # 发送到飞书（多账号）
    feishu_urls = parse_multi_account_config(config["FEISHU_WEBHOOK_URL"])
    if feishu_urls:
        feishu_urls = limit_accounts(feishu_urls, max_accounts, "飞书")
        feishu = FeishuNotifier(config)
        feishu_results = []
        for i, url in enumerate(feishu_urls):
            if url:
                account_label = f"账号{i+1}" if len(feishu_urls) > 1 else ""
                result = feishu.send(
                    url, report_data, report_type, update_info_to_send, proxy_url, mode, account_label
                )
                feishu_results.append(result)
        results["feishu"] = any(feishu_results) if feishu_results else False

    # 发送到钉钉（多账号）
    dingtalk_urls = parse_multi_account_config(config["DINGTALK_WEBHOOK_URL"])
    if dingtalk_urls:
        dingtalk_urls = limit_accounts(dingtalk_urls, max_accounts, "钉钉")
        dingtalk = DingTalkNotifier(config)
        dingtalk_results = []
        for i, url in enumerate(dingtalk_urls):
            if url:
                account_label = f"账号{i+1}" if len(dingtalk_urls) > 1 else ""
                result = dingtalk.send(
                    url, report_data, report_type, update_info_to_send, proxy_url, mode, account_label
                )
                dingtalk_results.append(result)
        results["dingtalk"] = any(dingtalk_results) if dingtalk_results else False

    # 发送到企业微信（多账号）
    wework_urls = parse_multi_account_config(config["WEWORK_WEBHOOK_URL"])
    if wework_urls:
        wework_urls = limit_accounts(wework_urls, max_accounts, "企业微信")
        wework = WeComNotifier(config)
        wework_results = []
        for i, url in enumerate(wework_urls):
            if url:
                account_label = f"账号{i+1}" if len(wework_urls) > 1 else ""
                result = wework.send(
                    url, report_data, report_type, update_info_to_send, proxy_url, mode, account_label
                )
                wework_results.append(result)
        results["wework"] = any(wework_results) if wework_results else False

    # 发送到 Telegram（多账号，需验证配对）
    telegram_tokens = parse_multi_account_config(config["TELEGRAM_BOT_TOKEN"])
    telegram_chat_ids = parse_multi_account_config(config["TELEGRAM_CHAT_ID"])
    if telegram_tokens and telegram_chat_ids:
        valid, count = validate_paired_configs(
            {"bot_token": telegram_tokens, "chat_id": telegram_chat_ids},
            "Telegram",
            required_keys=["bot_token", "chat_id"]
        )
        if valid and count > 0:
            telegram_tokens = limit_accounts(telegram_tokens, max_accounts, "Telegram")
            telegram_chat_ids = telegram_chat_ids[:len(telegram_tokens)]
            telegram_results = []
            for i in range(len(telegram_tokens)):
                token = telegram_tokens[i]
                chat_id = telegram_chat_ids[i]
                if token and chat_id:
                    account_label = f"账号{i+1}" if len(telegram_tokens) > 1 else ""
                    telegram = TelegramNotifier(config, token, chat_id)
                    result = telegram.send(
                        "", report_data, report_type, update_info_to_send, proxy_url, mode, account_label
                    )
                    telegram_results.append(result)
            results["telegram"] = any(telegram_results) if telegram_results else False

    # 发送到 ntfy（多账号，需验证配对）
    ntfy_server_url = config["NTFY_SERVER_URL"]
    ntfy_topics = parse_multi_account_config(config["NTFY_TOPIC"])
    ntfy_tokens = parse_multi_account_config(config["NTFY_TOKEN"])
    if ntfy_server_url and ntfy_topics:
        if ntfy_tokens and len(ntfy_tokens) != len(ntfy_topics):
            logger.error(f"❌ ntfy 配置错误：topic 数量({len(ntfy_topics)})与 token 数量({len(ntfy_tokens)})不一致，跳过 ntfy 推送")
        else:
            ntfy_topics = limit_accounts(ntfy_topics, max_accounts, "ntfy")
            if ntfy_tokens:
                ntfy_tokens = ntfy_tokens[:len(ntfy_topics)]
            ntfy_results = []
            for i, topic in enumerate(ntfy_topics):
                if topic:
                    token = get_account_at_index(ntfy_tokens, i, "") if ntfy_tokens else ""
                    account_label = f"账号{i+1}" if len(ntfy_topics) > 1 else ""
                    ntfy = NtfyNotifier(config, ntfy_server_url, topic, token)
                    result = ntfy.send(
                        "", report_data, report_type, update_info_to_send, proxy_url, mode, account_label
                    )
                    ntfy_results.append(result)
            results["ntfy"] = any(ntfy_results) if ntfy_results else False

    # 发送到 Bark（多账号）
    bark_urls = parse_multi_account_config(config["BARK_URL"])
    if bark_urls:
        bark_urls = limit_accounts(bark_urls, max_accounts, "Bark")
        bark = BarkNotifier(config)
        bark_results = []
        for i, url in enumerate(bark_urls):
            if url:
                account_label = f"账号{i+1}" if len(bark_urls) > 1 else ""
                result = bark.send(
                    url, report_data, report_type, update_info_to_send, proxy_url, mode, account_label
                )
                bark_results.append(result)
        results["bark"] = any(bark_results) if bark_results else False

    # 发送到 Slack（多账号）
    slack_urls = parse_multi_account_config(config["SLACK_WEBHOOK_URL"])
    if slack_urls:
        slack_urls = limit_accounts(slack_urls, max_accounts, "Slack")
        slack = SlackNotifier(config)
        slack_results = []
        for i, url in enumerate(slack_urls):
            if url:
                account_label = f"账号{i+1}" if len(slack_urls) > 1 else ""
                result = slack.send(
                    url, report_data, report_type, update_info_to_send, proxy_url, mode, account_label
                )
                slack_results.append(result)
        results["slack"] = any(slack_results) if slack_results else False

    # 发送邮件
    email_from = config["EMAIL_FROM"]
    email_password = config["EMAIL_PASSWORD"]
    email_to = config["EMAIL_TO"]
    email_smtp_server = config.get("EMAIL_SMTP_SERVER", "")
    email_smtp_port = config.get("EMAIL_SMTP_PORT", "")
    if email_from and email_password and email_to:
        email = EmailNotifier(config)
        results["email"] = email.send(
            email_from,
            email_password,
            email_to,
            report_type,
            html_file_path,
            email_smtp_server,
            email_smtp_port,
        )

    if not results:
        logger.info("未配置任何通知渠道，跳过通知发送")

    if (
        config["PUSH_WINDOW"]["ENABLED"]
        and config["PUSH_WINDOW"]["ONCE_PER_DAY"]
        and any(results.values())
    ):
        push_manager = PushRecordManager(config)
        push_manager.record_push(report_type)

    return results


def prepare_report_data(
    config: Union[Dict[str, Any], Any],
    stats: List[Dict],
    failed_ids: Optional[List] = None,
    new_titles: Optional[Dict] = None,
    id_to_name: Optional[Dict] = None,
    mode: str = "daily",
) -> Dict[str, Any]:
    """准备报告数据"""
    processed_new_titles = []

    hide_new_section = mode == "incremental"

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
                        "rank_threshold": config["RANK_THRESHOLD"],
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
