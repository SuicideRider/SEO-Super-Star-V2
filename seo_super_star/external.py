"""Optional external services: JS rendering and PageSpeed Insights.

Both work without a key and both fail soft: a failure returns a dict with
`ok: False` and a reason, never an exception.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import requests

from .http import USER_AGENT

READER_ENDPOINT = "https://r.jina.ai/"
PAGESPEED_ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"


def fetch_rendered_html(url: str, api_key: str = "", timeout: int = 40) -> dict:
    """Ask a hosted reader to run the page's JavaScript and return the DOM.

    This keeps headless browsers off the app host, which has little memory.
    """
    headers = {"X-Return-Format": "html", "User-Agent": USER_AGENT}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        resp = requests.get(READER_ENDPOINT + url, headers=headers, timeout=timeout)
    except requests.exceptions.RequestException as exc:
        return {"ok": False, "error": f"Renderer unreachable: {exc.__class__.__name__}."}
    if resp.status_code == 429:
        return {"ok": False, "error": "Renderer rate limit reached."}
    if resp.status_code != 200 or "<" not in resp.text[:2000]:
        return {"ok": False, "error": f"Renderer returned HTTP {resp.status_code}."}
    return {"ok": True, "html": resp.text}


def _pagespeed_one(url: str, strategy: str, api_key: str, timeout: int) -> dict:
    params = [("url", url), ("strategy", strategy),
              ("category", "performance"), ("category", "seo")]
    if api_key:
        params.append(("key", api_key))
    out = {"strategy": strategy, "ok": False, "error": ""}
    try:
        resp = requests.get(PAGESPEED_ENDPOINT, params=params, timeout=timeout)
    except requests.exceptions.RequestException as exc:
        out["error"] = f"PageSpeed unreachable: {exc.__class__.__name__}."
        return out
    if resp.status_code == 429:
        out["error"] = "PageSpeed quota reached. Add a free API key for a higher limit."
        return out
    if resp.status_code != 200:
        try:
            message = resp.json()["error"]["message"]
        except (ValueError, KeyError, TypeError):
            message = ""
        out["error"] = f"PageSpeed returned HTTP {resp.status_code}. {message[:200]}".strip()
        return out
    data = resp.json()
    lighthouse = data.get("lighthouseResult", {})
    categories = lighthouse.get("categories", {})
    audits = lighthouse.get("audits", {})
    field = data.get("loadingExperience", {}).get("metrics", {})

    def score(name: str):
        value = categories.get(name, {}).get("score")
        return round(value * 100) if value is not None else None

    def lab(name: str):
        return audits.get(name, {}).get("numericValue")

    def crux(name: str):
        return field.get(name, {}).get("percentile")

    lcp, cls, field_cls = lab("largest-contentful-paint"), lab("cumulative-layout-shift"), \
        crux("CUMULATIVE_LAYOUT_SHIFT_SCORE")
    out.update({
        "ok": True,
        "performance": score("performance"),
        "seo": score("seo"),
        "lcp_ms": round(lcp) if lcp is not None else None,
        "cls": round(cls, 3) if cls is not None else None,
        # INP is measured from real users only; pages with little traffic have none.
        "inp_ms": crux("INTERACTION_TO_NEXT_PAINT"),
        "field_lcp_ms": crux("LARGEST_CONTENTFUL_PAINT_MS"),
        "field_cls": field_cls / 100 if field_cls is not None else None,
    })
    return out


def run_pagespeed(url: str, api_key: str = "", timeout: int = 90) -> dict:
    """Mobile and desktop PageSpeed results, fetched in parallel."""
    with ThreadPoolExecutor(max_workers=2) as pool:
        mobile = pool.submit(_pagespeed_one, url, "mobile", api_key, timeout)
        desktop = pool.submit(_pagespeed_one, url, "desktop", api_key, timeout)
        return {"mobile": mobile.result(), "desktop": desktop.result()}
