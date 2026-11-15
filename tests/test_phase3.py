"""Offline smoke tests for phase 3: search waterfall, competitors, briefs, citations."""

import json

from seo_super_star import brief, citations, competitors, keywords, llm, serp
from seo_super_star.config import Settings, service_status
from seo_super_star.http import FetchResult

GOOD = [{"url": f"https://site{i}.com/standing-desks", "title": f"Best standing desk {i}",
         "snippet": "We tested standing desks."} for i in range(1, 6)]
JUNK = [{"url": f"https://news{i}.com/", "title": "Breaking news", "snippet": "Politics"}
        for i in range(1, 6)]


def page_html(n: int) -> bytes:
    return f"""<html><head><title>Best Standing Desks {n}</title>
    <script type="application/ld+json">{{"@type":"Article","headline":"x"}}</script></head><body><main>
    <h1>Best standing desks</h1>
    <h2>How we tested standing desks</h2><p>{'Motor noise and weight capacity matter. ' * (20 * n)}</p>
    <h3>Stability testing</h3>
    <h2>Electric vs manual desks</h2><p>Dual motor frames lift more weight capacity.</p>
    <h2>What is the best desk height?</h2><p>Elbow height.</p>
    <h2>Unique section {n} zebra{n}</h2><p>Only here.</p>
    <h2>Related posts</h2>
    </main></body></html>""".encode()


def fake_fetch(url, timeout=15, allow_private=False, session=None):
    if "broken" in url:
        return FetchResult(requested_url=url, error="Timed out.")
    if "mysite" in url:
        body = b"<html><body><main><h1>Desks</h1><h2>Electric vs manual desks</h2><p>motor noise</p></main></body></html>"
    else:
        body = page_html(int(url.split("site")[1][0]))
    return FetchResult(requested_url=url, final_url=url, status_code=200,
                       content_type="text/html", body=body)


def patch_search(monkeypatch, providers):
    monkeypatch.setattr(serp, "PROVIDERS", providers)


def test_search_waterfall_falls_through(monkeypatch):
    def blocked(*a):
        raise serp.ProviderError("HTTP 202 (likely blocked)")
    patch_search(monkeypatch, [("Tavily", serp._tavily), ("DuckDuckGo", blocked),
                               ("Bing", lambda *a: {"results": GOOD, "ai_overview": ""})])
    found = serp.search("best standing desk", Settings())
    assert found["ok"] and found["provider"] == "Bing"
    assert [a["outcome"] for a in found["attempts"]] == [
        "no key configured", "HTTP 202 (likely blocked)", "ok"]
    assert found["results"][0]["position"] == 1


def test_search_rejects_irrelevant_scraped_results(monkeypatch):
    patch_search(monkeypatch, [("Bing", lambda *a: {"results": JUNK, "ai_overview": ""})])
    found = serp.search("best standing desk", Settings())
    assert not found["ok"] and "Tavily" in found["error"]
    assert "did not match" in found["attempts"][0]["outcome"]
    assert serp.looks_relevant("best standing desk", GOOD)


def test_bing_redirect_decoding():
    wrapped = "https://www.bing.com/ck/a?!&&p=abc&u=a1aHR0cHM6Ly9leGFtcGxlLmNvbS9kZXNr&ntb=1"
    assert serp._decode_bing_url(wrapped) == "https://example.com/desk"
    assert serp._decode_bing_url("https://example.com/") == "https://example.com/"


def run_analysis(monkeypatch, settings=None, **kwargs):
    monkeypatch.setattr(competitors, "fetch_page", fake_fetch)
    monkeypatch.setattr(keywords, "fetch_suggestions",
                        lambda q, gl="us", hl="en": (["how tall should a standing desk be"], "Fake"))
    patch_search(monkeypatch, [("Tavily", lambda *a: {
        "results": GOOD + [{"url": "https://broken.com/standing-desk", "title": "standing desk",
                            "snippet": "desk"}],
        "ai_overview": "Standing desks vary."})])
    return competitors.reverse_engineer_serp_competitors(
        "best standing desk", settings=settings or Settings(), **kwargs)


def test_competitor_analysis_and_template_brief(monkeypatch):
    result = run_analysis(monkeypatch, my_url="mysite.com/desks", max_competitors=4)
    assert result["ok"] and result["serp"]["ai_overview"] == "Standing desks vary."
    readable = [c for c in result["competitors"] if c["ok"]]
    assert len(readable) == 4 and readable[0]["schema_types"] == ["Article"]
    assert readable[0]["heading_tree"][0] == {"h2": "How we tested standing desks",
                                              "h3": ["Stability testing"]}
    assert all("Related posts" not in n["h2"] for c in readable for n in c["heading_tree"])
    average = sum(c["word_count"] for c in readable) / 4
    assert result["avg_competitor_words"] == round(average)
    assert result["target_word_count"] >= average * 1.15
    assert result["target_word_count"] % 50 == 0

    entities = {e["entity"]: e for e in result["entities"]}
    assert entities["weight capacity"]["competitors_using"] == 4
    assert entities["weight capacity"]["in_my_page"] is False
    assert entities["motor noise"]["in_my_page"] is True
    subtopics = {s["heading"]: s for s in result["subtopics"]}
    assert subtopics["Electric vs manual desks"]["covered_by_my_page"] is True
    assert subtopics["How we tested standing desks"]["covered_by_my_page"] is False
    assert subtopics["How we tested standing desks"]["competitors_using"] == 4
    assert "How tall should a standing desk be?" in result["questions"]

    made = result["brief"]
    assert made["source"] == "Template" and len(made["title"]) <= 60
    assert made["direct_answer"] == "" and made["direct_answer_instructions"]
    assert made["outline"] and made["faqs"][0]["answer"] == ""
    assert "## Entities to add" in brief.brief_to_markdown(result)
    json.dumps(result)


def test_manual_competitor_urls_skip_search(monkeypatch):
    monkeypatch.setattr(competitors, "fetch_page", fake_fetch)
    monkeypatch.setattr(keywords, "fetch_suggestions", lambda *a, **k: ([], ""))
    patch_search(monkeypatch, [])           # no provider available at all
    result = competitors.reverse_engineer_serp_competitors(
        "best standing desk", settings=Settings(),
        competitor_urls=["site1.com/a", "site2.com/b", ""])
    assert result["ok"] and result["serp"]["provider"] == "Your list"
    assert result["competitors"][0]["title"] == "Best Standing Desks 1"
    blocked = competitors.reverse_engineer_serp_competitors("best standing desk", settings=Settings())
    assert not blocked["ok"] and "paste competitor URLs" in blocked["error"]


def test_llm_brief_and_fallback(monkeypatch):
    settings = Settings(gemini_api_key="k", groq_api_key="k")
    reply = {"title": "Best Standing Desks Tested", "meta_description": "m", "h1": "H",
             "direct_answer": "A direct answer.",
             "outline": [{"h2": "Testing", "h3": ["Noise"]}],
             "faqs": [{"question": "Q?", "answer": "A."}]}

    def gemini_down(prompt, want_json, s):
        raise llm.ProviderError("rate limit reached")
    monkeypatch.setattr(llm, "_PROVIDERS", [
        ("Gemini", "gemini_api_key", gemini_down),
        ("Groq", "groq_api_key", lambda p, j, s: "```json\n" + json.dumps(reply) + "\n```")])
    made = run_analysis(monkeypatch, settings=settings)["brief"]
    assert made["source"] == "Groq" and made["direct_answer"] == "A direct answer."
    assert made["outline"] == [{"h2": "Testing", "h3": ["Noise"]}]

    monkeypatch.setattr(llm, "_PROVIDERS", [("Gemini", "gemini_api_key", gemini_down)])
    made = run_analysis(monkeypatch, settings=settings)["brief"]
    assert made["source"] == "Template" and "rate limit" in made["note"]
    assert llm.generate("x", Settings())["ok"] is False        # no keys: no crash
    assert llm.parse_json("not json") is None


def test_citation_monitor_search_only(monkeypatch):
    results = [{"url": "https://www.acme-desks.com/best", "title": "Acme Desks review",
                "snippet": "standing desk brands"},
               {"url": "https://rival.com/standing-desk-brands", "title": "Rival standing desk",
                "snippet": "brands"}]
    patch_search(monkeypatch, [("Bing", lambda *a: {"results": results, "ai_overview": ""})])
    out = citations.check_brand_ai_citation_visibility(
        "Acme Desks", "https://www.acme-desks.com/", "best standing desk brands\n\n", Settings())
    assert out["ok"] and out["domain"] == "acme-desks.com"
    row = out["results"][0]
    assert row["mode"] == "Search results only (Bing)"
    assert row["brand_mentioned"] and row["domain_cited"]
    assert [c["domain"] for c in row["competitor_urls"]] == ["rival.com"]
    assert out["visibility_percent"] == 100 and out["mode_note"]
    assert out["top_competitors"] == [{"domain": "rival.com", "prompts_cited_in": 1}]


def test_citation_monitor_ai_answer_and_failures(monkeypatch):
    monkeypatch.setattr(citations, "_tavily_answer", lambda p, s: {
        "mode": "AI answer (Tavily)", "text": "Top picks include Rival and Other.",
        "sources": [{"url": "https://rival.com/x", "domain": "rival.com"}]})
    out = citations.check_brand_ai_citation_visibility("Acme", "acme.com", ["a", "b"], Settings())
    assert out["visibility_percent"] == 0 and out["prompts_checked"] == 2 and not out["mode_note"]
    assert out["top_competitors"][0] == {"domain": "rival.com", "prompts_cited_in": 2}

    monkeypatch.setattr(citations, "_tavily_answer", lambda p, s: None)
    patch_search(monkeypatch, [])
    down = citations.check_brand_ai_citation_visibility("Acme", "acme.com", ["a"], Settings())
    assert not down["ok"] and down["results"][0]["mode"] == "Unavailable"
    assert not citations.check_brand_ai_citation_visibility("", "", ["a"], Settings())["ok"]


def test_status_reflects_keys():
    modes = {s["service"]: s["mode"] for s in service_status(Settings())}
    assert modes["AI writing (briefs)"] == "Fallback" and modes["Live search results"] == "Keyless"
    keyed = {s["service"]: s["mode"] for s in service_status(
        Settings(tavily_api_key="k", groq_api_key="k"))}
    assert keyed["AI writing (briefs)"] == "Full" and keyed["AI citation monitor"] == "Full"
