"""
封面图渲染:1080x1350 杂志/目录页风。

设计(2026-05-02 v3 — 4 类目录页):
  - 顶部 brand + 日期(简洁)
  - 一行大字 hero tagline(避开数字 hero,因为之前 30 数字版本会和下方文字重叠)
  - 4 个类目纵向排列(每行:左色块 + 类目中英文 + 右侧条数 + 头条标题精简)
  - 底部细线 + 引导

调用方式:
    from render_cover import render_cover
    render_cover(latest_data, out_path)
"""

from pathlib import Path
from PIL import Image, ImageDraw

from cards_common import (
    font,
    wrap_text,
    draw_horizontal_rule,
    COLORS,
    CATEGORY_ACCENT,
    W,
    COVER_H,
    DIGEST_DIR,
    ROOT,
)


# ===== 版面常量 =====
MARGIN_X = 80


def _truncate(text: str, max_chars: int) -> str:
    text = (text or "").strip().replace("\n", " ")
    if len(text) <= max_chars:
        return text
    return text[:max_chars - 1] + "…"


def _peek_lead(insight: str, max_chars: int = 40) -> str:
    """从 insight 取一行精简见解(去掉常见标签前缀)"""
    if not insight:
        return ""
    text = insight.strip().replace("\n", " ").replace("  ", " ")
    for sep in ("：", ": "):
        if sep in text[:20]:
            head, _, rest = text.partition(sep)
            if rest.strip():
                text = rest.strip()
                break
    return _truncate(text, max_chars)


def render_cover(latest_data: dict, out_path: Path) -> Path:
    """渲染目录页式封面 1080×1350。"""
    date_str = latest_data.get("date", "")
    report = latest_data.get("report", {}) or {}
    categories = report.get("categories", []) or []
    total_count = sum(len(c.get("items", []) or []) for c in categories)
    n_cat = len(categories)

    img = Image.new("RGB", (W, COVER_H), COLORS["bg"])
    d = ImageDraw.Draw(img)

    # ========== Top: brand ==========
    f_brand = font(36, kind="times", bold=True)
    d.text((MARGIN_X, 70), "TRENDRADAR", fill=COLORS["ink"], font=f_brand)
    f_meta = font(20, kind="times")
    d.text((MARGIN_X, 122), f"DAILY DIGEST  ·  VOL.001  ·  {date_str}", fill=COLORS["muted"], font=f_meta)
    draw_horizontal_rule(d, 165, x0=MARGIN_X, x1=W - MARGIN_X, color=COLORS["rule"], weight=2)

    # ========== Hero tagline(单行,大但不夸张) ==========
    f_hero_zh = font(56, kind="songti", bold=True)
    f_hero_en = font(20, kind="times")
    hero_y = 210
    hero_text = f"今日 · {n_cat} 大版块 · {total_count} 条精读"
    d.text((MARGIN_X, hero_y), hero_text, fill=COLORS["ink"], font=f_hero_zh)
    d.text((MARGIN_X, hero_y + 80), "AI · OPEN-SOURCE · BIO & MEDTECH · HACKER NEWS · WORLD AFFAIRS",
           fill=COLORS["accent"], font=f_hero_en)

    # 复古橙短线,作为 hero 与目录的分隔
    sep_y = hero_y + 130
    d.rectangle([(MARGIN_X, sep_y), (MARGIN_X + 80, sep_y + 4)], fill=COLORS["accent"])

    # ========== 4 大版块目录(竖向) ==========
    list_y_start = sep_y + 50
    row_h = (COVER_H - list_y_start - 130) // max(n_cat, 1)  # 留出底部 130px 给页脚

    # 每行内的字体
    f_cat_zh = font(38, kind="songti", bold=True)
    f_cat_en = font(18, kind="times")
    f_count = font(40, kind="times", bold=True)
    f_count_label = font(16, kind="times")
    # 用 Songti bold 是因为 Songti regular 没有 '›' (U+203A) 字形,会渲染成豆腐块
    f_lead = font(22, kind="songti", bold=True)

    en_label = {
        "AI 领域": "THE AI FRONTIER",
        "GitHub 开源生态": "OPEN-SOURCE PULSE",
        "生物医疗工程": "BIO & MEDTECH",
        "Hacker News": "HACKER NEWS · TECH COMMUNITY",
        "国际局势": "WORLD AFFAIRS · GLOBAL DESK",
    }

    for i, cat in enumerate(categories):
        y0 = list_y_start + i * row_h
        cat_name = cat.get("name", "")
        accent = CATEGORY_ACCENT.get(cat_name, COLORS["accent"])
        items = cat.get("items") or []

        # 左侧色块竖条 6×80
        bar_h = 80
        bar_y = y0 + (row_h - bar_h) // 2
        d.rectangle([(MARGIN_X, bar_y), (MARGIN_X + 6, bar_y + bar_h)], fill=accent)

        # 中文类目名
        name_x = MARGIN_X + 28
        # 计算垂直居中起点
        asc_zh, desc_zh = f_cat_zh.getmetrics()
        asc_en, desc_en = f_cat_en.getmetrics()
        block_h = (asc_zh + desc_zh) + 6 + (asc_en + desc_en)
        text_y_top = bar_y + (bar_h - block_h) // 2
        d.text((name_x, text_y_top), cat_name, fill=COLORS["ink"], font=f_cat_zh)
        d.text((name_x, text_y_top + asc_zh + desc_zh + 6), en_label.get(cat_name, ""),
               fill=COLORS["muted"], font=f_cat_en)

        # 右侧条数 — 大字 "10" + "PICKS"
        cnt = len(items)
        cnt_text = f"{cnt:02d}"
        cnt_bbox = d.textbbox((0, 0), cnt_text, font=f_count)
        cnt_w = cnt_bbox[2] - cnt_bbox[0]
        cnt_x = W - MARGIN_X - cnt_w - 80
        cnt_y = bar_y + 8
        d.text((cnt_x, cnt_y), cnt_text, fill=accent, font=f_count)
        d.text((cnt_x + cnt_w + 12, cnt_y + 18), "PICKS", fill=COLORS["muted"], font=f_count_label)

        # 头条 lead(色块下方,如果空间允许)
        if items and row_h > 130:
            lead = _peek_lead(items[0].get("insight", ""), max_chars=42)
            if lead:
                lead_y = bar_y + bar_h + 10
                if lead_y + 30 < y0 + row_h:  # 别越界
                    # 限制在不超出 row 的范围
                    lead_lines = wrap_text(lead, f_lead, W - MARGIN_X * 2 - 20, d)
                    if lead_lines:
                        first = lead_lines[0]
                        if len(lead_lines) > 1:
                            first = first[:-1] + "…"
                        d.text((name_x, lead_y), f"› {first}", fill=COLORS["ink_soft"], font=f_lead)

        # 行间分隔线
        if i < n_cat - 1:
            sep2_y = y0 + row_h - 1
            d.rectangle([(MARGIN_X, sep2_y), (W - MARGIN_X, sep2_y)], fill=COLORS["accent_soft"])

    # ========== 底部 ==========
    foot_rule_y = COVER_H - 90
    draw_horizontal_rule(d, foot_rule_y, x0=MARGIN_X, x1=W - MARGIN_X, color=COLORS["rule"], weight=2)
    # 中文用 Songti(Times 不含中文字形,会渲染成豆腐块/小方框)
    f_foot = font(20, kind="songti")
    d.text((MARGIN_X, foot_rule_y + 18), "深度精读 · 每天一份",
           fill=COLORS["ink_soft"], font=f_foot)
    # 右下角不放任何技术细节字样(/output/digest/ 这种暴露路径会掉档次)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG", optimize=True)
    return out_path


if __name__ == "__main__":
    import json

    data = json.loads((DIGEST_DIR / "latest.json").read_text(encoding="utf-8"))
    out = ROOT / "output" / "cards" / "test_cover.png"
    render_cover(data, out)
    size_kb = out.stat().st_size / 1024
    print(f"已生成 {out} ({size_kb:.1f} KB)")
