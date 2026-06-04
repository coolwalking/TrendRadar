# coding=utf-8
"""
通知调度器模块

提供统一的通知分发接口。
支持所有通知渠道的多账号配置，使用 `;` 分隔多个账号。

使用示例:
    dispatcher = NotificationDispatcher(config, get_time_func, split_content_func)
    results = dispatcher.dispatch_all(report_data, report_type, ...)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

from trendradar.core.config import (
    get_account_at_index,
    limit_accounts,
    parse_multi_account_config,
    validate_paired_configs,
)
from trendradar.telegram_bot.access import build_telegram_access_config

from .senders import (
    resolve_report_attachment_path,
    send_telegram_document,
    send_to_bark,
    send_to_dingtalk,
    send_to_email,
    send_to_feishu,
    send_to_ntfy,
    send_to_slack,
    send_to_telegram,
    send_to_wework,
    send_to_generic_webhook,
    should_apply_realtime_alert_gate,
)


# 类型检查时导入，运行时不导入（避免循环导入）
if TYPE_CHECKING:
    from trendradar.ai import AIAnalysisResult, AITranslator


class _DeferredAlertStateStore:
    """Delay alert_state commits until all Telegram receivers have been tried."""

    def __init__(self, store: Any):
        self._store = store
        self._pending: List[Tuple[List[Tuple[str, Dict[str, Any]]], Any]] = []

    def get(self, key: str):
        return self._store.get(key)

    def has_pending(self) -> bool:
        """本轮 fanout 是否已有 receiver 真的推送了 alert items（尚未 flush 清算前的事实源）。

        语义精确说明（供附件 gate 复用）：返回 True 当且仅当至少有一个 receiver
        调用过 commit()，而 commit() 仅在 senders.send_to_telegram 成功 POST 异常提醒后
        才被调用、且 pushed_items 非空。因此 True == 「至少一个 receiver 收到了带 item 的
        异常提醒消息」——这正是附件 gate 想要的判定。
        这里**不**反映 flush() 去重后最终持久化的 topic 数量（topic_key 为空串等边界会被
        dedupe 跳过）：附件是否发送只取决于「用户是否真的收到了 alert」，与 cooldown 落盘
        的 topic 计数无关，故二者刻意解耦。
        """
        return bool(self._pending)

    def commit(self, pushed_items: List, now) -> bool:
        if pushed_items:
            self._pending.append((list(pushed_items), now))
        return True

    def flush(self) -> bool:
        """Commit one deduped topic set after receiver fanout completes."""
        if not self._pending:
            return False
        from trendradar.ai.alert_state import topic_key

        merged = []
        seen = set()
        # All receivers in one dispatcher fanout share the same rendered alert round.
        # Use the first successful send timestamp so cooldown state matches that round,
        # not the wall-clock time after slower receiver attempts.
        commit_time = self._pending[0][1]
        for items, _ in self._pending:
            for label, item in items:
                key = topic_key(item.get("topic", ""))
                dedupe_key = (label, key)
                if not key or dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                merged.append((label, item))
        if not merged:
            return False
        return bool(self._store.commit(merged, commit_time))


class NotificationDispatcher:
    """
    统一的多账号通知调度器

    将多账号发送逻辑封装，提供简洁的 dispatch_all 接口。
    内部处理账号解析、数量限制、配对验证等逻辑。
    """

    def __init__(
        self,
        config: Dict[str, Any],
        get_time_func: Callable,
        split_content_func: Callable,
        translator: Optional["AITranslator"] = None,
        storage_backend: Any = None,
        attachment_output_dir: str = "output",
    ):
        """
        初始化通知调度器

        Args:
            config: 完整的配置字典，包含所有通知渠道的配置
            get_time_func: 获取当前时间的函数
            split_content_func: 内容分批函数
            translator: AI 翻译器实例（可选）
            storage_backend: 存储后端（可选，用于 environment 实时提醒的 cooldown 状态持久化）
            attachment_output_dir: 发布根所在的输出目录（用于解析 public/{group}/full.html 附件路径），
                与 generate_html_report 的 output_dir 保持一致；可在测试中覆盖
        """
        self.config = config
        self.get_time_func = get_time_func
        self.split_content_func = split_content_func
        self.max_accounts = config.get("MAX_ACCOUNTS_PER_CHANNEL", 3)
        self.translator = translator
        self.storage_backend = storage_backend
        self.attachment_output_dir = attachment_output_dir

    def _telegram_access_config(self) -> Dict[str, Any]:
        access = self.config.get("TELEGRAM_ACCESS")
        if access:
            return access
        return build_telegram_access_config(
            {
                "TELEGRAM_CHAT_ID": self.config.get("TELEGRAM_CHAT_ID", ""),
                "TELEGRAM_OWNER_CHAT_IDS": self.config.get("TELEGRAM_OWNER_CHAT_IDS", ""),
                "TELEGRAM_RECEIVER_CHAT_IDS": self.config.get("TELEGRAM_RECEIVER_CHAT_IDS", ""),
                "TELEGRAM_COMMAND_CHAT_IDS": self.config.get("TELEGRAM_COMMAND_CHAT_IDS", ""),
                "TELEGRAM_COMMANDS_ENABLED": self.config.get("TELEGRAM_COMMANDS_ENABLED", False),
                "TELEGRAM_UNAUTHORIZED_BEHAVIOR": self.config.get(
                    "TELEGRAM_UNAUTHORIZED_BEHAVIOR", "ignore"
                ),
            }
        )

    def translate_content(
        self,
        report_data: Dict,
        rss_items: Optional[List[Dict]] = None,
        rss_new_items: Optional[List[Dict]] = None,
        standalone_data: Optional[Dict] = None,
        display_regions: Optional[Dict] = None,
        skip_rss: bool = False,
    ) -> tuple:
        """
        翻译推送内容

        Args:
            report_data: 报告数据
            rss_items: RSS 统计条目
            rss_new_items: RSS 新增条目
            standalone_data: 独立展示区数据
            display_regions: 区域显示配置（不展示的区域跳过翻译）
            skip_rss: 跳过 RSS 和独立展示区翻译（当数据已在上游翻译过时使用）

        Returns:
            tuple: (翻译后的 report_data, rss_items, rss_new_items, standalone_data)
        """
        if not self.translator or not self.translator.enabled:
            return report_data, rss_items, rss_new_items, standalone_data

        import copy
        print(f"[翻译] 开始翻译内容到 {self.translator.target_language}...")

        scope = self.translator.scope
        display_regions = display_regions or {}

        # 深拷贝避免修改原始数据
        report_data = copy.deepcopy(report_data)
        rss_items = copy.deepcopy(rss_items) if rss_items else None
        rss_new_items = copy.deepcopy(rss_new_items) if rss_new_items else None
        standalone_data = copy.deepcopy(standalone_data) if standalone_data else None

        # 收集所有需要翻译的标题
        titles_to_translate = []
        title_locations = []  # 记录标题位置，用于回填

        # 1. 热榜标题（scope 开启 且 区域展示）
        if scope.get("HOTLIST", True) and display_regions.get("HOTLIST", True):
            for stat_idx, stat in enumerate(report_data.get("stats", [])):
                for title_idx, title_data in enumerate(stat.get("titles", [])):
                    titles_to_translate.append(title_data.get("title", ""))
                    title_locations.append(("stats", stat_idx, title_idx))

            # 2. 新增热点标题
            for source_idx, source in enumerate(report_data.get("new_titles", [])):
                for title_idx, title_data in enumerate(source.get("titles", [])):
                    titles_to_translate.append(title_data.get("title", ""))
                    title_locations.append(("new_titles", source_idx, title_idx))

        # 3. RSS 统计标题（结构与 stats 一致：[{word, count, titles: [{title, ...}]}]）
        if not skip_rss and rss_items and scope.get("RSS", True) and display_regions.get("RSS", True):
            for stat_idx, stat in enumerate(rss_items):
                for title_idx, title_data in enumerate(stat.get("titles", [])):
                    titles_to_translate.append(title_data.get("title", ""))
                    title_locations.append(("rss_items", stat_idx, title_idx))

        # 4. RSS 新增标题（结构与 stats 一致）
        if not skip_rss and rss_new_items and scope.get("RSS", True) and display_regions.get("RSS", True) and display_regions.get("NEW_ITEMS", True):
            for stat_idx, stat in enumerate(rss_new_items):
                for title_idx, title_data in enumerate(stat.get("titles", [])):
                    titles_to_translate.append(title_data.get("title", ""))
                    title_locations.append(("rss_new_items", stat_idx, title_idx))

        # 5. 独立展示区 - 热榜平台
        if standalone_data and scope.get("STANDALONE", True) and display_regions.get("STANDALONE", False):
            for plat_idx, platform in enumerate(standalone_data.get("platforms", [])):
                for item_idx, item in enumerate(platform.get("items", [])):
                    titles_to_translate.append(item.get("title", ""))
                    title_locations.append(("standalone_platforms", plat_idx, item_idx))

            # 6. 独立展示区 - RSS 源（跳过已翻译的）
            if not skip_rss:
                for feed_idx, feed in enumerate(standalone_data.get("rss_feeds", [])):
                    for item_idx, item in enumerate(feed.get("items", [])):
                        titles_to_translate.append(item.get("title", ""))
                        title_locations.append(("standalone_rss", feed_idx, item_idx))

        if not titles_to_translate:
            print("[翻译] 没有需要翻译的内容")
            return report_data, rss_items, rss_new_items, standalone_data

        print(f"[翻译] 共 {len(titles_to_translate)} 条标题待翻译")

        # 批量翻译
        result = self.translator.translate_batch(titles_to_translate)

        if result.success_count == 0:
            print(f"[翻译] 翻译失败: {result.results[0].error if result.results else '未知错误'}")
            return report_data, rss_items, rss_new_items, standalone_data

        print(f"[翻译] 翻译完成: {result.success_count}/{result.total_count} 成功")

        # debug 模式：输出完整 prompt、AI 原始响应、逐条对照
        if self.config.get("DEBUG", False):
            if result.prompt:
                print(f"[翻译][DEBUG] === 发送给 AI 的 Prompt ===")
                print(result.prompt)
                print(f"[翻译][DEBUG] === Prompt 结束 ===")
            if result.raw_response:
                print(f"[翻译][DEBUG] === AI 原始响应 ===")
                print(result.raw_response)
                print(f"[翻译][DEBUG] === 响应结束 ===")
            # 行数不匹配警告
            expected = len(titles_to_translate)
            if result.parsed_count != expected:
                print(f"[翻译][DEBUG] ⚠️ 行数不匹配：期望 {expected} 条，AI 返回 {result.parsed_count} 条")
            # 逐条对照
            unchanged_count = 0
            for i, res in enumerate(result.results):
                if not res.success and res.error:
                    print(f"[翻译][DEBUG] [{i+1}] !! 失败: {res.error}")
                elif res.original_text == res.translated_text:
                    unchanged_count += 1
                else:
                    print(f"[翻译][DEBUG] [{i+1}] {res.original_text} => {res.translated_text}")
            if unchanged_count > 0:
                print(f"[翻译][DEBUG] （另有 {unchanged_count} 条未变化，已省略）")

        # 回填翻译结果（仅在翻译文本非空时替换，防止空翻译覆盖原始标题）
        for i, (loc_type, idx1, idx2) in enumerate(title_locations):
            if i < len(result.results) and result.results[i].success:
                translated = result.results[i].translated_text
                if not translated or not translated.strip():
                    continue
                if loc_type == "stats":
                    report_data["stats"][idx1]["titles"][idx2]["title"] = translated
                elif loc_type == "new_titles":
                    report_data["new_titles"][idx1]["titles"][idx2]["title"] = translated
                elif loc_type == "rss_items" and rss_items:
                    rss_items[idx1]["titles"][idx2]["title"] = translated
                elif loc_type == "rss_new_items" and rss_new_items:
                    rss_new_items[idx1]["titles"][idx2]["title"] = translated
                elif loc_type == "standalone_platforms" and standalone_data:
                    standalone_data["platforms"][idx1]["items"][idx2]["title"] = translated
                elif loc_type == "standalone_rss" and standalone_data:
                    standalone_data["rss_feeds"][idx1]["items"][idx2]["title"] = translated

        return report_data, rss_items, rss_new_items, standalone_data

    def dispatch_all(
        self,
        report_data: Dict,
        report_type: str,
        update_info: Optional[Dict] = None,
        proxy_url: Optional[str] = None,
        mode: str = "daily",
        html_file_path: Optional[str] = None,
        rss_items: Optional[List[Dict]] = None,
        rss_new_items: Optional[List[Dict]] = None,
        ai_analysis: Optional[AIAnalysisResult] = None,
        standalone_data: Optional[Dict] = None,
        skip_translation: bool = False,
    ) -> Dict[str, bool]:
        """
        分发通知到所有已配置的渠道（支持热榜+RSS合并推送+AI分析+独立展示区）

        Args:
            report_data: 报告数据（由 prepare_report_data 生成）
            report_type: 报告类型（如 "全天汇总"、"当前榜单"、"增量分析"）
            update_info: 版本更新信息（可选）
            proxy_url: 代理 URL（可选）
            mode: 报告模式 (daily/current/incremental)
            html_file_path: HTML 报告文件路径（邮件使用）
            rss_items: RSS 统计条目列表（用于 RSS 统计区块）
            rss_new_items: RSS 新增条目列表（用于 RSS 新增区块）
            ai_analysis: AI 分析结果（可选）
            standalone_data: 独立展示区数据（可选）
            skip_translation: 跳过翻译（当数据已在上游翻译过时使用）

        Returns:
            Dict[str, bool]: 每个渠道的发送结果，key 为渠道名，value 为是否成功
        """
        results = {}

        # 获取区域显示配置
        display_regions = self.config.get("DISPLAY", {}).get("REGIONS", {})

        # 执行翻译（如果启用，根据 display_regions 跳过不展示的区域）
        # skip_translation=True 时，RSS 已在上游翻译过，跳过 RSS 重复翻译
        if not skip_translation:
            report_data, rss_items, rss_new_items, standalone_data = self.translate_content(
                report_data, rss_items, rss_new_items, standalone_data, display_regions
            )
        else:
            # RSS 已翻译，仅翻译热榜 report_data 和独立展示区热榜部分
            report_data, _, _, standalone_data = self.translate_content(
                report_data, standalone_data=standalone_data, display_regions=display_regions,
                skip_rss=True,
            )

        # 飞书
        if self.config.get("FEISHU_WEBHOOK_URL"):
            results["feishu"] = self._send_feishu(
                report_data, report_type, update_info, proxy_url, mode, rss_items, rss_new_items,
                ai_analysis, display_regions, standalone_data
            )

        # 钉钉
        if self.config.get("DINGTALK_WEBHOOK_URL"):
            results["dingtalk"] = self._send_dingtalk(
                report_data, report_type, update_info, proxy_url, mode, rss_items, rss_new_items,
                ai_analysis, display_regions, standalone_data
            )

        # 企业微信
        if self.config.get("WEWORK_WEBHOOK_URL"):
            results["wework"] = self._send_wework(
                report_data, report_type, update_info, proxy_url, mode, rss_items, rss_new_items,
                ai_analysis, display_regions, standalone_data
            )

        # Telegram
        telegram_receivers = self._telegram_access_config().get("receiver_chat_ids", [])
        if self.config.get("TELEGRAM_BOT_TOKEN") and telegram_receivers:
            results["telegram"] = self._send_telegram(
                report_data, report_type, update_info, proxy_url, mode, rss_items, rss_new_items,
                ai_analysis, display_regions, standalone_data, html_file_path
            )

        # ntfy（需要配对验证）
        if self.config.get("NTFY_SERVER_URL") and self.config.get("NTFY_TOPIC"):
            results["ntfy"] = self._send_ntfy(
                report_data, report_type, update_info, proxy_url, mode, rss_items, rss_new_items,
                ai_analysis, display_regions, standalone_data
            )

        # Bark
        if self.config.get("BARK_URL"):
            results["bark"] = self._send_bark(
                report_data, report_type, update_info, proxy_url, mode, rss_items, rss_new_items,
                ai_analysis, display_regions, standalone_data
            )

        # Slack
        if self.config.get("SLACK_WEBHOOK_URL"):
            results["slack"] = self._send_slack(
                report_data, report_type, update_info, proxy_url, mode, rss_items, rss_new_items,
                ai_analysis, display_regions, standalone_data
            )

        # 通用 Webhook
        if self.config.get("GENERIC_WEBHOOK_URL"):
            results["generic_webhook"] = self._send_generic_webhook(
                report_data, report_type, update_info, proxy_url, mode, rss_items, rss_new_items,
                ai_analysis, display_regions, standalone_data
            )

        # 邮件（保持原有逻辑，已支持多收件人，AI 分析已嵌入 HTML）
        if (
            self.config.get("EMAIL_FROM")
            and self.config.get("EMAIL_PASSWORD")
            and self.config.get("EMAIL_TO")
        ):
            results["email"] = self._send_email(report_type, html_file_path)

        return results

    def _send_to_multi_accounts(
        self,
        channel_name: str,
        config_value: str,
        send_func: Callable[..., bool],
        **kwargs,
    ) -> bool:
        """
        通用多账号发送逻辑

        Args:
            channel_name: 渠道名称（用于日志和账号数量限制提示）
            config_value: 配置值（可能包含多个账号，用 ; 分隔）
            send_func: 发送函数，签名为 (account, account_label=..., **kwargs) -> bool
            **kwargs: 传递给发送函数的其他参数

        Returns:
            bool: 任一账号发送成功则返回 True
        """
        accounts = parse_multi_account_config(config_value)
        if not accounts:
            return False

        accounts = limit_accounts(accounts, self.max_accounts, channel_name)
        results = []

        for i, account in enumerate(accounts):
            if account:
                account_label = f"账号{i+1}" if len(accounts) > 1 else ""
                result = send_func(account, account_label=account_label, **kwargs)
                results.append(result)

        return any(results) if results else False

    def _apply_display_regions(
        self,
        report_data: Dict,
        display_regions: Optional[Dict],
        rss_items: Optional[List[Dict]] = None,
        rss_new_items: Optional[List[Dict]] = None,
        ai_analysis: Optional[AIAnalysisResult] = None,
        standalone_data: Optional[Dict] = None,
    ) -> tuple:
        """根据 display_regions 过滤各区域数据，返回 (report_data, rss_items, rss_new_items, ai_analysis, standalone_data)"""
        display_regions = display_regions or {}
        if not display_regions.get("HOTLIST", True):
            report_data = {"stats": [], "failed_ids": [], "new_titles": [], "id_to_name": {}}
        show_rss = display_regions.get("RSS", True)
        return (
            report_data,
            rss_items if show_rss else None,
            rss_new_items if (show_rss and display_regions.get("NEW_ITEMS", True)) else None,
            ai_analysis if display_regions.get("AI_ANALYSIS", True) else None,
            standalone_data if display_regions.get("STANDALONE", False) else None,
        )

    def _send_feishu(
        self,
        report_data: Dict,
        report_type: str,
        update_info: Optional[Dict],
        proxy_url: Optional[str],
        mode: str,
        rss_items: Optional[List[Dict]] = None,
        rss_new_items: Optional[List[Dict]] = None,
        ai_analysis: Optional[AIAnalysisResult] = None,
        display_regions: Optional[Dict] = None,
        standalone_data: Optional[Dict] = None,
    ) -> bool:
        """发送到飞书（多账号，支持热榜+RSS合并+AI分析+独立展示区）"""
        rd, ri, rn, ai, sd = self._apply_display_regions(
            report_data, display_regions, rss_items, rss_new_items, ai_analysis, standalone_data
        )

        return self._send_to_multi_accounts(
            channel_name="飞书",
            config_value=self.config["FEISHU_WEBHOOK_URL"],
            send_func=lambda url, account_label: send_to_feishu(
                webhook_url=url,
                report_data=rd,
                report_type=report_type,
                update_info=update_info,
                proxy_url=proxy_url,
                mode=mode,
                account_label=account_label,
                batch_size=self.config.get("FEISHU_BATCH_SIZE", 29000),
                batch_interval=self.config.get("BATCH_SEND_INTERVAL", 1.0),
                split_content_func=self.split_content_func,
                get_time_func=self.get_time_func,
                rss_items=ri,
                rss_new_items=rn,
                ai_analysis=ai,
                display_regions=display_regions or {},
                standalone_data=sd,
            ),
        )

    def _send_dingtalk(
        self,
        report_data: Dict,
        report_type: str,
        update_info: Optional[Dict],
        proxy_url: Optional[str],
        mode: str,
        rss_items: Optional[List[Dict]] = None,
        rss_new_items: Optional[List[Dict]] = None,
        ai_analysis: Optional[AIAnalysisResult] = None,
        display_regions: Optional[Dict] = None,
        standalone_data: Optional[Dict] = None,
    ) -> bool:
        """发送到钉钉（多账号，支持热榜+RSS合并+AI分析+独立展示区）"""
        rd, ri, rn, ai, sd = self._apply_display_regions(
            report_data, display_regions, rss_items, rss_new_items, ai_analysis, standalone_data
        )

        return self._send_to_multi_accounts(
            channel_name="钉钉",
            config_value=self.config["DINGTALK_WEBHOOK_URL"],
            send_func=lambda url, account_label: send_to_dingtalk(
                webhook_url=url,
                report_data=rd,
                report_type=report_type,
                update_info=update_info,
                proxy_url=proxy_url,
                mode=mode,
                account_label=account_label,
                batch_size=self.config.get("DINGTALK_BATCH_SIZE", 20000),
                batch_interval=self.config.get("BATCH_SEND_INTERVAL", 1.0),
                split_content_func=self.split_content_func,
                rss_items=ri,
                rss_new_items=rn,
                ai_analysis=ai,
                display_regions=display_regions or {},
                standalone_data=sd,
            ),
        )

    def _send_wework(
        self,
        report_data: Dict,
        report_type: str,
        update_info: Optional[Dict],
        proxy_url: Optional[str],
        mode: str,
        rss_items: Optional[List[Dict]] = None,
        rss_new_items: Optional[List[Dict]] = None,
        ai_analysis: Optional[AIAnalysisResult] = None,
        display_regions: Optional[Dict] = None,
        standalone_data: Optional[Dict] = None,
    ) -> bool:
        """发送到企业微信（多账号，支持热榜+RSS合并+AI分析+独立展示区）"""
        rd, ri, rn, ai, sd = self._apply_display_regions(
            report_data, display_regions, rss_items, rss_new_items, ai_analysis, standalone_data
        )

        return self._send_to_multi_accounts(
            channel_name="企业微信",
            config_value=self.config["WEWORK_WEBHOOK_URL"],
            send_func=lambda url, account_label: send_to_wework(
                webhook_url=url,
                report_data=rd,
                report_type=report_type,
                update_info=update_info,
                proxy_url=proxy_url,
                mode=mode,
                account_label=account_label,
                batch_size=self.config.get("MESSAGE_BATCH_SIZE", 4000),
                batch_interval=self.config.get("BATCH_SEND_INTERVAL", 1.0),
                msg_type=self.config.get("WEWORK_MSG_TYPE", "markdown"),
                split_content_func=self.split_content_func,
                rss_items=ri,
                rss_new_items=rn,
                ai_analysis=ai,
                display_regions=display_regions or {},
                standalone_data=sd,
            ),
        )

    def _send_telegram(
        self,
        report_data: Dict,
        report_type: str,
        update_info: Optional[Dict],
        proxy_url: Optional[str],
        mode: str,
        rss_items: Optional[List[Dict]] = None,
        rss_new_items: Optional[List[Dict]] = None,
        ai_analysis: Optional[AIAnalysisResult] = None,
        display_regions: Optional[Dict] = None,
        standalone_data: Optional[Dict] = None,
        html_file_path: Optional[str] = None,
    ) -> bool:
        """发送到 Telegram（旧多账号兼容；新 receiver 白名单 fanout）"""
        report_data, rss_items, rss_new_items, ai_analysis, standalone_data = self._apply_display_regions(
            report_data, display_regions, rss_items, rss_new_items, ai_analysis, standalone_data
        )
        display_regions = display_regions or {}

        telegram_tokens = parse_multi_account_config(self.config["TELEGRAM_BOT_TOKEN"])
        legacy_chat_ids = parse_multi_account_config(self.config.get("TELEGRAM_CHAT_ID", ""))
        telegram_access = self._telegram_access_config()
        receiver_chat_ids = list(telegram_access.get("receiver_chat_ids") or [])

        if not telegram_tokens or not receiver_chat_ids:
            return False

        has_new_access_targets = bool(
            self.config.get("TELEGRAM_OWNER_CHAT_IDS")
            or self.config.get("TELEGRAM_RECEIVER_CHAT_IDS")
        )
        targets = []
        if not has_new_access_targets:
            valid, count = validate_paired_configs(
                {"bot_token": telegram_tokens, "chat_id": legacy_chat_ids},
                "Telegram",
                required_keys=["bot_token", "chat_id"],
            )
            if not valid or count == 0:
                return False
            telegram_tokens = limit_accounts(telegram_tokens, self.max_accounts, "Telegram")
            legacy_chat_ids = legacy_chat_ids[: len(telegram_tokens)]
            targets = list(zip(telegram_tokens, legacy_chat_ids))
        else:
            # 新 access 模式：单 bot fan-out。所有 receiver 都由第一个 bot_token 发送，
            # 不做「多 bot × 多 chat」的配对 routing（那条路径仅在未启用新 owner/receiver
            # 白名单时保留，见上方 if 分支）。这意味着旧的多 bot 配置一旦叠加新白名单，
            # 第二个及之后的 bot_token 不再参与发送——若某个 receiver 只在第二个 bot 上
            # 建立过会话，用第一个 token 发会被 Telegram 拒投。本阶段不扩展多 bot routing，
            # 仅在下方打印明确告警，由用户决定是否迁移到单 bot。
            token = next((item for item in telegram_tokens if item), "")
            if not token:
                return False
            non_empty_tokens = [item for item in telegram_tokens if item]
            if len(non_empty_tokens) > 1:
                print(
                    "Telegram 配置了多个 bot_token，但新 owner/receiver 白名单为单 bot fan-out；"
                    f"将只用第一个 bot_token 发送给全部 {len(receiver_chat_ids)} 个 receiver，"
                    "其余 bot_token 不参与发送"
                )
                if legacy_chat_ids:
                    print(
                        "Telegram 同时配置了旧 TELEGRAM_CHAT_ID 多账号配对与新 owner/receiver 白名单；"
                        "旧的 token↔chat 配对已失效，仅在某 receiver 只在非首个 bot 上建立过会话时"
                        "才会出现投递失败——如需多 bot 请勿启用新白名单"
                    )
            targets = [(token, chat_id) for chat_id in receiver_chat_ids]

        # environment 实时提醒的 cooldown 状态存储（仅在启用且有存储后端时构造，惰性加载）。
        # 多账号共享 topic 级状态：同议题对所有账号一次冷却（MVP 取舍）。classic 风格不会触达。
        alert_config = self.config.get("ALERT", {})
        report_style = getattr(ai_analysis, "report_style", "classic") if ai_analysis else "classic"
        alert_state_store = None
        if (
            self.storage_backend is not None
            and alert_config.get("ENABLED", True)
            and should_apply_realtime_alert_gate(report_style, mode)
        ):
            from trendradar.ai.alert_state import AlertStateStore

            alert_state_store = AlertStateStore(
                self.storage_backend,
                state_ttl_days=alert_config.get("STATE_TTL_DAYS", 14),
                cooldown_minutes=alert_config.get("COOLDOWN_MINUTES", 180),
            )

        deferred_store = _DeferredAlertStateStore(alert_state_store) if alert_state_store else None
        results = []
        # 记录每个 target 的文本是否成功，供附件 fanout「仅对文本成功的 receiver 附 HTML」使用
        text_ok_targets: List[Tuple[str, str, bool]] = []
        for i, (token, chat_id) in enumerate(targets):
            if token and chat_id:
                account_label = f"接收者{i+1}" if len(targets) > 1 else ""
                result = send_to_telegram(
                    bot_token=token,
                    chat_id=chat_id,
                    report_data=report_data,
                    report_type=report_type,
                    update_info=update_info,
                    proxy_url=proxy_url,
                    mode=mode,
                    account_label=account_label,
                    batch_size=self.config.get("MESSAGE_BATCH_SIZE", 4000),
                    batch_interval=self.config.get("BATCH_SEND_INTERVAL", 1.0),
                    split_content_func=self.split_content_func,
                    rss_items=rss_items,
                    rss_new_items=rss_new_items,
                    ai_analysis=ai_analysis,
                    display_regions=display_regions,
                    standalone_data=standalone_data,
                    html_file_path=html_file_path,
                    get_time_func=self.get_time_func,
                    alert_state_store=deferred_store or alert_state_store,
                    alert_config=alert_config,
                )
                results.append(result)
                text_ok_targets.append((token, chat_id, bool(result)))

        # 必须在 flush 前记录「本轮 realtime 是否真的推了 alert items」：
        # flush 后 pending 语义不再可靠，会把冷却/静默成功误判为真实推送。
        had_realtime_alert_items = (
            deferred_store is not None and deferred_store.has_pending()
        )

        any_success = any(results) if results else False
        if any_success and deferred_store is not None:
            deferred_store.flush()
        if results and not all(results):
            failed_count = len([result for result in results if not result])
            print(f"Telegram {failed_count}/{len(results)} 个接收者发送失败，其它接收者已继续尝试")

        # 附件 fanout（增强项，严格在 flush 之后；失败不影响文本结果 any_success）
        self._maybe_send_telegram_attachments(
            text_ok_targets=text_ok_targets,
            mode=mode,
            report_style=report_style,
            report_type=report_type,
            proxy_url=proxy_url,
            had_realtime_alert_items=had_realtime_alert_items,
        )
        return any_success

    def _maybe_send_telegram_attachments(
        self,
        *,
        text_ok_targets: List[Tuple[str, str, bool]],
        mode: str,
        report_style: str,
        report_type: str,
        proxy_url: Optional[str],
        had_realtime_alert_items: bool,
    ) -> None:
        """文本推送成功后，可选地把 public/{group}/full.html 作为附件发给各 receiver。

        - 仅 environment 风格触发（classic 一律不附附件）；
        - realtime_alert：current/incremental 且本轮真的推了 alert（had_realtime_alert_items）；
        - daily_digest ：environment 每日简报（mode==daily）；
        - 仅对文本发送成功的 receiver 附 HTML；
        - 任何附件失败都只记录日志，绝不改变文本推送结果，也绝不触碰 alert_state。
        """
        cfg = self.config.get("TELEGRAM_ATTACHMENTS") or {}
        if not cfg.get("ENABLED"):
            return

        attach_on = set(cfg.get("ATTACH_ON") or [])
        is_env = report_style == "environment"
        eligible = False
        if is_env and mode in ("current", "incremental"):
            # current/incremental 改为轻量 dashboard，不再生成 public/current/full.html。
            # 打断式推送回退为纯文本简报（详情见 dashboard 网页），realtime_alert 的
            # HTML 附件已停用——避免尝试投递不存在的文件并产生误导性失败日志。
            eligible = False
        elif is_env and mode == "daily":
            eligible = "daily_digest" in attach_on
        if not eligible:
            return

        path = resolve_report_attachment_path(
            self.attachment_output_dir, mode, cfg.get("REPORT_KIND", "full")
        )

        group = "daily" if mode == "daily" else "current"
        ts = ""
        if self.get_time_func:
            try:
                ts = self.get_time_func().strftime("%Y%m%d-%H%M")
            except Exception:
                ts = ""
        filename = f"trendradar-{group}{('-' + ts) if ts else ''}.html"

        failure_behavior = cfg.get("FAILURE_BEHAVIOR", "warn")
        max_file_mb = cfg.get("MAX_FILE_MB", 8)

        ok = 0
        fail = 0
        for i, (token, chat_id, text_ok) in enumerate(text_ok_targets):
            if not (token and chat_id and text_ok):
                continue
            label = f"Telegram接收者{i+1}" if len(text_ok_targets) > 1 else "Telegram"
            sent = send_telegram_document(
                token,
                chat_id,
                path,
                filename=filename,
                proxy_url=proxy_url,
                max_file_mb=max_file_mb,
                log_prefix=label,
            )
            if sent:
                ok += 1
            else:
                fail += 1

        if ok or fail:
            summary = (
                f"Telegram 附件：{ok} 成功 / {fail} 失败"
                f"（共 {ok + fail} 个接收者）[{report_type}]"
            )
            if fail and failure_behavior != "silent":
                print("⚠️ " + summary)
            else:
                print(summary)

    def _send_ntfy(
        self,
        report_data: Dict,
        report_type: str,
        update_info: Optional[Dict],
        proxy_url: Optional[str],
        mode: str,
        rss_items: Optional[List[Dict]] = None,
        rss_new_items: Optional[List[Dict]] = None,
        ai_analysis: Optional[AIAnalysisResult] = None,
        display_regions: Optional[Dict] = None,
        standalone_data: Optional[Dict] = None,
    ) -> bool:
        """发送到 ntfy（多账号，需验证 topic 和 token 配对，支持热榜+RSS合并+AI分析+独立展示区）"""
        report_data, rss_items, rss_new_items, ai_analysis, standalone_data = self._apply_display_regions(
            report_data, display_regions, rss_items, rss_new_items, ai_analysis, standalone_data
        )
        display_regions = display_regions or {}

        ntfy_server_url = self.config["NTFY_SERVER_URL"]
        ntfy_topics = parse_multi_account_config(self.config["NTFY_TOPIC"])
        ntfy_tokens = parse_multi_account_config(self.config.get("NTFY_TOKEN", ""))

        if not ntfy_server_url or not ntfy_topics:
            return False

        if ntfy_tokens and len(ntfy_tokens) != len(ntfy_topics):
            print(
                f"❌ ntfy 配置错误：topic 数量({len(ntfy_topics)})与 token 数量({len(ntfy_tokens)})不一致，跳过 ntfy 推送"
            )
            return False

        ntfy_topics = limit_accounts(ntfy_topics, self.max_accounts, "ntfy")
        if ntfy_tokens:
            ntfy_tokens = ntfy_tokens[: len(ntfy_topics)]

        results = []
        for i, topic in enumerate(ntfy_topics):
            if topic:
                token = get_account_at_index(ntfy_tokens, i, "") if ntfy_tokens else ""
                account_label = f"账号{i+1}" if len(ntfy_topics) > 1 else ""
                result = send_to_ntfy(
                    server_url=ntfy_server_url,
                    topic=topic,
                    token=token,
                    report_data=report_data,
                    report_type=report_type,
                    update_info=update_info,
                    proxy_url=proxy_url,
                    mode=mode,
                    account_label=account_label,
                    batch_size=3800,
                    split_content_func=self.split_content_func,
                    rss_items=rss_items,
                    rss_new_items=rss_new_items,
                    ai_analysis=ai_analysis,
                    display_regions=display_regions,
                    standalone_data=standalone_data,
                )
                results.append(result)

        return any(results) if results else False

    def _send_bark(
        self,
        report_data: Dict,
        report_type: str,
        update_info: Optional[Dict],
        proxy_url: Optional[str],
        mode: str,
        rss_items: Optional[List[Dict]] = None,
        rss_new_items: Optional[List[Dict]] = None,
        ai_analysis: Optional[AIAnalysisResult] = None,
        display_regions: Optional[Dict] = None,
        standalone_data: Optional[Dict] = None,
    ) -> bool:
        """发送到 Bark（多账号，支持热榜+RSS合并+AI分析+独立展示区）"""
        rd, ri, rn, ai, sd = self._apply_display_regions(
            report_data, display_regions, rss_items, rss_new_items, ai_analysis, standalone_data
        )

        return self._send_to_multi_accounts(
            channel_name="Bark",
            config_value=self.config["BARK_URL"],
            send_func=lambda url, account_label: send_to_bark(
                bark_url=url,
                report_data=rd,
                report_type=report_type,
                update_info=update_info,
                proxy_url=proxy_url,
                mode=mode,
                account_label=account_label,
                batch_size=self.config.get("BARK_BATCH_SIZE", 3600),
                batch_interval=self.config.get("BATCH_SEND_INTERVAL", 1.0),
                split_content_func=self.split_content_func,
                rss_items=ri,
                rss_new_items=rn,
                ai_analysis=ai,
                display_regions=display_regions or {},
                standalone_data=sd,
            ),
        )

    def _send_slack(
        self,
        report_data: Dict,
        report_type: str,
        update_info: Optional[Dict],
        proxy_url: Optional[str],
        mode: str,
        rss_items: Optional[List[Dict]] = None,
        rss_new_items: Optional[List[Dict]] = None,
        ai_analysis: Optional[AIAnalysisResult] = None,
        display_regions: Optional[Dict] = None,
        standalone_data: Optional[Dict] = None,
    ) -> bool:
        """发送到 Slack（多账号，支持热榜+RSS合并+AI分析+独立展示区）"""
        rd, ri, rn, ai, sd = self._apply_display_regions(
            report_data, display_regions, rss_items, rss_new_items, ai_analysis, standalone_data
        )

        return self._send_to_multi_accounts(
            channel_name="Slack",
            config_value=self.config["SLACK_WEBHOOK_URL"],
            send_func=lambda url, account_label: send_to_slack(
                webhook_url=url,
                report_data=rd,
                report_type=report_type,
                update_info=update_info,
                proxy_url=proxy_url,
                mode=mode,
                account_label=account_label,
                batch_size=self.config.get("SLACK_BATCH_SIZE", 4000),
                batch_interval=self.config.get("BATCH_SEND_INTERVAL", 1.0),
                split_content_func=self.split_content_func,
                rss_items=ri,
                rss_new_items=rn,
                ai_analysis=ai,
                display_regions=display_regions or {},
                standalone_data=sd,
            ),
        )

    def _send_generic_webhook(
        self,
        report_data: Dict,
        report_type: str,
        update_info: Optional[Dict],
        proxy_url: Optional[str],
        mode: str,
        rss_items: Optional[List[Dict]] = None,
        rss_new_items: Optional[List[Dict]] = None,
        ai_analysis: Optional[AIAnalysisResult] = None,
        display_regions: Optional[Dict] = None,
        standalone_data: Optional[Dict] = None,
    ) -> bool:
        """发送到通用 Webhook（多账号，支持热榜+RSS合并+AI分析+独立展示区）"""
        report_data, rss_items, rss_new_items, ai_analysis, standalone_data = self._apply_display_regions(
            report_data, display_regions, rss_items, rss_new_items, ai_analysis, standalone_data
        )
        display_regions = display_regions or {}

        urls = parse_multi_account_config(self.config.get("GENERIC_WEBHOOK_URL", ""))
        templates = parse_multi_account_config(self.config.get("GENERIC_WEBHOOK_TEMPLATE", ""))

        if not urls:
            return False

        urls = limit_accounts(urls, self.max_accounts, "通用Webhook")
        results = []

        for i, url in enumerate(urls):
            if not url:
                continue

            template = ""
            if templates:
                if i < len(templates):
                    template = templates[i]
                elif len(templates) == 1:
                    template = templates[0]

            account_label = f"账号{i+1}" if len(urls) > 1 else ""

            result = send_to_generic_webhook(
                webhook_url=url,
                payload_template=template,
                report_data=report_data,
                report_type=report_type,
                update_info=update_info,
                proxy_url=proxy_url,
                mode=mode,
                account_label=account_label,
                batch_size=self.config.get("MESSAGE_BATCH_SIZE", 4000),
                batch_interval=self.config.get("BATCH_SEND_INTERVAL", 1.0),
                split_content_func=self.split_content_func,
                rss_items=rss_items,
                rss_new_items=rss_new_items,
                ai_analysis=ai_analysis,
                display_regions=display_regions,
                standalone_data=standalone_data,
            )
            results.append(result)

        return any(results) if results else False

    def _send_email(
        self,
        report_type: str,
        html_file_path: Optional[str],
    ) -> bool:
        """发送邮件（保持原有逻辑，已支持多收件人）

        Note:
            AI 分析内容已在 HTML 生成时嵌入，无需在此传递
        """
        return send_to_email(
            from_email=self.config["EMAIL_FROM"],
            password=self.config["EMAIL_PASSWORD"],
            to_email=self.config["EMAIL_TO"],
            report_type=report_type,
            html_file_path=html_file_path,
            custom_smtp_server=self.config.get("EMAIL_SMTP_SERVER", ""),
            custom_smtp_port=self.config.get("EMAIL_SMTP_PORT", ""),
            get_time_func=self.get_time_func,
        )
