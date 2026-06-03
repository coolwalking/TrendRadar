# coding=utf-8
"""
AI 分析结果格式化模块

将 AI 分析结果格式化为各推送渠道的样式
"""

import html as html_lib
import re
from .analyzer import AIAnalysisResult
from .evidence import LABELS, SECTION_ORDER, SUPPRESSED_BUCKETS, derive_radar_readout

ENV_TITLE = "🛰 信息环境异常监测日报"

# 呈现层：各监测栏目的用户可见标题（含阅读动作前缀）。顺序由 SECTION_ORDER 决定。
_SECTION_TITLES = {
    "cross_layer_verified": "① 优先看 · 跨层呼应",
    "high_heat_unverified": "② 隔离看 · 高热待核实",
    "chinese_only_hot": "③ 中文独热（中热缺外）",
    "silence_gap": "④ 沉默温差（外热中静）",
}


def _escape_html(text: str) -> str:
    """转义 HTML 特殊字符，防止 XSS 攻击"""
    return html_lib.escape(text) if text else ""


def _format_list_content(text: str) -> str:
    """
    格式化列表内容，确保序号前有换行
    例如将 "1. xxx 2. yyy" 转换为:
    1. xxx
    2. yyy
    """
    if not text:
        return ""
    
    # 去除首尾空白，防止 AI 返回的内容开头就有换行导致显示空行
    text = text.strip()

    # 0. 合并序号与紧随的【标签】（防御性处理）
    # 将 "1.\n【投资者】：" 或 "1. 【投资者】：" 合并为 "1. 投资者："
    text = re.sub(r'(\d+\.)\s*【([^】]+)】([:：]?)', r'\1 \2：', text)

    # 1. 规范化：确保 "1." 后面有空格
    result = re.sub(r'(\d+)\.([^ \d])', r'\1. \2', text)

    # 2. 强制换行：匹配 "数字."，且前面不是换行符
    #    (?!\d) 排除版本号/小数（如 2.0、3.5），避免将其误判为列表序号
    result = re.sub(r'(?<=[^\n])\s+(\d+\.)(?!\d)', r'\n\1', result)
    
    # 3. 处理 "1.**粗体**" 这种情况（虽然 Prompt 要求不输出 Markdown，但防御性处理）
    result = re.sub(r'(?<=[^\n])(\d+\.\*\*)', r'\n\1', result)

    # 4. 处理中文标点后的换行（排除版本号/小数）
    result = re.sub(r'([：:;,。；，])\s*(\d+\.)(?!\d)', r'\1\n\2', result)

    # 5. 处理 "XX方面："、"XX领域：" 等子标题换行
    # 只有在中文标点（句号、逗号、分号等）后才触发换行，避免破坏 "1. XX领域：" 格式
    result = re.sub(r'([。！？；，、])\s*([a-zA-Z0-9\u4e00-\u9fa5]+(方面|领域)[:：])', r'\1\n\2', result)

    # 6. 处理 【标签】 格式
    # 6a. 标签前确保空行分隔（文本开头除外）
    result = re.sub(r'(?<=\S)\n*(【[^】]+】)', r'\n\n\1', result)
    # 6b. 合并标签与被换行拆开的冒号：【tag】\n： → 【tag】：
    result = re.sub(r'(【[^】]+】)\n+([:：])', r'\1\2', result)
    # 6c. 标签后（含可选冒号），如果紧跟非空白非冒号内容则另起一行
    # 用 (?=[^\s:：]) 避免正则回溯将冒号误判为"内容"而拆开 【tag】：
    result = re.sub(r'(【[^】]+】[:：]?)[ \t]*(?=[^\s:：])', r'\1\n', result)

    # 7. 在列表项之间增加视觉空行（排除版本号/小数）
    # 排除 【标签】 行（以】结尾）和子标题行（以冒号结尾）之后的情况，避免标题与首项之间出现空行
    result = re.sub(r'(?<![:：】])\n(\d+\.)(?!\d)', r'\n\n\1', result)

    return result


def _format_standalone_summaries(summaries: dict) -> str:
    """格式化独立展示区概括为纯文本行，每个源名称单独一行"""
    if not summaries:
        return ""
    lines = []
    for source_name, summary in summaries.items():
        if summary:
            lines.append(f"[{source_name}]:\n{summary}")
    return "\n\n".join(lines)


# ════════════════════════════════════════════════════════════════
# 信息环境异常监测（environment 风格）渲染
# ════════════════════════════════════════════════════════════════

def _env_item_lines(item: dict) -> str:
    """单条监测议题的纯文本块（标题/概述/层级/平台/热度/研判/风险）。"""
    lines = []
    topic = item.get("topic", "")
    vs = item.get("verification_status", "")
    flag = "（含情绪信号）" if item.get("sentiment_flag") else ""
    lines.append(f"▸ {topic}（{vs}）{flag}")

    summary = (item.get("summary") or "").strip()
    if summary:
        lines.append(summary)

    meta = []
    if item.get("source_layers") and item["source_layers"] != "-":
        meta.append(f"来源层级 {item['source_layers']}")
    plats = item.get("platforms", "")
    if plats and plats != "-":
        meta.append(f"平台 {plats}（{item.get('platform_count', 0)}）")
    if item.get("highest_heat") and item["highest_heat"] != "-":
        meta.append(f"最高热度 {item['highest_heat']}")
    if meta:
        lines.append(" ｜ ".join(meta))

    analysis = (item.get("analysis") or "").strip()
    if analysis:
        lines.append(f"研判：{analysis}")

    risk = item.get("risk_note") or item.get("factual_boundary")
    if risk:
        lines.append(f"⚠ {risk}")
    return "\n".join(lines)


def _render_radar_header(overview_stats: dict) -> str:
    """今日盘面：四行程序写死的雷达读数（密度 / 热度↔证据错位 / 中外温差 / 层级覆盖）。"""
    r = derive_radar_readout(overview_stats or {})
    ly = r["layers"]
    return "\n".join([
        f"信号密度：异常 {r['anomaly']} 条 ｜ 已抑制 {r['suppressed']} 条",
        f"热度↔证据：跨层呼应 {r['cross_layer']} ｜ 高热待核实(无呼应) {r['high_heat']}",
        f"中外温差：沉默温差 {r['silence_gap']}（外热中静）｜ 中文独热 {r['chinese_only']}（中热缺外）",
        f"层级覆盖：A {ly['A']} ｜ B {ly['B']} ｜ C {ly['C']} ｜ D {ly['D']}",
    ])


def _environment_blocks(result: AIAnalysisResult):
    """返回有序的 (标题, 正文文本) 列表，供各渠道按自身 heading 语法渲染。

    结构：今日盘面 → 四个监测栏目（按阅读动作排序，不含情绪）→ 已抑制 → 方法说明。
    情绪降为属性：低热情绪项折叠进"已抑制"，并以「含情绪信号」标注。
    """
    blocks = []

    # 1. 今日盘面（程序写死数字，AI overview 文字作为补充置于其上）
    overview = (result.overview or "").strip()
    radar = _render_radar_header(result.overview_stats or {})
    ov_body = (overview + "\n" if overview else "") + radar
    blocks.append(("今日盘面", ov_body))

    # 2. 监测栏目（按"该怎么对待它"排序；情绪不单独成栏）
    for label in SECTION_ORDER:
        items = getattr(result, label, []) or []
        if not items:
            continue
        title = _SECTION_TITLES.get(label, LABELS[label]["title"])
        body = "\n\n".join(_env_item_lines(it) for it in items)
        blocks.append((title, body))

    # 3. 已抑制（未达异常阈值）：背景提示 + 低热情绪项（折叠，情绪作为属性，语气最弱）
    suppressed_lines = []
    for n in (result.background_notes or []):
        suppressed_lines.append(f"· {n}")
    for label in SUPPRESSED_BUCKETS:
        for it in (getattr(result, label, []) or []):
            flag = "（含情绪信号）" if it.get("sentiment_flag") else ""
            layers = it.get("source_layers", "-")
            suppressed_lines.append(f"· {it.get('topic', '')}（{layers}）{flag}")
    if suppressed_lines:
        blocks.append(("⑤ 已抑制 · 未达异常阈值", "\n".join(suppressed_lines)))

    # 4. 方法说明
    if result.method_note:
        blocks.append(("方法说明", result.method_note))

    return blocks


def _render_env_simple(result: AIAnalysisResult, heading_prefix: str, heading_suffix: str,
                       top_title: str) -> str:
    """通用文本渲染：heading_prefix + 标题 + heading_suffix。"""
    lines = [f"{heading_prefix}{top_title}{heading_suffix}", ""]
    for heading, body in _environment_blocks(result):
        lines.append(f"{heading_prefix}{heading}{heading_suffix}")
        lines.append(body)
        lines.append("")
    return "\n".join(lines).rstrip()


def _render_env_dingtalk(result: AIAnalysisResult) -> str:
    lines = [f"### {ENV_TITLE}", ""]
    for heading, body in _environment_blocks(result):
        lines.append(f"#### {heading}")
        lines.append(body)
        lines.append("")
    return "\n".join(lines).rstrip()


def _render_env_plain(result: AIAnalysisResult) -> str:
    lines = [f"【{ENV_TITLE}】", ""]
    for heading, body in _environment_blocks(result):
        lines.append(f"[{heading}]")
        lines.append(body)
        lines.append("")
    return "\n".join(lines).rstrip()


def _render_env_telegram(result: AIAnalysisResult) -> str:
    lines = [f"<b>{_escape_html(ENV_TITLE)}</b>", ""]
    for heading, body in _environment_blocks(result):
        lines.append(f"<b>{_escape_html(heading)}</b>")
        lines.append(_escape_html(body))
        lines.append("")
    return "\n".join(lines).rstrip()


def _render_env_html_rich(result: AIAnalysisResult) -> str:
    ai_html = """
                <div class="ai-section">
                    <div class="ai-section-header">
                        <div class="ai-section-title">🛰 信息环境异常监测日报</div>
                        <span class="ai-section-badge">监测</span>
                    </div>
                    <div class="ai-blocks-grid">"""
    for heading, body in _environment_blocks(result):
        content_html = _escape_html(body).replace("\n", "<br>")
        ai_html += f"""
                    <div class="ai-block">
                        <div class="ai-block-title">{_escape_html(heading)}</div>
                        <div class="ai-block-content">{content_html}</div>
                    </div>"""
    ai_html += """
                    </div>
                </div>"""
    return ai_html


def render_ai_analysis_markdown(result: AIAnalysisResult) -> str:
    """渲染为通用 Markdown 格式（Telegram、企业微信、ntfy、Bark、Slack）"""
    if not result.success:
        if result.skipped:
            return f"ℹ️ {result.error}"
        return f"⚠️ AI 分析失败: {result.error}"

    if getattr(result, "report_style", "classic") == "environment":
        return _render_env_simple(result, "**", "**", ENV_TITLE)

    lines = ["**✨ AI 热点分析**", ""]

    if result.core_trends:
        lines.extend(["**核心热点态势**", _format_list_content(result.core_trends), ""])

    if result.sentiment_controversy:
        lines.extend(
            ["**舆论风向争议**", _format_list_content(result.sentiment_controversy), ""]
        )

    if result.signals:
        lines.extend(["**异动与弱信号**", _format_list_content(result.signals), ""])

    if result.rss_insights:
        lines.extend(
            ["**RSS 深度洞察**", _format_list_content(result.rss_insights), ""]
        )

    if result.outlook_strategy:
        lines.extend(
            ["**研判策略建议**", _format_list_content(result.outlook_strategy), ""]
        )

    if result.standalone_summaries:
        summaries_text = _format_standalone_summaries(result.standalone_summaries)
        if summaries_text:
            lines.extend(["**独立源点速览**", summaries_text])

    return "\n".join(lines)


def render_ai_analysis_feishu(result: AIAnalysisResult) -> str:
    """渲染为飞书卡片 Markdown 格式"""
    if not result.success:
        if result.skipped:
            return f"ℹ️ {result.error}"
        return f"⚠️ AI 分析失败: {result.error}"

    if getattr(result, "report_style", "classic") == "environment":
        return _render_env_simple(result, "**", "**", ENV_TITLE)

    lines = ["**✨ AI 热点分析**", ""]

    if result.core_trends:
        lines.extend(["**核心热点态势**", _format_list_content(result.core_trends), ""])

    if result.sentiment_controversy:
        lines.extend(
            ["**舆论风向争议**", _format_list_content(result.sentiment_controversy), ""]
        )

    if result.signals:
        lines.extend(["**异动与弱信号**", _format_list_content(result.signals), ""])

    if result.rss_insights:
        lines.extend(
            ["**RSS 深度洞察**", _format_list_content(result.rss_insights), ""]
        )

    if result.outlook_strategy:
        lines.extend(
            ["**研判策略建议**", _format_list_content(result.outlook_strategy), ""]
        )

    if result.standalone_summaries:
        summaries_text = _format_standalone_summaries(result.standalone_summaries)
        if summaries_text:
            lines.extend(["**独立源点速览**", summaries_text])

    return "\n".join(lines)


def render_ai_analysis_dingtalk(result: AIAnalysisResult) -> str:
    """渲染为钉钉 Markdown 格式"""
    if not result.success:
        if result.skipped:
            return f"ℹ️ {result.error}"
        return f"⚠️ AI 分析失败: {result.error}"

    if getattr(result, "report_style", "classic") == "environment":
        return _render_env_dingtalk(result)

    lines = ["### ✨ AI 热点分析", ""]

    if result.core_trends:
        lines.extend(
            ["#### 核心热点态势", _format_list_content(result.core_trends), ""]
        )

    if result.sentiment_controversy:
        lines.extend(
            [
                "#### 舆论风向争议",
                _format_list_content(result.sentiment_controversy),
                "",
            ]
        )

    if result.signals:
        lines.extend(["#### 异动与弱信号", _format_list_content(result.signals), ""])

    if result.rss_insights:
        lines.extend(
            ["#### RSS 深度洞察", _format_list_content(result.rss_insights), ""]
        )

    if result.outlook_strategy:
        lines.extend(
            ["#### 研判策略建议", _format_list_content(result.outlook_strategy), ""]
        )

    if result.standalone_summaries:
        summaries_text = _format_standalone_summaries(result.standalone_summaries)
        if summaries_text:
            lines.extend(["#### 独立源点速览", summaries_text])

    return "\n".join(lines)


def render_ai_analysis_plain(result: AIAnalysisResult) -> str:
    """渲染为纯文本格式"""
    if not result.success:
        if result.skipped:
            return result.error
        return f"AI 分析失败: {result.error}"

    if getattr(result, "report_style", "classic") == "environment":
        return _render_env_plain(result)

    lines = ["【✨ AI 热点分析】", ""]

    if result.core_trends:
        lines.extend(["[核心热点态势]", _format_list_content(result.core_trends), ""])

    if result.sentiment_controversy:
        lines.extend(
            ["[舆论风向争议]", _format_list_content(result.sentiment_controversy), ""]
        )

    if result.signals:
        lines.extend(["[异动与弱信号]", _format_list_content(result.signals), ""])

    if result.rss_insights:
        lines.extend(["[RSS 深度洞察]", _format_list_content(result.rss_insights), ""])

    if result.outlook_strategy:
        lines.extend(["[研判策略建议]", _format_list_content(result.outlook_strategy), ""])

    if result.standalone_summaries:
        summaries_text = _format_standalone_summaries(result.standalone_summaries)
        if summaries_text:
            lines.extend(["[独立源点速览]", summaries_text])

    return "\n".join(lines)


def render_ai_analysis_telegram(result: AIAnalysisResult) -> str:
    """渲染为 Telegram HTML 格式（配合 parse_mode: HTML）

    Telegram Bot API 的 HTML 模式仅支持有限标签：
    <b>, <i>, <u>, <s>, <code>, <pre>, <a href="">, <blockquote>
    换行直接使用 \\n，不支持 <br>, <div>, <h1>-<h6> 等标签。
    """
    if not result.success:
        if result.skipped:
            return f"ℹ️ {_escape_html(result.error)}"
        return f"⚠️ AI 分析失败: {_escape_html(result.error)}"

    if getattr(result, "report_style", "classic") == "environment":
        return _render_env_telegram(result)

    lines = ["<b>✨ AI 热点分析</b>", ""]

    if result.core_trends:
        lines.extend(["<b>核心热点态势</b>", _escape_html(_format_list_content(result.core_trends)), ""])

    if result.sentiment_controversy:
        lines.extend(["<b>舆论风向争议</b>", _escape_html(_format_list_content(result.sentiment_controversy)), ""])

    if result.signals:
        lines.extend(["<b>异动与弱信号</b>", _escape_html(_format_list_content(result.signals)), ""])

    if result.rss_insights:
        lines.extend(["<b>RSS 深度洞察</b>", _escape_html(_format_list_content(result.rss_insights)), ""])

    if result.outlook_strategy:
        lines.extend(["<b>研判策略建议</b>", _escape_html(_format_list_content(result.outlook_strategy)), ""])

    if result.standalone_summaries:
        summaries_text = _format_standalone_summaries(result.standalone_summaries)
        if summaries_text:
            lines.extend(["<b>独立源点速览</b>", _escape_html(summaries_text)])

    return "\n".join(lines)


def get_ai_analysis_renderer(channel: str):
    """根据渠道获取对应的渲染函数"""
    renderers = {
        "feishu": render_ai_analysis_feishu,
        "dingtalk": render_ai_analysis_dingtalk,
        "wework": render_ai_analysis_markdown,
        "telegram": render_ai_analysis_telegram,
        "email": render_ai_analysis_html_rich,  # 邮件使用丰富样式，配合 HTML 报告的 CSS
        "ntfy": render_ai_analysis_markdown,
        "bark": render_ai_analysis_plain,
        "slack": render_ai_analysis_markdown,
    }
    return renderers.get(channel, render_ai_analysis_markdown)


def render_ai_analysis_html_rich(result: AIAnalysisResult) -> str:
    """渲染为丰富样式的 HTML 格式（HTML 报告用）"""
    if not result:
        return ""

    # 检查是否成功
    if not result.success:
        if result.skipped:
            return f"""
                <div class="ai-section">
                    <div class="ai-info">ℹ️ {_escape_html(str(result.error))}</div>
                </div>"""
        error_msg = result.error or "未知错误"
        return f"""
                <div class="ai-section">
                    <div class="ai-warning">AI 分析失败: {_escape_html(str(error_msg))}</div>
                </div>"""

    if getattr(result, "report_style", "classic") == "environment":
        return _render_env_html_rich(result)

    ai_html = """
                <div class="ai-section">
                    <div class="ai-section-header">
                        <div class="ai-section-title">✨ AI 热点分析</div>
                        <span class="ai-section-badge">AI</span>
                    </div>
                    <div class="ai-blocks-grid">"""

    if result.core_trends:
        content = _format_list_content(result.core_trends)
        content_html = _escape_html(content).replace("\n", "<br>")
        ai_html += f"""
                    <div class="ai-block">
                        <div class="ai-block-title">核心热点态势</div>
                        <div class="ai-block-content">{content_html}</div>
                    </div>"""

    if result.sentiment_controversy:
        content = _format_list_content(result.sentiment_controversy)
        content_html = _escape_html(content).replace("\n", "<br>")
        ai_html += f"""
                    <div class="ai-block">
                        <div class="ai-block-title">舆论风向争议</div>
                        <div class="ai-block-content">{content_html}</div>
                    </div>"""

    if result.signals:
        content = _format_list_content(result.signals)
        content_html = _escape_html(content).replace("\n", "<br>")
        ai_html += f"""
                    <div class="ai-block">
                        <div class="ai-block-title">异动与弱信号</div>
                        <div class="ai-block-content">{content_html}</div>
                    </div>"""

    if result.rss_insights:
        content = _format_list_content(result.rss_insights)
        content_html = _escape_html(content).replace("\n", "<br>")
        ai_html += f"""
                    <div class="ai-block">
                        <div class="ai-block-title">RSS 深度洞察</div>
                        <div class="ai-block-content">{content_html}</div>
                    </div>"""

    if result.outlook_strategy:
        content = _format_list_content(result.outlook_strategy)
        content_html = _escape_html(content).replace("\n", "<br>")
        ai_html += f"""
                    <div class="ai-block">
                        <div class="ai-block-title">研判策略建议</div>
                        <div class="ai-block-content">{content_html}</div>
                    </div>"""

    if result.standalone_summaries:
        summaries_text = _format_standalone_summaries(result.standalone_summaries)
        if summaries_text:
            summaries_html = _escape_html(summaries_text).replace("\n", "<br>")
            ai_html += f"""
                    <div class="ai-block">
                        <div class="ai-block-title">独立源点速览</div>
                        <div class="ai-block-content">{summaries_html}</div>
                    </div>"""

    ai_html += """
                    </div>
                </div>"""
    return ai_html
