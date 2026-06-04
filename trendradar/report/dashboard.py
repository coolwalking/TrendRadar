# coding=utf-8
"""
Current Dashboard 模块

生成轻量"当前盘面"页面与发布安全的摘要缓存，用于远程主动查看当前盘面。

设计约束（见 plan）：
- 与 alert cooldown / notify_labels 解耦：直接读 AIAnalysisResult 全量 buckets，
  不调用 apply_alert_cooldown / select_environment_alert_items。
- 不依赖 formatter.py 的私有下划线 helper；只复用数据层的 derive_radar_readout
  与 SECTION_ORDER（evidence.py 的公开常量），card / CSS 在本模块内自实现。
- build_dashboard_state 产出的是**发布安全摘要**：禁止包含 source_links、
  sample_titles、evidence_detail、sources_by_tier、原始 RSS/热榜 URL，
  以及任何 db/log/alert_state/secrets。
"""

import html as _html_lib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from trendradar.ai.evidence import SECTION_ORDER, derive_radar_readout

# state.json schema 版本（future /now 据此读取）
DASHBOARD_SCHEMA_VERSION = 1

# 发布根落地页 output/public/index.html：静态、幂等，链接 current / daily 两个盘面。
PUBLIC_LANDING_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>TrendRadar</title>
<style>
body { margin:0; padding:32px 16px; background:#0f1115; color:#e6e6e6;
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",
  "Microsoft YaHei",sans-serif; line-height:1.6; }
.wrap { max-width:560px; margin:0 auto; }
h1 { font-size:22px; margin:0 0 20px; }
a.card { display:block; background:#1a1d24; border:1px solid #262a33;
  border-radius:12px; padding:18px 20px; margin-bottom:12px;
  text-decoration:none; color:#e6e6e6; }
a.card:hover { border-color:#7aa2ff; }
a.card .t { font-size:17px; font-weight:600; color:#7aa2ff; }
a.card .d { font-size:13px; color:#8a909a; margin-top:4px; }
</style>
</head>
<body>
<div class="wrap">
  <h1>TrendRadar</h1>
  <a class="card" href="current/index.html">
    <div class="t">当前盘面 →</div>
    <div class="d">随刷新更新，远程查看当前榜单状态</div>
  </a>
  <a class="card" href="daily/index.html">
    <div class="t">每日盘面 →</div>
    <div class="d">每日汇总回看</div>
  </a>
</div>
</body>
</html>"""

# 呈现层栏目标题（本模块自持，避免耦合 formatter._SECTION_TITLES）
_SECTION_TITLES: Dict[str, str] = {
    "cross_layer_verified": "跨层呼应",
    "high_heat_unverified": "高热待核实",
    "chinese_only_hot": "中文独热",
    "silence_gap": "沉默温差",
}

# top_items / 卡片允许透出的字段白名单（发布安全：绝不 dump bucket 原始 dict）
_SAFE_ITEM_FIELDS = (
    "topic",
    "label",
    "summary",
    "highest_heat",
    "platform_count",
    "source_layers",
    "sentiment_flag",
)

_DASHBOARD_CSS = """
* { box-sizing: border-box; }
body {
  margin: 0; padding: 16px;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
    "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
  background: #0f1115; color: #e6e6e6; line-height: 1.6;
}
.wrap { max-width: 760px; margin: 0 auto; }
.head { display: flex; flex-wrap: wrap; align-items: baseline;
  justify-content: space-between; gap: 8px; margin-bottom: 12px; }
.head h1 { font-size: 20px; margin: 0; }
.meta { color: #8a909a; font-size: 13px; }
.meta .dot { margin: 0 6px; }
.full-link { display: inline-block; margin: 4px 0 18px;
  color: #7aa2ff; text-decoration: none; font-size: 14px; }
.full-link:hover { text-decoration: underline; }
.radar { display: grid; grid-template-columns: repeat(2, 1fr);
  gap: 10px; margin-bottom: 20px; }
.metric { background: #1a1d24; border: 1px solid #262a33;
  border-radius: 10px; padding: 12px 14px; }
.metric .label { font-size: 12px; color: #8a909a; }
.metric .main { font-size: 15px; font-weight: 600; margin-top: 2px; }
.metric .sub { font-size: 12px; color: #9aa0aa; margin-top: 2px; }
.overview { background: #161922; border-left: 3px solid #7aa2ff;
  border-radius: 6px; padding: 10px 14px; margin-bottom: 18px;
  color: #cdd2da; font-size: 14px; }
.sec { margin-bottom: 18px; }
.sec h2 { font-size: 15px; margin: 0 0 8px; color: #cdd2da; }
.sec h2 .cnt { color: #8a909a; font-weight: 400; font-size: 13px; }
.card { background: #1a1d24; border: 1px solid #262a33;
  border-radius: 10px; padding: 12px 14px; margin-bottom: 8px; }
.card .topic { font-size: 14px; font-weight: 600; }
.card .summary { font-size: 13px; color: #c2c7d0; margin-top: 4px; }
.chips { margin-top: 8px; display: flex; flex-wrap: wrap; gap: 6px; }
.chip { font-size: 11px; padding: 2px 8px; border-radius: 999px;
  background: #232733; color: #9aa0aa; }
.chip.heat { background: #3a2730; color: #ffb4c0; }
.chip.sent { background: #3a3320; color: #ffe08a; }
.empty { color: #8a909a; font-size: 14px; padding: 24px 0; }
.foot { color: #6b717c; font-size: 12px; margin-top: 24px;
  border-top: 1px solid #262a33; padding-top: 12px; }
@media (max-width: 520px) { .radar { grid-template-columns: 1fr; } }
"""


def _esc(text: Any) -> str:
    """转义 HTML 特殊字符。"""
    if text is None:
        return ""
    return _html_lib.escape(str(text))


def _group_for_mode(mode: str) -> str:
    """run mode → 发布 group。current/incremental → current；daily → daily。"""
    return "daily" if mode == "daily" else "current"


def _is_environment(ai_analysis: Optional[Any]) -> bool:
    return bool(
        ai_analysis is not None
        and getattr(ai_analysis, "report_style", "") == "environment"
    )


def _safe_item(item: Dict[str, Any], label: str) -> Dict[str, Any]:
    """从 bucket item 中**按白名单**挑出发布安全字段，绝不透传原始 dict。"""
    out: Dict[str, Any] = {"label": label}
    for key in _SAFE_ITEM_FIELDS:
        if key == "label":
            continue
        if key in item:
            out[key] = item.get(key)
    return out


def _collect_safe_items(ai_analysis: Any) -> List[Dict[str, Any]]:
    """按 SECTION_ORDER 收集各栏目 item 的发布安全摘要（扁平，带 label）。"""
    items: List[Dict[str, Any]] = []
    for label in SECTION_ORDER:
        for item in (getattr(ai_analysis, label, None) or []):
            if isinstance(item, dict):
                items.append(_safe_item(item, label))
    return items


def _fmt_display(generated_at: datetime) -> str:
    return generated_at.strftime("%Y-%m-%d %H:%M")


# ── 公开 API ──────────────────────────────────────────────────────────────


def build_dashboard_state(
    ai_analysis: Optional[Any],
    report_metadata: Optional[Dict[str, Any]],
    generated_at: datetime,
    mode: str,
) -> Dict[str, Any]:
    """
    构建 current/daily 盘面的**发布安全摘要缓存**（future /now 数据源）。

    禁止包含 source_links / sample_titles / evidence_detail / sources_by_tier /
    原始 URL / db / log / alert_state / secrets。
    """
    meta = report_metadata or {}
    radar: Dict[str, Any] = {}
    overview = ""
    top_items: List[Dict[str, Any]] = []

    if _is_environment(ai_analysis):
        radar = derive_radar_readout(getattr(ai_analysis, "overview_stats", {}) or {})
        overview = (getattr(ai_analysis, "overview", "") or "").strip()
        top_items = _collect_safe_items(ai_analysis)

    return {
        "schema_version": DASHBOARD_SCHEMA_VERSION,
        "mode": mode,
        "group": _group_for_mode(mode),
        "generated_at": generated_at.isoformat(),
        "report_style": getattr(ai_analysis, "report_style", "classic")
        if ai_analysis is not None
        else "none",
        "overview": overview,
        "radar": radar,
        "top_items": top_items,
        "counts": {
            "hotlist_total": meta.get("hotlist_total", 0),
            "platform_total": meta.get("platform_total", 0),
            "rss_matched_count": meta.get("rss_matched_count", 0),
        },
    }


def _render_radar_html(radar: Dict[str, Any]) -> str:
    layers = radar.get("layers", {}) or {}
    metrics = [
        ("信号密度", f"异常 {radar.get('anomaly', 0)} 条",
         f"已抑制 {radar.get('suppressed', 0)} 条"),
        ("热度/证据", f"跨层呼应 {radar.get('cross_layer', 0)}",
         f"高热待核实 {radar.get('high_heat', 0)}"),
        ("中外温差", f"沉默温差 {radar.get('silence_gap', 0)}",
         f"中文独热 {radar.get('chinese_only', 0)}"),
        ("层级覆盖", f"A {layers.get('A', 0)} / B {layers.get('B', 0)}",
         f"C {layers.get('C', 0)} / D {layers.get('D', 0)}"),
    ]
    cells = "".join(
        f'<div class="metric"><div class="label">{_esc(label)}</div>'
        f'<div class="main">{_esc(main)}</div>'
        f'<div class="sub">{_esc(sub)}</div></div>'
        for label, main, sub in metrics
    )
    return f'<div class="radar">{cells}</div>'


def _render_card_html(item: Dict[str, Any]) -> str:
    topic = _esc(item.get("topic", ""))
    summary = (item.get("summary", "") or "").strip()
    summary_html = f'<div class="summary">{_esc(summary)}</div>' if summary else ""
    chips: List[str] = []
    layers = item.get("source_layers")
    if layers and layers != "-":
        chips.append(f'<span class="chip">{_esc(layers)}</span>')
    pc = item.get("platform_count")
    if pc:
        chips.append(f'<span class="chip">平台 {_esc(pc)}</span>')
    heat = item.get("highest_heat")
    if heat and heat != "-":
        chips.append(f'<span class="chip heat">{_esc(heat)}</span>')
    if item.get("sentiment_flag"):
        chips.append('<span class="chip sent">含情绪信号</span>')
    chips_html = f'<div class="chips">{"".join(chips)}</div>' if chips else ""
    return (
        f'<div class="card"><div class="topic">{topic}</div>'
        f"{summary_html}{chips_html}</div>"
    )


def _render_sections_html(ai_analysis: Any) -> str:
    safe_items = _collect_safe_items(ai_analysis)
    if not safe_items:
        return '<div class="empty">当前暂无异常信号。</div>'
    by_label: Dict[str, List[Dict[str, Any]]] = {}
    for it in safe_items:
        by_label.setdefault(it["label"], []).append(it)

    blocks: List[str] = []
    for label in SECTION_ORDER:
        items = by_label.get(label) or []
        if not items:
            continue
        title = _SECTION_TITLES.get(label, label)
        cards = "".join(_render_card_html(it) for it in items)
        blocks.append(
            f'<div class="sec"><h2>{_esc(title)} '
            f'<span class="cnt">{len(items)}</span></h2>{cards}</div>'
        )
    return "".join(blocks) if blocks else '<div class="empty">当前暂无异常信号。</div>'


def render_current_dashboard_html(
    ai_analysis: Optional[Any],
    report_metadata: Optional[Dict[str, Any]],
    generated_at: datetime,
    mode: str,
    full_href: str = "full.html",
) -> str:
    """
    渲染自包含的轻量盘面页（单文件、内联 CSS、无外部引用）。

    - environment 样式：盘面四行 + overview + 各栏目卡片。
    - classic / ai_analysis is None：降级为计数 + 完整报告链接。
    任何 mode 都能出页，不受 cooldown / notify_labels 影响。
    """
    group = _group_for_mode(mode)
    title = "每日盘面" if group == "daily" else "当前盘面"
    display_time = _fmt_display(generated_at)
    meta = report_metadata or {}

    body_parts: List[str] = []
    if _is_environment(ai_analysis):
        radar = derive_radar_readout(getattr(ai_analysis, "overview_stats", {}) or {})
        body_parts.append(_render_radar_html(radar))
        overview = (getattr(ai_analysis, "overview", "") or "").strip()
        if overview:
            body_parts.append(f'<div class="overview">{_esc(overview)}</div>')
        body_parts.append(_render_sections_html(ai_analysis))
    else:
        # 降级盘面：仅计数 + 完整报告链接。
        # 不渲染雷达——非 environment 样式下指标无意义，全零雷达会被误读为"真实数据为空"。
        body_parts.append(
            '<div class="empty">本次未生成信息环境监测盘面，'
            "请查看完整报告了解榜单详情。</div>"
        )

    counts_line = (
        f"热榜匹配 {meta.get('hotlist_total', 0)} 条"
        f" · 平台 {meta.get('platform_total', 0)} 个"
        f" · RSS 匹配 {meta.get('rss_matched_count', 0)} 条"
    )
    body = "".join(body_parts)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>{_esc(title)} · TrendRadar</title>
<style>{_DASHBOARD_CSS}</style>
</head>
<body>
<div class="wrap">
  <div class="head">
    <h1>{_esc(title)}</h1>
    <div class="meta">{_esc(display_time)}<span class="dot">·</span>{_esc(counts_line)}</div>
  </div>
  <a class="full-link" href="{_esc(full_href)}">查看完整报告 →</a>
  {body}
  <div class="foot">TrendRadar · {_esc(group)} dashboard · 更新于 {_esc(display_time)}</div>
</div>
</body>
</html>"""


def write_dashboard(
    output_dir: str,
    mode: str,
    ai_analysis: Optional[Any],
    report_metadata: Optional[Dict[str, Any]],
    generated_at: datetime,
) -> str:
    """
    渲染并写发布根的盘面产物（纯文件 IO，便于单测）：
    - output/public/{group}/index.html（盘面页）
    - output/public/{group}/state.json（发布安全摘要缓存）
    - output/public/index.html（落地页，幂等）

    **不写 full.html**（由 generate_html_report 负责）。

    Returns:
        str: 盘面页路径 output/public/{group}/index.html
    """
    group = _group_for_mode(mode)
    dashboard_html = render_current_dashboard_html(
        ai_analysis=ai_analysis,
        report_metadata=report_metadata,
        generated_at=generated_at,
        mode=mode,
        full_href="full.html",
    )
    state = build_dashboard_state(
        ai_analysis=ai_analysis,
        report_metadata=report_metadata,
        generated_at=generated_at,
        mode=mode,
    )

    public_dir = Path(output_dir) / "public"
    group_dir = public_dir / group
    group_dir.mkdir(parents=True, exist_ok=True)

    index_file = group_dir / "index.html"
    index_file.write_text(dashboard_html, encoding="utf-8")
    (group_dir / "state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (public_dir / "index.html").write_text(PUBLIC_LANDING_HTML, encoding="utf-8")

    return str(index_file)
