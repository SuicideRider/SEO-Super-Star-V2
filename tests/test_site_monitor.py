"""Offline tests for site-wide checks, change tracking and the daily monitor."""

import json

from seo_super_star import __main__ as cli
from seo_super_star import changes, service, site_checks, sitemap, storage
from seo_super_star.config import Settings
from seo_super_star.http import FetchResult


def html(title, links=(), extra_head=""):
    anchors = "".join(f'<a href="{link}">link</a>' for link in links)
    return (f"<html><head><title>{title}</title>{extra_head}</head><body><main><h1>{title}</h1>"
            f"<p>Some text about desks.</p>{anchors}</main></body></html>").encode()


SITEMAP = b"""<urlset><url><loc>https://shop.com/</loc></url><url><loc>https://shop.com/a</loc></url>
<url><loc>https://shop.com/b</loc></url><url><loc>https://shop.com/lonely</loc></url>
<url><loc>https://shop.com/old</loc></url></urlset>"""
SITE = {
    "https://shop.com/": html("Home of standing desks", ["/a", "/b", "/old", "/gone", "/extra",
                                                         "/moved", "https://other.com/x"]),
    "https://shop.com/a": html("Standing desk guide", ["/", "/b#top"]),
    "https://shop.com/b": html("Standing desk guide", ["/"],
                               '<meta name="robots" content="noindex">'),
    "https://shop.com/lonely": html("Nobody links here",
                                    extra_head='<link rel="canonical" href="https://shop.com/a">'),
    "https://shop.com/new": html("Old page moved here", ["/"]),
    "https://shop.com/extra": html("Not in the sitemap"),
}
STATE = {"broken": set()}


def fake_fetch(url, timeout=15, allow_private=False, session=None, read_body=True):
    def reply(body=b"", kind="text/html", status=200, final=url, chain=()):
        return FetchResult(requested_url=url, final_url=final, status_code=status,
                           content_type=kind, body=body, redirect_chain=list(chain))
    if url.endswith("/sitemap.xml"):
        return reply(SITEMAP, "application/xml")
    if url in STATE["broken"] or url.endswith("/gone"):
        return reply(status=404)
    if url.endswith(("/old", "/moved")):
        return reply(SITE["https://shop.com/new"], final="https://shop.com/new",
                     chain=[{"url": url, "status_code": 301}])
    if url in SITE:
        return reply(SITE[url])
    return FetchResult(requested_url=url, error="Timed out.")


def patch(monkeypatch):
    STATE["broken"] = set()
    for module in (sitemap, service, site_checks):
        monkeypatch.setattr(module, "fetch_page", fake_fetch)


def found(site, check):
    return [i["url"] for i in site["issues"] if i["check"] == check]


def test_site_wide_checks(tmp_path, monkeypatch):
    patch(monkeypatch)
    settings = Settings(db_path=str(tmp_path / "s.db"))
    result = sitemap.spider_xml_sitemap("https://shop.com/sitemap.xml", settings=settings)
    site = result["site"]
    assert site["complete"] and site["pages_compared"] == 5
    assert found(site, "Broken internal link") == ["https://shop.com/gone"]
    assert "linked from 1 page(s): https://shop.com/" in site["issues"][0]["detail"]
    assert found(site, "Sitemap URL redirects") == ["https://shop.com/old"]
    assert found(site, "Internal link redirects") == ["https://shop.com/moved"]
    assert found(site, "Noindex page in sitemap") == ["https://shop.com/b"]
    assert found(site, "Canonical points elsewhere") == ["https://shop.com/lonely"]
    assert found(site, "Duplicate title") == ["https://shop.com/a", "https://shop.com/b"]
    assert found(site, "Orphan page") == ["https://shop.com/lonely"]
    assert found(site, "Linked but not in sitemap") == ["https://shop.com/extra"]
    assert site["high"] == 1 and site["links_checked"] == 3     # external link not fetched
    json.dumps(result)

    partial = sitemap.spider_xml_sitemap("https://shop.com/sitemap.xml", max_urls=2,
                                         settings=settings)["site"]
    assert not partial["complete"] and not found(partial, "Orphan page")
    assert "orphan" in partial["note"]


def test_url_key_ignores_cosmetic_differences():
    assert site_checks.url_key("http://www.Shop.com/a/#x") == site_checks.url_key("https://shop.com/a")
    assert site_checks.url_key("https://shop.com/a?p=1") != site_checks.url_key("https://shop.com/a")
    assert site_checks.url_key("https://shop.com/z%e1%90%a9/") == site_checks.url_key("https://shop.com/zᐩ")


def row(url, **over):
    base = {"url": url, "status_code": 200, "title_status": "Optimal",
            "description_status": "Optimal", "h1_count": 1, "schema_types": "Article",
            "word_count": 800, "issue_count": 2, "geo_score": 60}
    return {**base, **over}


def test_compare_runs_finds_regressions_and_improvements():
    before = {"u1": row("u1"), "u2": row("u2", status_code=404), "u3": row("u3"),
              "u4": row("u4", title_status="Too Long", schema_types="")}
    now = [row("u1", status_code=500), row("u2"),
           row("u3", title_status="Missing", h1_count=0, schema_types="", word_count=300,
               issue_count=5, geo_score=40),
           row("u4", issue_count=1, schema_types="FAQPage"), row("u6")]
    old_run = {"urls": ["u1", "u2", "u3", "u4", "u5"],
               "site_issues": [{"check": "Broken internal link", "severity": "High",
                                "url": "x", "detail": "HTTP 404"}]}
    site_now = [{"check": "Duplicate title", "severity": "Medium", "url": "u3", "detail": "d"}]
    diff = changes.compare_runs(before, now, [{"url": "u5", "error": "Timed out."}],
                                old_run, site_now)
    seen = {(c["url"], c["change"]) for c in diff["changes"]}
    assert {("u1", "Page now returns an error"), ("u2", "Page works again"),
            ("u3", "Title got worse"), ("u3", "H1 heading disappeared"),
            ("u3", "Structured data disappeared"), ("u3", "Content shrank sharply"),
            ("u3", "More issues than before"), ("u3", "GEO score dropped"),
            ("u4", "Title fixed"), ("u4", "Structured data added"),
            ("u4", "Fewer issues than before"), ("u3", "New: Duplicate title"),
            ("x", "Resolved: Broken internal link"), ("u6", "New page in the sitemap")} <= seen
    assert ("u5", "Page stopped loading") not in seen       # u5 had no saved audit
    assert diff["summary"]["serious"] == 1 and diff["changes"][0]["severity"] == "High"
    assert diff["has_baseline"] and diff["site_issues_compared"]

    first = changes.compare_runs({}, now, [], None, site_now)
    assert not first["has_baseline"] and first["changes"] == [] and first["note"]
    # With page history but no stored run, site issues are not reported as "new".
    assert not changes.compare_runs(before, now, [], None, site_now)["site_issues_compared"]


def test_site_run_storage(tmp_path):
    store = storage.Storage(str(tmp_path / "s.db"))
    assert store.latest_site_run() is None
    for n in range(storage.KEEP_SITE_RUNS + 3):
        store.save_site_run("s", n, 0, {"urls": [str(n)]})
    assert store.latest_site_run("s")["data"] == {"urls": [str(storage.KEEP_SITE_RUNS + 2)]}
    assert store.latest_site_run("other") is None
    assert len(store._run("SELECT id FROM site_runs", fetch=True)) == storage.KEEP_SITE_RUNS


def test_daily_job_tracks_changes_and_alerts(tmp_path, monkeypatch):
    patch(monkeypatch)
    settings = Settings(db_path=str(tmp_path / "s.db"))
    config = tmp_path / "automation.json"
    config.write_text(json.dumps({"sitemap": "https://shop.com/sitemap.xml", "max_urls": 50,
                                  "fail_on_regression": True}))
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    out = str(tmp_path / "reports")

    first = cli.run_daily(str(config), settings, out)
    assert first["ok"] and first["pages_audited"] == 5
    assert first["changes"]["note"].startswith("First run")
    assert first["site_issues"]["Broken internal link"] == 1
    assert (tmp_path / "reports").joinpath(f"site_issues_{first['date']}.csv").exists()

    second = cli.run_daily(str(config), settings, out)
    assert second["ok"] and second["changes"]["regressions"] == 0     # nothing changed

    STATE["broken"] = {"https://shop.com/a"}
    third = cli.run_daily(str(config), settings, out)
    assert not third["ok"] and "serious regression" in third["error"]
    assert third["changes"]["serious_examples"][0]["url"] == "https://shop.com/a"
    assert "What got worse" in summary.read_text(encoding="utf-8")
    run = storage.open_storage(settings).latest_site_run()
    assert run["serious_changes"] >= 1 and run["data"]["changes"]

    STATE["broken"] = set()
    fourth = cli.run_daily(str(config), settings, out)
    assert fourth["ok"] and fourth["changes"]["improvements"] >= 1
