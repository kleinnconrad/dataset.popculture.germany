"""Tests of the README overview generator."""
from datetime import datetime, timezone

import pytest

from conftest import CHARTS_FIXTURE, ENTITIES_FIXTURE, SOURCES_FIXTURE
from generate_dataset_overview import END_MARKER, START_MARKER, build_overview, update_readme
from state import append_run_log
from storage import dumps_records, partition_file

NOW = datetime(2026, 10, 28, 3, 0, tzinfo=timezone.utc)


def write(data_dir, dataset: str, records: list[dict]) -> None:
    path = partition_file(data_dir, dataset, NOW.date())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps_records(records), encoding="utf-8")


def overview_of(data_dir, config_path=SOURCES_FIXTURE) -> str:
    return build_overview(data_dir, config_path, now=NOW, charts_path=CHARTS_FIXTURE, entities_path=ENTITIES_FIXTURE)


def chart_record(chart_date: str, revision: int, titles: list[str]) -> dict:
    return {"record_id": f"chart:apple.music_top_songs:{chart_date}", "revision": revision,
            "chart_id": "apple.music_top_songs", "chart_date": chart_date, "observed_at": f"{chart_date}T02:20:00Z",
            "entries": [{"rank": rank, "title": title, "creator": "Artist"} for rank, title in enumerate(titles, 1)]}


def test_build_overview_counts_records_charts_views_and_annotations(tmp_path) -> None:
    data_dir = tmp_path / "data"
    write(data_dir, "chart_snapshots", [chart_record("2026-10-27", 0, ["Old"]),
                                        chart_record("2026-10-28", 0, ["A", "B", "C", "D"]),
                                        chart_record("2026-10-28", 1, ["B", "A", "C", "D"])])
    write(data_dir, "wikipedia_pageviews", [
        {"record_id": "pageviews:helene_fischer:a", "entity_id": "helene_fischer", "observed_at": "2026-10-28T02:30:00Z",
         "daily_views": {"2026-10-20": 50, "2026-10-26": 900, "2026-10-27": 1100}},
        {"record_id": "pageviews:bundeswehr:a", "entity_id": "bundeswehr", "observed_at": "2026-10-28T02:30:00Z",
         "daily_views": {"2026-10-27": 700}},
    ])
    write(data_dir, "news_items", [{"record_id": "news:a", "observed_at": "2026-10-28T01:00:00Z",
                                    "source_id": "general_daily", "published_at": "2026-10-27T22:00:00Z",
                                    "content_basis": "lead_text", "keywords": ["Musik", "Tour"]},
                                   {"record_id": "news:b", "observed_at": "2026-10-28T01:00:00Z",
                                    "source_id": "general_daily", "content_basis": "title_only", "keywords": []}])
    write(data_dir, "text_annotations", [{
        "record_id": "annotation:news:a:x", "item_id": "news:a", "observed_at": "2026-10-28T01:00:00Z",
        "is_pop_culture": True, "domain": "Music",
        "people": [{"wikidata_qid": "Q1", "wikipedia_title": "Helene Fischer", "role": "Subject", "tone": "Positive"}],
        "products": [{"name": "Labubu", "category": "Toys and children's products", "role": "Recommended"}],
        "lifestyle_advice": ["Sleep"],
        "stances": [{"target": "Marriage", "stance": "Favorable", "holder": "Author or outlet"},
                    {"target": "Marriage", "stance": "Unfavorable", "holder": "Celebrity or influencer"}]}])
    append_run_log(data_dir / "state", {"started": "2026-10-28T02:17:00+00:00", "mode": "normal",
                                        "collectors": {"charts": {"status": "ok", "records": {"chart_snapshots": 3}}}})

    overview = overview_of(data_dir)

    assert "| `chart_snapshots` | 3 | 2 | 1 | 2026-10-27 to 2026-10-28 |" in overview
    assert "- **Active charts:** 3; with stored lists: 1" in overview
    assert ("| Apple Music: most played songs, Germany | 3 | 2026-10-28 | 1. B (Artist); 2. A (Artist); "
            "3. C (Artist) |") in overview
    assert "| German Wikipedia: most viewed articles per day | 0 | none | |" in overview
    assert "- **Registered entities:** 4 (person 2, institution 1, tv show 1)" in overview
    assert "**Most viewed entities, 2026-10-21 to 2026-10-27**" in overview
    assert "| Helene Fischer | 2,000 |" in overview
    assert "**Annotated items:** 1 of 2 (50.0%)" in overview
    assert "| Music | 1 | 100.0% |" in overview
    assert "| Helene Fischer | 1 | 100.0% |" in overview
    assert "| Labubu | 1 | 100.0% |" in overview
    assert "| Marriage | 2 | 1 | 1 | 0 |" in overview
    assert "- **News outlets:** 3 configured (2 active, 1 removed)" in overview
    assert "`charts`: ok (chart_snapshots 3)" in overview
    assert "| General Daily | national daily | active | 2 | 2 | 50.0% (1) | 50.0% (1) | 2026-10-27 |" in overview
    assert "| `keywords` | Keywords from the article page or the news sitemap. | 50.0% (1) | 2 |" in overview
    assert "<summary><code>entities</code>" not in overview


def test_update_readme_replaces_only_the_marked_section(tmp_path) -> None:
    readme = tmp_path / "README.md"
    readme.write_text(f"# Title\n{START_MARKER}\nold\n{END_MARKER}\nrest\n", encoding="utf-8")

    assert update_readme(readme, "new") is True
    assert readme.read_text(encoding="utf-8") == f"# Title\n{START_MARKER}\nnew\n{END_MARKER}\nrest\n"
    assert update_readme(readme, "new") is False


def test_update_readme_requires_markers(tmp_path) -> None:
    readme = tmp_path / "README.md"
    readme.write_text("# Title\n", encoding="utf-8")
    with pytest.raises(ValueError):
        update_readme(readme, "x")


def test_overview_without_data(tmp_path) -> None:
    overview = overview_of(tmp_path / "data")
    assert "- No run yet." in overview
    assert "| `news_items` | 0 | 0 | 0 | none |" in overview
    assert "with stored lists: 0" in overview
    assert "- No news articles or press releases yet." in overview
