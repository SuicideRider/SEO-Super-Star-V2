"""Offline smoke tests for phase 2: keywords, search performance, experiments."""

import json

from seo_super_star import keywords, search_performance as sp, service, trends
from seo_super_star.config import Settings
from seo_super_star.storage import Storage


def fake_suggestions(query, gl="us", hl="en"):
    table = {
        "standing desk": ["standing desk", "Standing Desk  Converter", "buy standing desk"],
        "best standing desk": ["best standing desk", "best standing desk for tall people"],
        "standing desk vs": ["standing desk vs treadmill desk"],
    }
    return table.get(query, []), "Fake autocomplete"


def test_intent_classification():
    assert keywords.classify_intent("buy standing desk") == "Transactional"
    assert keywords.classify_intent("standing desk price") == "Transactional"
    assert keywords.classify_intent("best standing desk") == "Commercial"
    assert keywords.classify_intent("desk a vs desk b") == "Commercial"
    assert keywords.classify_intent("how to assemble a desk") == "Informational"


def test_query_building():
    assert keywords.build_queries("desk", False, False) == ["desk"]
    assert len(keywords.build_queries("desk", True, False)) == 8
    assert len(keywords.build_queries("desk", True, True)) == 34


def test_discovery_without_trends(tmp_path, monkeypatch):
    monkeypatch.setattr(keywords, "fetch_suggestions", fake_suggestions)
    monkeypatch.setattr(trends, "fetch_trends",
                        lambda *a, **k: {"trends": {}, "error": "Trend source is rate-limiting requests."})
    settings = Settings(db_path=str(tmp_path / "k.db"))
    result = keywords.discover_keywords_and_trends("Standing  Desk", settings=settings)
    assert result["ok"] and result["seed_keyword"] == "standing desk"
    names = [k["keyword"] for k in result["keywords"]]
    assert len(names) == len(set(names)) == 6            # deduplicated, normalised
    assert "standing desk converter" in names
    assert all(0 <= k["opportunity_score"] <= 100 for k in result["keywords"])
    assert all(k["trend_direction"] == "unknown" for k in result["keywords"])
    assert "neutral" in result["trend_status"]
    assert result["keywords"][0]["intent"] in ("Transactional", "Commercial")
    assert result["saved"] == 6
    assert len(Storage(settings.db_path).list_keywords()) == 6
    json.dumps(result)


def test_discovery_with_trends(tmp_path, monkeypatch):
    monkeypatch.setattr(keywords, "fetch_suggestions", fake_suggestions)
    monkeypatch.setattr(trends, "fetch_trends", lambda seed, kws, *a, **k: {
        "trends": {"buy standing desk": {"interest": 100, "direction": "rising"},
                   "standing desk": {"interest": 10, "direction": "cooling"}},
        "error": ""})
    result = keywords.discover_keywords_and_trends(
        "standing desk", settings=Settings(db_path=str(tmp_path / "k.db")), save=False)
    by_name = {k["keyword"]: k for k in result["keywords"]}
    assert by_name["buy standing desk"]["trend_interest"] == 100
    assert by_name["buy standing desk"]["opportunity_score"] > by_name["standing desk"]["opportunity_score"]


def test_discovery_handles_no_provider(monkeypatch):
    monkeypatch.setattr(keywords, "fetch_suggestions", lambda *a, **k: ([], ""))
    result = keywords.discover_keywords_and_trends("desk", save=False)
    assert result["ok"] is False and result["error"]


def test_score_is_bounded():
    top = keywords.opportunity_score("Transactional", 0, 5, 4, {"interest": 100, "direction": "rising"})
    low = keywords.opportunity_score("Informational", 30, 1, 1, {"interest": 0, "direction": "cooling"})
    assert top == 100 and 0 <= low < 25


CSV_EXPORT = (
    "﻿Top queries,Clicks,Impressions,CTR,Position\n"
    'standing desk,"1,840","21,500",8.56%,2.1\n'
    "frame only,10,4200,0.24%,1.6\n"
    "desk converter,61,5200,1.17%,8.9\n"
    "desk benefits,22,7400,0.3%,18.5\n"
    "office chair,1,8200,0.01%,47.3\n"
    "tiny query,0,5,0%,9.0\n"
)


def test_csv_parsing_and_buckets():
    parsed = sp.parse_csv(CSV_EXPORT.encode("utf-8"))
    assert parsed["error"] == "" and len(parsed["rows"]) == 6
    first = parsed["rows"][0]
    assert first == {"query": "standing desk", "page": "", "clicks": 1840,
                     "impressions": 21500, "ctr": 8.56, "position": 2.1}
    mined = sp.mine_opportunities(parsed["rows"])
    buckets = {r["query"]: r["bucket"] for r in mined["rows"]}
    assert buckets == {
        "standing desk": "Top Winner", "frame only": "CTR Underperformer",
        "desk converter": "Striking Distance", "desk benefits": "Cluster Expansion",
        "office chair": "Monitor", "tiny query": "Monitor"}
    assert sum(mined["summary"].values()) == 6
    assert mined["rows"][-1]["bucket"] == "Monitor"


def test_bucket_boundaries():
    def bucket(position, ctr, impressions=1000):
        return sp.classify_row({"position": position, "ctr": ctr, "impressions": impressions})
    assert bucket(3.0, 3.5) == "Top Winner"
    assert bucket(3.0, 3.4) == "CTR Underperformer"
    assert bucket(4.5, 2.0) == "CTR Underperformer"     # overlap rule: CTR fix first
    assert bucket(4.5, 6.0) == "Striking Distance"
    assert bucket(15.0, 1.0) == "Striking Distance"
    assert bucket(15.1, 1.0) == "Cluster Expansion"
    assert bucket(35.0, 0.1) == "Cluster Expansion"
    assert bucket(35.1, 0.1) == "Monitor"
    assert bucket(8.0, 1.0, impressions=10) == "Monitor"


def test_fraction_ctr_and_bad_csv():
    parsed = sp.parse_csv("query;impressions;ctr;position\ndesk;1000;0.035;4\n")
    assert parsed["rows"][0]["ctr"] == 3.5 and parsed["rows"][0]["clicks"] == 35
    assert "missing" in sp.parse_csv("a,b\n1,2\n")["error"]


def test_demo_dataset_covers_every_bucket():
    mined = sp.mine_opportunities(sp.demo_rows())
    assert mined["total_rows"] == 30
    assert all(mined["summary"][b] > 0 for b in sp.BUCKETS)


def test_experiment_ledger(tmp_path):
    settings = Settings(db_path=str(tmp_path / "e.db"))
    logged = service.log_seo_optimization_experiment(
        "example.com/desks", "standing desk", "FAQ block lifts CTR", "Added FAQ",
        baseline_position=8.4, baseline_ctr=1.25, settings=settings)
    assert logged == {"ok": True, "error": "", "experiment_id": 1, "status": "Running"}
    store = Storage(settings.db_path)
    row = store.list_experiments()[0]
    assert row["url"] == "https://example.com/desks"
    assert row["baseline_position"] == 8.4 and row["baseline_ctr"] == 1.25   # no rounding
    assert row["current_position"] is None and row["position_gain"] is None

    assert store.update_experiment(1, current_position=5.1, current_ctr=2.75, status="Won")
    row = store.list_experiments()[0]
    assert (row["current_position"], row["current_ctr"], row["status"]) == (5.1, 2.75, "Won")
    assert row["position_gain"] == 3.3 and row["ctr_change"] == 1.5
    assert store.update_experiment(1, status="Inconclusive")       # keeps the numbers
    assert store.list_experiments()[0]["current_position"] == 5.1
    assert store.update_experiment(99, status="Won") is False

    hostile = service.log_seo_optimization_experiment(
        "example.com/x", "desk'); DROP TABLE seo_experiments;--", settings=settings)
    assert hostile["ok"] and len(store.list_experiments()) == 2
    assert service.log_seo_optimization_experiment("", settings=settings)["ok"] is False
