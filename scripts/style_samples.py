"""
4 种风格样品图生成器(供风格挑选)

同一条新闻(GPT-5.5)用 4 种风格各画一张 1080×1350 竖版图,
输出到 ~/Desktop/TrendRadar-styles/
"""

from PIL import Image, ImageDraw, ImageFont
from pathlib import Path
import textwrap

# ===== 字体 =====
F_HEITI_M = "/System/Library/Fonts/STHeiti Medium.ttc"
F_HEITI_L = "/System/Library/Fonts/STHeiti Light.ttc"
F_HIRAGINO = "/System/Library/Fonts/Hiragino Sans GB.ttc"
F_SONGTI = "/System/Library/Fonts/Supplemental/Songti.ttc"
F_HELVETICA = "/System/Library/Fonts/Helvetica.ttc"
F_EMOJI = "/System/Library/Fonts/Apple Color Emoji.ttc"


def draw_emoji(draw, xy, char, target_size, img):
    """
    渲染彩色 emoji。Apple Color Emoji 是位图字体,只支持几个固定尺寸(16/20/40/48/96/160)。
    我们渲染最近的固定尺寸到一张子图,再 resize 到目标尺寸贴回。
    """
    fixed_sizes = [160, 96, 48, 40, 20, 16]
    src_size = min(fixed_sizes, key=lambda s: abs(s - target_size))
    f = ImageFont.truetype(F_EMOJI, src_size)
    # 在透明子图上先画
    tmp = Image.new("RGBA", (src_size + 20, src_size + 20), (0, 0, 0, 0))
    tdraw = ImageDraw.Draw(tmp)
    tdraw.text((10, 10), char, font=f, embedded_color=True)
    bbox = tmp.getbbox()
    if bbox:
        tmp = tmp.crop(bbox)
    # 等比缩放到目标尺寸
    ratio = target_size / max(tmp.size)
    new_size = (int(tmp.size[0] * ratio), int(tmp.size[1] * ratio))
    tmp = tmp.resize(new_size, Image.LANCZOS)
    img.paste(tmp, xy, tmp)
F_TIMES = "/System/Library/Fonts/Supplemental/Times New Roman.ttf"

W, H = 1080, 1350

# ===== 同一条新闻数据(用最好的 GPT-5.5 那条) =====
NEWS = {
    "category": "AI 领域",
    "emoji": "🤖",
    "title_zh": "OpenAI 发布 GPT-5.5,1M 上下文 + 速度翻倍",
    "title_en": "Introducing GPT-5.5",
    "insight": (
        "OpenAI 于 4 月 30 日正式发布 GPT-5.5,首 token 延迟从 3 秒降到 1.5 秒,"
        "上下文窗口升级到 1M tokens,价格比 5.4 降低约 30%(输入 $10/M,输出 $30/M)。"
        "在 SWE-bench、GPQA、AIME 等多项基准上创下新纪录,原生支持 Codex 工具调用。"
        "1M context 降到这个价位后,RAG 类应用的成本顾虑基本消除。"
    ),
    "source": "openai-blog",
    "url": "https://openai.com/news/gpt-5-5",
    "date": "2026-05-02",
}


def wrap_text(text, font, max_width, draw):
    """根据像素宽度自动换行"""
    lines, current = [], ""
    for ch in text:
        test = current + ch
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] > max_width and current:
            lines.append(current)
            current = ch
        else:
            current = test
    if current:
        lines.append(current)
    return lines


# ============================================================
# 风格 1: 暗黑科技
# ============================================================
def style_dark_tech():
    img = Image.new("RGB", (W, H), "#0F1115")
    d = ImageDraw.Draw(img)

    # 顶部装饰条
    d.rectangle([(0, 0), (W, 8)], fill="#4cc9f0")

    # 顶部信息(分类标签 + 日期)
    f_tag = ImageFont.truetype(F_HEITI_M, 22)
    f_date = ImageFont.truetype(F_HELVETICA, 22)
    d.rectangle([(60, 60), (260, 110)], fill="#1a3a52")
    d.text((85, 72), f"AI 领域", fill="#4cc9f0", font=f_tag)
    d.text((W - 230, 72), NEWS["date"], fill="#666", font=f_date)

    # 大 emoji(彩色)
    draw_emoji(d, (W // 2 - 90, 160), NEWS["emoji"], 180, img)

    # 主标题
    f_title = ImageFont.truetype(F_HEITI_M, 54)
    title_lines = wrap_text(NEWS["title_zh"], f_title, W - 120, d)
    y = 400
    for line in title_lines:
        d.text((60, y), line, fill="#ffffff", font=f_title)
        y += 70

    # 英文副标题
    f_sub = ImageFont.truetype(F_TIMES, 28)
    d.text((60, y + 10), NEWS["title_en"], fill="#888", font=f_sub)

    # 分隔线
    y_div = y + 70
    d.rectangle([(60, y_div), (W - 60, y_div + 2)], fill="#2a2e36")

    # insight
    f_body = ImageFont.truetype(F_HEITI_L, 30)
    body_lines = wrap_text(NEWS["insight"], f_body, W - 120, d)
    y = y_div + 30
    for line in body_lines[:8]:
        d.text((60, y), line, fill="#cccccc", font=f_body)
        y += 45

    # 底部源标
    f_meta = ImageFont.truetype(F_HELVETICA, 22)
    d.text((60, H - 80), f"◆ {NEWS['source']}", fill="#4cc9f0", font=f_meta)
    d.text((W - 280, H - 80), "TrendRadar Daily", fill="#666", font=f_meta)

    return img


# ============================================================
# 风格 2: 小红书亮色
# ============================================================
def style_xiaohongshu():
    img = Image.new("RGB", (W, H), "#ffffff")
    d = ImageDraw.Draw(img)

    # 顶部红色色块
    d.rectangle([(60, 60), (W - 60, 240)], fill="#ff2742")
    f_tag = ImageFont.truetype(F_HEITI_M, 38)
    d.text((90, 95), "今日 AI 必看", fill="#ffffff", font=f_tag)
    f_date = ImageFont.truetype(F_HELVETICA, 28)
    d.text((90, 160), NEWS["date"] + " · OpenAI", fill="#ffe5e8", font=f_date)
    draw_emoji(d, (W - 200, 80), NEWS["emoji"], 100, img)

    # 主标题(超大)
    f_title = ImageFont.truetype(F_HEITI_M, 60)
    title_lines = wrap_text(NEWS["title_zh"], f_title, W - 120, d)
    y = 290
    for line in title_lines:
        d.text((60, y), line, fill="#222222", font=f_title)
        y += 78

    # 红色重点条
    d.rectangle([(60, y + 10), (180, y + 16)], fill="#ff2742")

    # insight
    f_body = ImageFont.truetype(F_HEITI_L, 30)
    body_lines = wrap_text(NEWS["insight"], f_body, W - 120, d)
    y += 45
    for line in body_lines[:9]:
        d.text((60, y), line, fill="#444444", font=f_body)
        y += 46

    # 底部 CTA 框
    d.rounded_rectangle([(60, H - 120), (W - 60, H - 50)], radius=20, fill="#fff0f2")
    f_cta = ImageFont.truetype(F_HEITI_M, 28)
    d.text((90, H - 105), f"💡 点链接看原文 · {NEWS['source']}", fill="#ff2742", font=f_cta)

    return img


# ============================================================
# 风格 3: 极简日式
# ============================================================
def style_minimal_jp():
    img = Image.new("RGB", (W, H), "#f5f0e8")
    d = ImageDraw.Draw(img)

    # 顶部小标记
    d.rectangle([(80, 100), (90, 160)], fill="#222")
    f_tag = ImageFont.truetype(F_HEITI_M, 22)
    d.text((110, 110), "AI 領域", fill="#222", font=f_tag)
    d.text((110, 145), NEWS["date"], fill="#888", font=f_tag)

    # 大量留白后的主标题
    f_title = ImageFont.truetype(F_SONGTI, 52, index=2)  # Songti Bold-ish
    title_lines = wrap_text(NEWS["title_zh"], f_title, W - 200, d)
    y = 320
    for line in title_lines:
        d.text((80, y), line, fill="#1a1a1a", font=f_title)
        y += 76

    # 英文小字
    f_en = ImageFont.truetype(F_TIMES, 26)
    d.text((80, y + 20), NEWS["title_en"], fill="#999", font=f_en)

    # 一根细线分隔
    y_div = y + 80
    d.rectangle([(80, y_div), (W - 80, y_div + 1)], fill="#bbb")

    # 正文(Songti 衬线中文)
    f_body = ImageFont.truetype(F_SONGTI, 28)
    body_lines = wrap_text(NEWS["insight"], f_body, W - 160, d)
    y = y_div + 35
    for line in body_lines[:9]:
        d.text((80, y), line, fill="#333", font=f_body)
        y += 44

    # 底部极简标
    f_meta = ImageFont.truetype(F_TIMES, 22)
    d.text((80, H - 70), f"— {NEWS['source']}", fill="#888", font=f_meta)

    return img


# ============================================================
# 风格 4: 杂志风
# ============================================================
def style_magazine():
    img = Image.new("RGB", (W, H), "#fff8e7")
    d = ImageDraw.Draw(img)

    # 顶部杂志标识
    f_brand = ImageFont.truetype(F_TIMES, 48)
    d.text((60, 50), "TRENDRADAR", fill="#1a1a1a", font=f_brand)
    f_date = ImageFont.truetype(F_TIMES, 22)
    d.text((60, 110), f"VOL.001 · {NEWS['date']} · AI", fill="#888", font=f_date)
    d.rectangle([(60, 150), (W - 60, 154)], fill="#1a1a1a")

    # 大型 Drop Cap 风格 emoji(彩色)
    draw_emoji(d, (W - 200, 50), NEWS["emoji"], 140, img)

    # 主标题(大衬线 + 段首大写)
    f_title = ImageFont.truetype(F_TIMES, 64)
    f_title_zh = ImageFont.truetype(F_SONGTI, 50, index=2)
    d.text((60, 230), NEWS["title_en"], fill="#1a1a1a", font=f_title)

    title_lines = wrap_text(NEWS["title_zh"], f_title_zh, W - 120, d)
    y = 320
    for line in title_lines:
        d.text((60, y), line, fill="#444", font=f_title_zh)
        y += 70

    # 引用块装饰
    quote_y = y + 30
    d.rectangle([(60, quote_y), (66, quote_y + 50)], fill="#cc7722")  # 复古橙竖条

    f_lead = ImageFont.truetype(F_SONGTI, 30)
    lead = NEWS["insight"][:40] + "……"
    d.text((85, quote_y + 5), lead, fill="#1a1a1a", font=f_lead)

    # 双栏正文(单栏简化版,保留杂志感)
    f_body = ImageFont.truetype(F_SONGTI, 28)
    body_lines = wrap_text(NEWS["insight"], f_body, W - 120, d)
    y = quote_y + 100
    for line in body_lines[:8]:
        d.text((60, y), line, fill="#222", font=f_body)
        y += 42

    # 底部页码 + 来源
    d.rectangle([(60, H - 90), (W - 60, H - 86)], fill="#1a1a1a")
    f_meta = ImageFont.truetype(F_TIMES, 22)
    d.text((60, H - 70), f"— Source: {NEWS['source']}", fill="#666", font=f_meta)
    d.text((W - 110, H - 70), "P.001", fill="#666", font=f_meta)

    return img


def main():
    out_dir = Path.home() / "Desktop" / "TrendRadar-styles"
    out_dir.mkdir(exist_ok=True)

    styles = [
        ("01_暗黑科技.png", style_dark_tech),
        ("02_小红书亮色.png", style_xiaohongshu),
        ("03_极简日式.png", style_minimal_jp),
        ("04_杂志风.png", style_magazine),
    ]

    for name, fn in styles:
        print(f"生成 {name} ...")
        img = fn()
        img.save(out_dir / name, "PNG", optimize=True)

    print(f"\n✅ 4 张样品已生成: {out_dir}")
    import subprocess
    subprocess.run(["open", str(out_dir)])


if __name__ == "__main__":
    main()
