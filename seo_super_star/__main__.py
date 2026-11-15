"""Headless command line: python -m seo_super_star <command> [options].

Prints JSON, so output can be piped into other tools. The `daily` command is
what the scheduled job runs.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import date, datetime, timezone

from . import changes, citations, competitors, google_api, keywords, service, sitemap
from .config import Settings, service_status
from .storage import SUMMARY_COLUMNS, open_storage


def _write_csv(path: str, rows: list[dict], columns: list[str]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def run_daily(config_path: str, settings: Settings, output_dir: str = "reports") -> dict:
    """Audit the pages named in the automation config, then sync the sheet.

    Config keys: "sitemap" (URL), "urls" (list), "max_urls" (default 25),
    "target_keyword", "fail_on_regression" (exit with an error when something
    serious broke since the last run, so the scheduler sends an alert).
    With nothing configured the job reports that and stops.
    """
    out = {"ok": False, "error": "", "date": date.today().isoformat(), "pages_audited": 0,
           "pages_failed": 0, "csv": "", "sheet": None, "database": ""}
    try:
        with open(config_path, encoding="utf-8") as handle:
            config = json.load(handle)
    except FileNotFoundError:
        out["error"] = f"No automation config at {config_path}."
        return out
    except ValueError as exc:
        out["error"] = f"{config_path} is not valid JSON: {exc}"
        return out

    rows, failed, site = [], [], None
    keyword = config.get("target_keyword", "")
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if config.get("sitemap"):
        result = sitemap.spider_xml_sitemap(config["sitemap"], int(config.get("max_urls", 25)),
                                            keyword, settings,
                                            max_cap=sitemap.MAX_SCHEDULED)
        out["urls_in_sitemap"] = result["urls_found"]
        site = result["site"]
        rows += result["pages"]
        failed += result["failed"]
        if not result["ok"]:
            failed.append({"url": config["sitemap"], "error": result["error"]})
    for url in config.get("urls") or []:
        report = service.inspect_url_seo_and_geo(url, target_keyword=keyword, settings=settings)
        if report["ok"]:
            from .storage import summary_row
            rows.append(summary_row(report))
        else:
            failed.append({"url": url, "error": report["error"]})
    if not rows and not failed:
        out["error"] = (f"Nothing to audit: add a \"sitemap\" or \"urls\" to {config_path}.")
        return out

    store = open_storage(settings)
    out.update(pages_audited=len(rows), pages_failed=len(failed), failed=failed,
               database=store.label + (f" - {store.fallback_reason}" if store.fallback_reason else ""))
    if rows:
        out["csv"] = os.path.join(output_dir, f"audit_{out['date']}.csv")
        _write_csv(out["csv"], rows, [c for c in SUMMARY_COLUMNS if c != "id"] + ["top_issue"])

    # Site-wide findings and what changed since the last run.
    source = config.get("sitemap") or "url list"
    site_issues = site["issues"] if site else None
    last_run = store.latest_site_run(source)
    diff = changes.compare_runs(store.latest_audits_before(started), rows, failed,
                                last_run["data"] if last_run else None, site_issues)
    out["site_issues"] = site["summary"] if site else {}
    out["site_note"] = site["note"] if site else ""
    out["changes"] = {**diff["summary"], "note": diff["note"],
                      "serious_examples": [c for c in diff["changes"]
                                           if c["severity"] == "High"][:10]}
    if site_issues:
        out["site_csv"] = os.path.join(output_dir, f"site_issues_{out['date']}.csv")
        _write_csv(out["site_csv"], site_issues, ["severity", "check", "url", "detail"])
    if diff["changes"]:
        out["changes_csv"] = os.path.join(output_dir, f"changes_{out['date']}.csv")
        _write_csv(out["changes_csv"], diff["changes"],
                   ["severity", "change", "url", "before", "after"])
    try:
        store.save_site_run(source, len(rows), diff["summary"]["serious"], {
            "urls": [r["url"] for r in rows] + [f["url"] for f in failed],
            "site_issues": (site_issues or [])[:1500], "site_summary": out["site_issues"],
            "site_note": out["site_note"], "changes": diff["changes"][:500],
            "change_summary": diff["summary"], "change_note": diff["note"]})
    except Exception as exc:              # history must never break the run
        out["site_note"] = (out["site_note"] + f" Run not saved: {exc}").strip()
    _write_job_summary(out, site_issues or [], diff)

    if settings.google_sheet_id:
        out["sheet"] = google_api.sync_history_to_sheet(store, settings)
    out["ok"] = bool(rows)
    if not rows:
        out["error"] = "No page could be audited."
    elif config.get("fail_on_regression") and diff["summary"]["serious"]:
        out["ok"] = False
        out["error"] = (f"{diff['summary']['serious']} serious regression(s) since the last "
                        "run. See changes_csv.")
    return out


def _write_job_summary(out: dict, site_issues: list[dict], diff: dict) -> None:
    """Readable summary on the GitHub Actions run page (no-op elsewhere)."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    def cell(text) -> str:
        return str(text).replace("|", "\\|").replace("\n", " ")[:300]
    lines = [f"## SEO audit {out['date']}",
             f"- Pages audited: **{out['pages_audited']}** (failed: {out['pages_failed']})",
             f"- Database: {out['database']}",
             f"- Changes since last run: **{diff['summary']['regressions']}** regressions "
             f"({diff['summary']['serious']} serious), {diff['summary']['improvements']} "
             f"improvements. {diff['note']}", "", "### Site-wide issues"]
    lines += [f"- {name}: {count}" for name, count in sorted(out["site_issues"].items())] or ["- None"]
    worst = [c for c in diff["changes"] if c["severity"] in ("High", "Medium")][:30]
    if worst:
        lines += ["", "### What got worse", "| Severity | Change | URL | Before | After |",
                  "|---|---|---|---|---|"]
        lines += [f"| {c['severity']} | {cell(c['change'])} | {cell(c['url'])} | "
                  f"{cell(c['before'])} | {cell(c['after'])} |" for c in worst]
    high = [i for i in site_issues if i["severity"] == "High"][:30]
    if high:
        lines += ["", "### Serious site-wide issues", "| Check | URL | Detail |", "|---|---|---|"]
        lines += [f"| {cell(i['check'])} | {cell(i['url'])} | {cell(i['detail'])} |" for i in high]
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="seo_super_star", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("audit", help="audit one URL")
    p.add_argument("url")
    p.add_argument("--keyword", default="")
    p.add_argument("--schema-type", default="Article")
    p.add_argument("--pagespeed", action="store_true")
    p.add_argument("--render-js", default="auto", choices=["auto", "always", "never"])

    p = sub.add_parser("sitemap", help="batch-audit a sitemap")
    p.add_argument("sitemap_url")
    p.add_argument("--max-urls", type=int, default=25)
    p.add_argument("--keyword", default="")

    p = sub.add_parser("keywords", help="discover keywords for a seed")
    p.add_argument("seed")
    p.add_argument("--country", default="us")

    p = sub.add_parser("serp", help="competitor gap and content brief")
    p.add_argument("keyword")
    p.add_argument("--my-url", default="")
    p.add_argument("--competitor-url", action="append", default=[])

    p = sub.add_parser("citations", help="AI citation visibility")
    p.add_argument("brand")
    p.add_argument("domain")
    p.add_argument("prompts", nargs="+")

    p = sub.add_parser("gsc", help="search performance opportunities")
    p.add_argument("--site-url", default="")
    p.add_argument("--days", type=int, default=28)
    p.add_argument("--csv", default="", help="path to a search performance export")
    p.add_argument("--source", default="auto", choices=["auto", "live", "csv", "demo"])

    p = sub.add_parser("experiment", help="log an optimisation experiment")
    p.add_argument("url")
    p.add_argument("--keyword", default="")
    p.add_argument("--hypothesis", default="")
    p.add_argument("--changes", default="")
    p.add_argument("--position", type=float)
    p.add_argument("--ctr", type=float)

    sub.add_parser("sync-sheet", help="copy history to the configured Google Sheet")
    sub.add_parser("status", help="show which services have keys")

    p = sub.add_parser("daily", help="run the scheduled audit job")
    p.add_argument("--config", default="automation.json")
    p.add_argument("--output-dir", default="reports")

    args = parser.parse_args(argv)
    settings = Settings.from_env()
    if args.command == "audit":
        result = service.inspect_url_seo_and_geo(
            args.url, target_keyword=args.keyword, schema_type=args.schema_type,
            run_pagespeed=args.pagespeed, render_js=args.render_js, settings=settings)
    elif args.command == "sitemap":
        result = sitemap.spider_xml_sitemap(args.sitemap_url, args.max_urls, args.keyword, settings,
                                            max_cap=sitemap.MAX_SCHEDULED)
    elif args.command == "keywords":
        result = keywords.discover_keywords_and_trends(args.seed, country_code=args.country,
                                                       settings=settings)
    elif args.command == "serp":
        result = competitors.reverse_engineer_serp_competitors(
            args.keyword, my_url=args.my_url, settings=settings,
            competitor_urls=args.competitor_url)
    elif args.command == "citations":
        result = citations.check_brand_ai_citation_visibility(
            args.brand, args.domain, args.prompts, settings)
    elif args.command == "gsc":
        data = None
        if args.csv:
            with open(args.csv, "rb") as handle:
                data = handle.read()
        result = service.get_gsc_striking_distance_opportunities(
            site_url=args.site_url, days=args.days, source=args.source, csv_data=data,
            settings=settings)
    elif args.command == "experiment":
        result = service.log_seo_optimization_experiment(
            args.url, args.keyword, args.hypothesis, args.changes, args.position, args.ctr,
            settings=settings)
    elif args.command == "sync-sheet":
        result = google_api.sync_history_to_sheet(open_storage(settings), settings)
    elif args.command == "status":
        result = {"ok": True, "services": service_status(settings)}
    else:
        result = run_daily(args.config, settings, args.output_dir)

    json.dump(result, sys.stdout, indent=2, ensure_ascii=False, default=str)
    sys.stdout.write("\n")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
