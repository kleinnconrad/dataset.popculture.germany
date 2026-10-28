"""Tests of the collectors that write the chart configuration and the entity registry."""
from collectors.registries import ChartCatalogCollector, EntitiesCollector
from conftest import CHARTS_FIXTURE, ENTITIES_FIXTURE, read_dataset
from state import CollectorEntry


def test_chart_entries_are_written_once_and_again_after_a_change(make_ctx, tmp_path) -> None:
    ctx = make_ctx()
    entry = CollectorEntry(first_run=ctx.today)
    result = ChartCatalogCollector(CHARTS_FIXTURE).collect(ctx, entry)
    assert result.counters == {"items": 4, "new": 4, "changed": 0}
    by_id = {record["chart_id"]: record for record in read_dataset(tmp_path / "data", "chart_catalog")}
    assert by_id["apple.music_top_songs"]["format"] == "apple_rss"
    assert by_id["wikimedia.removed_chart"]["removed"] == "2026-10-20"
    assert all(record["license"].startswith("Configuration of this project") for record in by_id.values())

    assert ChartCatalogCollector(CHARTS_FIXTURE).collect(make_ctx(), entry).counters["new"] == 0
    changed = tmp_path / "charts.toml"
    changed.write_text(CHARTS_FIXTURE.read_text(encoding="utf-8").replace("max_entries = 3", "max_entries = 5"),
                       encoding="utf-8")
    assert ChartCatalogCollector(changed).collect(make_ctx(), entry).counters["changed"] == 1
    revisions = [r for r in read_dataset(tmp_path / "data", "chart_catalog") if r["revision"] == 1]
    assert [r["chart_id"] for r in revisions] == ["wikimedia.top_articles_dewiki"]


def test_entities_are_written_with_their_articles(make_ctx, tmp_path) -> None:
    ctx = make_ctx()
    EntitiesCollector(ENTITIES_FIXTURE).collect(ctx, CollectorEntry(first_run=ctx.today))
    records = {record["entity_id"]: record for record in read_dataset(tmp_path / "data", "entities")}
    assert records["bundeswehr"]["domain"] == "Politics and society"
    assert records["helene_fischer"]["wikipedia_title"] == "Helene Fischer"
    assert records["old_entity"]["removed_reason"] == "test of a removed entry"
