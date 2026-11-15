"""Site-wide checks: problems that only show up when pages are compared.

Works on the full audit reports of one batch run. Finds broken pages and
links, redirects, duplicate titles and descriptions, orphan pages, and pages
that are linked but missing from the sitemap.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import unquote, urlparse

from .config import Settings
from .http import fetch_page

MAX_LINK_CHECKS = 400      # link targets fetched per run, most-linked first
_MAX_NOT_IN_SITEMAP = 100  # rows listed for the lowest-value check
_SEVERITY_ORDER = {"High": 0, "Medium": 1, "Low": 2}


def url_key(url: str) -> str:
    """Comparison key: ignores scheme, www, trailing slash and fragment."""
    parts = urlparse(url or "")
    host = (parts.hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    path = unquote(parts.path).rstrip("/") or "/"       # %e1%90%a9 and its character match
    return host + path + (f"?{parts.query}" if parts.query else "")


def _host(url: str) -> str:
    return url_key(url).split("/")[0]


def _sources(pages: list[str]) -> str:
    more = f" and {len(pages) - 3} more" if len(pages) > 3 else ""
    return f"linked from {len(pages)} page(s): {', '.join(pages[:3])}{more}"


def _check_link(url: str, settings: Settings):
    result = None
    for _ in range(2):                       # one retry, so a blip is not reported
        result = fetch_page(url, timeout=settings.timeout,
                            allow_private=settings.allow_private_hosts, read_body=False)
        if result.ok and (result.status_code or 0) < 500:
            break
    return result


def analyze_site(reports: list[dict], sitemap_urls: list[str],
                 settings: Settings | None = None, check_links: bool = True,
                 max_link_checks: int = MAX_LINK_CHECKS) -> dict:
    """Cross-page findings for one batch of audit reports. Returns a JSON-safe dict.

    reports: full reports from inspect_url_seo_and_geo, including failed ones.
    sitemap_urls: every URL the sitemap lists, audited or not.
    """
    settings = settings or Settings.from_env()
    issues: list[dict] = []

    def add(check: str, severity: str, url: str, detail: str = "") -> None:
        issues.append({"check": check, "severity": severity, "url": url, "detail": detail})

    good = [r for r in reports if r["ok"]]
    sitemap_keys = {url_key(u) for u in sitemap_urls}
    sitemap_hosts = {_host(u) for u in sitemap_urls}
    audited: dict[str, dict] = {}
    for report in good:
        audited[url_key(report["url"])] = report
        audited.setdefault(url_key(report["fetch"]["final_url"] or report["url"]), report)
    complete = bool(good) and len(reports) >= len(sitemap_urls)

    # Who links to what.
    inbound: dict[str, list[str]] = defaultdict(list)
    first_seen: dict[str, str] = {}
    for report in good:
        source = report["fetch"]["final_url"] or report["url"]
        own = {url_key(source), url_key(report["url"])}
        for link in report["page"]["links"].get("internal_urls", []):
            key = url_key(link)
            if key in own:
                continue
            first_seen.setdefault(key, link)
            if source not in inbound[key]:
                inbound[key].append(source)

    # Per-page findings.
    titles: dict[str, list[str]] = defaultdict(list)
    descriptions: dict[str, list[str]] = defaultdict(list)
    for report in reports:
        url = report["url"]
        if not report["ok"]:
            add("Page cannot be loaded", "High", url, report["error"])
            continue
        fetch, page = report["fetch"], report["page"]
        final = fetch["final_url"] or url
        status = fetch["status_code"] or 0
        if status >= 400:
            linked = inbound.get(url_key(url)) or inbound.get(url_key(final)) or []
            add("Broken page", "High", url,
                f"HTTP {status}" + (f"; {_sources(linked)}" if linked else ""))
            continue
        if fetch["redirect_chain"]:
            # The content belongs to the target page, so nothing else is checked here.
            add("Sitemap URL redirects", "Medium", url, f"Redirects to {final}")
            continue
        if page["robots"]["noindex"]:
            add("Noindex page in sitemap", "Medium", url,
                "The page asks search engines not to index it.")
        canonical = page["canonical"]
        if canonical["present"] and url_key(canonical["url"]) != url_key(final):
            add("Canonical points elsewhere", "Medium", url, f"Canonical is {canonical['url']}")
        if page["title"]["text"]:
            titles[page["title"]["text"].strip().lower()].append(url)
        if page["description"]["text"]:
            descriptions[page["description"]["text"].strip().lower()].append(url)
        is_home = (urlparse(final).path.rstrip("/") == "")
        if complete and not is_home and not inbound.get(url_key(url)) \
                and not inbound.get(url_key(final)):
            add("Orphan page", "Medium", url, "No other audited page links to it.")

    for label, severity, groups in (("Duplicate title", "Medium", titles),
                                    ("Duplicate meta description", "Low", descriptions)):
        for text, urls in groups.items():
            if len(urls) < 2:
                continue
            for url in urls:
                other = next(u for u in urls if u != url)
                add(label, severity, url,
                    f"\"{text[:80]}\" is also used on {len(urls) - 1} other page(s), e.g. {other}")

    # Links that point outside the audited set: fetch them to see if they work.
    unknown = sorted((k for k in inbound if k not in audited),
                     key=lambda k: -len(inbound[k]))
    to_check = unknown[:max(0, int(max_link_checks))] if check_links else []
    not_in_sitemap = 0
    if to_check:
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda k: _check_link(first_seen[k], settings), to_check))
        for key, result in zip(to_check, results):
            url, sources = first_seen[key], inbound[key]
            if not result.ok:
                add("Internal link could not be checked", "Low", url,
                    f"{result.error} {_sources(sources)}")
            elif (result.status_code or 0) >= 400:
                add("Broken internal link", "High", url,
                    f"HTTP {result.status_code}; {_sources(sources)}")
            elif result.redirect_chain:
                add("Internal link redirects", "Low", url,
                    f"Redirects to {result.final_url}; {_sources(sources)}")
            elif (result.is_html and url_key(result.final_url) not in sitemap_keys
                  and _host(result.final_url) in sitemap_hosts):   # other hosts have their own sitemap
                not_in_sitemap += 1
                if not_in_sitemap <= _MAX_NOT_IN_SITEMAP:
                    add("Linked but not in sitemap", "Low", url, _sources(sources))

    issues.sort(key=lambda i: (_SEVERITY_ORDER[i["severity"]], i["check"], i["url"]))
    summary = dict(Counter(i["check"] for i in issues))
    if not_in_sitemap > _MAX_NOT_IN_SITEMAP:
        summary["Linked but not in sitemap"] = not_in_sitemap
    notes = []
    if not complete:
        notes.append("Only part of the sitemap was audited, so orphan pages were not checked.")
    if len(unknown) > len(to_check):
        notes.append(f"{len(unknown) - len(to_check)} linked URL(s) were not checked.")
    return {"complete": complete, "pages_compared": len(good),
            "links_checked": len(to_check), "summary": summary,
            "high": sum(1 for i in issues if i["severity"] == "High"),
            "issues": issues, "note": " ".join(notes)}
