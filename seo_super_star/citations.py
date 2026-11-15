"""AI citation visibility: is a brand mentioned or cited for buyer prompts?

Answer sources, in order: Tavily's web-grounded answer, Gemini with search
grounding, then plain search results. The last tier has no AI answer and says
so; it still shows whether the brand appears in what an AI would read.
"""

from __future__ import annotations

import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import requests

from . import llm, serp
from .config import Settings

MAX_PROMPTS = 10


def clean_domain(domain: str) -> str:
    domain = (domain or "").strip().lower()
    if "://" in domain:
        domain = urlparse(domain).hostname or ""
    domain = domain.split("/")[0]
    return domain[4:] if domain.startswith("www.") else domain


def _host(url: str) -> str:
    return clean_domain(urlparse(url).hostname or "")


def _matches(host: str, domain: str) -> bool:
    return bool(domain) and (host == domain or host.endswith("." + domain))


def _tavily_answer(prompt: str, settings: Settings) -> dict | None:
    if not settings.tavily_api_key:
        return None
    try:
        resp = requests.post(
            "https://api.tavily.com/search", timeout=30,
            headers={"Authorization": f"Bearer {settings.tavily_api_key}"},
            json={"query": prompt, "max_results": 8, "include_answer": True})
        if resp.status_code != 200:
            return None
        data = resp.json()
    except (requests.exceptions.RequestException, ValueError):
        return None
    if not data.get("answer"):
        return None
    return {"mode": "AI answer (Tavily)", "text": data["answer"],
            "sources": [{"url": r.get("url", ""), "domain": _host(r.get("url", ""))}
                        for r in data.get("results", [])]}


def _gemini_answer(prompt: str, settings: Settings) -> dict | None:
    reply = llm.grounded_answer(prompt, settings)
    if not reply["ok"]:
        return None
    sources = [{"url": s["url"], "domain": clean_domain(s["domain"]) or _host(s["url"])}
               for s in reply["sources"]]
    return {"mode": "AI answer (Gemini)", "text": reply["text"], "sources": sources}


def _search_only(prompt: str, settings: Settings) -> dict | None:
    found = serp.search(prompt, settings, max_results=8)
    if not found["ok"]:
        return None
    text = " ".join(f"{r['title']} {r['snippet']}" for r in found["results"])
    return {"mode": f"Search results only ({found['provider']})", "text": text,
            "sources": [{"url": r["url"], "domain": _host(r["url"])} for r in found["results"]]}


def _check_prompt(prompt: str, brand: str, domain: str, settings: Settings) -> dict:
    answer = (_tavily_answer(prompt, settings) or _gemini_answer(prompt, settings)
              or _search_only(prompt, settings))
    row = {"prompt": prompt, "mode": "Unavailable", "brand_mentioned": None,
           "domain_cited": None, "answer_excerpt": "", "competitor_urls": []}
    if not answer:
        return row
    text = answer["text"]
    mentioned = bool(brand and re.search(r"(?<!\w)" + re.escape(brand) + r"(?!\w)", text, re.I))
    cited = any(_matches(s["domain"], domain) for s in answer["sources"])
    if domain and domain in text.lower():
        mentioned = True
    competitors, seen = [], set()
    for source in answer["sources"]:
        if source["domain"] and not _matches(source["domain"], domain) and source["domain"] not in seen:
            seen.add(source["domain"])
            competitors.append({"domain": source["domain"], "url": source["url"]})
    row.update(mode=answer["mode"], brand_mentioned=mentioned, domain_cited=cited,
               answer_excerpt=" ".join(text.split())[:400],
               competitor_urls=competitors[:5])
    return row


def check_brand_ai_citation_visibility(
    brand_name: str,
    domain: str,
    buyer_prompts: list[str] | str,
    settings: Settings | None = None,
) -> dict:
    settings = settings or Settings.from_env()
    brand = " ".join((brand_name or "").split())
    domain = clean_domain(domain)
    if isinstance(buyer_prompts, str):
        buyer_prompts = buyer_prompts.splitlines()
    prompts = list(dict.fromkeys(" ".join(p.split()) for p in buyer_prompts if p.strip()))
    out = {"brand_name": brand, "domain": domain, "ok": False, "error": "",
           "prompts_checked": 0, "prompts_skipped": max(0, len(prompts) - MAX_PROMPTS),
           "visibility_percent": 0, "mentions": 0, "citations": 0,
           "results": [], "top_competitors": [], "mode_note": ""}
    if not brand and not domain:
        out["error"] = "Enter a brand name or a domain."
        return out
    if not prompts:
        out["error"] = "Enter at least one buyer prompt."
        return out
    prompts = prompts[:MAX_PROMPTS]
    with ThreadPoolExecutor(max_workers=3) as pool:
        rows = list(pool.map(lambda p: _check_prompt(p, brand, domain, settings), prompts))
    answered = [r for r in rows if r["mode"] != "Unavailable"]
    if not answered:
        out["results"] = rows
        out["error"] = ("No answer source responded. Keyless search is often blocked on "
                        "shared hosts; a free Tavily or Gemini key makes this reliable.")
        return out
    visible = sum(1 for r in answered if r["brand_mentioned"] or r["domain_cited"])
    tally = Counter(c["domain"] for r in answered for c in r["competitor_urls"])
    out.update(
        ok=True, results=rows, prompts_checked=len(answered),
        mentions=sum(1 for r in answered if r["brand_mentioned"]),
        citations=sum(1 for r in answered if r["domain_cited"]),
        visibility_percent=round(visible * 100 / len(answered)),
        top_competitors=[{"domain": d, "prompts_cited_in": n} for d, n in tally.most_common(10)])
    if any(r["mode"].startswith("Search results only") for r in answered):
        out["mode_note"] = ("Some prompts were checked against search results, not an AI "
                            "answer, because no AI answer key is configured or it failed.")
    return out
