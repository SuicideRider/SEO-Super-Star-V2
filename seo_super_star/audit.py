"""Pure HTML analysis: metadata, directives, headings, links, images, schema, content.

`analyze_html` takes markup and a base URL and returns a plain dict. It does
no network I/O, so it can be tested offline and reused on rendered HTML.
"""

from __future__ import annotations

import json
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from markdownify import markdownify

# Arial advance widths in 1/1000 em, used to estimate SERP display width.
_WIDTHS: dict[str, int] = {}
for _chars, _w in (
    (" .,:;!'|ijlI/\\[]ft", 278), ("-()r\"`*", 333), ("ckssvxyzJ", 500),
    ("abdeghnopqu0123456789$#?_L", 556), ("FTZ=+<>~^", 611),
    ("ABEKPSVXY&", 667), ("CDHNRUw", 722), ("GOQ", 778), ("mM", 833),
    ("W", 944), ("@", 1015), ("%", 889),
):
    for _c in _chars:
        _WIDTHS[_c] = _w

TITLE_FONT_PX, TITLE_MAX_PX, TITLE_MIN_CHARS = 20, 580, 30
DESC_FONT_PX, DESC_MAX_PX, DESC_MIN_CHARS = 14, 920, 70

_SKIP_SCHEMES = ("javascript:", "mailto:", "tel:", "data:", "#")
_SOCIAL_HOSTS = ("facebook.com", "twitter.com", "x.com", "linkedin.com",
                 "instagram.com", "youtube.com", "tiktok.com", "pinterest.com",
                 "github.com")
_HEADING_TAGS = ["h1", "h2", "h3", "h4", "h5", "h6"]
_BOILERPLATE_TAGS = ["script", "style", "noscript", "nav", "aside", "form",
                     "svg", "iframe", "template"]


def pixel_width(text: str, font_px: int) -> int:
    units = sum(_WIDTHS.get(ch, 556) for ch in text)
    return round(units * font_px / 1000)


def _length_check(text: str, font_px: int, max_px: int, min_chars: int) -> dict:
    text = " ".join((text or "").split())
    width = pixel_width(text, font_px)
    if not text:
        status = "Missing"
    elif width > max_px:
        status = "Truncated"
    elif len(text) < min_chars:
        status = "Too Short"
    else:
        status = "Optimal"
    return {"text": text, "chars": len(text), "pixels": width,
            "max_pixels": max_px, "status": status}


def _clean(text: str | None) -> str:
    return " ".join((text or "").split())


def _meta(soup: BeautifulSoup, *names: str) -> str:
    """First non-empty content of a meta tag matched by name or property."""
    wanted = {n.lower() for n in names}
    for tag in soup.find_all("meta"):
        key = (tag.get("name") or tag.get("property") or "").lower()
        if key in wanted and _clean(tag.get("content")):
            return _clean(tag.get("content"))
    return ""


def _host(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _extract_links(soup: BeautifulSoup, base_url: str) -> dict:
    base_host = _host(base_url)
    internal = external = nofollow = 0
    empty_anchors: list[str] = []
    external_domains: list[str] = []
    social: list[str] = []
    phones: list[str] = []
    emails: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        lower = href.lower()
        if lower.startswith("tel:"):
            phones.append(href[4:].strip())
        elif lower.startswith("mailto:"):
            emails.append(href[7:].split("?")[0].strip())
        if not href or lower.startswith(_SKIP_SCHEMES):
            continue
        target = urljoin(base_url, href)
        host = _host(target)
        if not host:
            continue
        if host == base_host or host.endswith("." + base_host):
            internal += 1
        else:
            external += 1
            if host not in external_domains:
                external_domains.append(host)
            if any(host == s or host.endswith("." + s) for s in _SOCIAL_HOSTS):
                if target not in social:
                    social.append(target)
        if "nofollow" in [r.lower() for r in (a.get("rel") or [])]:
            nofollow += 1
        label = _clean(a.get_text()) or _clean(a.get("aria-label")) or _clean(a.get("title"))
        if not label:
            img = a.find("img")
            label = _clean(img.get("alt")) if img else ""
        if not label:
            empty_anchors.append(target)
    return {
        "internal": internal, "external": external, "nofollow": nofollow,
        "empty_anchor_count": len(empty_anchors),
        "empty_anchors": empty_anchors[:50],
        "external_domains": external_domains,
        "social_profiles": social[:20],
        "phones": list(dict.fromkeys(phones))[:5],
        "emails": list(dict.fromkeys(emails))[:5],
    }


def _extract_images(soup: BeautifulSoup, base_url: str) -> dict:
    images = soup.find_all("img")
    missing: list[str] = []
    empty_alt = 0
    for img in images:
        src = img.get("src") or img.get("data-src") or ""
        if not img.has_attr("alt"):
            missing.append(urljoin(base_url, src) if src else "(no src)")
        elif not _clean(img.get("alt")):
            empty_alt += 1
    return {"total": len(images), "missing_alt_count": len(missing),
            "missing_alt_src": missing, "empty_alt_count": empty_alt}


def _flatten_jsonld(node):
    if isinstance(node, list):
        for item in node:
            yield from _flatten_jsonld(item)
    elif isinstance(node, dict):
        if "@graph" in node:
            yield from _flatten_jsonld(node["@graph"])
        if "@type" in node:
            yield node


def _extract_jsonld(soup: BeautifulSoup) -> dict:
    entities: list[dict] = []
    blocks = parse_errors = 0
    for script in soup.find_all("script"):
        if (script.get("type") or "").lower().strip() != "application/ld+json":
            continue
        blocks += 1
        try:
            data = json.loads(script.string or script.get_text() or "")
        except (json.JSONDecodeError, TypeError):
            parse_errors += 1
            continue
        for entity in _flatten_jsonld(data):
            types = entity["@type"]
            types = types if isinstance(types, list) else [types]
            entities.append({
                "types": [str(t) for t in types],
                "property_count": len([k for k in entity if not k.startswith("@")]),
            })
    type_names = list(dict.fromkeys(t for e in entities for t in e["types"]))
    return {"blocks": blocks, "parse_errors": parse_errors,
            "types": type_names, "entities": entities}


def _main_root(soup: BeautifulSoup):
    root = soup.find("main") or soup.find(attrs={"role": "main"})
    if root is None:
        articles = soup.find_all("article")
        root = articles[0] if len(articles) == 1 else (soup.body or soup)
    return root


def _extract_content(soup: BeautifulSoup) -> dict:
    """Main-body sections, text and Markdown. Mutates the soup; call last."""
    root = _main_root(soup)
    for tag in root.find_all(_BOILERPLATE_TAGS):
        tag.decompose()
    # Site-level header/footer are boilerplate; an <article>'s own header
    # usually holds the H1, so those are kept.
    for tag in root.find_all(["header", "footer"]):
        if not tag.find_parent(["article", "main"]):
            tag.decompose()

    sections: list[dict] = []
    current = {"level": 0, "heading": "", "paragraphs": []}
    list_count = table_count = 0
    for el in root.find_all(_HEADING_TAGS + ["p", "ul", "ol", "table"]):
        if el.name in _HEADING_TAGS:
            if current["heading"] or current["paragraphs"]:
                sections.append(current)
            current = {"level": int(el.name[1]), "heading": _clean(el.get_text(" ")),
                       "paragraphs": []}
        elif el.name == "p":
            text = _clean(el.get_text(" "))
            if text:
                current["paragraphs"].append(text)
        elif el.name in ("ul", "ol"):
            if not el.find_parent(["ul", "ol"]):
                list_count += 1
        else:
            table_count += 1
    if current["heading"] or current["paragraphs"]:
        sections.append(current)

    text = _clean(root.get_text(" "))
    markdown = markdownify(str(root), heading_style="ATX", strip=["img"])
    markdown = re.sub(r"\n{3,}", "\n\n", markdown).strip()
    return {
        "sections": sections, "list_count": list_count, "table_count": table_count,
        "text": text, "word_count": len(re.findall(r"\b\w+\b", text)),
        "markdown": markdown,
    }


def analyze_html(html: str | bytes, url: str, x_robots_tag: str = "") -> dict:
    soup = BeautifulSoup(html, "lxml")

    title_tag = soup.find("title")
    title = _length_check(title_tag.get_text() if title_tag else "",
                          TITLE_FONT_PX, TITLE_MAX_PX, TITLE_MIN_CHARS)
    description = _length_check(_meta(soup, "description"),
                                DESC_FONT_PX, DESC_MAX_PX, DESC_MIN_CHARS)

    canonical = ""
    for link in soup.find_all("link", href=True):
        if "canonical" in [r.lower() for r in (link.get("rel") or [])]:
            canonical = urljoin(url, link["href"].strip())
            break
    robots = _meta(soup, "robots")
    directives = f"{robots},{x_robots_tag}".lower()

    headings = [{"level": int(h.name[1]), "text": _clean(h.get_text(" "))}
                for h in soup.find_all(_HEADING_TAGS)]
    h1_count = sum(1 for h in headings if h["level"] == 1)
    skipped = []
    previous = 0
    for h in headings:
        if previous and h["level"] > previous + 1:
            skipped.append(f"H{previous} to H{h['level']} at \"{h['text'][:60]}\"")
        previous = h["level"]

    html_tag = soup.find("html")
    time_tag = soup.find("time", datetime=True)
    facts = {
        "lang": _clean(html_tag.get("lang")) if html_tag else "",
        "site_name": _meta(soup, "og:site_name"),
        "og_title": _meta(soup, "og:title"),
        "og_image": urljoin(url, _meta(soup, "og:image")) if _meta(soup, "og:image") else "",
        "author": _meta(soup, "author", "article:author"),
        "date_published": _meta(soup, "article:published_time", "datePublished")
        or (time_tag["datetime"].strip() if time_tag else ""),
        "date_modified": _meta(soup, "article:modified_time", "dateModified"),
        "price_amount": _meta(soup, "product:price:amount", "og:price:amount"),
        "price_currency": _meta(soup, "product:price:currency", "og:price:currency"),
    }

    return {
        "title": title,
        "description": description,
        "canonical": {"url": canonical, "present": bool(canonical),
                      "self_referencing": bool(canonical)
                      and canonical.rstrip("/") == url.rstrip("/")},
        "robots": {"meta": robots, "x_robots_tag": x_robots_tag,
                   "noindex": "noindex" in directives,
                   "nofollow": "nofollow" in directives},
        "headings": {"items": headings, "h1_count": h1_count,
                     "h2_count": sum(1 for h in headings if h["level"] == 2),
                     "skipped_levels": skipped},
        "links": _extract_links(soup, url),
        "images": _extract_images(soup, url),
        "jsonld": _extract_jsonld(soup),
        "facts": facts,
        "content": _extract_content(soup),
    }


def keyword_usage(page: dict, url: str, keyword: str) -> dict | None:
    """Where the target keyword appears on the page."""
    keyword = _clean(keyword).lower()
    if not keyword:
        return None
    content = page["content"]
    text = content["text"].lower()
    occurrences = len(re.findall(r"(?<!\w)" + re.escape(keyword) + r"(?!\w)", text))
    first_100 = " ".join(text.split()[:100])
    h1s = [h["text"].lower() for h in page["headings"]["items"] if h["level"] == 1]
    subheads = [h["text"].lower() for h in page["headings"]["items"] if h["level"] in (2, 3)]
    slug = re.sub(r"%20|[-_/+]+", " ", urlparse(url).path.lower())
    words = max(content["word_count"], 1)
    return {
        "keyword": keyword,
        "in_title": keyword in page["title"]["text"].lower(),
        "in_description": keyword in page["description"]["text"].lower(),
        "in_h1": any(keyword in h for h in h1s),
        "in_subheadings": any(keyword in h for h in subheads),
        "in_first_100_words": keyword in first_100,
        "in_url": keyword in slug,
        "occurrences": occurrences,
        "density_percent": round(occurrences * len(keyword.split()) * 100 / words, 2),
    }


def collect_issues(page: dict) -> list[dict]:
    """Flat, prioritised list of problems found by the audit."""
    issues: list[dict] = []

    def add(severity: str, message: str) -> None:
        issues.append({"severity": severity, "issue": message})

    for label, key in (("Title tag", "title"), ("Meta description", "description")):
        status = page[key]["status"]
        if status == "Missing":
            add("High", f"{label} is missing.")
        elif status != "Optimal":
            add("Medium", f"{label} is {status.lower()} "
                          f"({page[key]['chars']} chars, ~{page[key]['pixels']} px).")
    if page["robots"]["noindex"]:
        add("High", "Page is set to noindex and will not appear in search results.")
    if not page["canonical"]["present"]:
        add("Medium", "No canonical URL is declared.")
    h1 = page["headings"]["h1_count"]
    if h1 == 0:
        add("High", "No H1 heading found.")
    elif h1 > 1:
        add("Medium", f"{h1} H1 headings found; use exactly one.")
    if page["headings"]["skipped_levels"]:
        add("Low", f"Heading levels are skipped in {len(page['headings']['skipped_levels'])} place(s).")
    if page["images"]["missing_alt_count"]:
        add("Medium", f"{page['images']['missing_alt_count']} image(s) have no alt attribute.")
    if page["links"]["empty_anchor_count"]:
        add("Low", f"{page['links']['empty_anchor_count']} link(s) have no anchor text.")
    if page["jsonld"]["parse_errors"]:
        add("Medium", f"{page['jsonld']['parse_errors']} JSON-LD block(s) contain invalid JSON.")
    if not page["jsonld"]["types"]:
        add("Medium", "No JSON-LD structured data found.")
    if page["content"]["word_count"] < 300:
        add("Low", f"Thin content: {page['content']['word_count']} words in the main body.")
    order = {"High": 0, "Medium": 1, "Low": 2}
    return sorted(issues, key=lambda i: order[i["severity"]])
