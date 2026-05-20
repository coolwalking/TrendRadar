"""
PubMed E-utilities enricher (生物医疗 板块专用)

2026-05-18 加(Phase C, 借鉴 Awesome-AI-Agents-for-Healthcare 思路)。

DDG enrich 对 biotech 专业术语支持弱(返回的多是 SEO 农场, 不是真实学术)。
PubMed E-utilities 是 NIH 官方权威源, 免费无认证:
  1. esearch.fcgi 用 title 关键词搜 PMID
  2. efetch.fcgi 用 PMID 拿 abstract

只对 category=生物医疗工程 的 top N item enrich。每天可能命中 30-60% items
(News 标题不一定都对应 paper, 但很多 biotech news 引用具体研究)。
"""

from __future__ import annotations
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Optional


ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
TIMEOUT = 12
MAX_ABSTRACT_CHARS = 2500
UA = "TrendRadar/1.0 (https://github.com/sansan0/TrendRadar)"


def _http_get(url: str) -> Optional[str]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            if r.status != 200:
                return None
            return r.read().decode("utf-8", errors="replace")
    except Exception:
        return None


def _build_query(title: str) -> str:
    """从 news title 提取 query。规则: 去标点 + 限 60 字以内 + 保留主标题(分号 / 冒号 / 破折号前)。

    PubMed 对 query 做 stemming + relevance, 不需要精确 keyword。
    """
    if not title:
        return ""
    # 取主标题(在 ":" / "–" / "—" / "|" 前)
    main = re.split(r"[:|–—\|]", title, maxsplit=1)[0].strip()
    if not main:
        main = title.strip()
    # 去掉特殊符号但保留连字符
    clean = re.sub(r"[\"'`(){}\[\]]", "", main)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean[:80]


def search_and_fetch_abstract(title: str) -> Optional[dict]:
    """从 PubMed 搜 title, 拿 top 1 的 PMID + abstract。无结果返回 None。

    返回 {pmid, title, abstract, journal, pub_year} 或 None
    """
    q = _build_query(title)
    if not q:
        return None
    # 1) esearch 拿 top 1 PMID
    search_url = f"{ESEARCH}?db=pubmed&term={urllib.parse.quote_plus(q)}&retmax=1&sort=relevance"
    search_xml = _http_get(search_url)
    if not search_xml:
        return None
    pmid_match = re.search(r"<Id>(\d+)</Id>", search_xml)
    if not pmid_match:
        return None
    pmid = pmid_match.group(1)
    # 2) efetch 拿 abstract (rate limit: 3 req/sec without key)
    time.sleep(0.4)
    fetch_url = f"{EFETCH}?db=pubmed&id={pmid}&rettype=abstract&retmode=xml"
    fetch_xml = _http_get(fetch_url)
    if not fetch_xml:
        return None
    return _parse_pubmed_xml(fetch_xml, pmid)


def _parse_pubmed_xml(xml_str: str, pmid: str) -> Optional[dict]:
    """从 PubMed efetch XML 解析 abstract / title / journal / year。"""
    try:
        root = ET.fromstring(xml_str)
    except ET.ParseError:
        return None
    # ArticleTitle
    title_el = root.find(".//ArticleTitle")
    title = title_el.text.strip() if (title_el is not None and title_el.text) else ""
    # AbstractText (可能多段, 拼起来)
    abs_parts: list[str] = []
    for at in root.findall(".//AbstractText"):
        label = at.get("Label") or ""
        body = "".join(at.itertext()).strip()
        if label:
            abs_parts.append(f"{label}: {body}")
        else:
            abs_parts.append(body)
    abstract = " ".join(abs_parts).strip()
    if not abstract:
        return None
    # Journal
    j_el = root.find(".//Journal/Title")
    journal = j_el.text if (j_el is not None and j_el.text) else ""
    # Year (XPath: PubDate/Year 或 PubDate/MedlineDate 之一)
    y_el = root.find(".//PubDate/Year")
    if y_el is None:
        y_el = root.find(".//PubDate/MedlineDate")
    year = ""
    if y_el is not None and y_el.text:
        m = re.search(r"\d{4}", y_el.text)
        if m:
            year = m.group(0)
    return {
        "pmid": pmid,
        "title": title[:200],
        "abstract": abstract[:MAX_ABSTRACT_CHARS],
        "journal": journal[:80],
        "pub_year": year,
    }


def enrich_items(
    items: list[dict],
    *,
    category_filter: str = "生物医疗工程",
    top_n: int = 8,
    sleep_between: float = 0.5,
    wall_budget_s: float = 60.0,
    verbose: bool = True,
) -> int:
    """
    对 category=生物医疗工程 的 top N items(按 score) 搜 PubMed 拿 abstract。
    写入 it["pubmed"] = {pmid, title, abstract, journal, pub_year}。
    """
    # 按 score 筛 top N
    bio_items = [it for it in items if it.get("category") == category_filter]
    bio_items.sort(key=lambda x: x.get("score", 0), reverse=True)
    candidates = bio_items[:top_n]

    hit = 0
    deadline = time.monotonic() + wall_budget_s
    for idx, it in enumerate(candidates):
        if time.monotonic() > deadline:
            if verbose:
                print(f"  [PM] ⏱  已用 {wall_budget_s:.0f}s, 余下 {len(candidates) - idx} 项跳过")
            break
        title = it.get("title") or ""
        result = search_and_fetch_abstract(title)
        if result:
            it["pubmed"] = result
            hit += 1
            if verbose:
                print(f"  [PM] ✅ PMID={result['pmid']} {result.get('journal','')[:30]} · {title[:50]}")
        else:
            if verbose:
                print(f"  [PM] —     无 PubMed 命中 · {title[:55]}")
        time.sleep(sleep_between)
    return hit


# ============== 自测 ==============
if __name__ == "__main__":
    print("=== pubmed_enricher unit test ===")
    test_items = [
        {"category": "生物医疗工程", "score": 0.9, "title": "CRISPR-Cas9 gene editing breakthrough in sickle cell disease"},
        {"category": "生物医疗工程", "score": 0.8, "title": "GLP-1 agonists weight loss trial results"},
        {"category": "AI 领域", "score": 0.9, "title": "GPT-5 released (should be skipped)"},
    ]
    n = enrich_items(test_items, top_n=3, sleep_between=0.3)
    print(f"\nhit: {n}/2")
    for it in test_items:
        pm = it.get("pubmed") or {}
        if pm:
            print(f"  → {it['title'][:40]}")
            print(f"    PMID={pm.get('pmid')} ({pm.get('pub_year')}) {pm.get('journal','')}")
            print(f"    Title: {pm.get('title','')[:80]}")
            print(f"    Abstract: {pm.get('abstract','')[:200]}...")
