"""Reverse-engineer the pages that rank for a keyword and find content gaps."""

from __future__ import annotations

import math
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

from . import brief as brief_module
from . import keywords as keywords_module
from . import serp
from .audit import analyze_html
from .config import Settings
from .geo import is_question
from .http import fetch_page, normalize_url

STOPWORDS = frozenset("""
a about above after again all also am an and any are as at be because been before being
below between both but by can could did do does doing down during each few for from further
get had has have having he her here hers him his how i if in into is it its just like
ll may me might more most much must my no nor not now of off on once one only or other our
out over own re same she should so some such than that the their them then there these they
this those through to too under until up us very was we were what when where which while
who whom why will with would you your yours vs via per use used using new best top make
made many need help find way ways know want see share content article time work better
based reviewed read learn click page site website home said says may also even still well
""".split())

_SKIP_HEADINGS = re.compile(
    r"related|comment|share this|newsletter|subscribe|about the author|table of contents|"
    r"references|leave a reply|sign up|follow us|recent posts|you may also|read more|"
    r"contact us|menu|footer|navigation|cookie|privacy|main content|helpful links|"
    r"skip to|sources|^contents$|see also|external links|further reading|what's next|advertisement|more from|popular|trending|was this helpful", re.I)
_NUMBERING = re.compile(r"^\s*(?:step\s+)?\d{1,2}[.):]\s+", re.I)
_SKIP_HOSTS = ("youtube.com", "facebook.com", "instagram.com", "tiktok.com", "x.com",
               "twitter.com", "pinterest.com")
_TOKEN = re.compile(r"[a-z][a-z0-9'-]*")


def _host(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def _key_tokens(text: str) -> frozenset[str]:
    return frozenset(t for t in _tokens(text) if t not in STOPWORDS and len(t) > 2)


def _terms(text: str) -> Counter:
    """Candidate entities: meaningful single words and 2-3 word phrases."""
    tokens = _tokens(text)
    counts: Counter = Counter()
    for i, token in enumerate(tokens):
        if token not in STOPWORDS and len(token) >= 4:
            counts[token] += 1
        for size in (2, 3):
            gram = tokens[i:i + size]
            if (len(gram) == size and gram[0] not in STOPWORDS and gram[-1] not in STOPWORDS
                    and len(gram[0]) > 2 and len(gram[-1]) > 2):
                counts[" ".join(gram)] += 1
    return counts


def _heading_tree(page: dict) -> list[dict]:
    tree: list[dict] = []
    for heading in page["headings"]["items"]:
        text = _NUMBERING.sub("", heading["text"])
        if not text or _SKIP_HEADINGS.search(text) or len(text) > 120:
            continue
        if heading["level"] == 2:
            tree.append({"h2": text, "h3": []})
        elif heading["level"] == 3 and tree:
            tree[-1]["h3"].append(text)
    return tree


def _audit_competitor(result: dict, settings: Settings) -> dict:
    entry = {"position": result["position"], "url": result["url"], "title": result["title"],
             "ok": False, "error": "", "word_count": 0, "heading_tree": [],
             "h2_count": 0, "h3_count": 0, "schema_types": [], "_text": ""}
    fetched = fetch_page(result["url"], timeout=settings.timeout,
                         allow_private=settings.allow_private_hosts)
    if not fetched.ok or not fetched.is_html or (fetched.status_code or 0) >= 400:
        entry["error"] = fetched.error or f"HTTP {fetched.status_code}, not readable"
        return entry
    page = analyze_html(fetched.body, fetched.final_url)
    tree = _heading_tree(page)
    headings = " . ".join(h["text"] for h in page["headings"]["items"])
    entry.update(ok=True, title=entry["title"] or page["title"]["text"], word_count=page["content"]["word_count"], heading_tree=tree,
                 h2_count=len(tree), h3_count=sum(len(n["h3"]) for n in tree),
                 schema_types=page["jsonld"]["types"],
                 _text=headings + " . " + page["content"]["text"])
    return entry


def _entity_gap(competitors: list[dict], keyword: str, my_text: str | None) -> list[dict]:
    needed = max(2, math.ceil(len(competitors) / 2))
    doc_frequency: Counter = Counter()
    totals: Counter = Counter()
    for competitor in competitors:
        counts = _terms(competitor["_text"])
        doc_frequency.update(counts.keys())
        totals.update(counts)
    keyword_tokens = set(_tokens(keyword))
    ranked = sorted(
        (t for t, df in doc_frequency.items()
         if df >= needed and not set(t.split()) <= keyword_tokens),
        key=lambda t: (-doc_frequency[t], -totals[t]))
    phrases = [t for t in ranked if " " in t][:15]
    covered_words = {w for p in phrases for w in p.split()}
    singles = [t for t in ranked if " " not in t and t not in covered_words][:15]
    mine = my_text.lower() if my_text is not None else None
    return [{"entity": term, "competitors_using": doc_frequency[term],
             "total_mentions": totals[term],
             "in_my_page": (term in mine) if mine is not None else None}
            for term in sorted(phrases + singles,
                               key=lambda t: (-doc_frequency[t], -totals[t]))]


def _subtopics(competitors: list[dict], my_text: str | None) -> list[dict]:
    """Group similar competitor H2s; a group used by several pages is a subtopic."""
    clusters: list[dict] = []
    for index, competitor in enumerate(competitors):
        for order, node in enumerate(competitor["heading_tree"]):
            tokens = _key_tokens(node["h2"])
            if not tokens:
                continue
            for cluster in clusters:
                overlap = len(tokens & cluster["tokens"]) / len(tokens | cluster["tokens"])
                if overlap >= 0.5:
                    break
            else:
                cluster = {"tokens": tokens, "headings": [], "pages": set(),
                           "orders": [], "h3": []}
                clusters.append(cluster)
            cluster["headings"].append(node["h2"])
            cluster["pages"].add(index)
            cluster["orders"].append(order)
            cluster["h3"].extend(node["h3"])
    mine = _key_tokens(my_text) if my_text is not None else None
    subtopics = []
    for cluster in clusters:
        h3_seen, h3 = set(), []
        for text in cluster["h3"]:
            key = _key_tokens(text)
            if key and key not in h3_seen:
                h3_seen.add(key)
                h3.append(text)
        covered = None
        if mine is not None:
            covered = len(cluster["tokens"] & mine) / len(cluster["tokens"]) >= 0.6
        subtopics.append({
            "heading": min(cluster["headings"], key=len),
            "competitors_using": len(cluster["pages"]),
            "typical_order": sum(cluster["orders"]) / len(cluster["orders"]),
            "h3": h3[:4], "covered_by_my_page": covered})
    subtopics.sort(key=lambda s: (-s["competitors_using"], s["typical_order"]))
    return subtopics[:20]


def _questions(keyword: str, competitors: list[dict], country: str) -> list[str]:
    """Questions people ask: autocomplete question forms plus competitor headings."""
    queries = [f"{prefix} {keyword}" for prefix in ("how", "what", "why", "can", "is")]
    questions: list[str] = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        for suggestions, _ in pool.map(lambda q: keywords_module.fetch_suggestions(q, country), queries):
            questions.extend(s for s in suggestions if is_question(s)
                             and not re.search(r"\b(reddit|amazon|quora|youtube)\b", s))
    for competitor in competitors:
        for node in competitor["heading_tree"]:
            questions.extend(h for h in [node["h2"], *node["h3"]] if is_question(h))
    unique, seen = [], set()
    for question in questions:
        key = _key_tokens(question)
        if key and key not in seen:
            seen.add(key)
            unique.append(question.strip().rstrip("?").capitalize() + "?")
    return unique[:10]


def reverse_engineer_serp_competitors(
    keyword: str,
    my_url: str = "",
    max_competitors: int = 5,
    country_code: str = "us",
    settings: Settings | None = None,
    competitor_urls: list[str] | None = None,
) -> dict:
    """Analyse the pages ranking for a keyword and build a content brief.

    competitor_urls: optional hand-picked pages. When given, live search is
    skipped, which keeps this working when no search provider is reachable.
    """
    settings = settings or Settings.from_env()
    keyword = " ".join((keyword or "").split())
    max_competitors = max(1, min(int(max_competitors), 5))
    out = {"keyword": keyword, "ok": False, "error": "", "serp": None, "competitors": [],
           "my_page": None, "entities": [], "subtopics": [], "questions": [],
           "avg_competitor_words": 0, "target_word_count": 0, "brief": None}
    if not keyword:
        out["error"] = "Enter a keyword."
        return out
    manual = list(dict.fromkeys(normalize_url(u) for u in (competitor_urls or []) if u.strip()))
    if manual:
        found = {"ok": True, "provider": "Your list", "ai_overview": "", "attempts": [],
                 "results": [{"position": i, "url": u, "title": "", "snippet": ""}
                             for i, u in enumerate(manual, 1)]}
    else:
        found = serp.search(keyword, settings, country_code, max_results=10)
    out["serp"] = {k: found[k] for k in ("provider", "results", "ai_overview", "attempts")}
    if not found["ok"]:
        out["error"] = found["error"] + " You can also paste competitor URLs yourself."
        return out

    my_url = normalize_url(my_url)
    my_host = _host(my_url) if my_url else ""
    candidates = [r for r in found["results"]
                  if _host(r["url"]) != my_host
                  and not _host(r["url"]).endswith(_SKIP_HOSTS)
                  and not urlparse(r["url"]).path.lower().endswith(".pdf")]
    # Fetch a couple of spares so unreadable pages do not shrink the comparison set.
    with ThreadPoolExecutor(max_workers=6) as pool:
        audited = list(pool.map(lambda r: _audit_competitor(r, settings),
                                candidates[:max_competitors + 2]))
    readable = [c for c in audited if c["ok"]][:max_competitors]
    skipped = [c for c in audited if not c["ok"]]
    if not readable:
        out["error"] = "None of the ranking pages could be read."
        return out

    my_text = None
    if my_url:
        mine = _audit_competitor({"position": 0, "url": my_url, "title": ""}, settings)
        out["my_page"] = {"url": my_url, "ok": mine["ok"], "error": mine["error"],
                          "word_count": mine["word_count"], "h2_count": mine["h2_count"]}
        if mine["ok"]:
            my_text = mine["_text"]

    out["entities"] = _entity_gap(readable, keyword, my_text)
    out["subtopics"] = _subtopics(readable, my_text)
    out["questions"] = _questions(keyword, readable, country_code)
    average = sum(c["word_count"] for c in readable) / len(readable)
    out["avg_competitor_words"] = round(average)
    out["target_word_count"] = int(math.ceil(average * 1.15 / 50) * 50)
    out["competitors"] = [{k: v for k, v in c.items() if not k.startswith("_")}
                          for c in readable + skipped]
    out["brief"] = brief_module.build_brief(out, settings)
    out["ok"] = True
    return out
