"""Offline smoke tests for phase 1: auditing, GEO scoring, schema, storage."""

import json

from seo_super_star import service
from seo_super_star.audit import analyze_html, collect_issues, keyword_usage, pixel_width
from seo_super_star.config import Settings, service_status
from seo_super_star.geo import is_question, score_geo
from seo_super_star.http import FetchResult, check_public_url, normalize_url
from seo_super_star.schema_gen import generate_jsonld
from seo_super_star.storage import Storage

URL = "https://example.com/guides/standing-desks"
ANSWER = ("A standing desk is a height-adjustable work surface that lets you alternate "
          "between sitting and standing during the day. Most models adjust between 70 and "
          "120 centimetres using an electric motor, and research from 2021 links regular "
          "posture changes to a 32% drop in reported back discomfort among office workers.")

GOOD_HTML = f"""<!doctype html><html lang="en"><head>
<title>Standing Desks: The Complete Buyer's Guide for 2026</title>
<meta name="description" content="Compare standing desk types, prices and health evidence, with clear answers to the questions buyers ask before choosing a desk.">
<link rel="canonical" href="{URL}">
<meta property="og:site_name" content="Example Office">
<meta property="og:image" content="/img/desk.jpg">
<meta name="author" content="Asha Rao">
<meta property="article:published_time" content="2026-01-10T08:00:00Z">
<script type="application/ld+json">{{"@context":"https://schema.org","@graph":[
 {{"@type":"Article","headline":"x","author":"a","image":"i","datePublished":"d","publisher":"p","url":"u"}},
 {{"@type":"BreadcrumbList","itemListElement":[]}}]}}</script>
</head><body>
<header><nav><a href="/">Home</a></nav></header>
<main>
<h1>Standing Desks: The Complete Buyer's Guide</h1>
<p>Intro text with a link to <a href="https://www.nih.gov/study">a study</a> and
<a href="https://other.example.org/x" rel="nofollow">another source</a>.</p>
<h2>What is a standing desk?</h2><p>{ANSWER}</p>
<h2>How much does a standing desk cost?</h2><p>Short answer.</p>
<h2>Types</h2><ul><li>Electric</li><li>Manual</li></ul><ol><li>One</li></ol>
<table><tr><td>Model</td><td>Price</td></tr></table>
<img src="/a.png"><img src="/b.png" alt="A desk"><a href="/empty"></a>
<a href="tel:+15550100">Call</a>
</main><footer><a href="/privacy">Privacy</a></footer></body></html>"""

BARE_HTML = "<html><head></head><body><h1>A</h1><h1>B</h1><h4>C</h4><p>Hi.</p></body></html>"


def test_audit_extracts_page_facts():
    page = analyze_html(GOOD_HTML, URL)
    assert page["title"]["status"] == "Optimal"
    assert page["description"]["status"] == "Optimal"
    assert page["canonical"]["self_referencing"] is True
    assert page["headings"]["h1_count"] == 1
    assert page["headings"]["h2_count"] == 3
    assert page["links"]["external"] == 2
    assert page["links"]["nofollow"] == 1
    assert page["links"]["empty_anchor_count"] == 1
    assert page["links"]["phones"] == ["+15550100"]
    assert page["images"]["total"] == 2
    assert page["images"]["missing_alt_src"] == ["https://example.com/a.png"]
    assert page["jsonld"]["types"] == ["Article", "BreadcrumbList"]
    assert page["content"]["list_count"] == 2
    assert page["content"]["table_count"] == 1
    assert "## What is a standing desk?" in page["content"]["markdown"]
    assert "Privacy" not in page["content"]["markdown"]


def test_audit_flags_problems():
    page = analyze_html(BARE_HTML, URL)
    assert page["title"]["status"] == "Missing"
    assert page["description"]["status"] == "Missing"
    assert page["headings"]["h1_count"] == 2
    assert page["headings"]["skipped_levels"]
    issues = [i["issue"] for i in collect_issues(page)]
    assert any("Title tag is missing" in i for i in issues)
    assert any("2 H1 headings" in i for i in issues)
    assert any("canonical" in i for i in issues)


def test_title_length_flags():
    short = analyze_html("<title>Desks</title>", URL)["title"]
    long = analyze_html("<title>" + "Wide standing desk words " * 6 + "</title>", URL)["title"]
    assert short["status"] == "Too Short"
    assert long["status"] == "Truncated"
    assert pixel_width("WWW", 20) > pixel_width("iii", 20)


def test_keyword_usage():
    page = analyze_html(GOOD_HTML, URL)
    usage = keyword_usage(page, URL, "Standing Desk")
    assert usage["in_h1"] and usage["in_subheadings"] and usage["in_url"]
    assert usage["occurrences"] >= 3
    assert keyword_usage(page, URL, "") is None


def test_geo_score():
    good = score_geo(analyze_html(GOOD_HTML, URL))
    bare = score_geo(analyze_html(BARE_HTML, URL))
    assert [p["max"] for p in good["pillars"]] == [25, 25, 25, 25]
    assert 0 <= bare["score"] < good["score"] <= 100
    assert good["pillars"][0]["score"] == 5       # one ideal answer block
    assert good["pillars"][2]["score"] == 25      # fully scannable
    assert good["pillars"][3]["score"] == 20      # Article + Breadcrumb + depth
    assert is_question("How do I choose") and not is_question("Types")


def test_schema_is_grounded():
    page = analyze_html(GOOD_HTML, URL)
    article = generate_jsonld(page, URL, "Article")
    assert article["primary"]["author"] == {"@type": "Person", "name": "Asha Rao"}
    assert article["primary"]["image"] == "https://example.com/img/desk.jpg"
    assert "dateModified" in article["omitted"]
    assert len(article["faq"]["mainEntity"]) == 2
    product = generate_jsonld(page, URL, "Product")
    dumped = json.dumps(product["primary"])
    assert "aggregateRating" not in dumped and "offers" not in dumped
    business = generate_jsonld(page, URL, "LocalBusiness")["primary"]
    assert business["telephone"] == "+15550100" and "address" not in business
    assert generate_jsonld(analyze_html(BARE_HTML, URL), URL)["faq"] is None


def test_url_guard():
    assert normalize_url("example.com") == "https://example.com"
    assert check_public_url("http://127.0.0.1/") != ""
    assert check_public_url("ftp://example.com/") != ""


def test_inspect_saves_history(tmp_path, monkeypatch):
    def fake_fetch(url, timeout=15, allow_private=False, session=None):
        return FetchResult(requested_url=url, final_url=URL, status_code=200,
                           response_ms=123, total_ms=150,
                           content_type="text/html; charset=utf-8",
                           body=GOOD_HTML.encode("utf-8"))

    monkeypatch.setattr(service, "fetch_page", fake_fetch)
    settings = Settings(db_path=str(tmp_path / "test.db"))
    report = service.inspect_url_seo_and_geo(URL, target_keyword="standing desk",
                                             settings=settings)
    assert report["ok"] and report["saved_id"] == 1
    assert report["content_source"] == "static HTML"
    json.dumps(report)  # must be JSON-serialisable for the agent tools

    store = Storage(settings.db_path)
    rows = store.list_audits()
    assert rows[0]["geo_score"] == report["geo"]["score"]
    assert rows[0]["schema_types"] == "Article, BreadcrumbList"
    assert store.get_audit(1)["url"] == URL


def test_inspect_reports_fetch_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "fetch_page",
                        lambda url, **kw: FetchResult(requested_url=url, error="Timed out."))
    report = service.inspect_url_seo_and_geo(URL, settings=Settings(db_path=str(tmp_path / "t.db")))
    assert report["ok"] is False and report["error"] == "Timed out."


def test_status_runs_without_keys():
    modes = {s["service"]: s["mode"] for s in service_status(Settings())}
    assert modes["Core Web Vitals (PageSpeed)"] == "Keyless"
