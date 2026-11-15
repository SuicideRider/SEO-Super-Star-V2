"""Grounded JSON-LD generator.

Every value comes from the audited page. Fields that cannot be found are left
out and listed under `omitted`; ratings, reviews and prices are never invented.
"""

from __future__ import annotations

from .geo import is_question

SCHEMA_TYPES = ["Article", "Product", "LocalBusiness", "Organization"]
MAX_FAQ_ITEMS = 10


def _put(target: dict, key: str, value, omitted: list[str], label: str | None = None) -> None:
    if value:
        target[key] = value
    else:
        omitted.append(label or key)


def _base(page: dict, url: str) -> tuple[str, str, str]:
    h1 = next((h["text"] for h in page["headings"]["items"] if h["level"] == 1), "")
    name = h1 or page["facts"]["og_title"] or page["title"]["text"]
    page_url = page["canonical"]["url"] or url
    return name, page["description"]["text"], page_url


def _article(page: dict, url: str, omitted: list[str]) -> dict:
    facts = page["facts"]
    name, description, page_url = _base(page, url)
    data = {"@context": "https://schema.org", "@type": "Article"}
    _put(data, "headline", name[:110], omitted)
    _put(data, "description", description, omitted)
    _put(data, "image", facts["og_image"], omitted)
    _put(data, "author", {"@type": "Person", "name": facts["author"]} if facts["author"] else None,
         omitted)
    _put(data, "datePublished", facts["date_published"], omitted)
    _put(data, "dateModified", facts["date_modified"], omitted)
    _put(data, "publisher",
         {"@type": "Organization", "name": facts["site_name"]} if facts["site_name"] else None,
         omitted)
    data["mainEntityOfPage"] = page_url
    if facts["lang"]:
        data["inLanguage"] = facts["lang"]
    return data


def _product(page: dict, url: str, omitted: list[str]) -> dict:
    facts = page["facts"]
    name, description, page_url = _base(page, url)
    data = {"@context": "https://schema.org", "@type": "Product"}
    _put(data, "name", name, omitted)
    _put(data, "description", description, omitted)
    _put(data, "image", facts["og_image"], omitted)
    data["url"] = page_url
    if facts["price_amount"] and facts["price_currency"]:
        data["offers"] = {"@type": "Offer", "price": facts["price_amount"],
                          "priceCurrency": facts["price_currency"], "url": page_url}
    else:
        omitted.append("offers (no machine-readable price on the page)")
    omitted.append("aggregateRating and review (never generated; add only real data)")
    return data


def _organization(page: dict, url: str, omitted: list[str], schema_type: str) -> dict:
    facts, links = page["facts"], page["links"]
    name = facts["site_name"] or facts["og_title"] or page["title"]["text"]
    data = {"@context": "https://schema.org", "@type": schema_type}
    _put(data, "name", name, omitted)
    data["url"] = page["canonical"]["url"] or url
    _put(data, "description", page["description"]["text"], omitted)
    _put(data, "image", facts["og_image"], omitted)
    _put(data, "telephone", links["phones"][0] if links["phones"] else "", omitted)
    _put(data, "email", links["emails"][0] if links["emails"] else "", omitted)
    _put(data, "sameAs", links["social_profiles"], omitted, "sameAs (social profiles)")
    if schema_type == "LocalBusiness":
        omitted.append("address, geo and openingHours (not machine-readable on the page)")
    return data


def _faq(page: dict) -> dict | None:
    items = []
    for section in page["content"]["sections"]:
        if section["level"] in (2, 3) and is_question(section["heading"]) and section["paragraphs"]:
            items.append({
                "@type": "Question", "name": section["heading"],
                "acceptedAnswer": {"@type": "Answer", "text": section["paragraphs"][0]},
            })
    if not items:
        return None
    return {"@context": "https://schema.org", "@type": "FAQPage",
            "mainEntity": items[:MAX_FAQ_ITEMS]}


def generate_jsonld(page: dict, url: str, schema_type: str = "Article") -> dict:
    if schema_type not in SCHEMA_TYPES:
        schema_type = "Article"
    omitted: list[str] = []
    if schema_type == "Article":
        primary = _article(page, url, omitted)
    elif schema_type == "Product":
        primary = _product(page, url, omitted)
    else:
        primary = _organization(page, url, omitted, schema_type)
    faq = _faq(page)
    return {
        "schema_type": schema_type,
        "primary": primary,
        "faq": faq,
        "omitted": omitted,
        "note": "Built only from facts found on the page. Omitted fields were "
                "not found and were not invented.",
    }
