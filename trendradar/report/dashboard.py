# coding=utf-8
"""
Current Dashboard 模块（newsletter 风格）

生成轻量"当前盘面"页面与发布安全的摘要缓存，用于远程主动查看当前盘面。

设计约束（见 plan）：
- 与 alert cooldown / notify_labels 解耦：直接读 AIAnalysisResult 全量 buckets，
  不调用 apply_alert_cooldown / select_environment_alert_items。
- 不依赖 formatter.py 的私有下划线 helper；只复用数据层的 derive_radar_readout
  与 SECTION_ORDER（evidence.py 的公开常量），渲染与 CSS 在本模块内自实现。
- build_dashboard_state 产出的是**发布安全摘要**：禁止包含 source_links、
  sample_titles、evidence_detail、sources_by_tier、原始 RSS/热榜 URL，
  以及任何 db/log/alert_state/secrets。
- dashboard HTML 是公开发布页：只透出热榜标题/来源/排名（公开榜单信息，无 URL）
  与 AI 分析文字（topic/highest_heat/risk_note，无敏感字段）。
"""

import html as _html_lib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from trendradar.ai.evidence import SECTION_ORDER, derive_radar_readout

# state.json schema 版本（future /now 据此读取）
DASHBOARD_SCHEMA_VERSION = 1

# 发布根落地页 output/public/index.html：静态、幂等。
# 路由：current → dashboard（current/index.html）；daily → full report（daily/full.html）。
PUBLIC_LANDING_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>TrendRadar</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{background:#fff;color:#111;padding:0 20px;
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;line-height:1.6}
.wrap{max-width:680px;margin:0 auto;padding-top:48px}
.brand{font-size:11px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:#999}
h1{font-size:22px;font-weight:700;margin:6px 0 28px;border-bottom:2px solid #111;padding-bottom:18px}
a.card{display:block;border:1px solid #e5e5e5;padding:18px 20px;margin-bottom:12px;
  text-decoration:none;color:#111}
a.card:hover{border-color:#111}
a.card .t{font-size:16px;font-weight:700}
a.card .d{font-size:13px;color:#777;margin-top:4px}
</style>
</head>
<body>
<div class="wrap">
  <div class="brand">TrendRadar · 信息环境监测</div>
  <h1>盘面入口</h1>
  <a class="card" href="current/index.html">
    <div class="t">当前盘面 →</div>
    <div class="d">随刷新更新，远程查看当前榜单状态与异常信号</div>
  </a>
  <a class="card" href="daily/full.html">
    <div class="t">每日盘面 →</div>
    <div class="d">每日汇总完整报告</div>
  </a>
</div>
</body>
</html>"""

# 呈现层栏目标签（异常信号分类）
_DASH_CAT_LABELS: Dict[str, str] = {
    "cross_layer_verified": "跨层",
    "high_heat_unverified": "高热",
    "chinese_only_hot": "中文独热",
    "silence_gap": "沉默温差",
}

# top_items / state.json 允许透出的字段白名单（发布安全：绝不 dump bucket 原始 dict）
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
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#fff;--text:#111;--muted:#555;--faint:#999;
  --border:#e5e5e5;--risk:#b91c1c;
  --max:680px;
}
body{
  background:var(--bg);color:var(--text);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,"PingFang SC","Microsoft YaHei",sans-serif;
  font-size:15px;line-height:1.72;padding:0 20px;
}
.wrap{max-width:var(--max);margin:0 auto;padding-top:0}

.cur-head{padding:24px 0 18px;border-bottom:2px solid var(--text);margin-bottom:22px;
  display:flex;align-items:baseline;justify-content:space-between;flex-wrap:wrap;gap:6px}
.cur-brand{font-size:11px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:var(--text)}
.cur-ts{font-size:11px;color:var(--faint)}

.cur-lead{font-size:13px;color:var(--muted);margin-bottom:20px}
.cur-lead strong{font-size:20px;font-weight:700;color:var(--text);margin-right:4px;vertical-align:baseline}

.sec-label{font-size:10px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;
  color:var(--faint);margin-bottom:8px}

.cur-signals{border-top:1px solid var(--border)}
.cur-row{display:grid;grid-template-columns:52px 1fr;gap:0 12px;
  padding:11px 0;border-bottom:1px solid var(--border)}
.cur-row:last-child{border-bottom:none}
.cur-cat{font-size:10px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;
  color:var(--muted);padding-top:3px;line-height:1.3}
.cur-cat.dim{text-transform:none;font-weight:400;color:var(--faint)}
.cur-topic{font-size:14px;font-weight:600;color:var(--text);line-height:1.4;margin-bottom:3px}
.cur-topic.plain{font-weight:400}
.cur-heat{font-size:11px;color:var(--faint);line-height:1.4}
.cur-risk{font-size:11px;color:var(--risk);margin-top:5px}
.cur-risk::before{content:"\\2691  "}
.t-new{font-size:9px;font-weight:700;color:var(--risk);padding:0 3px;
  border:1px solid var(--risk);border-radius:2px}

.cur-track{margin-top:20px;border-top:1px solid var(--border);padding-top:4px}

.cur-sup{font-size:11px;color:var(--faint);margin-top:22px;padding-top:14px;
  border-top:1px solid var(--border);line-height:1.7}

.cur-empty{font-size:13px;color:var(--faint);padding:20px 0}

a{color:var(--text);text-decoration:underline}
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


def _rep_rank(title: Dict[str, Any]) -> Optional[int]:
    """从 ranks 历史列表取代表位次（最高位 = 最小数字）。"""
    ranks = title.get("ranks") or []
    nums = [r for r in ranks if isinstance(r, int)]
    return min(nums) if nums else None


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


def _render_signal_rows(ai_analysis: Any) -> str:
    """异常信号列表：每条 = 分类标签 + topic（可换行）+ 热度 + 风险提示。"""
    rows: List[str] = []
    for key in SECTION_ORDER:
        cat_label = _DASH_CAT_LABELS.get(key, key)
        for item in (getattr(ai_analysis, key, None) or []):
            if not isinstance(item, dict):
                continue
            topic = _esc(item.get("topic", ""))
            heat = _esc(item.get("highest_heat", ""))
            risk = item.get("risk_note")
            heat_html = f'<div class="cur-heat">{heat}</div>' if heat else ""
            risk_html = f'<div class="cur-risk">{_esc(risk)}</div>' if risk else ""
            rows.append(
                f'<div class="cur-row">'
                f'<div class="cur-cat">{_esc(cat_label)}</div>'
                f'<div><div class="cur-topic">{topic}</div>{heat_html}{risk_html}</div>'
                f"</div>"
            )
    return "\n".join(rows)


def _render_hotlist_rows(stats: Optional[List[Dict[str, Any]]]) -> str:
    """热榜追踪：每个关键词组取最热标题 + 来源摘要行（source #rank [新]）。"""
    rows: List[str] = []
    for grp in (stats or []):
        word = _esc(grp.get("word", ""))
        titles = grp.get("titles", []) or []
        if not titles:
            continue
        top_title = _esc(titles[0].get("title", ""))
        src_parts: List[str] = []
        for t in titles[:3]:
            src = _esc(t.get("source_name", ""))
            rank = _rep_rank(t)
            rank_str = f"&nbsp;#{rank}" if rank is not None else ""
            badge = ' <span class="t-new">新</span>' if t.get("is_new") else ""
            src_parts.append(f"{src}{rank_str}{badge}")
        extra = (
            f" <span style=\"color:var(--faint)\">+{len(titles) - 3}条</span>"
            if len(titles) > 3
            else ""
        )
        src_line = " · ".join(src_parts) + extra
        rows.append(
            f'<div class="cur-row">'
            f'<div class="cur-cat dim">{word}</div>'
            f'<div><div class="cur-topic plain">{top_title}</div>'
            f'<div class="cur-heat">{src_line}</div></div>'
            f"</div>"
        )
    return "\n".join(rows)


def _render_rss_rows(rss_items: Optional[List[Dict[str, Any]]]) -> str:
    """RSS 追踪：每个 RSS 关键词组取最新一条 + 来源/时间。"""
    rows: List[str] = []
    for grp in (rss_items or []):
        word = _esc(grp.get("word", ""))
        titles = grp.get("titles", []) or []
        if not titles:
            continue
        top = titles[0]
        top_title = _esc(top.get("title", ""))
        src = _esc(top.get("source_name", ""))
        time_d = _esc(top.get("time_display", ""))
        extra = (
            f" <span style=\"color:var(--faint)\">+{len(titles) - 1}条</span>"
            if len(titles) > 1
            else ""
        )
        rows.append(
            f'<div class="cur-row">'
            f'<div class="cur-cat dim">{word}</div>'
            f'<div><div class="cur-topic plain">{top_title}</div>'
            f'<div class="cur-heat">{src}&nbsp;{time_d}{extra}</div></div>'
            f"</div>"
        )
    return "\n".join(rows)


def render_current_dashboard_html(
    ai_analysis: Optional[Any],
    report_metadata: Optional[Dict[str, Any]],
    generated_at: datetime,
    mode: str,
    stats: Optional[List[Dict[str, Any]]] = None,
    rss_items: Optional[List[Dict[str, Any]]] = None,
    full_href: str = "full.html",
) -> str:
    """
    渲染自包含的轻量盘面页（newsletter 风格、单文件、内联 CSS、无外部引用）。

    - environment 样式：异常信号列表 + 热榜/RSS 追踪 + 已抑制脚注。
    - 无异常信号：lead 改"未检测到异常信号"，仍展示热榜/RSS 追踪。
    - classic / ai_analysis is None：降级提示 + 热榜/RSS 追踪。

    stats / rss_items 为发布安全的追踪数据（公开榜单信息，无 URL）。
    任何 mode 都能出页，不受 cooldown / notify_labels 影响。
    """
    group = _group_for_mode(mode)
    title = "每日盘面" if group == "daily" else "当前盘面"
    display_time = _fmt_display(generated_at)

    # ── lead + 异常信号区 ──
    anomaly = 0
    signal_section = ""
    sup_html = ""
    if _is_environment(ai_analysis):
        radar = derive_radar_readout(getattr(ai_analysis, "overview_stats", {}) or {})
        anomaly = radar.get("anomaly", 0)
        suppressed_n = radar.get("suppressed", 0)

        if anomaly > 0:
            signals_html = _render_signal_rows(ai_analysis)
            signal_section = (
                '<div class="sec-label">异常信号</div>'
                f'<div class="cur-signals">{signals_html}</div>'
            )
            lead_html = (
                f'<div class="cur-lead"><strong>{anomaly}</strong>个异常信号'
                f" · {_esc(title)}</div>"
            )
        else:
            lead_html = f'<div class="cur-lead">{_esc(title)} · 未检测到异常信号</div>'

        # 已抑制脚注
        sup_names: List[str] = []
        for item in (getattr(ai_analysis, "sentiment_heavy", None) or []):
            t = (item.get("topic", "") or "").strip()
            if t:
                sup_names.append(_esc(t))
        for note in (getattr(ai_analysis, "background_notes", None) or []):
            note = (note or "").strip()
            if note:
                short = note[:28] + ("…" if len(note) > 28 else "")
                sup_names.append(_esc(short))
        if sup_names:
            sup_html = (
                f'<div class="cur-sup">已抑制 {suppressed_n} · '
                f'{"　·　".join(sup_names)}</div>'
            )
    else:
        lead_html = (
            f'<div class="cur-lead">{_esc(title)} · '
            "本次未生成信息环境监测盘面</div>"
        )

    # ── 热榜 / RSS 追踪区 ──
    hotlist_rows = _render_hotlist_rows(stats)
    rss_rows = _render_rss_rows(rss_items)
    tracking_html = ""
    if hotlist_rows or rss_rows:
        inner = "\n".join(x for x in (hotlist_rows, rss_rows) if x)
        tracking_html = (
            '<div class="cur-track"><div class="cur-signals">'
            f"{inner}</div></div>"
        )

    # 完全无内容时的兜底
    if not signal_section and not tracking_html:
        tracking_html = '<div class="cur-empty">当前暂无可展示的盘面数据。</div>'

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
  <div class="cur-head">
    <span class="cur-brand">TrendRadar</span>
    <span class="cur-ts">{_esc(display_time)}</span>
  </div>
  {lead_html}
  {signal_section}
  {tracking_html}
  {sup_html}
</div>
</body>
</html>"""


def write_dashboard(
    output_dir: str,
    mode: str,
    ai_analysis: Optional[Any],
    report_metadata: Optional[Dict[str, Any]],
    generated_at: datetime,
    stats: Optional[List[Dict[str, Any]]] = None,
    rss_items: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """
    渲染并写发布根的盘面产物（纯文件 IO，便于单测）：
    - output/public/{group}/index.html（盘面页）
    - output/public/{group}/state.json（发布安全摘要缓存）
    - output/public/index.html（落地页，幂等）

    **不写 full.html**（由 generate_html_report 负责）。

    stats / rss_items 为发布安全的追踪数据（公开榜单信息，无 URL），
    用于盘面页热榜/RSS 追踪区；不写入 state.json。

    Returns:
        str: 盘面页路径 output/public/{group}/index.html
    """
    group = _group_for_mode(mode)
    dashboard_html = render_current_dashboard_html(
        ai_analysis=ai_analysis,
        report_metadata=report_metadata,
        generated_at=generated_at,
        mode=mode,
        stats=stats,
        rss_items=rss_items,
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
