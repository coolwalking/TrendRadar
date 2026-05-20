"""
图卡渲染共享工具模块

所有图片渲染脚本(render_cover.py / render_category.py / digest_to_card.py)
都使用这里的字体、颜色、文本换行工具,保证视觉一致性。

杂志风调色板 + Songti/Times 衬线字体。
"""

from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

# ===== 输出尺寸 =====
W = 1080  # 宽度统一 1080
COVER_H = 1350  # 封面高度

# ===== macOS 系统字体路径 =====
F_HEITI_M = "/System/Library/Fonts/STHeiti Medium.ttc"  # 中文无衬线 中粗
F_HEITI_L = "/System/Library/Fonts/STHeiti Light.ttc"   # 中文无衬线 细
F_HIRAGINO = "/System/Library/Fonts/Hiragino Sans GB.ttc"  # 中文备用
F_SONGTI = "/System/Library/Fonts/Supplemental/Songti.ttc"  # 中文衬线(主用)
F_TIMES = "/System/Library/Fonts/Supplemental/Times New Roman.ttf"  # 英文衬线
F_TIMES_BOLD = "/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf"
F_HELVETICA = "/System/Library/Fonts/Helvetica.ttc"  # 英文无衬线(metadata 用)

# Songti.ttc 索引(0=Regular, 2=Bold-ish, 3=Black)— 使用 ImageFont.truetype(F_SONGTI, size, index=N)

# ===== 杂志风调色板 =====
COLORS = {
    "bg": "#fff8e7",          # 主背景:奶油色
    "bg_alt": "#f7eed5",      # 次背景:略深奶油
    "ink": "#1a1a1a",         # 主文字:近黑
    "ink_soft": "#444444",    # 次文字
    "muted": "#888888",       # 弱文字(日期、来源)
    "rule": "#1a1a1a",        # 主分隔线:黑
    "accent": "#cc7722",      # 复古橙(引用条、强调)
    "accent_soft": "#e6ddc4", # 浅米色(底纹)
    "category_ai": "#2a4d6e",       # AI 类章节色:深蓝
    "category_github": "#3d5a3d",   # GitHub 类章节色:深绿
    "category_bio": "#7d3548",      # 生医类章节色:酒红
}

COLORS["category_hn"] = "#ff6600"  # Hacker News 经典橙
COLORS["category_intl"] = "#4a2a5e"  # 国际局势章节色:深紫(报刊评论员色,跟 AI 蓝/Bio 酒红/HN 橙都不撞)

CATEGORY_ACCENT = {
    "AI 领域": COLORS["category_ai"],
    "GitHub 开源生态": COLORS["category_github"],
    "生物医疗工程": COLORS["category_bio"],
    "Hacker News": COLORS["category_hn"],
    "国际局势": COLORS["category_intl"],
}

# ===== 字体加载快捷函数 =====
def font(size: int, *, bold: bool = False, kind: str = "songti") -> ImageFont.FreeTypeFont:
    """
    返回字体对象。kind:
      - "songti":   中文衬线(默认 — 杂志风主用)
      - "heiti":    中文无衬线
      - "times":    英文衬线
      - "helvetica":英文无衬线(metadata 用)
    """
    if kind == "songti":
        return ImageFont.truetype(F_SONGTI, size, index=2 if bold else 0)
    elif kind == "heiti":
        return ImageFont.truetype(F_HEITI_M if bold else F_HEITI_L, size)
    elif kind == "times":
        return ImageFont.truetype(F_TIMES_BOLD if bold else F_TIMES, size)
    elif kind == "helvetica":
        return ImageFont.truetype(F_HELVETICA, size)
    else:
        raise ValueError(f"unknown font kind: {kind}")


# ===== 文本换行(按像素宽度) =====
def wrap_text(text: str, fnt: ImageFont.FreeTypeFont, max_width: int, draw: ImageDraw.ImageDraw) -> list[str]:
    """根据像素宽度自动换行(中英文混合友好)"""
    if not text:
        return []
    lines, current = [], ""
    for ch in text:
        # 显式换行符直接断
        if ch == "\n":
            lines.append(current)
            current = ""
            continue
        test = current + ch
        bbox = draw.textbbox((0, 0), test, font=fnt)
        if bbox[2] - bbox[0] > max_width and current:
            lines.append(current)
            current = ch
        else:
            current = test
    if current:
        lines.append(current)
    return lines


def text_height(fnt: ImageFont.FreeTypeFont, lines: int = 1, line_spacing: int = 0) -> int:
    """计算 N 行文字所占高度(用 'A高' 字符高度)"""
    asc, desc = fnt.getmetrics()
    line_h = asc + desc
    return line_h * lines + line_spacing * max(lines - 1, 0)


# ===== 通用绘制单元 =====
def draw_paragraph(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    fnt: ImageFont.FreeTypeFont,
    max_width: int,
    *,
    color: str = "#1a1a1a",
    line_spacing: int = 8,
    max_lines: int | None = None,
) -> int:
    """
    在 xy 起点画段落,自动换行。
    返回:画完后下一行起始的 y 坐标(供下一个区块续画)。
    """
    x, y = xy
    lines = wrap_text(text, fnt, max_width, draw)
    if max_lines is not None:
        lines = lines[:max_lines]
    asc, desc = fnt.getmetrics()
    line_h = asc + desc
    for line in lines:
        draw.text((x, y), line, fill=color, font=fnt)
        y += line_h + line_spacing
    return y


def draw_horizontal_rule(draw: ImageDraw.ImageDraw, y: int, x0: int = 60, x1: int = W - 60,
                        color: str = "#1a1a1a", weight: int = 2) -> None:
    """画横线"""
    draw.rectangle([(x0, y), (x1, y + weight - 1)], fill=color)


# ===== 配图相关(图片插入) =====
def fit_image_into(img: Image.Image, target_w: int, target_h: int, *, mode: str = "cover") -> Image.Image:
    """
    把任意尺寸图片缩放为 target_w x target_h。
    mode="cover":短边填满,长边裁剪(类似 CSS object-fit: cover)
    mode="contain":长边填满,短边留白
    """
    img = img.convert("RGB")
    src_w, src_h = img.size
    if mode == "cover":
        ratio = max(target_w / src_w, target_h / src_h)
        new_w, new_h = int(src_w * ratio), int(src_h * ratio)
        img = img.resize((new_w, new_h), Image.LANCZOS)
        # 中心裁剪
        x0 = (new_w - target_w) // 2
        y0 = (new_h - target_h) // 2
        img = img.crop((x0, y0, x0 + target_w, y0 + target_h))
        return img
    else:  # contain
        ratio = min(target_w / src_w, target_h / src_h)
        new_w, new_h = int(src_w * ratio), int(src_h * ratio)
        img = img.resize((new_w, new_h), Image.LANCZOS)
        # 用浅色背景对齐
        canvas = Image.new("RGB", (target_w, target_h), COLORS["accent_soft"])
        x = (target_w - new_w) // 2
        y = (target_h - new_h) // 2
        canvas.paste(img, (x, y))
        return canvas


def placeholder_block(target_w: int, target_h: int, label: str = "") -> Image.Image:
    """
    没找到图片时的占位:浅色色块 + 一个细边框 + 中央小字标签
    保持版面节奏,不显得空。
    """
    img = Image.new("RGB", (target_w, target_h), COLORS["accent_soft"])
    d = ImageDraw.Draw(img)
    # 边框
    d.rectangle([(0, 0), (target_w - 1, target_h - 1)], outline=COLORS["muted"], width=2)
    # 内框斜线装饰(简单的几何感)
    d.line([(0, 0), (target_w, target_h)], fill=COLORS["muted"], width=1)
    d.line([(target_w, 0), (0, target_h)], fill=COLORS["muted"], width=1)
    # 中央标签
    if label:
        f = font(20, kind="times")
        bbox = d.textbbox((0, 0), label, font=f)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        # 给标签加白底防止被斜线压
        pad = 8
        d.rectangle([
            ((target_w - tw) // 2 - pad, (target_h - th) // 2 - pad),
            ((target_w + tw) // 2 + pad, (target_h + th) // 2 + pad),
        ], fill=COLORS["bg"])
        d.text(((target_w - tw) // 2, (target_h - th) // 2), label, fill=COLORS["muted"], font=f)
    return img


# ===== 路径助手 =====
ROOT = Path(__file__).parent.parent
OUTPUT_DIR = ROOT / "output"
DIGEST_DIR = OUTPUT_DIR / "digest"
CARDS_DIR = OUTPUT_DIR / "cards"
IMG_CACHE_DIR = OUTPUT_DIR / "cache" / "images"


def load_latest_digest() -> dict:
    """读 output/digest/latest.json"""
    import json
    p = DIGEST_DIR / "latest.json"
    if not p.exists():
        raise FileNotFoundError(f"找不到 {p},请先跑 generate_digest.py")
    return json.loads(p.read_text(encoding="utf-8"))
