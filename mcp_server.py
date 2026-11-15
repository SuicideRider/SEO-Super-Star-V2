"""SEO Super Star agent tool server (Model Context Protocol, stdio).

Run:  python mcp_server.py
Needs: pip install -r requirements-mcp.txt

Exposes the same engine as the dashboard through seven tools. Every tool
returns a JSON-safe dict with "ok" and "error" keys and never raises. Keys
are read from environment variables; with none set, tools run in their
keyless fallback modes.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from seo_super_star import citations, competitors, keywords, service, sitemap

server = MCPServer(
    "seo-super-star",
    instructions="SEO, GEO and AEO analysis tools. All tools work without API keys in a "
                 "reduced mode; each result says which data source was used.")

_MARKDOWN_LIMIT = 6000


def _slim_audit(report: dict) -> dict:
    """Trim the bulky parts of an audit so it fits comfortably in an agent's context."""
    page = report.get("page")
    if page:
        content = dict(page["content"])
        content.pop("text", None)
        markdown = content.get("markdown", "")
        content["markdown"] = markdown[:_MARKDOWN_LIMIT]
        content["markdown_truncated"] = len(markdown) > _MARKDOWN_LIMIT
        report = {**report, "page": {**page, "content": content}}
    return report


@server.tool()
def inspect_url_seo_and_geo(url: str, target_keyword: str = "", schema_type: str = "Article",
                            run_pagespeed: bool = False, render_js: str = "auto") -> dict:
    """Audit one URL: status, redirects, title and meta pixel widths, canonical, robots,
    headings, links, images, existing JSON-LD, a 0-100 GEO/AEO readiness score with
    recommendations, generated grounded JSON-LD, and main content as Markdown.

    schema_type: Article, Product, LocalBusiness or Organization.
    render_js: "auto" (render only thin pages), "always" or "never".
    run_pagespeed: add Core Web Vitals and Lighthouse scores (20-60 seconds slower).
    """
    return _slim_audit(service.inspect_url_seo_and_geo(
        url, target_keyword=target_keyword, schema_type=schema_type,
        run_pagespeed=run_pagespeed, render_js=render_js))


@server.tool()
def spider_xml_sitemap(sitemap_url: str, max_urls: int = 25, target_keyword: str = "") -> dict:
    """Batch-audit the pages listed in an XML sitemap (or a site URL, whose sitemap is
    looked up in robots.txt). Returns one summary row per page, sorted worst first,
    plus site-wide averages and, under "site", cross-page issues: broken links,
    redirects, duplicate titles, orphan pages. max_urls is capped at 100."""
    return sitemap.spider_xml_sitemap(sitemap_url, max_urls=max_urls,
                                      target_keyword=target_keyword)


@server.tool()
def discover_keywords_and_trends(seed_keyword: str, country_code: str = "us",
                                 include_questions: bool = True) -> dict:
    """Expand a seed keyword through search autocomplete, classify each keyword's intent
    (Transactional, Commercial, Informational) and rank them by a 0-100 Opportunity
    Score. The score is a prioritisation heuristic, not search volume."""
    return keywords.discover_keywords_and_trends(
        seed_keyword, country_code=country_code, include_questions=include_questions)


@server.tool()
def reverse_engineer_serp_competitors(keyword: str, my_url: str = "", max_competitors: int = 5,
                                      competitor_urls: list[str] | None = None) -> dict:
    """Analyse the pages ranking for a keyword: word counts, heading trees, entities and
    subtopics they share, gaps versus my_url, and a content brief. Live search needs a
    Tavily key; without one, pass competitor_urls to choose the pages yourself."""
    return competitors.reverse_engineer_serp_competitors(
        keyword, my_url=my_url, max_competitors=max_competitors,
        competitor_urls=competitor_urls)


@server.tool()
def check_brand_ai_citation_visibility(brand_name: str, domain: str,
                                       buyer_prompts: list[str]) -> dict:
    """Ask up to 10 buyer prompts and report whether AI answers mention the brand or
    cite the domain, and which competitor domains are cited instead. Without an AI
    key it checks search results only and says so in mode_note."""
    return citations.check_brand_ai_citation_visibility(brand_name, domain, buyer_prompts)


@server.tool()
def get_gsc_striking_distance_opportunities(site_url: str = "", days: int = 28,
                                            min_impressions: int = 50, source: str = "auto",
                                            csv_data: str = "") -> dict:
    """Bucket search performance rows into Top Winner, CTR Underperformer, Striking
    Distance, Cluster Expansion and Monitor, each with a recommendation.

    source: "live" (Search Console API), "csv" (pass the export text in csv_data),
    "demo" (sample data), or "auto". Check the returned "source": "demo" means the
    rows are sample data, not the user's site."""
    return service.get_gsc_striking_distance_opportunities(
        site_url=site_url, days=days, min_impressions=min_impressions, source=source,
        csv_data=csv_data or None)


@server.tool()
def log_seo_optimization_experiment(url: str, target_keyword: str = "", hypothesis: str = "",
                                    changes_made: str = "",
                                    baseline_position: float | None = None,
                                    baseline_ctr: float | None = None) -> dict:
    """Record an optimisation (what changed and the baseline position and CTR percent)
    so its effect can be tracked later. Returns the new experiment id."""
    return service.log_seo_optimization_experiment(
        url, target_keyword, hypothesis, changes_made, baseline_position, baseline_ctr)


if __name__ == "__main__":
    server.run()
