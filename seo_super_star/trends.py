"""Best-effort search-trend momentum.

There is no stable free trends API. This talks to the public trends endpoints
the way a browser does and is often rate-limited, so every failure returns an
empty result and a reason; callers must work without trend data.
"""

from __future__ import annotations

import json

import requests

from .http import USER_AGENT

_BASE = "https://trends.google.com"
BATCH_SIZE = 5  # the endpoint compares at most five terms at once
MIN_PEAK = 5    # below this the series is too coarse to read a trend from


def _parse(text: str) -> dict:
    return json.loads(text[text.index("{"):])


def _direction(values: list[float]) -> str:
    recent, earlier = values[-13:], values[-26:-13]
    if not earlier or not sum(earlier):
        return "steady"
    change = (sum(recent) / len(recent)) / (sum(earlier) / len(earlier)) - 1
    return "rising" if change > 0.15 else "cooling" if change < -0.15 else "steady"


def _fetch_batch(session: requests.Session, terms: list[str], geo: str, hl: str) -> dict[str, list[float]]:
    payload = {"comparisonItem": [{"keyword": t, "time": "today 12-m", "geo": geo} for t in terms],
               "category": 0, "property": ""}
    explore = session.get(f"{_BASE}/trends/api/explore", timeout=15,
                          params={"hl": hl, "tz": "0", "req": json.dumps(payload)})
    explore.raise_for_status()
    widget = next(w for w in _parse(explore.text)["widgets"] if w.get("id") == "TIMESERIES")
    series = session.get(f"{_BASE}/trends/api/widgetdata/multiline", timeout=15,
                         params={"hl": hl, "tz": "0", "token": widget["token"],
                                 "req": json.dumps(widget["request"])})
    series.raise_for_status()
    points = _parse(series.text)["default"]["timelineData"]
    return {term: [float(p["value"][i]) for p in points] for i, term in enumerate(terms)}


def fetch_trends(seed: str, keywords: list[str], country: str = "us",
                 language: str = "en") -> dict:
    """Momentum (0-100) and direction for the seed and the given keywords.

    Interest is each keyword's last-four-week average as a percentage of its
    own 12-month peak. Measuring against its own peak, not against the seed,
    keeps long-tail keywords from all rounding down to zero.
    """
    geo = country.upper() if country else ""
    terms = list(dict.fromkeys([seed] + keywords))
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": f"{language},en;q=0.8"})
    trends: dict[str, dict] = {}
    error = ""
    try:
        session.get(f"{_BASE}/?geo={geo}", timeout=15)  # sets the cookie the API expects
        for start in range(0, len(terms), BATCH_SIZE):
            data = _fetch_batch(session, terms[start:start + BATCH_SIZE], geo, language)
            for term, values in data.items():
                peak = max(values, default=0)
                if peak < MIN_PEAK:
                    continue  # too little signal next to its batch neighbours to trust
                trends[term] = {"interest": round(sum(values[-4:]) / 4 / peak * 100),
                                "direction": _direction(values)}
    except requests.exceptions.HTTPError as exc:
        code = exc.response.status_code if exc.response is not None else "?"
        error = ("Trend source is rate-limiting requests." if code == 429
                 else f"Trend source returned HTTP {code}.")
    except (requests.exceptions.RequestException, ValueError, KeyError,
            StopIteration, IndexError) as exc:
        error = f"Trend source unavailable ({exc.__class__.__name__})."

    return {"trends": trends, "error": error}
