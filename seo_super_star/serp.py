"""Live search results through a free-tier waterfall.

Order: Tavily (free key, also returns an AI answer), then keyless scraping of
DuckDuckGo and Bing. Each tier that fails is recorded and the next one runs.
"""

from __future__ import annotations

import base64
import re
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup

from .config import Settings
from .http import USER_AGENT

_BROWSER_HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.8",
                    "Accept": "text/html,application/xhtml+xml"}


class ProviderError(Exception):
    pass


def _tavily(keyword: str, country: str, limit: int, settings: Settings) -> dict:
    if not settings.tavily_api_key:
        raise ProviderError("no key configured")
    resp = requests.post(
        "https://api.tavily.com/search", timeout=25,
        headers={"Authorization": f"Bearer {settings.tavily_api_key}"},
        json={"query": keyword, "max_results": limit, "include_answer": True,
              "search_depth": "basic"})
    if resp.status_code in (429, 432, 433):
        raise ProviderError("quota reached")
    if resp.status_code != 200:
        raise ProviderError(f"HTTP {resp.status_code}")
    data = resp.json()
    results = [{"url": r.get("url", ""), "title": r.get("title", ""),
                "snippet": (r.get("content") or "")[:300]}
               for r in data.get("results", [])]
    return {"results": results, "ai_overview": data.get("answer") or ""}


def _duckduckgo(keyword: str, country: str, limit: int, settings: Settings) -> dict:
    resp = requests.post("https://html.duckduckgo.com/html/", timeout=15,
                         headers=_BROWSER_HEADERS,
                         data={"q": keyword, "kl": f"{country}-en"})
    if resp.status_code != 200:
        raise ProviderError(f"HTTP {resp.status_code} (likely blocked)")
    soup = BeautifulSoup(resp.text, "lxml")
    results = []
    for block in soup.select("div.result"):
        if "result--ad" in (block.get("class") or []):
            continue
        link = block.select_one("a.result__a")
        if not link or not link.get("href"):
            continue
        href = link["href"]
        if "uddg=" in href:
            href = parse_qs(urlparse(href).query).get("uddg", [""])[0]
        snippet = block.select_one(".result__snippet")
        results.append({"url": href, "title": link.get_text(" ", strip=True),
                        "snippet": snippet.get_text(" ", strip=True) if snippet else ""})
    return {"results": results, "ai_overview": ""}


def _decode_bing_url(href: str) -> str:
    if "bing.com/ck/a" not in href:
        return href
    token = parse_qs(urlparse(href).query).get("u", [""])[0]
    if not token.startswith("a1"):
        return ""
    raw = token[2:]
    try:
        return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return ""


def _bing(keyword: str, country: str, limit: int, settings: Settings) -> dict:
    resp = requests.get("https://www.bing.com/search", timeout=15, headers=_BROWSER_HEADERS,
                        params={"q": keyword, "cc": country, "setlang": "en"})
    if resp.status_code != 200:
        raise ProviderError(f"HTTP {resp.status_code} (likely blocked)")
    soup = BeautifulSoup(resp.text, "lxml")
    results = []
    for block in soup.select("li.b_algo"):
        link = block.select_one("h2 a")
        if not link or not link.get("href"):
            continue
        snippet = block.select_one(".b_caption p") or block.select_one("p")
        results.append({"url": _decode_bing_url(link["href"]),
                        "title": link.get_text(" ", strip=True),
                        "snippet": snippet.get_text(" ", strip=True) if snippet else ""})
    return {"results": results, "ai_overview": ""}


PROVIDERS = [("Tavily", _tavily), ("DuckDuckGo", _duckduckgo), ("Bing", _bing)]
SCRAPED = {"DuckDuckGo", "Bing"}
_FILLER = {"the", "for", "and", "how", "what", "why", "with", "are", "can", "you", "does"}


def looks_relevant(keyword: str, results: list[dict]) -> bool:
    """Guard against degraded pages that search engines serve to scrapers.

    A result counts when it contains at least half of the query's meaningful
    words; the set passes when at least half of the results count.
    """
    words = {w for w in re.findall(r"[a-z0-9]+", keyword.lower())
             if len(w) > 2 and w not in _FILLER}
    if not words or not results:
        return bool(results)
    needed = max(1, (len(words) + 1) // 2)
    hits = 0
    for item in results:
        text = f"{item['title']} {item['snippet']} {item['url']}".lower()
        if sum(1 for w in words if w in text) >= needed:
            hits += 1
    return hits * 2 >= len(results)


def search(keyword: str, settings: Settings | None = None, country: str = "us",
           max_results: int = 10) -> dict:
    """Top organic results from the first provider that answers."""
    settings = settings or Settings.from_env()
    keyword = " ".join((keyword or "").split())
    out = {"keyword": keyword, "ok": False, "provider": "", "results": [],
           "ai_overview": "", "attempts": [], "error": ""}
    if not keyword:
        out["error"] = "Enter a keyword."
        return out
    for name, provider in PROVIDERS:
        try:
            found = provider(keyword, (country or "us").lower(), max_results, settings)
        except ProviderError as exc:
            out["attempts"].append({"provider": name, "outcome": str(exc)})
            continue
        except (requests.exceptions.RequestException, ValueError, KeyError) as exc:
            out["attempts"].append({"provider": name, "outcome": exc.__class__.__name__})
            continue
        results, seen = [], set()
        for item in found["results"]:
            url = (item.get("url") or "").strip()
            if not url.startswith(("http://", "https://")) or url in seen:
                continue
            seen.add(url)
            results.append({"position": len(results) + 1, "url": url,
                            "title": item["title"], "snippet": item["snippet"]})
        if not results:
            out["attempts"].append({"provider": name, "outcome": "no results (possibly blocked)"})
            continue
        if name in SCRAPED and not looks_relevant(keyword, results):
            out["attempts"].append({"provider": name,
                                    "outcome": "results did not match the query (blocked)"})
            continue
        out["attempts"].append({"provider": name, "outcome": "ok"})
        out.update(ok=True, provider=name, results=results[:max_results],
                   ai_overview=found["ai_overview"])
        return out
    out["error"] = ("No search provider returned usable results. Search engines block "
                    "keyless scraping; add a free Tavily key in the sidebar to make "
                    "this reliable.")
    return out
