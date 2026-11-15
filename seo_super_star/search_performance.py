"""First-party search performance miner: CSV ingestion and opportunity buckets."""

from __future__ import annotations

import csv
import io

CTR_THRESHOLD = 3.5
DEFAULT_MIN_IMPRESSIONS = 50

BUCKETS = ["Top Winner", "CTR Underperformer", "Striking Distance",
           "Cluster Expansion", "Monitor"]

RECOMMENDATIONS = {
    "Top Winner": "Protect it: keep the content fresh and link from this page to "
                  "pages that need a lift.",
    "CTR Underperformer": "Rewrite the title hook and meta description, and add "
                          "rich-snippet schema (FAQPage, Review, HowTo).",
    "Striking Distance": "Add the exact query to an H2, answer it in a 50-word "
                         "block, and add internal links to this page.",
    "Cluster Expansion": "Add a dedicated section for this query, or write a "
                         "supporting article that links back.",
    "Monitor": "No action yet: too few impressions or too far down to act on.",
}

# Rough share of clicks by organic position, used only to rank opportunities.
_EXPECTED_CTR = {1: 28.0, 2: 15.0, 3: 10.0, 4: 7.0, 5: 5.0, 6: 4.0, 7: 3.0,
                 8: 2.5, 9: 2.0, 10: 1.8}

_ALIASES = {
    "query": {"query", "queries", "top queries", "keyword", "search query"},
    "page": {"page", "pages", "top pages", "url", "landing page"},
    "clicks": {"clicks", "url clicks"},
    "impressions": {"impressions"},
    "ctr": {"ctr", "url ctr", "site ctr"},
    "position": {"position", "average position", "avg position", "avg. position"},
}


def _number(value) -> float | None:
    text = str(value if value is not None else "").strip().replace(",", "").replace("%", "")
    try:
        return float(text)
    except ValueError:
        return None


def parse_csv(data: str | bytes) -> dict:
    """Read a search performance export. Returns {"rows": [...], "error": str}."""
    if isinstance(data, bytes):
        data = data.decode("utf-8-sig", errors="replace")
    data = data.lstrip("﻿")
    try:
        dialect = csv.Sniffer().sniff(data[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(data), dialect=dialect)
    columns: dict[str, str] = {}
    for header in reader.fieldnames or []:
        normalised = (header or "").strip().lower()
        for field, names in _ALIASES.items():
            if normalised in names and field not in columns:
                columns[field] = header
    missing = [f for f in ("impressions", "position") if f not in columns]
    if "query" not in columns and "page" not in columns:
        missing.insert(0, "query or page")
    if missing:
        return {"rows": [], "error": "The CSV is missing these columns: "
                                     + ", ".join(missing) + "."}

    raw_rows = list(reader)
    ctr_column = columns.get("ctr")
    # Exports write CTR as "3.5%"; API dumps write it as a 0-1 fraction.
    ctr_is_percent = bool(ctr_column) and any("%" in str(r.get(ctr_column, "")) for r in raw_rows)
    if ctr_column and not ctr_is_percent:
        values = [_number(r.get(ctr_column)) for r in raw_rows]
        ctr_is_percent = any(v is not None and v > 1 for v in values)

    rows = []
    for raw in raw_rows:
        impressions = _number(raw.get(columns["impressions"]))
        position = _number(raw.get(columns["position"]))
        if impressions is None or position is None:
            continue
        clicks = _number(raw.get(columns["clicks"])) if "clicks" in columns else None
        ctr = _number(raw.get(ctr_column)) if ctr_column else None
        if ctr is not None and not ctr_is_percent:
            ctr *= 100
        if ctr is None:
            ctr = (clicks or 0) * 100 / impressions if impressions else 0.0
        if clicks is None:
            clicks = impressions * ctr / 100
        rows.append({
            "query": (raw.get(columns.get("query", "")) or "").strip(),
            "page": (raw.get(columns.get("page", "")) or "").strip(),
            "clicks": int(round(clicks)),
            "impressions": int(round(impressions)),
            "ctr": round(ctr, 2),
            "position": round(position, 1),
        })
    if not rows:
        return {"rows": [], "error": "The CSV has no usable data rows."}
    return {"rows": rows, "error": ""}


def classify_row(row: dict, min_impressions: int = DEFAULT_MIN_IMPRESSIONS) -> str:
    """Bucket one row. Earlier rules win, so each row lands in exactly one bucket."""
    position, ctr, impressions = row["position"], row["ctr"], row["impressions"]
    if position <= 3 and ctr >= CTR_THRESHOLD:
        return "Top Winner"
    if impressions < min_impressions:
        return "Monitor"
    if position <= 5 and ctr < CTR_THRESHOLD:
        return "CTR Underperformer"
    if 3 < position <= 15:
        return "Striking Distance"
    if 15 < position <= 35:
        return "Cluster Expansion"
    return "Monitor"


def _potential_clicks(row: dict, bucket: str) -> int:
    if bucket == "CTR Underperformer":
        target = _EXPECTED_CTR.get(max(1, round(row["position"])), 1.0)
    elif bucket == "Striking Distance":
        target = _EXPECTED_CTR[3]
    elif bucket == "Cluster Expansion":
        target = _EXPECTED_CTR[10]
    else:
        return 0
    return max(0, round(row["impressions"] * target / 100 - row["clicks"]))


def mine_opportunities(rows: list[dict], min_impressions: int = DEFAULT_MIN_IMPRESSIONS) -> dict:
    classified = []
    for row in rows:
        bucket = classify_row(row, min_impressions)
        classified.append({**row, "bucket": bucket,
                           "potential_clicks": _potential_clicks(row, bucket),
                           "recommendation": RECOMMENDATIONS[bucket]})
    classified.sort(key=lambda r: (r["bucket"] == "Monitor",
                                   -r["potential_clicks"], -r["impressions"]))
    summary = {bucket: sum(1 for r in classified if r["bucket"] == bucket) for bucket in BUCKETS}
    return {"rows": classified, "summary": summary, "total_rows": len(classified),
            "min_impressions": min_impressions}


def demo_rows() -> list[dict]:
    """Built-in sample so the miner can be tried with no data of your own."""
    site = "https://www.example-desks.com"
    sample = [
        ("standing desk", "/standing-desks", 1840, 21500, 2.1),
        ("best standing desk", "/guides/best-standing-desks", 960, 12400, 2.8),
        ("electric standing desk", "/standing-desks/electric", 310, 9800, 4.6),
        ("standing desk for tall people", "/guides/tall-users", 44, 3900, 7.2),
        ("standing desk converter", "/converters", 61, 5200, 8.9),
        ("l shaped standing desk", "/standing-desks/l-shaped", 38, 4100, 11.4),
        ("how high should a standing desk be", "/guides/desk-height", 25, 2600, 9.6),
        ("standing desk height calculator", "/tools/height-calculator", 12, 1900, 13.8),
        ("standing desk with drawers", "/standing-desks/with-drawers", 19, 2300, 6.3),
        ("adjustable desk", "/standing-desks", 52, 6400, 4.2),
        ("sit stand desk", "/standing-desks", 71, 5600, 3.4),
        ("standing desk benefits", "/guides/benefits", 22, 7400, 18.5),
        ("are standing desks worth it", "/guides/benefits", 9, 3100, 21.7),
        ("standing desk mat", "/accessories/mats", 6, 2800, 24.3),
        ("standing desk vs treadmill desk", "/guides/treadmill-desks", 4, 1500, 29.9),
        ("cable management for standing desk", "/accessories/cable-management", 7, 1750, 17.2),
        ("standing desk frame only", "/frames", 310, 4200, 1.6),
        ("bamboo standing desk", "/standing-desks/bamboo", 265, 3300, 2.4),
        ("example desks", "/", 1420, 3100, 1.0),
        ("example desks review", "/reviews", 88, 950, 1.3),
        ("dual motor standing desk", "/standing-desks/dual-motor", 18, 2900, 2.7),
        ("standing desk under 300", "/guides/budget", 11, 3400, 4.8),
        ("standing desk assembly", "/support/assembly", 3, 40, 5.5),
        ("standing desk wobble fix", "/support/wobble", 2, 35, 12.1),
        ("office chair", "/chairs", 1, 8200, 47.3),
        ("ergonomic desk setup", "/guides/ergonomics", 5, 2100, 38.6),
        ("corner standing desk", "/standing-desks/l-shaped", 14, 1650, 14.6),
        ("standing desk weight capacity", "/guides/weight-capacity", 8, 880, 10.2),
        ("white standing desk", "/standing-desks/white", 96, 2500, 3.9),
        ("small standing desk", "/standing-desks/compact", 27, 3600, 5.1),
    ]
    return [{"query": query, "page": site + path, "clicks": clicks,
             "impressions": impressions,
             "ctr": round(clicks * 100 / impressions, 2), "position": position}
            for query, path, clicks, impressions, position in sample]
