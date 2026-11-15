"""XML sitemap discovery and batch auditing."""

from __future__ import annotations

import gzip
import html
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse

from .config import Settings
from .http import fetch_page, normalize_url

MAX_BATCH = 100            # interactive limit (dashboard and agent tools)
MAX_SCHEDULED = 1000       # limit for the scheduled job, which has time to spare
_MAX_SCAN = 5000           # URLs counted so "found" is the real size of the sitemap
_MAX_SITEMAPS = 20          # child sitemaps read from one index
_LOC = re.compile(r"<loc>\s*(?:<!\[CDATA\[)?\s*(.*?)\s*(?:\]\]>)?\s*</loc>", re.I | re.S)


def _read(url: str, settings: Settings) -> tuple[str, str]:
    """Return (text, error) for a sitemap or robots.txt URL."""
    fetched = fetch_page(url, timeout=settings.timeout, allow_private=settings.allow_private_hosts)
    if not fetched.ok:
        return "", fetched.error
    if (fetched.status_code or 0) >= 400:
        return "", f"HTTP {fetched.status_code}"
    body = fetched.body or b""
    if body[:2] == b"\x1f\x8b":
        try:
            body = gzip.decompress(body)
        except (OSError, EOFError):
            return "", "could not decompress"
    return body.decode("utf-8", errors="replace"), ""


def _locs(text: str) -> list[str]:
    return [html.unescape(loc).strip() for loc in _LOC.findall(text) if loc.strip()]


def collect_sitemap_urls(sitemap_url: str, settings: Settings, limit: int = MAX_BATCH) -> dict:
    """Page URLs from a sitemap, a sitemap index, or a site's home page.

    Given a plain site URL, the sitemap is looked up in robots.txt and then at
    /sitemap.xml.
    """
    start = normalize_url(sitemap_url)
    out = {"urls": [], "sitemaps_read": [], "error": ""}
    if not start:
        out["error"] = "Enter a sitemap URL or a site URL."
        return out
    queue = [start]
    if not urlparse(start).path.lower().endswith((".xml", ".xml.gz", ".txt")):
        root = f"{urlparse(start).scheme}://{urlparse(start).netloc}"
        robots, _ = _read(urljoin(root, "/robots.txt"), settings)
        declared = re.findall(r"(?im)^\s*sitemap:\s*(\S+)", robots)
        queue = declared or [urljoin(root, "/sitemap.xml")]

    seen_sitemaps, seen_urls, errors = set(), set(), []
    while queue and len(seen_sitemaps) < _MAX_SITEMAPS and len(out["urls"]) < limit:
        sitemap = queue.pop(0)
        if sitemap in seen_sitemaps:
            continue
        seen_sitemaps.add(sitemap)
        text, error = _read(sitemap, settings)
        if error:
            errors.append(f"{sitemap}: {error}")
            continue
        out["sitemaps_read"].append(sitemap)
        locs = _locs(text)
        if "<sitemapindex" in text.lower():
            queue.extend(locs)
            continue
        if not locs and "<" not in text[:200]:
            locs = [line.strip() for line in text.splitlines()]      # plain-text sitemap
        for loc in locs:
            if loc.startswith(("http://", "https://")) and loc not in seen_urls:
                seen_urls.add(loc)
                out["urls"].append(loc)
                if len(out["urls"]) >= limit:
                    break
    if not out["urls"]:
        out["error"] = ("No page URLs were found. " + "; ".join(errors[:3])).strip()
    return out


def spider_xml_sitemap(sitemap_url: str, max_urls: int = 25, target_keyword: str = "",
                       settings: Settings | None = None, save: bool = True,
                       max_cap: int = MAX_BATCH) -> dict:
    """Audit up to `max_urls` pages listed in a sitemap. Returns a JSON-safe dict.

    max_urls is capped at max_cap. Each page is saved to history as a summary row.
    """
    from .service import inspect_url_seo_and_geo          # avoids an import cycle
    from .storage import summary_row

    settings = settings or Settings.from_env()
    max_urls = max(1, min(int(max_urls), int(max_cap)))
    found = collect_sitemap_urls(sitemap_url, settings, _MAX_SCAN)
    urls_in_sitemap = len(found["urls"])
    found["urls"] = found["urls"][:max_urls]
    out = {"sitemap_url": sitemap_url, "ok": False, "error": found["error"],
           "sitemaps_read": found["sitemaps_read"], "urls_found": urls_in_sitemap,
           "pages": [], "failed": [], "summary": {}}
    if not found["urls"]:
        return out

    def audit(url: str) -> dict:
        # Static HTML only: rendering and speed tests are too slow for a batch.
        return inspect_url_seo_and_geo(url, target_keyword=target_keyword, render_js="never",
                                       run_pagespeed=False, settings=settings, save=save,
                                       save_full_report=False)

    with ThreadPoolExecutor(max_workers=4) as pool:
        reports = list(pool.map(audit, found["urls"]))
    for report in reports:
        if not report["ok"]:
            out["failed"].append({"url": report["url"], "error": report["error"]})
            continue
        row = summary_row(report)
        row["top_issue"] = report["issues"][0]["issue"] if report["issues"] else ""
        out["pages"].append(row)
    pages = out["pages"]
    if pages:
        def average(field: str) -> float:
            values = [p[field] for p in pages if p[field] is not None]
            return round(sum(values) / len(values), 1) if values else 0
        out["summary"] = {
            "pages_audited": len(pages), "pages_failed": len(out["failed"]),
            "avg_geo_score": average("geo_score"), "avg_word_count": average("word_count"),
            "avg_response_ms": average("response_ms"),
            "total_issues": sum(p["issue_count"] or 0 for p in pages),
            "pages_with_title_problems": sum(1 for p in pages if p["title_status"] != "Optimal"),
            "pages_without_schema": sum(1 for p in pages if not p["schema_types"]),
            "pages_with_errors": sum(1 for p in pages if (p["status_code"] or 0) >= 400),
        }
        pages.sort(key=lambda p: (-(p["issue_count"] or 0), p["geo_score"] or 0))
    out["ok"] = bool(pages)
    if not pages:
        out["error"] = "None of the sitemap's pages could be audited."
    return out
