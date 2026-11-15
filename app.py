"""SEO Super Star dashboard. All logic lives in the seo_super_star package."""

from __future__ import annotations

import json
import os

import pandas as pd
import streamlit as st

from seo_super_star.config import Settings, service_status
from seo_super_star import search_performance
from seo_super_star.brief import brief_to_markdown
from seo_super_star.citations import check_brand_ai_citation_visibility
from seo_super_star.competitors import reverse_engineer_serp_competitors
from seo_super_star.config import ENV_KEYS
from seo_super_star.keywords import discover_keywords_and_trends
from seo_super_star.schema_gen import SCHEMA_TYPES
from seo_super_star.service import inspect_url_seo_and_geo, log_seo_optimization_experiment
from seo_super_star.storage import EXPERIMENT_STATUSES, open_storage, summary_row

st.set_page_config(page_title="SEO Super Star", page_icon="⭐", layout="wide")

# Hosted secrets become environment variables so the engine reads one source.
try:
    for _name in ENV_KEYS.values():
        if _name in st.secrets and not os.environ.get(_name):
            os.environ[_name] = str(st.secrets[_name])
except Exception:
    pass  # no secrets file configured


def sidebar_settings() -> Settings:
    with st.sidebar:
        st.header("Optional keys")
        st.caption("Everything works without keys. Keys entered here last for "
                   "this browser session only and are not saved.")
        labels = {"tavily_api_key": "Tavily key (live search, AI answers)",
                  "gemini_api_key": "Gemini key (AI writing, AI answers)",
                  "groq_api_key": "Groq key (AI writing fallback)",
                  "openrouter_api_key": "OpenRouter key (AI writing fallback)",
                  "pagespeed_api_key": "PageSpeed Insights key",
                  "jina_api_key": "Reader key (JS rendering)"}
        settings = Settings.from_env({name: st.text_input(label, type="password")
                                      for name, label in labels.items()})
        st.header("System & quota status")
        for item in service_status(settings):
            st.markdown(f"**{item['service']}**: {item['mode']}")
            st.caption(item["detail"])
    return settings


def audit_form(key: str, settings: Settings) -> None:
    """URL form shared by the audit and GEO tabs; stores the report in session."""
    with st.form(f"audit_form_{key}"):
        url = st.text_input("Page URL", placeholder="https://example.com/page")
        left, middle, right = st.columns(3)
        keyword = left.text_input("Target keyword (optional)")
        schema_type = middle.selectbox("Schema type to generate", SCHEMA_TYPES)
        render_label = right.selectbox(
            "JavaScript rendering",
            ["Auto (only thin pages)", "Always", "Never"])
        pagespeed = st.checkbox("Run Core Web Vitals and speed test (adds 20-60 seconds)")
        submitted = st.form_submit_button("Run audit", type="primary")
    if submitted:
        if not url.strip():
            st.warning("Enter a URL to audit.")
            return
        with st.spinner("Auditing page..."):
            st.session_state["report"] = inspect_url_seo_and_geo(
                url, target_keyword=keyword, schema_type=schema_type,
                run_pagespeed=pagespeed,
                render_js=render_label.split(" ")[0].lower(),
                settings=settings)


def current_report() -> dict | None:
    report = st.session_state.get("report")
    if not report:
        st.info("Run an audit to see results here.")
        return None
    if not report["ok"]:
        st.error(f"Could not audit {report['url']}: {report['error']}")
        return None
    return report


def show_length_check(label: str, check: dict) -> None:
    st.markdown(f"**{label}**: {check['status']} "
                f"({check['chars']} characters, about {check['pixels']} of "
                f"{check['max_pixels']} px)")
    if check["text"]:
        st.caption(check["text"])


def fmt(value, suffix: str = "") -> str:
    return "n/a" if value is None else f"{value}{suffix}"


def show_pagespeed(speed: dict) -> None:
    st.subheader("Core Web Vitals and speed")
    for strategy in ("mobile", "desktop"):
        result = speed[strategy]
        st.markdown(f"**{strategy.title()}**")
        if not result["ok"]:
            st.warning(result["error"])
            continue
        cols = st.columns(5)
        cols[0].metric("Performance", fmt(result["performance"]))
        cols[1].metric("Lighthouse SEO", fmt(result["seo"]))
        cols[2].metric("LCP", fmt(result["lcp_ms"], " ms"))
        cols[3].metric("CLS", fmt(result["cls"]))
        cols[4].metric("INP (real users)", fmt(result["inp_ms"], " ms"))
        if result["inp_ms"] is None:
            st.caption("INP comes from real-user data, which this page does not have yet.")


def tab_technical(settings: Settings) -> None:
    st.subheader("Technical SEO audit")
    audit_form("technical", settings)
    report = current_report()
    if not report:
        return
    page, fetch = report["page"], report["fetch"]

    cols = st.columns(5)
    cols[0].metric("HTTP status", fetch["status_code"])
    cols[1].metric("Response time", f"{fetch['response_ms']} ms")
    cols[2].metric("Words", page["content"]["word_count"])
    cols[3].metric("Issues", len(report["issues"]))
    cols[4].metric("GEO (of 100)", report["geo"]["score"])
    st.caption(f"Final URL: {fetch['final_url']} | Content source: {report['content_source']}"
               + (f" | {report['render_note']}" if report["render_note"] else ""))
    if fetch["redirect_chain"]:
        st.caption("Redirects: " + " → ".join(
            f"{hop['url']} ({hop['status_code']})" for hop in fetch["redirect_chain"]))

    st.subheader("Issues")
    if report["issues"]:
        st.dataframe(pd.DataFrame(report["issues"]), hide_index=True)
    else:
        st.success("No issues found.")

    st.subheader("Metadata and directives")
    show_length_check("Title tag", page["title"])
    show_length_check("Meta description", page["description"])
    canonical, robots = page["canonical"], page["robots"]
    st.markdown("**Canonical**: " + (canonical["url"] if canonical["present"] else "not declared")
                + (" (self-referencing)" if canonical["self_referencing"] else ""))
    st.markdown(f"**Robots**: {robots['meta'] or 'no meta robots tag'}"
                + (f" | X-Robots-Tag: {robots['x_robots_tag']}" if robots["x_robots_tag"] else "")
                + (" | **noindex**" if robots["noindex"] else ""))

    if report["keyword"]:
        st.subheader(f"Target keyword: {report['keyword']['keyword']}")
        usage = report["keyword"]
        checks = {"Title": "in_title", "Meta description": "in_description", "H1": "in_h1",
                  "H2/H3": "in_subheadings", "First 100 words": "in_first_100_words",
                  "URL": "in_url"}
        st.dataframe(pd.DataFrame(
            [{"Placement": name, "Present": "Yes" if usage[field] else "No"}
             for name, field in checks.items()]), hide_index=True)
        st.caption(f"{usage['occurrences']} occurrence(s) in the body, "
                   f"{usage['density_percent']}% density.")

    left, right = st.columns(2)
    with left:
        st.subheader("Heading hierarchy")
        if page["headings"]["items"]:
            st.dataframe(pd.DataFrame(
                [{"Level": f"H{h['level']}", "Heading": h["text"]}
                 for h in page["headings"]["items"]]), hide_index=True, height=300)
        else:
            st.write("No headings found.")
    with right:
        links, images = page["links"], page["images"]
        st.subheader("Links and images")
        cols = st.columns(4)
        cols[0].metric("Internal links", links["internal"])
        cols[1].metric("External links", links["external"])
        cols[2].metric("Nofollow", links["nofollow"])
        cols[3].metric("Empty anchors", links["empty_anchor_count"])
        cols = st.columns(4)
        cols[0].metric("Images", images["total"])
        cols[1].metric("Missing alt", images["missing_alt_count"])
        if images["missing_alt_src"]:
            with st.expander("Images missing an alt attribute"):
                st.dataframe(pd.DataFrame({"src": images["missing_alt_src"]}), hide_index=True)

    st.subheader("Structured data")
    if page["jsonld"]["entities"]:
        st.dataframe(pd.DataFrame(
            [{"@type": ", ".join(e["types"]), "Properties": e["property_count"]}
             for e in page["jsonld"]["entities"]]), hide_index=True)
    else:
        st.write("No JSON-LD found.")

    if report["pagespeed"]:
        show_pagespeed(report["pagespeed"])

    with st.expander("Main content as Markdown (LLM-ready)"):
        st.download_button("Download Markdown", page["content"]["markdown"],
                           file_name="content.md", mime="text/markdown")
        st.code(page["content"]["markdown"][:20000], language="markdown")

    st.download_button(
        "Download audit summary (CSV)",
        pd.DataFrame([summary_row(report)]).to_csv(index=False),
        file_name="audit.csv", mime="text/csv")


def tab_geo(settings: Settings) -> None:
    st.subheader("GEO / AEO readiness and grounded JSON-LD")
    audit_form("geo", settings)
    report = current_report()
    if not report:
        return
    geo = report["geo"]
    st.metric("GEO / AEO readiness", f"{geo['score']}/100", geo["grade"],
              delta_color="off")
    for pillar in geo["pillars"]:
        st.markdown(f"**{pillar['name']}: {pillar['score']}/{pillar['max']}**")
        st.progress(pillar["score"] / pillar["max"])
        for finding in pillar["findings"]:
            st.caption(finding)
        for tip in pillar["recommendations"]:
            st.markdown(f"- {tip}")

    schema = report["generated_schema"]
    st.subheader(f"Generated JSON-LD: {schema['schema_type']}")
    st.caption(schema["note"])
    st.code(json.dumps(schema["primary"], indent=2, ensure_ascii=False), language="json")
    if schema["omitted"]:
        st.caption("Left out because the page does not state them: "
                   + "; ".join(schema["omitted"]) + ".")
    if schema["faq"]:
        st.subheader("Generated JSON-LD: FAQPage")
        st.code(json.dumps(schema["faq"], indent=2, ensure_ascii=False), language="json")
    else:
        st.caption("No FAQPage generated: the page has no question headings with answers.")


def tab_keywords(settings: Settings) -> None:
    st.subheader("Keyword discovery and opportunity scoring")
    with st.form("keyword_form"):
        seed = st.text_input("Seed keyword", placeholder="standing desk")
        left, right = st.columns(2)
        country = left.text_input("Country code", value="us", max_chars=2)
        language = right.text_input("Language code", value="en", max_chars=5)
        questions = st.checkbox("Add question and commercial modifiers "
                                "(how to, what is, best, vs, price of...)", value=True)
        alphabet = st.checkbox("Add a-z suffixes (26 more lookups)")
        trends = st.checkbox("Look up trend momentum for the top keywords", value=True)
        submitted = st.form_submit_button("Find keywords", type="primary")
    if submitted:
        with st.spinner("Collecting suggestions..."):
            st.session_state["keywords"] = discover_keywords_and_trends(
                seed, country_code=country, include_questions=questions,
                include_alphabet=alphabet, language=language,
                include_trends=trends, settings=settings)
    result = st.session_state.get("keywords")
    if not result:
        st.info("Enter a seed keyword to discover related searches.")
        return
    if not result["ok"]:
        st.error(result["error"])
        return

    frame = pd.DataFrame(result["keywords"])[
        ["keyword", "intent", "opportunity_score", "trend_interest",
         "trend_direction", "source_count", "sources"]]
    cols = st.columns(4)
    cols[0].metric("Keywords", len(frame))
    for col, intent in zip(cols[1:], ("Transactional", "Commercial", "Informational")):
        col.metric(intent, int((frame["intent"] == intent).sum()))
    st.caption(f"Source: {', '.join(result['providers'])} | "
               f"{result['queries_run']} lookups | {result['trend_status']}")
    chosen = st.multiselect("Show intents", ["Transactional", "Commercial", "Informational"],
                            default=["Transactional", "Commercial", "Informational"])
    shown = frame[frame["intent"].isin(chosen)]
    st.dataframe(shown, hide_index=True)
    st.download_button("Download keywords (CSV)", shown.to_csv(index=False),
                       file_name=f"keywords_{result['seed_keyword'].replace(' ', '_')}.csv",
                       mime="text/csv")
    st.caption("The Opportunity Score ranks keywords by intent, how prominently "
               "autocomplete suggests them, how specific they are, and trend "
               "momentum. It is a prioritisation guide, not search volume. "
               "Trend interest is the last four weeks as a percentage of the "
               "keyword's own 12-month peak; it is looked up for the seed and "
               "the top-ranked keywords only.")


def tab_serp(settings: Settings) -> None:
    st.subheader("Competitor gap and content brief")
    with st.form("serp_form"):
        keyword = st.text_input("Target keyword", placeholder="best standing desk")
        left, middle, right = st.columns([3, 1, 1])
        my_url = left.text_input("Your page URL (optional, for the gap check)")
        country = middle.text_input("Country code", value="us", max_chars=2)
        count = right.number_input("Competitors", min_value=2, max_value=5, value=5)
        manual = st.text_area(
            "Competitor URLs (optional, one per line)", height=90,
            help="Leave empty to use live search results. Paste URLs to choose "
                 "the competitors yourself or when live search is unavailable.")
        submitted = st.form_submit_button("Analyse competitors", type="primary")
    if submitted:
        with st.spinner("Reading the ranking pages..."):
            st.session_state["serp"] = reverse_engineer_serp_competitors(
                keyword, my_url=my_url, max_competitors=int(count), country_code=country,
                settings=settings, competitor_urls=manual.splitlines())
    result = st.session_state.get("serp")
    if not result:
        st.info("Enter a keyword to analyse the pages that rank for it.")
        return
    serp_info = result["serp"]
    if serp_info and serp_info["attempts"]:
        st.caption("Search providers tried: " + "; ".join(
            f"{a['provider']}: {a['outcome']}" for a in serp_info["attempts"]))
    if not result["ok"]:
        st.error(result["error"])
        return

    st.caption(f"Competitor source: {serp_info['provider']}")
    if serp_info["ai_overview"]:
        st.markdown("**AI answer overview for this query**")
        st.info(serp_info["ai_overview"])
    cols = st.columns(3)
    cols[0].metric("Competitor average", f"{result['avg_competitor_words']} words")
    cols[1].metric("Recommended length", f"{result['target_word_count']} words")
    mine = result["my_page"]
    if mine and mine["ok"]:
        cols[2].metric("Your page", f"{mine['word_count']} words")
    elif mine:
        st.warning(f"Your page could not be read: {mine['error']}")

    st.markdown("**Ranking pages**")
    st.dataframe(pd.DataFrame(
        [{"Rank": c["position"], "Title": c["title"], "Words": c["word_count"],
          "H2": c["h2_count"], "H3": c["h3_count"],
          "Schema": ", ".join(c["schema_types"]), "URL": c["url"],
          "Note": c["error"]} for c in result["competitors"]]), hide_index=True)
    for competitor in result["competitors"]:
        if competitor["ok"] and competitor["heading_tree"]:
            with st.expander(f"Heading tree: {competitor['title'] or competitor['url']}"):
                st.markdown("\n".join(
                    f"- {node['h2']}" + "".join(f"\n  - {h3}" for h3 in node["h3"])
                    for node in competitor["heading_tree"]))

    yes_no = {True: "Yes", False: "No", None: "n/a"}
    left, right = st.columns(2)
    with left:
        st.markdown("**Entities competitors mention**")
        st.dataframe(pd.DataFrame(
            [{"Entity": e["entity"], "Competitors": e["competitors_using"],
              "On your page": yes_no[e["in_my_page"]]} for e in result["entities"]]),
            hide_index=True, height=320)
    with right:
        st.markdown("**Subtopics competitors cover**")
        st.dataframe(pd.DataFrame(
            [{"Subtopic": s["heading"], "Competitors": s["competitors_using"],
              "On your page": yes_no[s["covered_by_my_page"]]} for s in result["subtopics"]]),
            hide_index=True, height=320)

    brief = result["brief"]
    st.subheader("Content brief")
    st.caption(f"Source: {brief['source']}. {brief['note']}")
    markdown = brief_to_markdown(result)
    st.download_button("Download brief (Markdown)", markdown,
                       file_name=f"brief_{result['keyword'].replace(' ', '_')}.md",
                       mime="text/markdown")
    st.markdown(markdown.split("\n", 1)[1])


def citation_monitor(settings: Settings) -> None:
    st.subheader("AI citation visibility monitor")
    with st.form("citation_form"):
        left, right = st.columns(2)
        brand = left.text_input("Brand name")
        domain = right.text_input("Domain", placeholder="example.com")
        prompts = st.text_area("Buyer prompts, one per line (up to 10)", height=110,
                               placeholder="best standing desk for tall people\n"
                                           "which standing desk brand is most reliable")
        submitted = st.form_submit_button("Check visibility", type="primary")
    if submitted:
        with st.spinner("Asking each prompt..."):
            st.session_state["citations"] = check_brand_ai_citation_visibility(
                brand, domain, prompts, settings=settings)
    result = st.session_state.get("citations")
    if not result:
        return
    if not result["ok"]:
        st.error(result["error"])
        return
    cols = st.columns(4)
    cols[0].metric("Visibility", f"{result['visibility_percent']}%")
    cols[1].metric("Prompts checked", result["prompts_checked"])
    cols[2].metric("Brand mentions", result["mentions"])
    cols[3].metric("Domain citations", result["citations"])
    if result["mode_note"]:
        st.warning(result["mode_note"])
    yes_no = {True: "Yes", False: "No", None: "n/a"}
    st.dataframe(pd.DataFrame(
        [{"Prompt": r["prompt"], "Source": r["mode"],
          "Brand mentioned": yes_no[r["brand_mentioned"]],
          "Domain cited": yes_no[r["domain_cited"]],
          "Top cited competitors": ", ".join(c["domain"] for c in r["competitor_urls"]),
          "Answer excerpt": r["answer_excerpt"]} for r in result["results"]]),
        hide_index=True)
    if result["top_competitors"]:
        st.markdown("**Most cited competitor domains**")
        st.dataframe(pd.DataFrame(result["top_competitors"]), hide_index=True)


def tab_search_performance() -> None:
    st.subheader("Search performance opportunity miner")
    source = st.radio("Data source", ["Demo dataset", "Upload CSV"], horizontal=True)
    rows: list[dict] = []
    if source == "Upload CSV":
        st.caption("Export the Queries or Pages table from your search console's "
                   "Performance report. Needed columns: query or page, impressions, "
                   "position; clicks and CTR are used when present.")
        upload = st.file_uploader("Search performance CSV", type=["csv", "tsv", "txt"])
        if upload is None:
            st.info("Drop a CSV here, or switch to the demo dataset.")
            return
        parsed = search_performance.parse_csv(upload.getvalue())
        if parsed["error"]:
            st.error(parsed["error"])
            return
        rows = parsed["rows"]
    else:
        rows = search_performance.demo_rows()
        st.caption("Showing built-in sample data for a fictional shop.")

    min_impressions = st.number_input(
        "Minimum impressions to act on", min_value=0, value=50, step=10,
        help="Rows below this are set aside as Monitor, except clear Top Winners.")
    mined = search_performance.mine_opportunities(rows, int(min_impressions))
    cols = st.columns(len(search_performance.BUCKETS))
    for col, bucket in zip(cols, search_performance.BUCKETS):
        col.metric(bucket, mined["summary"][bucket])

    bucket = st.selectbox("Bucket", ["All"] + search_performance.BUCKETS)
    frame = pd.DataFrame(mined["rows"])
    if bucket != "All":
        frame = frame[frame["bucket"] == bucket]
        st.info(search_performance.RECOMMENDATIONS[bucket])
    st.dataframe(frame[["bucket", "query", "page", "position", "ctr", "clicks",
                        "impressions", "potential_clicks", "recommendation"]],
                 hide_index=True)
    st.download_button("Download opportunities (CSV)", frame.to_csv(index=False),
                       file_name="search_opportunities.csv", mime="text/csv")
    st.caption("Potential clicks is a rough estimate from typical click shares by "
               "position. A direct connection to live search data arrives in phase 4.")


def tab_experiments(settings: Settings) -> None:
    store = open_storage(settings)
    st.subheader("Experiment tracker")
    with st.expander("Log a new experiment", expanded=not store.list_experiments(limit=1)):
        with st.form("experiment_form", clear_on_submit=True):
            url = st.text_input("Page URL")
            keyword = st.text_input("Target keyword")
            hypothesis = st.text_area("Hypothesis", height=70,
                                      placeholder="Adding an FAQ block will lift CTR.")
            changes = st.text_area("Changes made", height=70)
            left, right = st.columns(2)
            position = left.number_input("Baseline position", min_value=0.0, value=None, step=0.1)
            ctr = right.number_input("Baseline CTR (%)", min_value=0.0, max_value=100.0,
                                     value=None, step=0.1)
            if st.form_submit_button("Log experiment", type="primary"):
                outcome = log_seo_optimization_experiment(
                    url, keyword, hypothesis, changes, position, ctr, settings=settings)
                if outcome["ok"]:
                    st.success(f"Experiment {outcome['experiment_id']} logged.")
                else:
                    st.error(outcome["error"])

    experiments = store.list_experiments()
    if not experiments:
        st.info("No experiments logged yet.")
    else:
        frame = pd.DataFrame(experiments)
        st.dataframe(frame, hide_index=True)
        st.caption("position_gain is positive when the page moved up.")
        with st.form("experiment_update"):
            st.markdown("**Record current results**")
            cols = st.columns(4)
            chosen = cols[0].selectbox("Experiment", [e["id"] for e in experiments])
            position = cols[1].number_input("Current position", min_value=0.0, value=None, step=0.1)
            ctr = cols[2].number_input("Current CTR (%)", min_value=0.0, max_value=100.0,
                                       value=None, step=0.1)
            status = cols[3].selectbox("Status", EXPERIMENT_STATUSES)
            if st.form_submit_button("Save results"):
                store.update_experiment(chosen, position, ctr, status)
                st.rerun()
        st.download_button("Download experiments (CSV)", frame.to_csv(index=False),
                           file_name="seo_experiments.csv", mime="text/csv")

    st.subheader("Audit history")
    audits = store.list_audits()
    if audits:
        frame = pd.DataFrame(audits)
        st.dataframe(frame, hide_index=True)
        st.download_button("Download audit history (CSV)", frame.to_csv(index=False),
                           file_name="audit_history.csv", mime="text/csv")
    else:
        st.info("No audits saved yet.")

    st.subheader("Keyword history")
    keywords = store.list_keywords()
    if keywords:
        frame = pd.DataFrame(keywords)
        st.dataframe(frame, hide_index=True)
        st.download_button("Download keyword history (CSV)", frame.to_csv(index=False),
                           file_name="keyword_history.csv", mime="text/csv")
    else:
        st.info("No keyword runs saved yet.")
    st.caption("History is stored in a local file for now. On the free host it "
               "resets when the app restarts, until the cloud database is added.")


def coming(phase: int, what: str) -> None:
    st.info(f"{what} arrives in phase {phase}.")


def main() -> None:
    st.title("⭐ SEO Super Star")
    settings = sidebar_settings()
    tabs = st.tabs(["1 Technical audit", "2 Keywords", "3 SERP & briefs",
                    "4 GEO & schema", "5 Search performance", "6 History & experiments"])
    with tabs[0]:
        tab_technical(settings)
    with tabs[1]:
        tab_keywords(settings)
    with tabs[2]:
        tab_serp(settings)
    with tabs[3]:
        tab_geo(settings)
        citation_monitor(settings)
    with tabs[4]:
        tab_search_performance()
    with tabs[5]:
        tab_experiments(settings)


main()
