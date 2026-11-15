"""Orchestration: the single entry point shared by the dashboard, tools and CLI."""

from __future__ import annotations

from datetime import datetime, timezone

from . import external
from .audit import analyze_html, collect_issues, keyword_usage
from .config import Settings
from .geo import score_geo
from .http import fetch_page, normalize_url
from .schema_gen import generate_jsonld
from .storage import Storage

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
) -> dict:
    """Full technical SEO + GEO audit of one URL. Returns a JSON-safe dict.

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
            report["saved_id"] = Storage(settings.db_path).save_audit(report)
        except Exception as exc:  # storage must never break an audit
            report["render_note"] = (report["render_note"] + f" History not saved: {exc}").strip()
    return report
