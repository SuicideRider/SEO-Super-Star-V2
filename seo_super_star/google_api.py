"""Google service-account access: live Search Console rows and Google Sheet sync.

Both are optional. A service account is free and needs no card. The user
adds the account's email as a user on the Search Console property, and
shares the Google Sheet with it.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from urllib.parse import quote

from .config import Settings

_SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly",
           "https://www.googleapis.com/auth/spreadsheets"]


class GoogleError(Exception):
    pass


def service_account_email(settings: Settings) -> str:
    try:
        return json.loads(settings.google_service_account_json).get("client_email", "")
    except (ValueError, AttributeError):
        return ""


def _session(settings: Settings):
    if not settings.google_service_account_json:
        raise GoogleError("No Google service account is configured.")
    try:
        info = json.loads(settings.google_service_account_json)
    except ValueError as exc:
        raise GoogleError("GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON.") from exc
    try:
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2 import service_account
        credentials = service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)
    except ImportError as exc:
        raise GoogleError("The google-auth package is not installed.") from exc
    except (ValueError, KeyError) as exc:
        raise GoogleError(f"The service account key could not be read: {exc}") from exc
    return AuthorizedSession(credentials)


def _call(session, method: str, url: str, body: dict | None = None) -> dict:
    try:
        resp = session.request(method, url, json=body, timeout=30)
    except Exception as exc:          # google-auth raises its own transport errors
        raise GoogleError(f"Google request failed: {exc.__class__.__name__}") from exc
    if resp.status_code == 403:
        raise GoogleError("Google refused access (403). Check that the service account "
                          "email was added to the property or sheet, and that the API "
                          "is enabled in the Google Cloud project.")
    if resp.status_code == 404:
        raise GoogleError("Google could not find that property or sheet (404).")
    if resp.status_code != 200:
        raise GoogleError(f"Google answered HTTP {resp.status_code}.")
    return resp.json() if resp.content else {}


def fetch_search_console_rows(settings: Settings, site_url: str = "", days: int = 28,
                              row_limit: int = 1000, session=None) -> list[dict]:
    """Query + page rows for the last `days` days, in the miner's row format."""
    site = (site_url or settings.gsc_site_url).strip()
    if not site:
        raise GoogleError("No Search Console property is set (for example "
                          "sc-domain:example.com or https://www.example.com/).")
    session = session or _session(settings)
    # Search Console data lags by about three days.
    end = date.today() - timedelta(days=3)
    start = end - timedelta(days=max(1, int(days)) - 1)
    data = _call(session, "POST",
                 f"https://www.googleapis.com/webmasters/v3/sites/{quote(site, safe='')}"
                 "/searchAnalytics/query",
                 {"startDate": start.isoformat(), "endDate": end.isoformat(),
                  "dimensions": ["query", "page"], "rowLimit": int(row_limit)})
    rows = []
    for item in data.get("rows", []):
        keys = item.get("keys") or ["", ""]
        rows.append({"query": keys[0], "page": keys[1] if len(keys) > 1 else "",
                     "clicks": int(round(item.get("clicks", 0))),
                     "impressions": int(round(item.get("impressions", 0))),
                     "ctr": round(item.get("ctr", 0) * 100, 2),
                     "position": round(item.get("position", 0), 1)})
    return rows


def sync_history_to_sheet(store, settings: Settings, session=None) -> dict:
    """Overwrite the Audits, Keywords and Experiments tabs of the configured sheet."""
    out = {"ok": False, "error": "", "tabs": {}, "sheet_url": ""}
    if not settings.google_sheet_id:
        out["error"] = "No Google Sheet is configured (GOOGLE_SHEET_ID)."
        return out
    base = f"https://sheets.googleapis.com/v4/spreadsheets/{quote(settings.google_sheet_id, safe='')}"
    tables = {"Audits": store.list_audits(5000), "Keywords": store.list_keywords(20000),
              "Experiments": store.list_experiments(5000)}
    try:
        session = session or _session(settings)
        meta = _call(session, "GET", base + "?fields=sheets.properties.title")
        existing = {s["properties"]["title"] for s in meta.get("sheets", [])}
        missing = [name for name in tables if name not in existing]
        if missing:
            _call(session, "POST", base + ":batchUpdate", {"requests": [
                {"addSheet": {"properties": {"title": name}}} for name in missing]})
        for name, rows in tables.items():
            _call(session, "POST", f"{base}/values/{name}:clear", {})
            if rows:
                header = list(rows[0])
                values = [header] + [["" if r[c] is None else r[c] for c in header] for r in rows]
                _call(session, "PUT", f"{base}/values/{name}!A1?valueInputOption=RAW",
                      {"values": values})
            out["tabs"][name] = len(rows)
    except GoogleError as exc:
        out["error"] = str(exc)
        return out
    out.update(ok=True, sheet_url=f"https://docs.google.com/spreadsheets/d/{settings.google_sheet_id}")
    return out
