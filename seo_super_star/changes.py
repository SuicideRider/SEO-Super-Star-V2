"""Change tracking: what got worse or better since the previous run."""

from __future__ import annotations

_ORDER = {"High": 0, "Medium": 1, "Low": 2, "Good": 3, "Info": 4}


def _bad(status) -> bool:
    return (status or 0) >= 400


def compare_runs(previous_pages: dict[str, dict], current_pages: list[dict],
                 current_failed: list[dict] | None = None,
                 previous_run: dict | None = None,
                 current_site_issues: list[dict] | None = None) -> dict:
    """Compare this run with the last known state. Returns a JSON-safe dict.

    previous_pages: url -> the most recent summary row saved before this run.
    previous_run: the stored data of the last scheduled run ({"urls", "site_issues"}),
    or None when there is none; site-wide issues are only compared when it exists.
    Severity High/Medium/Low marks a regression, Good an improvement, Info a note.
    """
    changes: list[dict] = []

    def add(severity: str, url: str, change: str, before="", after="") -> None:
        changes.append({"severity": severity, "url": url, "change": change,
                        "before": str(before), "after": str(after)})

    for failure in current_failed or []:
        if failure["url"] in previous_pages:
            add("High", failure["url"], "Page stopped loading",
                f"HTTP {previous_pages[failure['url']].get('status_code')}", failure["error"])

    for row in current_pages:
        old = previous_pages.get(row["url"])
        if not old:
            continue
        url = row["url"]
        if _bad(row["status_code"]) and not _bad(old["status_code"]):
            add("High", url, "Page now returns an error", old["status_code"], row["status_code"])
            continue
        if _bad(old["status_code"]) and not _bad(row["status_code"]):
            add("Good", url, "Page works again", old["status_code"], row["status_code"])
            continue
        for field, label in (("title_status", "Title"), ("description_status", "Meta description")):
            if old[field] == "Optimal" and row[field] != "Optimal":
                add("Medium", url, f"{label} got worse", old[field], row[field])
            elif old[field] not in (None, "Optimal") and row[field] == "Optimal":
                add("Good", url, f"{label} fixed", old[field], row[field])
        if (old["h1_count"] or 0) > 0 and (row["h1_count"] or 0) == 0:
            add("Medium", url, "H1 heading disappeared", old["h1_count"], 0)
        if old["schema_types"] and not row["schema_types"]:
            add("Medium", url, "Structured data disappeared", old["schema_types"], "none")
        elif not old["schema_types"] and row["schema_types"]:
            add("Good", url, "Structured data added", "none", row["schema_types"])
        before, after = old["word_count"] or 0, row["word_count"] or 0
        if before - after > 100 and after < before * 0.7:
            add("Medium", url, "Content shrank sharply", f"{before} words", f"{after} words")
        before, after = old["issue_count"] or 0, row["issue_count"] or 0
        if after > before:
            add("Low", url, "More issues than before", before, after)
        elif after < before:
            add("Good", url, "Fewer issues than before", before, after)
        before, after = old["geo_score"], row["geo_score"]
        if before is not None and after is not None and abs(after - before) >= 10:
            add("Low" if after < before else "Good", url,
                "GEO score dropped" if after < before else "GEO score rose", before, after)

    compared_site = previous_run is not None and current_site_issues is not None
    if compared_site:
        def keyed(issues):
            return {(i["check"], i["url"]): i for i in issues}
        old_issues = keyed(previous_run.get("site_issues") or [])
        new_issues = keyed(current_site_issues)
        for key, issue in new_issues.items():
            if key not in old_issues:
                add(issue["severity"], issue["url"], f"New: {issue['check']}", "", issue["detail"])
        for key, issue in old_issues.items():
            if key not in new_issues:
                add("Good", issue["url"], f"Resolved: {issue['check']}", issue["detail"], "")
        old_urls = set(previous_run.get("urls") or [])
        now_urls = {r["url"] for r in current_pages} | {f["url"] for f in current_failed or []}
        if old_urls:
            for url in sorted(now_urls - old_urls):
                add("Info", url, "New page in the sitemap")
            for url in sorted(old_urls - now_urls):
                add("Info", url, "Page left the sitemap")

    changes.sort(key=lambda c: (_ORDER[c["severity"]], c["change"], c["url"]))
    count = lambda *levels: sum(1 for c in changes if c["severity"] in levels)  # noqa: E731
    has_baseline = bool(previous_pages) or previous_run is not None
    return {
        "has_baseline": has_baseline,
        "note": "" if has_baseline else "First run: nothing to compare with yet.",
        "site_issues_compared": compared_site,
        "summary": {"serious": count("High"), "regressions": count("High", "Medium", "Low"),
                    "improvements": count("Good"), "notes": count("Info")},
        "changes": changes,
    }
