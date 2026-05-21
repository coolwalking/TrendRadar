"""Image extraction for TrendRadar daily digest cards.

Two-layer strategy:
  Layer 1 — pull first <img src> from the RSS summary HTML (if any).
  Layer 2 — fetch the article URL and read <meta property="og:image">.

Found image URLs are downloaded into a local cache directory keyed by
sha1(url) so the same asset is never fetched twice. Anything that goes
wrong (network, decode, tiny image, wrong MIME) returns None — never raises.
"""

from __future__ import annotations

import hashlib
import re
import sys
from io import BytesIO
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from PIL import Image

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
}

# allow common non-svg/non-gif raster types
ALLOWED_EXT = {"jpg", "jpeg", "png", "webp", "avif"}
EXT_FROM_CT = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/avif": "avif",
}

MIN_EDGE = 200  # px — anything smaller is almost certainly a logo/favicon

_IMG_RE = re.compile(
    r"""<img\b[^>]*?\bsrc\s*=\s*["']([^"']+)["']""",
    re.IGNORECASE | re.DOTALL,
)
# og:image / og:image:url, attribute order can vary
_OG_RE = re.compile(
    r"""<meta\b[^>]*?\bproperty\s*=\s*["']og:image(?::url)?["'][^>]*?\bcontent\s*=\s*["']([^"']+)["']""",
    re.IGNORECASE | re.DOTALL,
)
_OG_RE_REVERSED = re.compile(
    r"""<meta\b[^>]*?\bcontent\s*=\s*["']([^"']+)["'][^>]*?\bproperty\s*=\s*["']og:image(?::url)?["']""",
    re.IGNORECASE | re.DOTALL,
)
# Twitter card image as a backup signal
_TWITTER_RE = re.compile(
    r"""<meta\b[^>]*?\bname\s*=\s*["']twitter:image(?::src)?["'][^>]*?\bcontent\s*=\s*["']([^"']+)["']""",
    re.IGNORECASE | re.DOTALL,
)
_TWITTER_RE_REVERSED = re.compile(
    r"""<meta\b[^>]*?\bcontent\s*=\s*["']([^"']+)["'][^>]*?\bname\s*=\s*["']twitter:image(?::src)?["']""",
    re.IGNORECASE | re.DOTALL,
)
# Older "share image" hint
_LINK_IMG_RE = re.compile(
    r"""<link\b[^>]*?\brel\s*=\s*["']image_src["'][^>]*?\bhref\s*=\s*["']([^"']+)["']""",
    re.IGNORECASE | re.DOTALL,
)


def _is_bad_url(url: str) -> bool:
    if not url:
        return True
    u = url.strip()
    if not u:
        return True
    if u.startswith("data:"):
        return True
    low = u.lower()
    # Skip vector and animated formats up-front (we only want still raster)
    if low.endswith(".svg") or low.endswith(".gif"):
        return True
    if ".svg?" in low or ".gif?" in low:
        return True
    return False


def _ext_from_url(url: str) -> Optional[str]:
    path = urlparse(url).path.lower()
    for ext in ALLOWED_EXT:
        if path.endswith("." + ext):
            return ext
    if path.endswith(".jpe"):
        return "jpg"
    return None


def _ext_from_ct(content_type: str) -> Optional[str]:
    if not content_type:
        return None
    ct = content_type.split(";", 1)[0].strip().lower()
    return EXT_FROM_CT.get(ct)


def _extract_first_img_src(html: str) -> Optional[str]:
    if not html:
        return None
    m = _IMG_RE.search(html)
    if not m:
        return None
    src = m.group(1).strip()
    if _is_bad_url(src):
        # try the next ones if the first is bad
        for m2 in _IMG_RE.finditer(html):
            cand = m2.group(1).strip()
            if not _is_bad_url(cand):
                return cand
        return None
    return src


def _extract_og_image(html: str) -> Optional[str]:
    if not html:
        return None
    for rx in (_OG_RE, _OG_RE_REVERSED, _TWITTER_RE, _TWITTER_RE_REVERSED, _LINK_IMG_RE):
        m = rx.search(html)
        if m:
            cand = m.group(1).strip()
            if not _is_bad_url(cand):
                return cand
    return None


def _download_image(
    img_url: str, cache_dir: Path, timeout: int
) -> Optional[Path]:
    """Download img_url into cache_dir, return local Path or None."""
    if _is_bad_url(img_url):
        return None

    digest = hashlib.sha1(img_url.encode("utf-8")).hexdigest()[:16]

    # If we have an obvious extension from the URL, the cached file might
    # already exist — check before any network call.
    url_ext = _ext_from_url(img_url)
    if url_ext:
        guess = cache_dir / f"{digest}.{url_ext}"
        if guess.exists() and guess.stat().st_size > 0:
            if _is_big_enough(guess):
                return guess
            return None

    # Otherwise, also check if any cached file with that digest exists
    for existing in cache_dir.glob(f"{digest}.*"):
        if existing.stat().st_size > 0:
            if _is_big_enough(existing):
                return existing
            return None

    try:
        resp = requests.get(
            img_url,
            headers=HEADERS,
            timeout=timeout,
            stream=False,
            allow_redirects=True,
        )
    except Exception:
        return None

    if resp.status_code != 200:
        return None

    ct = resp.headers.get("Content-Type", "")
    if not ct.lower().startswith("image/"):
        return None
    if "svg" in ct.lower() or "gif" in ct.lower():
        return None

    ext = _ext_from_ct(ct) or url_ext
    if not ext:
        # Probe with Pillow as a last resort
        try:
            with Image.open(BytesIO(resp.content)) as im:
                fmt = (im.format or "").lower()
            if fmt in ("jpeg", "jpg"):
                ext = "jpg"
            elif fmt in ALLOWED_EXT:
                ext = fmt
            else:
                return None
        except Exception:
            return None

    cache_dir.mkdir(parents=True, exist_ok=True)
    out = cache_dir / f"{digest}.{ext}"
    try:
        out.write_bytes(resp.content)
    except Exception:
        return None

    if not _is_big_enough(out):
        try:
            out.unlink()
        except Exception:
            pass
        return None

    return out


def _is_big_enough(path: Path) -> bool:
    """Filter out tiny logos/favicons (max edge < 200 px)."""
    try:
        with Image.open(path) as im:
            w, h = im.size
    except Exception:
        return False
    return max(w, h) >= MIN_EDGE


def fetch_image_for_news(
    url: str,
    summary_html: Optional[str],
    source_id: str,
    cache_dir: Path,
    *,
    timeout: int = 8,
) -> Optional[Path]:
    """Find a hero image for one news item.

    Layer 1: regex out the first usable <img src> from summary_html.
    Layer 2: GET the article URL and pull <meta property="og:image">.

    Returns the local cached image Path, or None if nothing usable was found.
    Never raises — any exception is swallowed and turned into None.
    """
    try:
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)

        # ---- Layer 1: RSS summary HTML ---------------------------------
        if summary_html:
            src = _extract_first_img_src(summary_html)
            if src:
                # Resolve relative URLs against the article URL
                if src.startswith("//"):
                    src = "https:" + src
                elif src.startswith("/") and url:
                    src = urljoin(url, src)
                local = _download_image(src, cache_dir, timeout)
                if local is not None:
                    return local

        # ---- Layer 2: og:image / twitter:image -------------------------
        if not url:
            return None
        try:
            page = requests.get(
                url,
                headers=HEADERS,
                timeout=timeout,
                allow_redirects=True,
            )
        except Exception:
            return None
        if page.status_code != 200:
            return None
        ct = page.headers.get("Content-Type", "").lower()
        if "html" not in ct and "xml" not in ct and ct != "":
            # not an HTML document — nothing to parse
            return None

        # Limit how much HTML we scan — og tags live in <head>
        html = page.text[:200_000] if page.text else ""
        og = _extract_og_image(html)
        if not og:
            return None
        if og.startswith("//"):
            og = "https:" + og
        elif og.startswith("/"):
            og = urljoin(page.url or url, og)
        return _download_image(og, cache_dir, timeout)
    except Exception:
        return None


# ----------------------------------------------------------------------
# Self-test
# ----------------------------------------------------------------------
def _self_test() -> int:
    import json
    import sqlite3

    root = Path(__file__).resolve().parent.parent
    digest_path = root / "output" / "digest" / "latest.json"
    cache_dir = root / "output" / "cache" / "images"
    cache_dir.mkdir(parents=True, exist_ok=True)

    if not digest_path.exists():
        print(f"[self-test] missing {digest_path}", file=sys.stderr)
        return 1

    with digest_path.open() as f:
        data = json.load(f)

    date = data.get("date")
    db_path = root / "output" / "rss" / f"{date}.db"
    summary_lookup: dict[str, str] = {}
    if db_path.exists():
        try:
            conn = sqlite3.connect(str(db_path))
            cur = conn.cursor()
            cur.execute("SELECT url, summary FROM rss_items WHERE summary IS NOT NULL")
            for u, s in cur.fetchall():
                if u and s:
                    summary_lookup[u] = s
            conn.close()
        except Exception as e:
            print(f"[self-test] rss db read failed: {e}", file=sys.stderr)

    items = []
    for cat in data.get("report", {}).get("categories", []):
        for it in cat.get("items", []):
            items.append(it)
            if len(items) >= 10:
                break
        if len(items) >= 10:
            break

    layer1_hits = 0
    layer2_hits = 0
    misses = 0
    print(f"[self-test] {len(items)} items, cache={cache_dir}")
    for i, it in enumerate(items, 1):
        url = it.get("url") or ""
        source = it.get("source") or ""
        summary_html = summary_lookup.get(url)

        # Run layers manually so we can attribute hits.
        local: Optional[Path] = None
        layer = "miss"

        if summary_html:
            src = _extract_first_img_src(summary_html)
            if src:
                if src.startswith("//"):
                    src = "https:" + src
                elif src.startswith("/") and url:
                    src = urljoin(url, src)
                local = _download_image(src, cache_dir, 8)
                if local is not None:
                    layer = "L1"
                    layer1_hits += 1

        if local is None and url:
            try:
                page = requests.get(url, headers=HEADERS, timeout=8, allow_redirects=True)
                if page.status_code == 200:
                    html = page.text[:200_000] if page.text else ""
                    og = _extract_og_image(html)
                    if og:
                        if og.startswith("//"):
                            og = "https:" + og
                        elif og.startswith("/"):
                            og = urljoin(page.url or url, og)
                        local = _download_image(og, cache_dir, 8)
                        if local is not None:
                            layer = "L2"
                            layer2_hits += 1
            except Exception:
                pass

        if local is None:
            misses += 1
        title = (it.get("title_zh") or it.get("title") or "")[:40]
        print(f"  [{i:2d}] {layer:4s} src={source:28s} -> {local} | {title}")

    total = len(items)
    hits = layer1_hits + layer2_hits
    print(
        f"[self-test] hits={hits}/{total} "
        f"(L1={layer1_hits}, L2={layer2_hits}, miss={misses})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(_self_test())
