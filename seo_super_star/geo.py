"""GEO / AEO readiness scorecard: four pillars of 25 points each."""

from __future__ import annotations

import re

_QUESTION_START = re.compile(
    r"^(how|what|why|when|where|who|which|can|could|does|do|did|is|are|was|"
    r"should|will|would)\b", re.I)
_NUMBER = re.compile(r"(?<![\w.])[$€£₹]?\d[\d,]*(?:\.\d+)?%?")
_AUTHORITY_SUFFIXES = (".gov", ".edu", ".int", ".mil")
_AUTHORITY_HOSTS = ("wikipedia.org", "who.int", "nih.gov", "nature.com",
                    "sciencedirect.com", "ieee.org", "w3.org", "schema.org",
                    "developers.google.com", "europa.eu", "oecd.org",
                    "statista.com", "pewresearch.org")
_CONTENT_TYPES = {"Article", "BlogPosting", "NewsArticle", "TechArticle",
                  "Product", "LocalBusiness", "Organization", "WebPage",
                  "Service", "SoftwareApplication", "Recipe", "Event", "Course"}
_ANSWER_TYPES = {"FAQPage", "HowTo", "QAPage"}

ANSWER_MIN_WORDS, ANSWER_MAX_WORDS = 40, 75


def is_question(heading: str) -> bool:
    heading = heading.strip()
    return heading.endswith("?") or bool(_QUESTION_START.match(heading))


def _is_authoritative(host: str) -> bool:
    if host.endswith(_AUTHORITY_SUFFIXES) or ".gov." in host or ".ac." in host or ".edu." in host:
        return True
    return any(host == h or host.endswith("." + h) for h in _AUTHORITY_HOSTS)


def _answer_blocks(page: dict) -> dict:
    ideal, near, question_headings = [], [], 0
    for section in page["content"]["sections"]:
        if section["level"] not in (2, 3) or not is_question(section["heading"]):
            continue
        question_headings += 1
        if not section["paragraphs"]:
            continue
        words = len(section["paragraphs"][0].split())
        if ANSWER_MIN_WORDS <= words <= ANSWER_MAX_WORDS:
            ideal.append(section["heading"])
        elif 20 <= words <= 120:
            near.append(f"{section['heading']} ({words} words)")
    score = min(25, len(ideal) * 5 + len(near) * 2)
    findings = [f"{question_headings} question-phrased H2/H3 heading(s).",
                f"{len(ideal)} followed by a self-contained 40-75 word answer."]
    tips = []
    if near:
        findings.append(f"{len(near)} answer(s) are close but outside 40-75 words.")
        tips.append("Tighten these answers to 40-75 words: " + "; ".join(near[:5]) + ".")
    if question_headings < 5:
        tips.append("Phrase more H2/H3 headings as the questions buyers actually ask.")
    if len(ideal) < 5:
        tips.append("Open each question section with a 40-75 word answer that "
                    "makes sense when quoted on its own.")
    return {"name": "Direct Answer Blocks", "score": score, "max": 25,
            "findings": findings, "recommendations": tips}


def _fact_density(page: dict) -> dict:
    content = page["content"]
    words = max(content["word_count"], 1)
    numbers = len(_NUMBER.findall(content["text"]))
    per_100 = numbers * 100 / words
    fact_points = min(15, round(per_100 / 2 * 15)) if content["word_count"] >= 50 else 0
    domains = page["links"]["external_domains"]
    authoritative = [d for d in domains if _is_authoritative(d)]
    link_points = min(10, len(authoritative) * 3 + (len(domains) - len(authoritative)) * 2)
    findings = [f"{numbers} numeric fact(s) in {content['word_count']} words "
                f"({per_100:.1f} per 100 words).",
                f"{len(domains)} external domain(s) cited, "
                f"{len(authoritative)} authoritative."]
    tips = []
    if fact_points < 15:
        tips.append("Add concrete numbers, benchmarks, dates and percentages; "
                    "aim for about 2 per 100 words.")
    if link_points < 10:
        tips.append("Cite primary sources (research, standards bodies, official "
                    "documentation) with outbound links.")
    return {"name": "Fact & Citation Density", "score": fact_points + link_points,
            "max": 25, "findings": findings, "recommendations": tips}


def _scannability(page: dict) -> dict:
    headings, content = page["headings"], page["content"]
    score, findings, tips = 0, [], []
    if headings["h1_count"] == 1:
        score += 8
        findings.append("Exactly one H1.")
    else:
        findings.append(f"{headings['h1_count']} H1 headings.")
        tips.append("Use exactly one H1 that states the page topic.")
    h2 = headings["h2_count"]
    score += 7 if h2 >= 3 else {2: 5, 1: 3}.get(h2, 0)
    findings.append(f"{h2} H2 section(s).")
    if h2 < 3:
        tips.append("Break the page into at least three H2 sections.")
    lists = content["list_count"]
    score += 5 if lists >= 2 else (3 if lists == 1 else 0)
    findings.append(f"{lists} list(s), {content['table_count']} table(s).")
    if lists < 2:
        tips.append("Turn steps, options and feature sets into bulleted or numbered lists.")
    if content["table_count"]:
        score += 3
    else:
        tips.append("Add a comparison or specification table.")
    if not headings["skipped_levels"]:
        score += 2
    else:
        tips.append("Do not skip heading levels (for example H2 straight to H4).")
    return {"name": "Structural Scannability", "score": score, "max": 25,
            "findings": findings, "recommendations": tips}


def _schema_coverage(page: dict) -> dict:
    jsonld = page["jsonld"]
    types = set(jsonld["types"])
    score, tips = 0, []
    if jsonld["entities"]:
        score += 10
    else:
        tips.append("Add JSON-LD structured data; the generator below builds it "
                    "from this page's own facts.")
    if types & _CONTENT_TYPES:
        score += 5
    elif jsonld["entities"]:
        tips.append("Add a primary content type such as Article, Product or Organization.")
    if types & _ANSWER_TYPES:
        score += 5
    else:
        tips.append("Add FAQPage markup for the question-and-answer sections.")
    if "BreadcrumbList" in types:
        score += 2
    else:
        tips.append("Add BreadcrumbList markup.")
    deepest = max((e["property_count"] for e in jsonld["entities"]), default=0)
    if deepest >= 6:
        score += 3
    elif jsonld["entities"]:
        tips.append("Fill in more properties on the main entity (author, dates, image, publisher).")
    findings = [f"{len(jsonld['entities'])} JSON-LD entity(ies): "
                f"{', '.join(jsonld['types']) or 'none'}."]
    if jsonld["parse_errors"]:
        findings.append(f"{jsonld['parse_errors']} block(s) failed to parse.")
    return {"name": "Machine-Readable Schema", "score": score, "max": 25,
            "findings": findings, "recommendations": tips}


def score_geo(page: dict) -> dict:
    pillars = [_answer_blocks(page), _fact_density(page),
               _scannability(page), _schema_coverage(page)]
    total = sum(p["score"] for p in pillars)
    grade = ("Strong" if total >= 75 else "Fair" if total >= 50
             else "Weak" if total >= 25 else "Poor")
    return {"score": total, "max": 100, "grade": grade, "pillars": pillars}
