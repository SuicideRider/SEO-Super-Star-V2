"""Audit history. SQLite for now; a cloud backend joins behind this class later."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

_SCHEMA = """
CREATE TABLE IF NOT EXISTS url_audits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audited_at TEXT NOT NULL,
    url TEXT NOT NULL,
    final_url TEXT,
    target_keyword TEXT,
    status_code INTEGER,
    response_ms INTEGER,
    title_status TEXT,
    description_status TEXT,
    h1_count INTEGER,
    word_count INTEGER,
    internal_links INTEGER,
    external_links INTEGER,
    images_missing_alt INTEGER,
    schema_types TEXT,
    issue_count INTEGER,
    geo_score INTEGER,
    perf_mobile INTEGER,
    perf_desktop INTEGER,
    seo_score INTEGER,
    lcp_ms INTEGER,
    cls REAL,
    inp_ms INTEGER,
    report_json TEXT
);
CREATE TABLE IF NOT EXISTS keyword_clusters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    seed_keyword TEXT NOT NULL,
    country_code TEXT,
    language TEXT,
    keyword TEXT NOT NULL,
    intent TEXT,
    trend_interest INTEGER,
    trend_direction TEXT,
    opportunity_score INTEGER
);
CREATE TABLE IF NOT EXISTS seo_experiments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    url TEXT NOT NULL,
    target_keyword TEXT,
    hypothesis TEXT,
    changes_made TEXT,
    baseline_position REAL,
    baseline_ctr REAL,
    current_position REAL,
    current_ctr REAL,
    status TEXT NOT NULL
);
"""

EXPERIMENT_STATUSES = ["Running", "Won", "Lost", "Inconclusive"]
KEYWORD_COLUMNS = ["created_at", "seed_keyword", "country_code", "language", "keyword",
                   "intent", "trend_interest", "trend_direction", "opportunity_score"]
EXPERIMENT_COLUMNS = ["id", "created_at", "updated_at", "url", "target_keyword",
                      "hypothesis", "changes_made", "baseline_position", "baseline_ctr",
                      "current_position", "current_ctr", "status"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _real(value) -> float | None:
    """Positions and CTRs are stored as REAL; blank stays NULL, never 0."""
    if value is None or value == "":
        return None
    return float(value)

SUMMARY_COLUMNS = [
    "id", "audited_at", "url", "final_url", "target_keyword", "status_code",
    "response_ms", "title_status", "description_status", "h1_count",
    "word_count", "internal_links", "external_links", "images_missing_alt",
    "schema_types", "issue_count", "geo_score", "perf_mobile", "perf_desktop",
    "seo_score", "lcp_ms", "cls", "inp_ms",
]


def summary_row(report: dict) -> dict:
    """Flatten a full report into the columns stored and exported."""
    page = report.get("page") or {}
    fetch = report["fetch"]
    speed = report.get("pagespeed") or {}
    mobile = speed.get("mobile") or {}
    desktop = speed.get("desktop") or {}

    def get(*path, default=None):
        node = page
        for key in path:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    return {
        "audited_at": report["audited_at"],
        "url": report["url"],
        "final_url": fetch.get("final_url"),
        "target_keyword": report.get("target_keyword") or "",
        "status_code": fetch.get("status_code"),
        "response_ms": fetch.get("response_ms"),
        "title_status": get("title", "status"),
        "description_status": get("description", "status"),
        "h1_count": get("headings", "h1_count"),
        "word_count": get("content", "word_count"),
        "internal_links": get("links", "internal"),
        "external_links": get("links", "external"),
        "images_missing_alt": get("images", "missing_alt_count"),
        "schema_types": ", ".join(get("jsonld", "types", default=[]) or []),
        "issue_count": len(report.get("issues") or []),
        "geo_score": (report.get("geo") or {}).get("score"),
        "perf_mobile": mobile.get("performance"),
        "perf_desktop": desktop.get("performance"),
        "seo_score": mobile.get("seo"),
        "lcp_ms": mobile.get("lcp_ms"),
        "cls": mobile.get("cls"),
        "inp_ms": mobile.get("inp_ms"),
    }


class Storage:
    def __init__(self, db_path: str):
        self.db_path = db_path
        folder = os.path.dirname(db_path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with closing(self._connect()) as conn, conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def save_audit(self, report: dict) -> int:
        row = summary_row(report)
        # The Markdown body is large and can be regenerated, so it is not stored.
        slim = json.loads(json.dumps(report))
        if slim.get("page"):
            slim["page"]["content"].pop("markdown", None)
            slim["page"]["content"].pop("text", None)
        row["report_json"] = json.dumps(slim)
        columns = list(row)
        sql = (f"INSERT INTO url_audits ({', '.join(columns)}) "
               f"VALUES ({', '.join('?' for _ in columns)})")
        with closing(self._connect()) as conn, conn:
            cursor = conn.execute(sql, [row[c] for c in columns])
            return int(cursor.lastrowid)

    def list_audits(self, limit: int = 500) -> list[dict]:
        sql = (f"SELECT {', '.join(SUMMARY_COLUMNS)} FROM url_audits "
               "ORDER BY id DESC LIMIT ?")
        with closing(self._connect()) as conn:
            return [dict(r) for r in conn.execute(sql, (int(limit),))]

    def get_audit(self, audit_id: int) -> dict | None:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT report_json FROM url_audits WHERE id = ?",
                               (int(audit_id),)).fetchone()
        return json.loads(row["report_json"]) if row else None

    # --- keyword clusters -------------------------------------------------

    def save_keywords(self, result: dict) -> int:
        rows = [(result["created_at"], result["seed_keyword"], result["country_code"],
                 result["language"], k["keyword"], k["intent"], k["trend_interest"],
                 k["trend_direction"], k["opportunity_score"])
                for k in result["keywords"]]
        sql = (f"INSERT INTO keyword_clusters ({', '.join(KEYWORD_COLUMNS)}) "
               f"VALUES ({', '.join('?' for _ in KEYWORD_COLUMNS)})")
        with closing(self._connect()) as conn, conn:
            conn.executemany(sql, rows)
        return len(rows)

    def list_keywords(self, limit: int = 2000) -> list[dict]:
        sql = (f"SELECT {', '.join(KEYWORD_COLUMNS)} FROM keyword_clusters "
               "ORDER BY id DESC LIMIT ?")
        with closing(self._connect()) as conn:
            return [dict(r) for r in conn.execute(sql, (int(limit),))]

    # --- experiments ------------------------------------------------------

    def log_experiment(self, url: str, target_keyword: str = "", hypothesis: str = "",
                       changes_made: str = "", baseline_position=None,
                       baseline_ctr=None) -> int:
        now = _now()
        sql = ("INSERT INTO seo_experiments (created_at, updated_at, url, target_keyword, "
               "hypothesis, changes_made, baseline_position, baseline_ctr, status) "
               "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)")
        values = (now, now, url.strip(), target_keyword.strip(), hypothesis.strip(),
                  changes_made.strip(), _real(baseline_position), _real(baseline_ctr),
                  "Running")
        with closing(self._connect()) as conn, conn:
            return int(conn.execute(sql, values).lastrowid)

    def update_experiment(self, experiment_id: int, current_position=None,
                          current_ctr=None, status: str | None = None) -> bool:
        if status is not None and status not in EXPERIMENT_STATUSES:
            raise ValueError(f"Status must be one of {', '.join(EXPERIMENT_STATUSES)}.")
        sql = ("UPDATE seo_experiments SET updated_at = ?, "
               "current_position = COALESCE(?, current_position), "
               "current_ctr = COALESCE(?, current_ctr), "
               "status = COALESCE(?, status) WHERE id = ?")
        values = (_now(), _real(current_position), _real(current_ctr), status,
                  int(experiment_id))
        with closing(self._connect()) as conn, conn:
            return conn.execute(sql, values).rowcount == 1

    def list_experiments(self, limit: int = 500) -> list[dict]:
        sql = (f"SELECT {', '.join(EXPERIMENT_COLUMNS)} FROM seo_experiments "
               "ORDER BY id DESC LIMIT ?")
        with closing(self._connect()) as conn:
            rows = [dict(r) for r in conn.execute(sql, (int(limit),))]
        for row in rows:
            base, now = row["baseline_position"], row["current_position"]
            # Positive means the page moved up the results.
            row["position_gain"] = round(base - now, 1) if base is not None and now is not None else None
            base, now = row["baseline_ctr"], row["current_ctr"]
            row["ctr_change"] = round(now - base, 2) if base is not None and now is not None else None
        return rows
