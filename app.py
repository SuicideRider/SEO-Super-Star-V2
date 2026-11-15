"""SEO Super Star dashboard. All logic lives in the seo_super_star package."""

from __future__ import annotations

import json
import os

import pandas as pd
import streamlit as st

from seo_super_star.config import Settings, service_status
from seo_super_star.schema_gen import SCHEMA_TYPES
from seo_super_star.service import inspect_url_seo_and_geo
from seo_super_star.storage import Storage, summary_row

st.set_page_config(page_title="SEO Super Star", page_icon="⭐", layout="wide")

# Hosted secrets become environment variables so the engine reads one source.
try:
    for _name in ("PAGESPEED_API_KEY", "JINA_API_KEY"):
        if _name in st.secrets and not os.environ.get(_name):
            os.environ[_name] = str(st.secrets[_name])
except Exception:
    pass  # no secrets file configured


def sidebar_settings() -> Settings:
    with st.sidebar:
        st.header("Optional keys")
        st.caption("Everything works without keys. Keys entered here last for "
                   "this browser session only and are not saved.")
        pagespeed_key = st.text_input("PageSpeed Insights API key", type="password")
        jina_key = st.text_input("Reader (JS rendering) API key", type="password")
        settings = Settings.from_env({"pagespeed_api_key": pagespeed_key,
                                      "jina_api_key": jina_key})
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
    coming(3, "The AI citation visibility monitor")


def tab_history(settings: Settings) -> None:
    st.subheader("Audit history")
    rows = Storage(settings.db_path).list_audits()
    if not rows:
        st.info("No audits saved yet.")
        return
    frame = pd.DataFrame(rows)
    st.dataframe(frame, hide_index=True)
    st.download_button("Download history (CSV)", frame.to_csv(index=False),
                       file_name="audit_history.csv", mime="text/csv")
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
        coming(2, "Keyword expansion and trend scoring")
    with tabs[2]:
        coming(3, "Competitor gap analysis and content briefs")
    with tabs[3]:
        tab_geo(settings)
    with tabs[4]:
        coming(2, "The search performance opportunity miner")
    with tabs[5]:
        tab_history(settings)
        coming(2, "The experiment tracker")


main()
