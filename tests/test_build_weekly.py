"""Tests of the weekly indicators."""
import csv
import importlib.util
from datetime import date
from pathlib import Path

from storage import dumps_records, partition_file

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_weekly.py"
_spec = importlib.util.spec_from_file_location("build_weekly", SCRIPT)
assert _spec is not None and _spec.loader is not None
build_weekly = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_weekly)

DAY = date(2026, 10, 28)


def write(data_dir: Path, dataset: str, records: list[dict]) -> None:
    path = partition_file(data_dir, dataset, DAY)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps_records(records), encoding="utf-8")


def annotation(item_id: str, **fields) -> dict:
    base = {"record_id": f"annotation:{item_id}:x", "item_id": item_id, "observed_at": "2026-10-28T03:00:00Z",
            "is_pop_culture": True, "domain": "Music", "overall_tone": "Positive", "article_type": "News",
            "purchase_recommendation": False, "sponsored": False, "reports_controversy": False,
            "stances": [], "family_forms": {}, "people": [], "products": [], "works": []}
    base.update(fields)
    return base


def rows_by(rows, variable: str) -> dict:
    return {(row[0], row[3]): (row[4], row[5]) for row in rows if row[2] == variable}


def test_weekly_rows_aggregate_annotations_charts_and_pageviews(tmp_path) -> None:
    data_dir = tmp_path / "data"
    write(data_dir, "news_items", [
        {"record_id": "news:a", "published_at": "2026-10-26T10:00:00Z", "outlet_type": "tabloid", "audience": "general"},
        {"record_id": "news:b", "published_at": "2026-10-27T10:00:00Z", "outlet_type": "music_magazine",
         "audience": "youth"},
        {"record_id": "news:c", "published_at": "2026-10-19T10:00:00Z", "outlet_type": "tabloid", "audience": "general"},
    ])
    write(data_dir, "text_annotations", [
        annotation("news:a", stances=[{"target": "Bundeswehr and military", "stance": "Unfavorable",
                                       "holder": "Celebrity or influencer"}],
                   family_forms={"Single parent": "Positive"}, purchase_recommendation=True,
                   products=[{"name": "Labubu", "category": "Toys and children's products", "role": "Recommended"}]),
        annotation("news:b", domain="Film and cinema", stances=[
            {"target": "Bundeswehr and military", "stance": "Favorable", "holder": "Author or outlet"},
            {"target": "Bundeswehr and military", "stance": "Unfavorable", "holder": "Fans or audience"}]),
        annotation("news:c", is_pop_culture=False),
    ])
    write(data_dir, "chart_snapshots", [
        {"chart_id": "apple.music_top_songs", "chart_date": "2026-10-26", "revision": 0,
         "observed_at": "2026-10-26T02:00:00Z", "entries": [{"rank": 1, "title": "A", "creator": "X"}]},
        {"chart_id": "apple.music_top_songs", "chart_date": "2026-10-28", "revision": 0,
         "observed_at": "2026-10-28T02:00:00Z", "entries": [{"rank": 1, "title": "B", "creator": "Y"},
                                                            {"rank": 2, "title": "A", "creator": "X"}]},
    ])
    write(data_dir, "wikipedia_pageviews", [
        {"entity_id": "bundeswehr", "daily_views": {"2026-10-26": 100, "2026-10-27": 150, "2026-10-19": 80}},
    ])

    rows = build_weekly.build_rows(data_dir)

    assert rows_by(rows, "annotated_items") == {("2026-W43", ""): (1.0, 1), ("2026-W44", ""): (2.0, 2)}
    assert rows_by(rows, "pop_culture_items")[("2026-W43", "")] == (0.0, 1)
    assert rows_by(rows, "domain_share")[("2026-W44", "Music")] == (0.5, 2)
    balance = rows_by(rows, "stance_balance")[("2026-W44", "Bundeswehr and military")]
    assert balance[0] == -1 / 3 and balance[1] == 3
    assert rows_by(rows, "stance_balance_celebrities")[("2026-W44", "Bundeswehr and military")] == (-1.0, 1)
    assert rows_by(rows, "family_form_tone_balance")[("2026-W44", "Single parent")] == (1.0, 1)
    assert rows_by(rows, "purchase_recommendation_share")[("2026-W44", "")] == (0.5, 2)
    assert rows_by(rows, "product_recommended")[("2026-W44", "Labubu")] == (1.0, 2)
    assert rows_by(rows, "items_by_audience")[("2026-W44", "youth")] == (1.0, 1)
    assert rows_by(rows, "chart_rank") == {("2026-W44", "apple.music_top_songs | B – Y"): (1.0, 1),
                                           ("2026-W44", "apple.music_top_songs | A – X"): (2.0, 1)}
    assert rows_by(rows, "chart_days_listed")[("2026-W44", "apple.music_top_songs | A – X")] == (2.0, 7)
    assert rows_by(rows, "pageviews") == {("2026-W43", "bundeswehr"): (80.0, 1), ("2026-W44", "bundeswehr"): (250.0, 2)}


def test_main_writes_a_csv_with_a_header(tmp_path) -> None:
    data_dir = tmp_path / "data"
    write(data_dir, "wikipedia_pageviews", [{"entity_id": "x", "daily_views": {"2026-10-26": 5}}])
    output = tmp_path / "features" / "weekly.csv"

    assert build_weekly.main(["--data-dir", str(data_dir), "--output", str(output)]) == 0
    with open(output, encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    assert rows[0] == list(build_weekly.COLUMNS)
    assert rows[1] == ["2026-W44", "pageviews", "pageviews", "x", "5", "1"]
    assert build_weekly.main(["--data-dir", str(tmp_path / "missing"), "--output", str(output)]) == 1
