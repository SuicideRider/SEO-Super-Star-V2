"""SEO / GEO content brief: written by a free LLM when a key is set, else templated."""

from __future__ import annotations

from datetime import date

from . import llm
from .config import Settings
from .keywords import classify_intent

_PROMPT = """You are an SEO and generative-engine-optimisation strategist.
Write a content brief for a page targeting the keyword "{keyword}".

Research gathered from the pages that currently rank (use ONLY this material;
do not invent statistics, prices, ratings, dates or brand claims; where a fact
is not in the material, write in general terms):

{research}

Return one JSON object with exactly these keys:
- "title": an optimised title tag, at most 60 characters
- "meta_description": at most 155 characters
- "direct_answer": a 45-55 word paragraph that answers the main question behind
  the keyword and makes sense when quoted on its own
- "outline": a list of objects {{"h2": string, "h3": [strings]}} with 6 to 9 H2s
- "h1": the page H1
- "faqs": a list of 5 objects {{"question": string, "answer": string}}, each
  answer 30-50 words
Recommended length: about {target} words. Return JSON only."""


def _trim(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(",;:-")


def _research(analysis: dict) -> str:
    lines = []
    for competitor in analysis["competitors"]:
        if not competitor["ok"]:
            continue
        headings = "; ".join(node["h2"] for node in competitor["heading_tree"][:12])
        lines.append(f"- Rank {competitor['position']}: \"{competitor['title']}\" "
                     f"({competitor['word_count']} words). H2s: {headings}")
    snippets = [r["snippet"] for r in analysis["serp"]["results"][:6] if r["snippet"]]
    return "\n".join([
        "RANKING PAGES:", *lines,
        "SEARCH SNIPPETS:", *[f"- {s}" for s in snippets],
        "ENTITIES COMPETITORS MENTION: "
        + ", ".join(e["entity"] for e in analysis["entities"][:25]),
        "SUBTOPICS MISSING FROM THE USER'S PAGE: "
        + ", ".join(s["heading"] for s in analysis["subtopics"]
                    if s["covered_by_my_page"] is False)[:600],
        "QUESTIONS PEOPLE ASK: " + " | ".join(analysis["questions"]),
    ])


def _template(analysis: dict) -> dict:
    keyword = analysis["keyword"]
    intent = classify_intent(keyword)
    suffix = {"Transactional": "Prices, Options and How to Choose",
              "Commercial": "Top Picks Compared"}.get(intent, "A Complete Guide")
    subtopics = analysis["subtopics"][:8]
    subtopics.sort(key=lambda s: s["typical_order"])
    main_question = (analysis["questions"] or [f"What is {keyword}?"])[0]
    entities = ", ".join(e["entity"] for e in analysis["entities"][:4])
    return {
        "source": "Template",
        "title": _trim(f"{keyword.title()}: {suffix} ({date.today().year})", 60),
        "meta_description": _trim(
            f"A practical guide to {keyword}: what matters, how the options compare, "
            "and clear answers to the questions people ask most.", 155),
        "h1": f"{keyword.title()}: {suffix}",
        "direct_answer": "",
        "direct_answer_instructions": (
            f"Write a 45-55 word paragraph that directly answers \"{main_question}\" in plain "
            f"language" + (f", naming {entities}" if entities else "")
            + ". It must make sense when quoted on its own."),
        "outline": [{"h2": s["heading"], "h3": s["h3"][:3]} for s in subtopics],
        "faqs": [{"question": q, "answer": ""} for q in analysis["questions"][:5]],
        "note": "Template brief built from competitor structure. Add a free Gemini, "
                "Groq or OpenRouter key to get the answer block and FAQ answers written.",
    }


def build_brief(analysis: dict, settings: Settings) -> dict:
    brief = _template(analysis)
    if not settings.llm_providers:
        return brief
    prompt = _PROMPT.format(keyword=analysis["keyword"], research=_research(analysis),
                            target=analysis["target_word_count"])
    reply = llm.generate(prompt, settings, want_json=True)
    data = llm.parse_json(reply["text"]) if reply["ok"] else None
    if not data or not isinstance(data.get("outline"), list):
        tried = ", ".join(f"{a['provider']}: {a['outcome']}" for a in reply["attempts"])
        brief["note"] = f"AI providers failed ({tried or 'unreadable reply'}); template used."
        return brief
    outline = [{"h2": str(node.get("h2", "")).strip(),
                "h3": [str(h).strip() for h in node.get("h3") or [] if str(h).strip()]}
               for node in data["outline"] if isinstance(node, dict) and node.get("h2")]
    faqs = [{"question": str(f.get("question", "")).strip(),
             "answer": str(f.get("answer", "")).strip()}
            for f in data.get("faqs") or [] if isinstance(f, dict) and f.get("question")]
    return {
        "source": reply["provider"],
        "title": _trim(str(data.get("title") or brief["title"]), 60),
        "meta_description": _trim(str(data.get("meta_description") or brief["meta_description"]), 155),
        "h1": str(data.get("h1") or brief["h1"]).strip(),
        "direct_answer": str(data.get("direct_answer") or "").strip(),
        "direct_answer_instructions": "",
        "outline": outline or brief["outline"],
        "faqs": faqs or brief["faqs"],
        "note": f"Written by {reply['provider']} from the competitor research. "
                "Check facts before publishing.",
    }


def brief_to_markdown(analysis: dict) -> str:
    brief = analysis["brief"]
    lines = [f"# Content brief: {analysis['keyword']}", "",
             f"- **Title tag:** {brief['title']}",
             f"- **Meta description:** {brief['meta_description']}",
             f"- **Target length:** about {analysis['target_word_count']} words "
             f"(competitor average {analysis['avg_competitor_words']})",
             f"- **Brief source:** {brief['source']}", "",
             "## Direct answer block", "",
             brief["direct_answer"] or f"_{brief['direct_answer_instructions']}_", "",
             "## Outline", "", f"# {brief['h1']}"]
    for node in brief["outline"]:
        lines.append(f"## {node['h2']}")
        lines.extend(f"### {h3}" for h3 in node["h3"])
    lines += ["", "## FAQs", ""]
    for faq in brief["faqs"]:
        lines += [f"**{faq['question']}**", faq["answer"] or "_Answer to be written._", ""]
    missing = [e["entity"] for e in analysis["entities"] if e["in_my_page"] is False]
    if missing:
        lines += ["## Entities to add", "", ", ".join(missing), ""]
    return "\n".join(lines)
