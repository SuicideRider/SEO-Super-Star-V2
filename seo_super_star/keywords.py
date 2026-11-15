"""Keyword discovery from search autocomplete, with intent and opportunity scoring.

The Opportunity Score is a prioritisation heuristic. It is not search volume:
no free source provides that.
"""

from __future__ import annotations

import re
import string
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests

from . import trends as trends_module
from .config import Settings
from .http import USER_AGENT
from .storage import open_storage

QUESTION_MODIFIERS = ["how to {}", "what is {}", "why {}", "best {}", "{} vs",
                      "price of {}", "{} for"]
TREND_SAMPLE = 12  # how many top keywords get a trend lookup besides the seed

_TRANSACTIONAL = re.compile(
    r"\b(buy|price|prices|pricing|cost|costs|cheap|cheapest|discount|deal|deals|"
    r"coupon|order|for sale|near me|hire|quote|booking|book|download|subscription|"
    r"shop|store|purchase|rent|rental|free trial)\b")
_COMMERCIAL = re.compile(
    r"\b(best|top|vs|versus|review|reviews|comparison|compare|alternative|"
    r"alternatives|recommended|rated|worth it)\b")


def classify_intent(keyword: str) -> str:
    keyword = keyword.lower()
    if _TRANSACTIONAL.search(keyword):
        return "Transactional"
    if _COMMERCIAL.search(keyword):
        return "Commercial"
    return "Informational"


def _google_suggest(query: str, gl: str, hl: str) -> list[str]:
    resp = requests.get(
        "https://suggestqueries.google.com/complete/search", timeout=8,
        params={"client": "firefox", "q": query, "gl": gl, "hl": hl,
                "ie": "utf-8", "oe": "utf-8"},
        headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    return [s for s in resp.json()[1] if isinstance(s, str)]


def _duckduckgo_suggest(query: str, gl: str, hl: str) -> list[str]:
    resp = requests.get("https://duckduckgo.com/ac/", timeout=8,
                        params={"q": query, "type": "list", "kl": f"{gl}-{hl}"},
                        headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    return [s for s in resp.json()[1] if isinstance(s, str)]


_PROVIDERS = [("Google autocomplete", _google_suggest),
              ("DuckDuckGo autocomplete", _duckduckgo_suggest)]


def fetch_suggestions(query: str, gl: str = "us", hl: str = "en") -> tuple[list[str], str]:
    """Suggestions for one query from the first provider that answers."""
    for name, provider in _PROVIDERS:
        try:
            suggestions = provider(query, gl, hl)
        except (requests.exceptions.RequestException, ValueError, IndexError, TypeError):
            continue
        if suggestions:
            return suggestions, name
    return [], ""


def build_queries(seed: str, include_questions: bool, include_alphabet: bool) -> list[str]:
    queries = [seed]
    if include_questions:
        queries += [m.format(seed) for m in QUESTION_MODIFIERS]
    if include_alphabet:
        queries += [f"{seed} {letter}" for letter in string.ascii_lowercase]
    return queries


def opportunity_score(intent: str, best_rank: int, source_count: int,
                      word_count: int, trend: dict | None) -> int:
    """0-100: intent (30) + autocomplete prominence (25) + specificity (15) + trend (30)."""
    intent_points = {"Transactional": 30, "Commercial": 25}.get(intent, 15)
    prominence = max(0.0, 15 - best_rank * 1.5) + min(10, (source_count - 1) * 5)
    specificity = 15 if 3 <= word_count <= 5 else 10 if word_count > 5 else 8 if word_count == 2 else 3
    if trend:
        momentum = trend["interest"] / 100 * 20 + {"rising": 10, "steady": 5}.get(trend["direction"], 0)
    else:
        momentum = 15  # neutral when trend data is unavailable
    return max(0, min(100, round(intent_points + prominence + specificity + momentum)))


def discover_keywords_and_trends(
    seed_keyword: str,
    country_code: str = "us",
    include_questions: bool = True,
    include_alphabet: bool = False,
    language: str = "en",
    include_trends: bool = True,
    settings: Settings | None = None,
    save: bool = True,
) -> dict:
    settings = settings or Settings.from_env()
    seed = " ".join(seed_keyword.lower().split())
    gl, hl = (country_code or "us").lower().strip(), (language or "en").lower().strip()
    result = {"seed_keyword": seed, "country_code": gl, "language": hl, "ok": False,
              "error": "", "providers": [], "queries_run": 0, "trend_status": "",
              "keywords": [], "saved": 0,
              "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    if not seed:
        result["error"] = "Enter a seed keyword."
        return result

    queries = build_queries(seed, include_questions, include_alphabet)
    result["queries_run"] = len(queries)
    with ThreadPoolExecutor(max_workers=6) as pool:
        answers = list(pool.map(lambda q: fetch_suggestions(q, gl, hl), queries))

    found: dict[str, dict] = {}
    providers: list[str] = []
    for query, (suggestions, provider) in zip(queries, answers):
        if provider and provider not in providers:
            providers.append(provider)
        for rank, suggestion in enumerate(suggestions):
            keyword = " ".join(suggestion.lower().split())
            if not keyword:
                continue
            entry = found.setdefault(keyword, {"keyword": keyword, "sources": [], "best_rank": rank})
            entry["best_rank"] = min(entry["best_rank"], rank)
            if query not in entry["sources"]:
                entry["sources"].append(query)
    result["providers"] = providers
    if not found:
        result["error"] = "No autocomplete provider returned suggestions. Try again shortly."
        return result

    for entry in found.values():
        entry["intent"] = classify_intent(entry["keyword"])
        entry["words"] = len(entry["keyword"].split())
        entry["opportunity_score"] = opportunity_score(
            entry["intent"], entry["best_rank"], len(entry["sources"]), entry["words"], None)

    trend_data: dict[str, dict] = {}
    if include_trends:
        ranked = sorted(found.values(), key=lambda e: -e["opportunity_score"])
        sample = [e["keyword"] for e in ranked[:TREND_SAMPLE]]
        lookup = trends_module.fetch_trends(seed, sample, gl, hl)
        trend_data = lookup["trends"]
        if trend_data:
            result["trend_status"] = (f"Trend data found for {len(trend_data)} keyword(s)."
                                      + (f" {lookup['error']}" if lookup["error"] else ""))
        else:
            result["trend_status"] = (f"{lookup['error']} Scores use a neutral trend value.")
    else:
        result["trend_status"] = "Trend lookup skipped. Scores use a neutral trend value."

    for entry in found.values():
        trend = trend_data.get(entry["keyword"])
        entry["trend_interest"] = trend["interest"] if trend else None
        entry["trend_direction"] = trend["direction"] if trend else "unknown"
        entry["opportunity_score"] = opportunity_score(
            entry["intent"], entry["best_rank"], len(entry["sources"]), entry["words"], trend)
        entry["source_count"] = len(entry["sources"])
        entry["sources"] = "; ".join(entry["sources"])

    result["keywords"] = sorted(found.values(),
                                key=lambda e: (-e["opportunity_score"], e["keyword"]))
    result["ok"] = True
    if save:
        try:
            result["saved"] = open_storage(settings).save_keywords(result)
        except Exception as exc:  # storage must never break discovery
            result["trend_status"] += f" History not saved: {exc}"
    return result
