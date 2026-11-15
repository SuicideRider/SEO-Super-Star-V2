"""Orchestration: the single entry point shared by the dashboard, tools and CLI."""

from __future__ import annotations

from datetime import datetime, timezone

from . import external
from .audit import analyze_html, collect_issues, keyword_usage
from .config import Settings
from .geo import score_geo
from .http import fetch_page, normalize_url
from .schema_gen import generate_jsonld
from . import google_api, search_performance
from .storage import open_storage

def log_seo_optimization_experiment(
    url: str,
    target_keyword: str = "",
    hypothesis: str = "",
    changes_made: str = "",
    baseline_position: float | None = None,
    baseline_ctr: float | None = None,
    settings: Settings | None = None,
) -> dict:
    """Record an optimisation so its effect can be tracked. Returns a JSON-safe dict."""
    settings = settings or Settings.from_env()
    url = normalize_url(url)
    if not url:
        return {"ok": False, "error": "A URL is required.", "experiment_id": None}
    try:
        experiment_id = open_storage(settings).log_experiment(
            url, target_keyword, hypothesis, changes_made, baseline_position, baseline_ctr)
    except (ValueError, TypeError) as exc:
        return {"ok": False, "error": f"Invalid value: {exc}", "experiment_id": None}
    return {"ok": True, "error": "", "experiment_id": experiment_id, "status": "Running"}


def get_gsc_striking_distance_opportunities(
    site_url: str = "",
    days: int = 28,
    min_impressions: int = search_performance.DEFAULT_MIN_IMPRESSIONS,
    source: str = "auto",
    csv_data: str | bytes | None = None,
    settings: Settings | None = None,
) -> dict:
    """Bucket search performance rows into opportunities. Returns a JSON-safe dict.

    source: "live" (Search Console API), "csv" (csv_data), "demo", or "auto",
    which uses csv_data when given, then live data when configured, then demo.
    """
    settings = settings or Settings.from_env()
    note = ""
    if source == "auto":
        if csv_data:
            source = "csv"
        elif settings.google_service_account_json and (site_url or settings.gsc_site_url):
            source = "live"
        else:
            source, note = "demo", ("No live connection or CSV was supplied, so this is "
                                    "built-in sample data, not your site.")
    if source == "csv":
        parsed = search_performance.parse_csv(csv_data or "")
        if parsed["error"]:
            return {"ok": False, "error": parsed["error"], "source": "csv", "rows": []}
        rows = parsed["rows"]
    elif source == "live":
        try:
            rows = google_api.fetch_search_console_rows(settings, site_url, days)
        except google_api.GoogleError as exc:
            return {"ok": False, "error": str(exc), "source": "live", "rows": []}
        if not rows:
            return {"ok": False, "source": "live", "rows": [],
                    "error": "Search Console returned no rows for that property and period."}
    elif source == "demo":
        rows = search_performance.demo_rows()
    else:
        return {"ok": False, "error": "source must be auto, live, csv or demo.",
                "source": source, "rows": []}
    mined = search_performance.mine_opportunities(rows, int(min_impressions))
    return {"ok": True, "error": "", "source": source, "note": note, **mined}


# Below this many words in the static HTML, the page is probably rendered
# client-side, so "auto" mode asks the renderer for the real DOM.
THIN_STATIC_WORDS = 150


def inspect_url_seo_and_geo(
    url: str,
    target_keyword: str = "",
    schema_type: str = "Article",
    run_pagespeed: bool = False,
    render_js: str = "auto",
    settings: Settings | None = None,
    save: bool = True,
    save_full_report: bool = True,
) -> dict:
    """Full technical SEO + GEO audit of one URL. Returns a JSON-safe dict.

    save_full_report=False stores only the summary row (used by batch runs).

    render_js: "auto" renders only thin pages, "always" forces it, "never" skips it.
    """
    settings = settings or Settings.from_env()
    url = normalize_url(url)
    fetched = fetch_page(url, timeout=settings.timeout,
                         allow_private=settings.allow_private_hosts)
    report: dict = {
        "url": url,
        "audited_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "target_keyword": target_keyword.strip(),
        "ok": False,
        "error": "",
        "fetch": fetched.summary(),
        "page": None,
        "content_source": None,
        "render_note": "",
        "keyword": None,
        "issues": [],
        "geo": None,
        "generated_schema": None,
        "pagespeed": None,
        "saved_id": None,
    }
    if not fetched.ok:
        report["error"] = fetched.error
        return report
    if not fetched.is_html:
        report["error"] = (f"The URL returned {fetched.content_type or 'non-HTML content'}, "
                           "not an HTML page.")
        return report

    final_url = fetched.final_url
    page = analyze_html(fetched.body, final_url, fetched.x_robots_tag)
    report["content_source"] = "static HTML"

    thin = page["content"]["word_count"] < THIN_STATIC_WORDS
    if render_js == "always" or (render_js == "auto" and thin):
        rendered = external.fetch_rendered_html(final_url, settings.jina_api_key)
        if rendered["ok"]:
            rendered_page = analyze_html(rendered["html"], final_url, fetched.x_robots_tag)
            if rendered_page["content"]["word_count"] >= page["content"]["word_count"]:
                page = rendered_page
                report["content_source"] = "JavaScript-rendered DOM"
            else:
                report["render_note"] = "Rendered DOM had less content; static HTML kept."
        else:
            report["render_note"] = f"{rendered['error']} Static HTML used instead."

    report["page"] = page
    report["keyword"] = keyword_usage(page, final_url, target_keyword)
    report["issues"] = collect_issues(page)
    if fetched.status_code and fetched.status_code >= 400:
        report["issues"].insert(0, {"severity": "High",
                                    "issue": f"Page returns HTTP {fetched.status_code}."})
    if fetched.redirect_chain:
        report["issues"].append({"severity": "Low",
                                 "issue": f"URL redirects {len(fetched.redirect_chain)} time(s) "
                                          f"before reaching {final_url}."})
    report["geo"] = score_geo(page)
    report["generated_schema"] = generate_jsonld(page, final_url, schema_type)
    if run_pagespeed:
        report["pagespeed"] = external.run_pagespeed(final_url, settings.pagespeed_api_key)
    report["ok"] = True

    if save:
        try:
            report["saved_id"] = open_storage(settings).save_audit(report, save_full_report)
        except Exception as exc:  # storage must never break an audit
            report["render_note"] = (report["render_note"] + f" History not saved: {exc}").strip()
    return report
