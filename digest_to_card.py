"""
TrendRadar Card Pipeline

读 output/digest/latest.json + 抓配图 → 生成 6 张图卡(1 封面 + 5 分类长图)。
输出到 output/cards/{date}/

由 daily_digest.sh 在 generate_digest.py 之后调用。
"""

import sys
import sqlite3
from pathlib import Path
from datetime import datetime

# 确保 scripts/ 在 import path 上
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "scripts"))

from cards_common import DIGEST_DIR, CARDS_DIR, IMG_CACHE_DIR, load_latest_digest, ROOT as CR_ROOT  # noqa
from image_extractor import fetch_image_for_news
from render_cover import render_cover
from render_category import render_category_long


def lookup_summary_html(rss_db_path: Path, url: str) -> str | None:
    """根据 url 从 rss_items 拿到 summary 字段(可能是纯文本,也可能含 HTML)"""
    if not rss_db_path.exists():
        return None
    try:
        conn = sqlite3.connect(rss_db_path)
        row = conn.execute("SELECT summary FROM rss_items WHERE url = ? LIMIT 1", (url,)).fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None


def collect_images(report: dict, date: str) -> dict[str, Path]:
    """对所有 30 条 items 跑 image_extractor,返回 {url: local_path} 字典"""
    rss_db = CR_ROOT / "output" / "rss" / f"{date}.db"
    images: dict[str, Path] = {}
    total = sum(len(c["items"]) for c in report["categories"])
    done = 0
    hit = 0
    for cat in report["categories"]:
        for item in cat["items"]:
            done += 1
            url = item.get("url", "")
            if not url or url == "#":
                continue
            summary = lookup_summary_html(rss_db, url)
            try:
                img_path = fetch_image_for_news(
                    url, summary, item.get("source", ""), IMG_CACHE_DIR, timeout=8
                )
            except Exception as e:
                print(f"  [配图][{done}/{total}] ❌ 异常: {e}")
                continue
            if img_path and img_path.exists():
                images[url] = img_path
                hit += 1
                print(f"  [配图][{done}/{total}] ✅ {item.get('source','?'):28s} → {img_path.name}")
            else:
                print(f"  [配图][{done}/{total}] —  {item.get('source','?'):28s} (无图)")
    print(f"\n[配图] 命中率 {hit}/{total} = {hit*100//total}%")
    return images


def main():
    print(f"[Cards] 读取 latest.json …")
    data = load_latest_digest()
    date = data["date"]

    # ---- 后处理:扫数字事实校验 ----
    print(f"\n[Cards] 数字事实校验(postprocess)")
    from digest_postprocess import postprocess_digest
    import json as _json
    data, actions = postprocess_digest(data, delete_unverified=True)
    if actions:
        print(f"  修正 {len(actions)} 处:")
        for a in actions[:30]:
            print(f"    {a}")
        # 写回 latest.json,让 HTML 渲染也用清洗后的内容
        latest_path = DIGEST_DIR / "latest.json"
        latest_path.write_text(_json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        print(f"  ✅ 未发现编造数字")

    report = data["report"]
    out_dir = CARDS_DIR / date
    out_dir.mkdir(parents=True, exist_ok=True)

    # 2026-05-13 修复(Codex 指出):文件名编号按固定 FIXED_ORDER 中的位置定,
    # 不按 AI 返回顺序。漏一个分类只是少一张图,后面分类不会顶上去导致 05 错位。
    FIXED_ORDER = ["AI 领域", "GitHub 开源生态", "生物医疗工程", "Hacker News", "国际局势"]
    by_name = {c["name"]: c for c in report.get("categories", [])}
    missing = [n for n in FIXED_ORDER if n not in by_name]
    unknown = [c["name"] for c in report.get("categories", []) if c["name"] not in FIXED_ORDER]
    if missing:
        print(f"  ⚠️  AI 漏返回分类: {missing}(对应图卡会缺,飞书会报缺图)")
    if unknown:
        print(f"  ⚠️  AI 多返回未知分类: {unknown}(忽略,不影响 01-05 文件名)")
    # 按固定顺序写回 latest.json(下游脚本读 JSON 时按主线 5 分类顺序展示)
    ordered_for_json = [by_name[n] for n in FIXED_ORDER if n in by_name]
    report["categories"] = ordered_for_json
    import json as _json
    (DIGEST_DIR / "latest.json").write_text(_json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # Step 1: 渲染封面
    print(f"\n[Cards] Step 1: 渲染封面图")
    cover_path = out_dir / "00_cover.png"
    render_cover(data, cover_path)
    print(f"  ✅ {cover_path}")

    # Step 2: 渲染分类长图,文件名编号 = FIXED_ORDER 索引 + 1(固定槽位)
    print(f"\n[Cards] Step 2: 渲染分类长图")
    cat_outs = []
    # 2026-05-12 加: AI 卡需要 frontier_voices(由 generate_digest 写入)
    frontier_voices = report.get("frontier_voices") or []
    for idx, name in enumerate(FIXED_ORDER, 1):
        if name not in by_name:
            continue
        cat = by_name[name]
        safe_name = name.replace(" ", "_").replace("/", "-")
        out = out_dir / f"{idx:02d}_{safe_name}.png"
        render_category_long(cat, date, out, images=None, frontier_voices=frontier_voices)
        from PIL import Image as _Im
        h = _Im.open(out).height
        size_kb = out.stat().st_size // 1024
        print(f"  ✅ {cat['name']:20s} {h}px {size_kb}KB → {out.name}")
        cat_outs.append(out)

    print(f"\n[Cards] 全部完成,输出目录: {out_dir}")
    return cover_path, cat_outs, out_dir


if __name__ == "__main__":
    cover, cats, out_dir = main()

    # 同步到桌面 trend trender/{YYYY-MM}/{YYYY-MM-DD}/(按月归档,每天 6 张)
    month = out_dir.name[:7]  # "2026-05-03" → "2026-05"
    desktop_dir = Path.home() / "Desktop" / "trend trender" / month / out_dir.name
    desktop_dir.mkdir(parents=True, exist_ok=True)
    import shutil
    for f in [cover] + cats:
        shutil.copy2(f, desktop_dir / f.name)
    print(f"[Cards] 6 张图已同步到桌面: {desktop_dir}")
