"""
TrendRadar 分类长图渲染器（杂志风）

单一职责:把一个分类（10 条新闻 + summary）渲染成一张
1080×N 的超长 PNG。N 由内容动态决定（≈ 4000-6000px）。

风格:奶油底 + 衬线（Songti / Times）+ 类目色 accent。

公开 API: render_category_long(category_data, date, out_path, images=None)
"""

from __future__ import annotations

from pathlib import Path
from PIL import Image, ImageDraw

from cards_common import (
    W,
    COLORS,
    CATEGORY_ACCENT,
    font,
    wrap_text,
    draw_paragraph,
    fit_image_into,
    placeholder_block,
    DIGEST_DIR,
    CARDS_DIR,
)


# ===== 版式常量 =====
PAD_X = 60                      # 左右页边
TOP_PAD = 60                    # 顶部留白
BOTTOM_PAD = 80                 # 底部留白
CONTENT_W = W - PAD_X * 2       # 1080 - 120 = 960

# 条目内部:序号列 + 内容列
NUM_COL_W = 80                  # 序号列宽（"01" 大字 + 右侧间距）
ITEM_GAP_LEFT = 20              # 序号与内容之间的间距
ITEM_CONTENT_W = CONTENT_W - NUM_COL_W - ITEM_GAP_LEFT  # 860

# 内容列里:标题区（左）+ 配图（右）
THUMB_W = 320
THUMB_H = 240
THUMB_GAP = 24
TITLE_W = ITEM_CONTENT_W - THUMB_W - THUMB_GAP   # 516

# Insight 段落跨满整个内容列
INSIGHT_W = ITEM_CONTENT_W

# 段落行距
LS_SUMMARY = 14
LS_INSIGHT = 12
LS_TITLE = 8


# ===== 高度估算 =====
def _para_height(text: str, fnt, max_w: int, line_spacing: int) -> tuple[int, int]:
    """返回 (像素高度, 行数)。

    注意: 这里的高度匹配 cards_common.draw_paragraph 实际消耗的 y 增量,
    即每行都加 line_h + line_spacing(包括最后一行,等于段尾留了一行间距)。
    这样 estimate 才不会比实际渲染少。
    """
    if not text:
        return 0, 0
    tmp_img = Image.new("RGB", (10, 10))
    tmp_draw = ImageDraw.Draw(tmp_img)
    lines = wrap_text(text, fnt, max_w, tmp_draw)
    if not lines:
        return 0, 0
    asc, desc = fnt.getmetrics()
    line_h = asc + desc
    n = len(lines)
    return n * (line_h + line_spacing), n


# 2026-05-12 加: 前沿声音区块的源名简写映射
_FRONTIER_SOURCE_LABEL = {
    "openai-blog": "OpenAI",
    "anthropic-blog": "Anthropic",
    "deepmind-blog": "DeepMind",
    "lex-fridman": "Lex Fridman",
    "dwarkesh": "Dwarkesh",
    "no-priors": "No Priors",
    "ai-explained": "AI Explained",
}


def _short_source_name(source_id: str) -> str:
    return _FRONTIER_SOURCE_LABEL.get(source_id, source_id)


def _frontier_voices_height(frontier_voices) -> int:
    """估算'今日 AI 前沿声音'区块占用高度。

    frontier_voices 是 dict: {"summary": str, "items": [{title, title_zh, insight, ...}]}
    """
    if not frontier_voices or not isinstance(frontier_voices, dict):
        return 0
    fv_items = frontier_voices.get("items") or []
    fv_summary = frontier_voices.get("summary") or ""
    if not fv_items and not fv_summary:
        return 0

    h = 20  # 与上方间距
    # 小标题块
    fv_title_fnt = font(56, bold=True, kind="songti")
    asc, desc = fv_title_fnt.getmetrics()
    h += asc + desc + 8
    fv_sub_fnt = font(24, kind="times")
    asc, desc = fv_sub_fnt.getmetrics()
    h += asc + desc + 18
    h += 3 + 32  # 分隔线

    # summary 段(带左竖条)
    if fv_summary:
        sum_fnt = font(30, kind="songti")
        sum_h, _ = _para_height(fv_summary, sum_fnt, CONTENT_W - 24, LS_SUMMARY)
        h += sum_h + 60

    # items(用主分类同款字体高度估算)
    item_title_fnt = font(44, bold=True, kind="songti")
    item_en_fnt = font(24, kind="times")
    item_insight_fnt = font(28, kind="songti")
    item_source_fnt = font(20, kind="helvetica")
    for it in fv_items[:12]:
        h += _item_height(it, item_title_fnt, item_en_fnt, item_insight_fnt, item_source_fnt)
        h += 36 + 1 + 36
    return h


def _estimate_height(category_data: dict, date: str, frontier_voices: list | None = None) -> int:
    """提前算总高度,避免开 1080×10000 浪费内存。"""
    # 顶部块
    h = TOP_PAD
    # 类目大标题(Songti Bold 80) — 单行
    title_fnt = font(80, bold=True, kind="songti")
    asc, desc = title_fnt.getmetrics()
    h += asc + desc
    h += 12  # 标题与副标间距
    # 副标 (Times Italic 28 — Times 没斜体我们用 times regular 替代)
    sub_fnt = font(28, kind="times")
    asc, desc = sub_fnt.getmetrics()
    h += asc + desc
    h += 18  # 副标与横线间距
    # 顶部横线
    h += 4
    h += 32  # 横线与 summary 间距
    # summary 段落
    sum_fnt = font(30, kind="songti")
    sum_h, _ = _para_height(category_data.get("summary", ""), sum_fnt, CONTENT_W - 24, LS_SUMMARY)
    # +24 给左竖条留位置
    h += sum_h
    h += 60  # summary 与第一条 item 间距

    # 每条 item
    item_title_fnt = font(44, bold=True, kind="songti")
    item_en_fnt = font(24, kind="times")
    item_insight_fnt = font(28, kind="songti")
    item_source_fnt = font(20, kind="helvetica")

    items = category_data.get("items", [])[:10]
    for it in items:
        item_h = _item_height(
            it,
            item_title_fnt,
            item_en_fnt,
            item_insight_fnt,
            item_source_fnt,
        )
        h += item_h
        h += 36  # 区块底部留白
        h += 1   # 分隔线
        h += 36  # 分隔线与下一条间距

    # 2026-05-12 加: AI 卡有 frontier_voices 时的额外高度
    h += _frontier_voices_height(frontier_voices)

    # 底部
    h += 8       # 底部横线（4px) + 上方留白补偿
    h += 28      # 横线上方间距
    h += 4       # 横线
    h += 18      # 横线与页脚间距
    foot_fnt = font(20, kind="helvetica")
    asc, desc = foot_fnt.getmetrics()
    h += asc + desc
    h += BOTTOM_PAD
    return h


def _item_height(it: dict, title_fnt, en_fnt, insight_fnt, source_fnt) -> int:
    """单条新闻的高度（不含底部分隔线/留白) — 已取消右侧配图,标题/英文/insight 全部跨满 INSIGHT_W。"""
    title_zh = it.get("title_zh") or it.get("title") or ""
    title_en = it.get("title") or ""
    show_en = bool(title_en) and title_en.strip() != title_zh.strip()

    title_h, _ = _para_height(title_zh, title_fnt, INSIGHT_W, LS_TITLE)
    en_h = 0
    if show_en:
        en_h, _ = _para_height(title_en, en_fnt, INSIGHT_W, 4)
        en_h += 8  # 标题与英文之间间距
    head_h = title_h + en_h

    # insight
    insight = it.get("insight") or ""
    insight_h, _ = _para_height(insight, insight_fnt, INSIGHT_W, LS_INSIGHT)

    # source
    source = it.get("source") or ""
    url = it.get("url") or ""
    src_text = _format_source_line(source, url)
    src_h, _ = _para_height(src_text, source_fnt, INSIGHT_W, 4)

    return head_h + 24 + insight_h + 18 + src_h


def _format_source_line(source: str, url: str) -> str:
    """格式化底部来源行: '— source · url'"""
    s = source.strip() if source else ""
    u = url.strip() if url else ""
    if not s and not u:
        return "— 无原文链接"
    parts = []
    if s:
        parts.append(s)
    if u:
        parts.append(u)
    else:
        parts.append("无原文链接")
    return "— " + " · ".join(parts)


# ===== 主渲染 =====
def render_category_long(
    category_data: dict,
    date: str,
    out_path: Path,
    images: dict[str, Path] | None = None,
    frontier_voices: list | None = None,
) -> Path:
    """渲染分类长图,返回 out_path。

    2026-05-12 加: frontier_voices — 仅 AI 卡使用,渲染底部"今日 AI 前沿声音"小区块。
    """
    images = images or {}
    cat_name = category_data.get("name", "")
    summary = category_data.get("summary", "")
    items = category_data.get("items", [])[:10]
    # 只有 AI 卡才显示 frontier_voices
    if cat_name != "AI 领域":
        frontier_voices = None

    accent = CATEGORY_ACCENT.get(cat_name, COLORS["accent"])

    # ---- 高度预估 ----
    H = _estimate_height(category_data, date, frontier_voices=frontier_voices)

    # ---- 创建画布 ----
    canvas = Image.new("RGB", (W, H), COLORS["bg"])
    draw = ImageDraw.Draw(canvas)

    y = TOP_PAD

    # ---- 类目大标题 ----
    title_fnt = font(80, bold=True, kind="songti")
    draw.text((PAD_X, y), cat_name, fill=accent, font=title_fnt)
    asc, desc = title_fnt.getmetrics()
    y += asc + desc + 12

    # ---- 英文副标 + 日期 ----
    sub_fnt = font(28, kind="times")
    sub_text = _english_section_label(cat_name) + "  ·  " + date
    draw.text((PAD_X, y), sub_text, fill=COLORS["ink_soft"], font=sub_fnt)
    asc, desc = sub_fnt.getmetrics()
    y += asc + desc + 18

    # ---- 顶部类目色横线(粗 4px,横跨内容区) ----
    draw.rectangle([(PAD_X, y), (W - PAD_X, y + 3)], fill=accent)
    y += 4 + 32

    # ---- summary 段落（左竖条 + 段落） ----
    if summary:
        sum_fnt = font(30, kind="songti")
        sum_para_w = CONTENT_W - 24  # 给左竖条腾空间
        # 先算高度,画竖条
        sum_h, _ = _para_height(summary, sum_fnt, sum_para_w, LS_SUMMARY)
        # 左竖条
        draw.rectangle([(PAD_X, y), (PAD_X + 5, y + sum_h)], fill=accent)
        # 段落,左缩进 24
        draw_paragraph(
            draw,
            (PAD_X + 24, y),
            summary,
            sum_fnt,
            sum_para_w,
            color=COLORS["ink"],
            line_spacing=LS_SUMMARY,
        )
        y += sum_h
    y += 60

    # ---- 10 条新闻 ----
    item_title_fnt = font(44, bold=True, kind="songti")
    item_en_fnt = font(24, kind="times")
    item_insight_fnt = font(28, kind="songti")
    item_source_fnt = font(20, kind="helvetica")
    item_num_fnt = font(60, bold=True, kind="times")

    for idx, it in enumerate(items, 1):
        y = _draw_item(
            canvas,
            draw,
            y,
            idx,
            it,
            accent,
            item_num_fnt,
            item_title_fnt,
            item_en_fnt,
            item_insight_fnt,
            item_source_fnt,
            images,
        )
        # 区块底部留白 + 分隔线
        y += 36
        draw.rectangle(
            [(PAD_X, y), (W - PAD_X, y)],
            fill=COLORS["accent_soft"],
        )
        y += 1 + 36

    # 2026-05-12 加: 仅 AI 卡 — "今日 AI 前沿声音"详细区块(走 _draw_item 同款排版)
    if frontier_voices and isinstance(frontier_voices, dict):
        fv_items = frontier_voices.get("items") or []
        fv_summary = frontier_voices.get("summary") or ""
        if fv_items or fv_summary:
            y += 20
            # 大标题
            fv_title_fnt = font(56, bold=True, kind="songti")
            draw.text((PAD_X, y), "今日 AI 前沿声音", fill=accent, font=fv_title_fnt)
            asc, desc = fv_title_fnt.getmetrics()
            y += asc + desc + 8
            # 英文副标
            fv_sub_fnt = font(24, kind="times")
            sub_text = f"FRONTIER VOICES  ·  {len(fv_items)} highlights from AI execs · podcasts · blogs"
            draw.text((PAD_X, y), sub_text, fill=COLORS["ink_soft"], font=fv_sub_fnt)
            asc, desc = fv_sub_fnt.getmetrics()
            y += asc + desc + 18
            # 顶部横线(粗,类目色)
            draw.rectangle([(PAD_X, y), (W - PAD_X, y + 3)], fill=accent)
            y += 3 + 32

            # 顶部 summary 段(带左竖条,跟分类 summary 一致)
            if fv_summary:
                sum_fnt = font(30, kind="songti")
                sum_para_w = CONTENT_W - 24
                sum_h, _ = _para_height(fv_summary, sum_fnt, sum_para_w, LS_SUMMARY)
                draw.rectangle([(PAD_X, y), (PAD_X + 5, y + sum_h)], fill=accent)
                draw_paragraph(
                    draw, (PAD_X + 24, y), fv_summary, sum_fnt, sum_para_w,
                    color=COLORS["ink"], line_spacing=LS_SUMMARY,
                )
                y += sum_h
                y += 60

            # items — 复用主分类同款排版
            for fv_idx, it in enumerate(fv_items[:12], 1):
                y = _draw_item(
                    canvas, draw, y, fv_idx, it, accent,
                    item_num_fnt, item_title_fnt, item_en_fnt,
                    item_insight_fnt, item_source_fnt, images,
                )
                y += 36
                draw.rectangle([(PAD_X, y), (W - PAD_X, y)], fill=COLORS["accent_soft"])
                y += 1 + 36

    # ---- 底部横线 + 页脚 ----
    y += 28
    draw.rectangle([(PAD_X, y), (W - PAD_X, y + 3)], fill=accent)
    y += 4 + 18
    foot_fnt = font(20, kind="helvetica")
    foot_text = f"TRENDRADAR  ·  {date}"
    draw.text((PAD_X, y), foot_text, fill=COLORS["muted"], font=foot_fnt)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, "PNG", optimize=True)
    return out_path


def _english_section_label(cat_name: str) -> str:
    return {
        "AI 领域": "THE AI FRONTIER",
        "GitHub 开源生态": "OPEN-SOURCE PULSE",
        "生物医疗工程": "BIO & MEDTECH",
        "Hacker News": "HACKER NEWS · TECH COMMUNITY",
        "国际局势": "WORLD AFFAIRS · GLOBAL DESK",
    }.get(cat_name, "SECTION")


def _draw_item(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    y: int,
    idx: int,
    it: dict,
    accent: str,
    num_fnt,
    title_fnt,
    en_fnt,
    insight_fnt,
    source_fnt,
    images: dict[str, Path],
) -> int:
    """画单条 item,返回 insight 段落底部 y(不含区块下方留白/分隔线)。"""
    title_zh = (it.get("title_zh") or it.get("title") or "").strip()
    title_en = (it.get("title") or "").strip()
    show_en = bool(title_en) and title_en != title_zh
    insight = it.get("insight") or ""
    source = it.get("source") or ""
    url = it.get("url") or ""

    # ---- 序号 (左列) ----
    num_text = f"{idx:02d}"
    draw.text((PAD_X, y - 6), num_text, fill=accent, font=num_fnt)
    # 序号下方装饰线（短粗线,类目色）
    asc, desc = num_fnt.getmetrics()
    num_h = asc + desc
    draw.rectangle(
        [(PAD_X, y - 6 + num_h + 4), (PAD_X + 36, y - 6 + num_h + 4 + 2)],
        fill=accent,
    )

    # ---- 内容列起点(取消右侧配图,标题和 insight 跨满 INSIGHT_W) ----
    content_x = PAD_X + NUM_COL_W + ITEM_GAP_LEFT
    head_y = y

    # ---- 中文标题(左,跨满整个内容宽度) ----
    title_y = head_y
    title_y_after = draw_paragraph(
        draw,
        (content_x, title_y),
        title_zh,
        title_fnt,
        INSIGHT_W,  # 取消配图后,标题宽度 = INSIGHT_W
        color=COLORS["ink"],
        line_spacing=LS_TITLE,
    )
    # 英文原标题(灰色,小字)
    if show_en:
        title_y_after += 8
        title_y_after = draw_paragraph(
            draw,
            (content_x, title_y_after),
            title_en,
            en_fnt,
            INSIGHT_W,
            color=COLORS["muted"],
            line_spacing=4,
        )

    head_bottom = title_y_after

    # ---- insight 段落（跨满 INSIGHT_W） ----
    ins_y = head_bottom + 24
    ins_y_after = draw_paragraph(
        draw,
        (content_x, ins_y),
        insight,
        insight_fnt,
        INSIGHT_W,
        color=COLORS["ink"],
        line_spacing=LS_INSIGHT,
    )

    # ---- 来源 + URL ----
    src_y = ins_y_after + 18
    src_text = _format_source_line(source, url)
    src_y_after = draw_paragraph(
        draw,
        (content_x, src_y),
        src_text,
        source_fnt,
        INSIGHT_W,
        color=COLORS["muted"],
        line_spacing=4,
    )

    return src_y_after


# ===== 自测 =====
if __name__ == "__main__":
    import json

    data = json.loads((DIGEST_DIR / "latest.json").read_text(encoding="utf-8"))
    date = data["date"]
    CARDS_DIR.mkdir(parents=True, exist_ok=True)

    for i, cat in enumerate(data["report"]["categories"], 1):
        out = CARDS_DIR / f"test_cat_{i:02d}.png"
        render_category_long(cat, date, out, images=None)
        h = Image.open(out).height
        size_kb = out.stat().st_size / 1024
        print(f"OK {cat['name']}: {out}, height={h}px, size={size_kb:.1f}KB")
