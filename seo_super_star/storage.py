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


_PG_READY: set[str] = set()      # database URLs whose tables were already created


def _pg_connect(database_url: str):
    """Open a Postgres connection from a postgres:// URL (pure-Python driver)."""
    import ssl
    from urllib.parse import parse_qs, unquote, urlparse

    import pg8000.dbapi

    parts = urlparse(database_url)
    if parts.scheme not in ("postgres", "postgresql") or not parts.hostname:
        raise ValueError("DATABASE_URL must look like postgresql://user:password@host/dbname")
    local = parts.hostname in ("localhost", "127.0.0.1")
    wants_ssl = parse_qs(parts.query).get("sslmode", ["require"])[0] != "disable"
    return pg8000.dbapi.connect(
        user=unquote(parts.username or ""), password=unquote(parts.password or ""),
        host=parts.hostname, port=parts.port or 5432,
        database=unquote(parts.path.lstrip("/")) or "postgres", timeout=15,
        ssl_context=ssl.create_default_context() if wants_ssl and not local else None)


class Storage:
    """History store. Local SQLite file, or cloud Postgres when a URL is given.

    Every method is written once in SQLite dialect; `_run` translates the few
    differences for Postgres.
    """

    fallback_reason = ""

    def __init__(self, db_path: str, database_url: str = ""):
        self.db_path = db_path
        self.database_url = database_url
        self.backend = "postgres" if database_url else "sqlite"
        if self.backend == "postgres":
            if database_url not in _PG_READY:
                ddl = (_SCHEMA.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
                       .replace(" REAL", " DOUBLE PRECISION"))
                with closing(self._connect()) as conn:
                    cursor = conn.cursor()
                    for statement in filter(str.strip, ddl.split(";")):
                        cursor.execute(statement)
                    conn.commit()
                _PG_READY.add(database_url)
            return
        folder = os.path.dirname(db_path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with closing(self._connect()) as conn, conn:
            conn.executescript(_SCHEMA)

    @property
    def label(self) -> str:
        return "Cloud Postgres" if self.backend == "postgres" else f"Local file ({self.db_path})"

    def _connect(self):
        if self.backend == "postgres":
            return _pg_connect(self.database_url)
        return sqlite3.connect(self.db_path, timeout=10)

    def _run(self, sql: str, params=(), *, many: bool = False, fetch: bool = False,
             insert_id: bool = False):
        """Run one statement. Returns rows (fetch), the new id (insert_id) or rowcount."""
        postgres = self.backend == "postgres"
        if postgres:
            sql = sql.replace("?", "%s") + (" RETURNING id" if insert_id else "")
        with closing(self._connect()) as conn:
            cursor = conn.cursor()
            if many:
                cursor.executemany(sql, params)
            else:
                cursor.execute(sql, params)
            if fetch:
                names = [d[0] for d in cursor.description]
                result = [dict(zip(names, row)) for row in cursor.fetchall()]
            elif insert_id:
                result = int(cursor.fetchone()[0] if postgres else cursor.lastrowid)
            else:
                result = cursor.rowcount
            conn.commit()
            return result

    def save_audit(self, report: dict, keep_report: bool = True) -> int:
        """Store an audit. keep_report=False stores the summary columns only,
        which keeps large batch runs from filling a small free database."""
        row = summary_row(report)
        # The Markdown body is large and can be regenerated, so it is not stored.
        slim = json.loads(json.dumps(report))
        if slim.get("page"):
            slim["page"]["content"].pop("markdown", None)
            slim["page"]["content"].pop("text", None)
        row["report_json"] = json.dumps(slim) if keep_report else None
        columns = list(row)
        sql = (f"INSERT INTO url_audits ({', '.join(columns)}) "
               f"VALUES ({', '.join('?' for _ in columns)})")
        return self._run(sql, [row[c] for c in columns], insert_id=True)

    def list_audits(self, limit: int = 500) -> list[dict]:
        sql = (f"SELECT {', '.join(SUMMARY_COLUMNS)} FROM url_audits "
               "ORDER BY id DESC LIMIT ?")
        return self._run(sql, (int(limit),), fetch=True)

    def get_audit(self, audit_id: int) -> dict | None:
        rows = self._run("SELECT report_json FROM url_audits WHERE id = ?",
                         (int(audit_id),), fetch=True)
        return json.loads(rows[0]["report_json"]) if rows and rows[0]["report_json"] else None

    # --- keyword clusters -------------------------------------------------

    def save_keywords(self, result: dict) -> int:
        rows = [(result["created_at"], result["seed_keyword"], result["country_code"],
                 result["language"], k["keyword"], k["intent"], k["trend_interest"],
                 k["trend_direction"], k["opportunity_score"])
                for k in result["keywords"]]
        sql = (f"INSERT INTO keyword_clusters ({', '.join(KEYWORD_COLUMNS)}) "
               f"VALUES ({', '.join('?' for _ in KEYWORD_COLUMNS)})")
        if rows:
            self._run(sql, rows, many=True)
        return len(rows)

    def list_keywords(self, limit: int = 2000) -> list[dict]:
        sql = (f"SELECT {', '.join(KEYWORD_COLUMNS)} FROM keyword_clusters "
               "ORDER BY id DESC LIMIT ?")
        return self._run(sql, (int(limit),), fetch=True)

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
        return self._run(sql, values, insert_id=True)

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
        return self._run(sql, values) == 1

    def list_experiments(self, limit: int = 500) -> list[dict]:
        sql = (f"SELECT {', '.join(EXPERIMENT_COLUMNS)} FROM seo_experiments "
               "ORDER BY id DESC LIMIT ?")
        rows = self._run(sql, (int(limit),), fetch=True)
        for row in rows:
            base, now = row["baseline_position"], row["current_position"]
            # Positive means the page moved up the results.
            row["position_gain"] = round(base - now, 1) if base is not None and now is not None else None
            base, now = row["baseline_ctr"], row["current_ctr"]
            row["ctr_change"] = round(now - base, 2) if base is not None and now is not None else None
        return rows

def open_storage(settings) -> Storage:
    """The cloud database when DATABASE_URL is set and reachable, else the local file.

    A cloud failure never stops the app: it falls back to SQLite and the
    reason is kept on `fallback_reason` for the status panel.
    """
    reason = ""
    if settings.database_url:
        try:
            return Storage(settings.db_path, settings.database_url)
        except Exception as exc:
            reason = f"Cloud database unreachable ({exc.__class__.__name__}); using the local file."
    store = Storage(settings.db_path)
    store.fallback_reason = reason
    return store
