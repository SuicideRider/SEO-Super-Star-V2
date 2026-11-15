"""Offline smoke tests for phase 4: sitemap spider, cloud storage, Google, CLI, tools."""

import gzip
import json

from seo_super_star import __main__ as cli
from seo_super_star import google_api, service, sitemap, storage
from seo_super_star.config import Settings, service_status
from seo_super_star.http import FetchResult

PAGE = b"""<html><head><title>Standing desks for every office and budget</title></head>
<body><main><h1>Standing desks</h1><p>Desks that move.</p></main></body></html>"""
INDEX = b"""<sitemapindex><sitemap><loc>https://shop.com/pages.xml.gz</loc></sitemap>
<sitemap><loc>https://shop.com/missing.xml</loc></sitemap></sitemapindex>"""
PAGES = gzip.compress(b"""<urlset><url><loc>https://shop.com/a?x=1&amp;y=2</loc></url>
<url><loc><![CDATA[https://shop.com/b]]></loc></url><url><loc>https://shop.com/broken</loc></url>
<url><loc>https://shop.com/a?x=1&amp;y=2</loc></url></urlset>""")


def fake_fetch(url, timeout=15, allow_private=False, session=None):
    def reply(body, kind="text/html", status=200):
        return FetchResult(requested_url=url, final_url=url, status_code=status,
                           content_type=kind, body=body)
    if url.endswith("/robots.txt"):
        return reply(b"User-agent: *\nSitemap: https://shop.com/index.xml\n", "text/plain")
    if url.endswith("/index.xml"):
        return reply(INDEX, "application/xml")
    if url.endswith("/pages.xml.gz"):
        return reply(PAGES, "application/gzip")
    if url.endswith("/missing.xml"):
        return reply(b"", "text/html", 404)
    if url.endswith("/broken"):
        return FetchResult(requested_url=url, error="Timed out.")
    return reply(PAGE)


def patch_fetch(monkeypatch):
    monkeypatch.setattr(sitemap, "fetch_page", fake_fetch)
    monkeypatch.setattr(service, "fetch_page", fake_fetch)


def test_sitemap_discovery_from_site_url(monkeypatch):
    patch_fetch(monkeypatch)
    found = sitemap.collect_sitemap_urls("shop.com", Settings())
    assert found["urls"] == ["https://shop.com/a?x=1&y=2", "https://shop.com/b",
                             "https://shop.com/broken"]
    assert found["sitemaps_read"] == ["https://shop.com/index.xml",
                                      "https://shop.com/pages.xml.gz"]
    assert len(sitemap.collect_sitemap_urls("shop.com/index.xml", Settings(), limit=2)["urls"]) == 2
    assert sitemap.collect_sitemap_urls("", Settings())["error"]


def test_spider_audits_and_saves(tmp_path, monkeypatch):
    patch_fetch(monkeypatch)
    settings = Settings(db_path=str(tmp_path / "s.db"))
    result = sitemap.spider_xml_sitemap("https://shop.com/index.xml", settings=settings)
    assert result["ok"] and result["urls_found"] == 3
    assert result["summary"]["pages_audited"] == 2 and result["summary"]["pages_failed"] == 1
    assert result["failed"] == [{"url": "https://shop.com/broken", "error": "Timed out."}]
    assert result["summary"]["pages_without_schema"] == 2
    assert result["pages"][0]["top_issue"]
    assert len(storage.open_storage(settings).list_audits()) == 2
    json.dumps(result)
    monkeypatch.setattr(sitemap, "fetch_page", lambda u, **k: FetchResult(requested_url=u, error="x"))
    assert not sitemap.spider_xml_sitemap("https://shop.com/index.xml", settings=settings)["ok"]


class FakeCursor:
    description = [("id",), ("url",)]
    rowcount = 1

    def __init__(self, log):
        self.log = log

    def execute(self, sql, params=()):
        self.log.append(sql)

    def executemany(self, sql, rows):
        self.log.append(sql)

    def fetchone(self):
        return (7,)

    def fetchall(self):
        return [(7, "https://shop.com/")]


class FakeConnection:
    def __init__(self, log):
        self.log = log

    def cursor(self):
        return FakeCursor(self.log)

    def commit(self):
        self.log.append("COMMIT")

    def close(self):
        pass


def test_postgres_dialect_and_fallback(tmp_path, monkeypatch):
    log = []
    monkeypatch.setattr(storage, "_pg_connect", lambda url: FakeConnection(log))
    monkeypatch.setattr(storage, "_PG_READY", set())
    settings = Settings(db_path=str(tmp_path / "x.db"), database_url="postgresql://u:p@host/db")
    store = storage.open_storage(settings)
    assert store.backend == "postgres" and store.label == "Cloud Postgres"
    ddl = " ".join(log)
    assert "BIGSERIAL PRIMARY KEY" in ddl and "AUTOINCREMENT" not in ddl
    assert "DOUBLE PRECISION" in ddl and ddl.count("CREATE TABLE") == 3
    log.clear()
    assert store.log_experiment("https://shop.com/", baseline_position=4.2) == 7
    assert "%s" in log[0] and "?" not in log[0] and log[0].endswith("RETURNING id")
    assert store.update_experiment(7, current_position=3.0) is True
    assert store.list_audits() == [{"id": 7, "url": "https://shop.com/"}]
    storage.open_storage(settings)
    assert not any("CREATE TABLE" in s for s in log)          # schema created once

    def down(url):
        raise ConnectionError("no route")
    monkeypatch.setattr(storage, "_pg_connect", down)
    monkeypatch.setattr(storage, "_PG_READY", set())
    local = storage.open_storage(settings)
    assert local.backend == "sqlite" and "unreachable" in local.fallback_reason
    assert local.log_experiment("https://shop.com/") == 1     # still works locally


class FakeResponse:
    def __init__(self, status=200, data=None):
        self.status_code, self._data = status, data or {}
        self.content = b"{}"

    def json(self):
        return self._data


class FakeSession:
    def __init__(self, replies=None, status=200):
        self.calls, self.replies, self.status = [], replies or {}, status

    def request(self, method, url, json=None, timeout=30):
        self.calls.append((method, url, json))
        for fragment, data in self.replies.items():
            if fragment in url:
                return FakeResponse(self.status, data)
        return FakeResponse(self.status)


def test_search_console_rows_and_errors(monkeypatch):
    settings = Settings(google_service_account_json="{}", gsc_site_url="sc-domain:shop.com")
    session = FakeSession({"searchAnalytics": {"rows": [
        {"keys": ["standing desk", "https://shop.com/desks"], "clicks": 12,
         "impressions": 900, "ctr": 0.0133, "position": 7.26}]}})
    rows = google_api.fetch_search_console_rows(settings, session=session)
    assert rows == [{"query": "standing desk", "page": "https://shop.com/desks", "clicks": 12,
                     "impressions": 900, "ctr": 1.33, "position": 7.3}]
    method, url, body = session.calls[0]
    assert "sc-domain%3Ashop.com" in url and body["dimensions"] == ["query", "page"]

    monkeypatch.setattr(google_api, "_session", lambda s: session)
    live = service.get_gsc_striking_distance_opportunities(settings=settings)
    assert live["ok"] and live["source"] == "live"
    assert live["rows"][0]["bucket"] == "Striking Distance"
    monkeypatch.setattr(google_api, "_session", lambda s: FakeSession(status=403))
    denied = service.get_gsc_striking_distance_opportunities(settings=settings)
    assert not denied["ok"] and "403" in denied["error"]

    demo = service.get_gsc_striking_distance_opportunities(settings=Settings())
    assert demo["ok"] and demo["source"] == "demo" and "sample data" in demo["note"]
    from_csv = service.get_gsc_striking_distance_opportunities(
        csv_data="Top queries,Clicks,Impressions,CTR,Position\ndesk,5,400,1.25%,4.0\n",
        settings=Settings())
    assert from_csv["source"] == "csv" and from_csv["summary"]["CTR Underperformer"] == 1
    assert not service.get_gsc_striking_distance_opportunities(
        source="csv", csv_data="a,b\n1,2\n", settings=Settings())["ok"]
    no_account = service.get_gsc_striking_distance_opportunities(source="live", settings=Settings())
    assert not no_account["ok"] and "property" in no_account["error"]
    monkeypatch.undo()
    no_key = service.get_gsc_striking_distance_opportunities(
        source="live", site_url="sc-domain:shop.com", settings=Settings())
    assert not no_key["ok"] and "service account" in no_key["error"]


def test_sheet_sync(tmp_path):
    settings = Settings(db_path=str(tmp_path / "g.db"), google_sheet_id="SHEET",
                        google_service_account_json="{}")
    store = storage.open_storage(settings)
    store.log_experiment("https://shop.com/", "desk", baseline_position=5)
    session = FakeSession({"fields=sheets": {"sheets": [{"properties": {"title": "Audits"}}]}})
    out = google_api.sync_history_to_sheet(store, settings, session=session)
    assert out["ok"] and out["tabs"] == {"Audits": 0, "Keywords": 0, "Experiments": 1}
    added = [c for c in session.calls if c[1].endswith(":batchUpdate")][0][2]["requests"]
    assert [r["addSheet"]["properties"]["title"] for r in added] == ["Keywords", "Experiments"]
    written = [c for c in session.calls if c[0] == "PUT"]
    assert len(written) == 1 and written[0][2]["values"][0][0] == "id"
    assert written[0][2]["values"][1][written[0][2]["values"][0].index("current_ctr")] == ""
    assert not google_api.sync_history_to_sheet(store, Settings(), session=session)["ok"]
    assert google_api.service_account_email(
        Settings(google_service_account_json='{"client_email": "bot@x.iam"}')) == "bot@x.iam"


def test_daily_job_and_cli(tmp_path, monkeypatch, capsys):
    patch_fetch(monkeypatch)
    settings = Settings(db_path=str(tmp_path / "d.db"))
    config = tmp_path / "automation.json"
    config.write_text(json.dumps({"sitemap": "", "urls": []}))
    assert "Nothing to audit" in cli.run_daily(str(config), settings)["error"]
    assert "No automation config" in cli.run_daily(str(tmp_path / "none.json"), settings)["error"]

    config.write_text(json.dumps({"sitemap": "https://shop.com/index.xml",
                                  "urls": ["https://shop.com/extra"]}))
    out = cli.run_daily(str(config), settings, str(tmp_path / "reports"))
    assert out["ok"] and out["pages_audited"] == 3 and out["pages_failed"] == 1
    assert out["sheet"] is None and out["database"].startswith("Local file")
    lines = open(out["csv"], encoding="utf-8").read().splitlines()
    assert lines[0].startswith("audited_at,url") and len(lines) == 4

    monkeypatch.setenv("SEO_DB_PATH", str(tmp_path / "cli.db"))
    assert cli.main(["gsc", "--source", "demo"]) == 0
    assert json.loads(capsys.readouterr().out)["source"] == "demo"
    assert cli.main(["experiment", "shop.com/desks", "--position", "6.5"]) == 0
    assert json.loads(capsys.readouterr().out)["experiment_id"] == 1
    assert cli.main(["daily", "--config", str(tmp_path / "none.json")]) == 1


def test_mcp_server_exposes_seven_tools(tmp_path, monkeypatch):
    import mcp_server
    names = ["inspect_url_seo_and_geo", "spider_xml_sitemap", "discover_keywords_and_trends",
             "reverse_engineer_serp_competitors", "check_brand_ai_citation_visibility",
             "get_gsc_striking_distance_opportunities", "log_seo_optimization_experiment"]
    assert all(callable(getattr(mcp_server, name)) for name in names)
    patch_fetch(monkeypatch)
    monkeypatch.setenv("SEO_DB_PATH", str(tmp_path / "m.db"))
    report = mcp_server.inspect_url_seo_and_geo("https://shop.com/a", render_js="never")
    assert report["ok"] and "text" not in report["page"]["content"]
    assert report["page"]["content"]["markdown_truncated"] is False
    json.dumps(report)
    assert mcp_server.get_gsc_striking_distance_opportunities(source="demo")["ok"]


def test_status_shows_cloud_services():
    modes = {s["service"]: s["mode"] for s in service_status(Settings())}
    assert modes["Database"] == "Local file" and modes["Google Sheet sync"] == "Off"
    full = {s["service"]: s["mode"] for s in service_status(Settings(
        database_url="postgresql://x", google_service_account_json="{}",
        gsc_site_url="sc-domain:x.com", google_sheet_id="S"))}
    assert full["Database"] == "Cloud" and full["Live Search Console data"] == "Full"
