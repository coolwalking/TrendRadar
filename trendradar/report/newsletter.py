# coding=utf-8
"""
Newsletter 风格完整报告渲染器（environment）

为 daily 定时盘面生成 newsletter 风格的完整报告（public/{group}/full.html）。
与 html.py 的 classic 巨型渲染器并存：context 按 report_style 分流，
environment → 本模块，classic → render_html_content。

页面结构（窄栏、黑白、无卡片阴影，对齐已审定的样例）：
- EDITORIAL ZONE：overview 导言 + 内联统计 + 各异常 bucket + 已抑制
- DATA ZONE：热榜关键词 + RSS 摘要 + RSS 新增（字级降一档）
- NOTES ZONE：获取失败 + 方法说明

数据来源全部为生产真实结构：
- report_data：prepare_report_data 产出（stats/new_titles/failed_ids/total_new_count）
- stats title：含 ranks(list) / source_name / is_new（无单值 rank，排名取 ranks 最小值）
- ai_analysis：AIAnalysisResult，environment buckets（字段对齐 formatter._env_html_item）
"""

import html as _html_lib
from datetime import datetime
from typing import Any, Dict, List, Optional

from trendradar.ai.evidence import derive_radar_readout

_NL_SECTION_LABELS: Dict[str, str] = {
    "cross_layer_verified": "跨层来源共振",
    "high_heat_unverified": "高热待核实",
    "chinese_only_hot": "中文独热",
    "silence_gap": "沉默温差",
}

_MODE_LABELS = {
    "current": "当前盘面",
    "incremental": "当前盘面",
    "daily": "每日盘面",
}

_NL_CSS = """
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#fff;--text:#111;--muted:#555;--faint:#999;
  --border:#e5e5e5;--border-strong:#bbb;
  --risk:#b91c1c;--warn:#b45309;--link:#1d4ed8;
  --max:680px;
}
body{
  background:var(--bg);color:var(--text);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,"PingFang SC","Microsoft YaHei",sans-serif;
  font-size:15px;line-height:1.72;padding:0 20px;
}
.wrap{max-width:var(--max);margin:0 auto}

header{padding:32px 0 22px;border-bottom:2px solid var(--text);margin-bottom:36px}
.brand{font-size:11px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:var(--faint)}
header h1{font-size:20px;font-weight:700;line-height:1.3;margin-top:6px}
.dateline{font-size:12px;color:var(--faint);margin-top:5px}

.sec{border-top:1px solid var(--border);padding-top:32px;margin-top:40px}
.sec-label{font-size:10px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:var(--faint);margin-bottom:16px}

.overview-text{font-size:15px;line-height:1.8;color:var(--muted);margin-bottom:22px}

.stats-brief{font-size:12px;color:var(--faint);margin-bottom:32px}
.stats-brief strong{color:var(--text);font-weight:600}

.ai-item{padding:16px 0;border-bottom:1px solid var(--border)}
.ai-item:last-child{border-bottom:none}
.ai-topic{font-weight:700;font-size:15px;line-height:1.4;margin-bottom:3px}
.ai-meta{font-size:11px;color:var(--faint);margin-bottom:7px;line-height:1.4}
.ai-body{font-size:14px;color:var(--muted);line-height:1.65}
.ai-risk{font-size:12px;color:var(--risk);margin-top:6px}
.ai-risk::before{content:"\\2691  "}

hr.data-div{border:none;border-top:1px solid var(--border);margin:44px 0 0}
.data-zone{color:var(--muted)}

.dsec{padding-top:24px;margin-top:24px;border-top:1px solid #f0f0f0}
.dsec:first-child{border-top:none;margin-top:0}
.dsec-label{font-size:10px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;
  color:var(--faint);margin-bottom:12px}

.kw-group{margin-bottom:22px}
.kw-label{font-weight:600;font-size:13px;margin-bottom:6px;display:flex;align-items:baseline;gap:6px}
.kw-count{font-size:11px;color:var(--faint);font-weight:400}
.title-row{display:flex;align-items:baseline;gap:8px;padding:4px 0;border-bottom:1px solid #f5f5f5;font-size:12px}
.title-row:last-child{border-bottom:none}
.t-rank{font-size:10px;color:var(--faint);min-width:20px;flex-shrink:0}
.t-title{flex:1;color:var(--muted)}
.t-source{font-size:10px;color:var(--faint);flex-shrink:0}
.t-new{font-size:9px;font-weight:700;color:var(--risk);padding:0 3px;border:1px solid var(--risk);border-radius:2px;flex-shrink:0}

.rss-group{margin-bottom:18px}
.rss-label{font-weight:600;font-size:12px;color:var(--faint);border-bottom:1px solid #f0f0f0;padding-bottom:3px;margin-bottom:6px}
.rss-row{display:flex;gap:8px;align-items:baseline;font-size:12px;padding:3px 0}
.rss-time{font-size:10px;color:var(--faint);min-width:34px;flex-shrink:0}
.rss-title{flex:1;color:var(--muted)}
.rss-src{font-size:10px;color:var(--faint);flex-shrink:0}

.notes-zone{margin-top:36px;padding-top:16px;border-top:1px solid var(--border);
  font-size:11px;color:var(--faint);line-height:1.7}

footer{margin-top:40px;padding:16px 0 28px;border-top:1px solid var(--border);
  font-size:11px;color:var(--faint);display:flex;justify-content:space-between;flex-wrap:wrap;gap:6px}

a{color:var(--link);text-decoration:none}
a:hover{text-decoration:underline}

@media(max-width:500px){.ai-meta{white-space:normal}}
"""


def _e(text: Any) -> str:
    """HTML 转义。"""
    if text is None:
        return ""
    return _html_lib.escape(str(text))


def _rep_rank(title: Dict[str, Any]) -> Optional[int]:
    """从 ranks 历史列表取代表位次（最高位 = 最小数字）。"""
    ranks = title.get("ranks") or []
    nums = [r for r in ranks if isinstance(r, int)]
    return min(nums) if nums else None


def _nl_ai_item_compact(item: Dict[str, Any]) -> str:
    """单条异常信号：topic + 一行 meta + 正文 + 可选风险提示。

    正文 fallback：summary → analysis → factual_boundary（对齐 formatter 的
    render_environment_telegram_alert_brief）。AI 失败/返回空时 summary、analysis 为空，
    factual_boundary 为程序常量始终有值，保证 ai-body 不缺失。
    """
    topic = _e(item.get("topic", ""))
    body = (
        item.get("summary") or item.get("analysis") or item.get("factual_boundary") or ""
    ).strip()
    risk = item.get("risk_note") or item.get("factual_boundary")
    layers = item.get("source_layers", "")
    platforms = item.get("platforms", "")
    heat = item.get("highest_heat", "")
    sentiment = item.get("sentiment_flag", False)

    meta_parts: List[str] = []
    for val in (layers, platforms, heat):
        if val and val != "-":
            meta_parts.append(_e(val))
    if sentiment:
        meta_parts.append("情绪聚集")
    meta = " · ".join(meta_parts)

    meta_html = f'<div class="ai-meta">{meta}</div>' if meta else ""
    body_html = f'<div class="ai-body">{_e(body)}</div>' if body else ""
    risk_html = f'<div class="ai-risk">{_e(risk)}</div>' if risk else ""

    return (
        f'<div class="ai-item">'
        f'<div class="ai-topic">{topic}</div>'
        f"{meta_html}{body_html}{risk_html}"
        f"</div>"
    )


def _nl_hotlist_html(stats: Optional[List[Dict[str, Any]]]) -> str:
    """热榜关键词区：每组关键词 + 其下标题行（排名取 ranks 最小值）。"""
    if not stats:
        return ""
    parts: List[str] = []
    for grp in stats:
        word = _e(grp.get("word", ""))
        count = grp.get("count", 0)
        titles = grp.get("titles", []) or []
        rows = ""
        for t in titles:
            rank = _rep_rank(t)
            rank_str = f"#{rank}" if rank is not None else "·"
            title = _e(t.get("title", ""))
            source = _e(t.get("source_name", ""))
            new_badge = '<span class="t-new">新</span>' if t.get("is_new") else ""
            rows += (
                f'<div class="title-row">'
                f'<span class="t-rank">{rank_str}</span>'
                f'<span class="t-title">{title}</span>'
                f"{new_badge}"
                f'<span class="t-source">{source}</span>'
                f"</div>"
            )
        parts.append(
            f'<div class="kw-group">'
            f'<div class="kw-label">{word}<span class="kw-count">{count} 条</span></div>'
            f"{rows}</div>"
        )
    return "\n".join(parts)


def _nl_rss_html(
    rss_items: Optional[List[Dict[str, Any]]], title_prefix: str = ""
) -> str:
    """RSS 区：每个 RSS 关键词组 + 标题/来源/时间。"""
    if not rss_items:
        return ""
    parts: List[str] = []
    for grp in rss_items:
        word = _e(grp.get("word", ""))
        titles = grp.get("titles", []) or []
        rows = ""
        for t in titles:
            time_d = _e(t.get("time_display", ""))
            title = _e(t.get("title", ""))
            source = _e(t.get("source_name", ""))
            rows += (
                f'<div class="rss-row">'
                f'<span class="rss-time">{time_d}</span>'
                f'<span class="rss-title">{title}</span>'
                f'<span class="rss-src">{source}</span>'
                f"</div>"
            )
        parts.append(
            f'<div class="rss-group">'
            f'<div class="rss-label">{title_prefix}{word}</div>'
            f"{rows}</div>"
        )
    return "\n".join(parts)


def render_newsletter_report(
    report_data: Dict[str, Any],
    total_titles: int,
    mode: str = "daily",
    update_info: Optional[Dict] = None,
    *,
    rss_items: Optional[List[Dict]] = None,
    rss_new_items: Optional[List[Dict]] = None,
    ai_analysis: Optional[Any] = None,
    get_time_func: Optional[Any] = None,
    **_ignored: Any,
) -> str:
    """渲染 newsletter 风格 environment 完整报告（自包含、内联 CSS、无脚本）。

    签名兼容 generate_html_report 的 render_html_func 接口（前 4 个位置参数），
    其余通过关键字由 context.render_html 注入；多余 kwargs 被 **_ignored 吞掉。
    """
    generated_at: datetime = get_time_func() if callable(get_time_func) else datetime.now()
    mode_label = _MODE_LABELS.get(mode, mode)
    date_str = generated_at.strftime("%Y-%m-%d %H:%M")

    # ── EDITORIAL ZONE ────────────────────────────────────────────────
    editorial = ""
    if ai_analysis is not None and getattr(ai_analysis, "success", False) \
            and getattr(ai_analysis, "report_style", "") == "environment":
        radar = derive_radar_readout(getattr(ai_analysis, "overview_stats", {}) or {})

        overview = (getattr(ai_analysis, "overview", "") or "").strip()
        if overview:
            editorial += f'<div class="overview-text">{_e(overview)}</div>\n'

        anomaly = radar.get("anomaly", 0)
        suppressed_n = radar.get("suppressed", 0)
        brief_parts = [f"<strong>{anomaly}</strong> 个异常信号"]
        for key, label in (
            ("cross_layer", "跨层共振"),
            ("high_heat", "高热待核实"),
            ("silence_gap", "沉默温差"),
            ("chinese_only", "中文独热"),
        ):
            n = radar.get(key, 0)
            if n:
                brief_parts.append(f"{n} {label}")
        if suppressed_n:
            brief_parts.append(f"{suppressed_n} 已抑制")
        editorial += f'<div class="stats-brief">{"　·　".join(brief_parts)}</div>\n'

        bucket_map = {
            "cross_layer_verified": getattr(ai_analysis, "cross_layer_verified", None),
            "high_heat_unverified": getattr(ai_analysis, "high_heat_unverified", None),
            "chinese_only_hot": getattr(ai_analysis, "chinese_only_hot", None),
            "silence_gap": getattr(ai_analysis, "silence_gap", None),
        }
        for key, items in bucket_map.items():
            if not items:
                continue
            label = _NL_SECTION_LABELS.get(key, key)
            items_html = "".join(_nl_ai_item_compact(it) for it in items if isinstance(it, dict))
            editorial += (
                f'<div class="sec"><div class="sec-label">{label}</div>'
                f"{items_html}</div>\n"
            )

        # 已抑制（与各 bucket 同级）
        sup_html = ""
        for it in (getattr(ai_analysis, "sentiment_heavy", None) or []):
            if isinstance(it, dict):
                sup_html += _nl_ai_item_compact(it)
        for note in (getattr(ai_analysis, "background_notes", None) or []):
            note = (note or "").strip()
            if note:
                sup_html += (
                    f'<div class="ai-item">'
                    f'<div class="ai-body" style="font-style:italic">{_e(note)}</div>'
                    f"</div>"
                )
        if sup_html:
            editorial += (
                f'<div class="sec"><div class="sec-label">已抑制</div>'
                f"{sup_html}</div>\n"
            )

    # ── DATA ZONE ─────────────────────────────────────────────────────
    data_sections = ""
    hotlist_html = _nl_hotlist_html(report_data.get("stats"))
    if hotlist_html:
        data_sections += (
            f'<div class="dsec"><div class="dsec-label">热榜关键词</div>'
            f"{hotlist_html}</div>\n"
        )
    rss_html = _nl_rss_html(rss_items)
    if rss_html:
        data_sections += (
            f'<div class="dsec"><div class="dsec-label">RSS 摘要</div>'
            f"{rss_html}</div>\n"
        )
    rss_new_html = _nl_rss_html(rss_new_items, title_prefix="新增 · ")
    if rss_new_html:
        data_sections += (
            f'<div class="dsec"><div class="dsec-label">RSS 新增</div>'
            f"{rss_new_html}</div>\n"
        )
    data_zone = (
        f'<hr class="data-div"><div class="data-zone">{data_sections}</div>'
        if data_sections
        else ""
    )

    # ── NOTES ZONE ────────────────────────────────────────────────────
    notes_parts: List[str] = []
    failed_ids = report_data.get("failed_ids") or []
    if failed_ids:
        notes_parts.append("获取失败：" + "、".join(_e(x) for x in failed_ids))
    method_note = getattr(ai_analysis, "method_note", "") if ai_analysis else ""
    if method_note:
        notes_parts.append(_e(method_note))
    notes_zone = (
        '<div class="notes-zone">' + "<br>".join(notes_parts) + "</div>"
        if notes_parts
        else ""
    )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>TrendRadar · {_e(mode_label)} · {_e(date_str)}</title>
<style>{_NL_CSS}</style>
</head>
<body>
<div class="wrap">
<header>
  <div class="brand">TrendRadar · 信息环境监测</div>
  <h1>{_e(mode_label)}</h1>
  <div class="dateline">{_e(date_str)}</div>
</header>
{editorial}
{data_zone}
{notes_zone}
<footer>
  <span>TrendRadar</span>
  <span>{_e(date_str)}</span>
</footer>
</div>
</body>
</html>"""
