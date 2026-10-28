"""Tests of the chart parsers and the charts collector."""
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from chart_catalog import load_chart_config
from collectors.charts import (
    ChartError,
    ChartsCollector,
    parse_apple_rss,
    parse_google_trends_rss,
    parse_traffic,
    parse_wikimedia_top,
    parse_wikimedia_top_country,
)
from conftest import CHARTS_FIXTURE, FIXTURES_DIR, RUN_STARTED, read_dataset
from state import CollectorEntry

APPLE_URL = "https://rss.marketingtools.apple.com/api/v2/de/music/most-played/100/songs.json"
TRENDS_URL = "https://trends.google.com/trending/rss?geo=DE"
WIKI_URL = "https://wikimedia.org/api/rest_v1/metrics/pageviews/top/de.wikipedia.org/all-access/{day:%Y/%m/%d}"
FIRST_RUN = RUN_STARTED.date()


def _charts() -> dict:
    return {chart.id: chart for chart in load_chart_config(CHARTS_FIXTURE).charts}


def _fixture(name: str) -> bytes:
    return (FIXTURES_DIR / "charts" / name).read_bytes()


def _apple(titles: list[str]) -> dict:
    data = json.loads(_fixture("apple_songs.json"))
    results = data["feed"]["results"]
    data["feed"]["results"] = [dict(results[0], name=title, id=str(number)) for number, title in enumerate(titles)]
    return data


def _wikimedia_day(day: date, articles: list[str]) -> dict:
    return {"items": [{"project": "de.wikipedia", "access": "all-access", "year": f"{day:%Y}", "month": f"{day:%m}",
                       "day": f"{day:%d}", "articles": [{"article": title, "views": 1000 - rank, "rank": rank}
                                                        for rank, title in enumerate(articles, start=1)]}]}


def _collect(make_ctx, entry: CollectorEntry, collector: ChartsCollector, started: datetime = RUN_STARTED, **kwargs):
    ctx = make_ctx(started=started, **kwargs)
    result = collector.collect(ctx, entry)
    ctx.save_seen()
    return result, CollectorEntry(first_run=entry.first_run, cursor=result.cursor or {})


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def test_apple_feed_is_parsed_into_ranked_entries() -> None:
    lists = parse_apple_rss(_fixture("apple_songs.json"), _charts()["apple.music_top_songs"])

    assert len(lists) == 1
    chart_list = lists[0]
    assert chart_list.title == "Top-Titel"
    assert chart_list.source_updated == datetime(2026, 10, 28, 12, 24, 25, tzinfo=timezone.utc)
    first = chart_list.entries[0]
    assert (first.rank, first.title, first.creator, first.item_id) == (1, "Housewife", "Tate McRae", "6822497774")
    assert first.genres == ["Pop", "Musik"]
    assert first.release_date == "2026-10-15"
    assert [entry.rank for entry in chart_list.entries] == [1, 2, 3]


def test_google_trends_feed_keeps_volume_and_first_news_article() -> None:
    chart_list = parse_google_trends_rss(_fixture("google_trends.xml"), _charts()["google_trends.trending_searches_de"])[0]

    first = chart_list.entries[0]
    assert first.title == "peter maffay"
    assert first.metric == 1000
    assert first.details["approx_traffic"] == "1000+"
    assert first.details["news_source"] == "T-Online"
    assert first.details["news_count"] == "3"
    assert first.details["started_at"] == "2026-10-28T12:10:00+00:00"
    assert len(chart_list.entries) == 2


@pytest.mark.parametrize("text, value", [("2000+", 2000), ("20.000+", 20000), ("50K+", 50000), ("1 Mio.+", 1e6),
                                         ("", None), ("viele", None)])
def test_approximate_search_volumes_are_parsed(text: str, value) -> None:
    assert parse_traffic(text) == value


def test_wikimedia_top_list_leaves_out_pages_that_are_not_articles() -> None:
    chart = _charts()["wikimedia.top_articles_dewiki"]
    chart_list = parse_wikimedia_top(_fixture("wikimedia_top.json"), chart)[0]

    assert chart_list.chart_date == date(2026, 10, 26)
    assert [entry.title for entry in chart_list.entries] == ["Peter Maffay", "Better Call Saul", "Halloween"]
    assert chart_list.entries[0].rank == 1
    assert chart_list.entries[0].details == {"source_rank": "3"}
    assert chart_list.entries[0].metric == 61000
    assert chart_list.entries[0].url == "https://de.wikipedia.org/wiki/Peter_Maffay"


def test_wikimedia_top_list_per_country_names_the_edition() -> None:
    chart = _charts()["wikimedia.top_articles_dewiki"]
    chart_list = parse_wikimedia_top_country(_fixture("wikimedia_top_country.json"), chart)[0]

    assert [(entry.title, entry.creator) for entry in chart_list.entries] == [
        ("Peter Maffay", "de.wikipedia"), ("Taylor Swift", "en.wikipedia")]
    assert chart_list.entries[1].url == "https://en.wikipedia.org/wiki/Taylor_Swift"


def test_invalid_documents_raise_chart_errors() -> None:
    charts = _charts()
    with pytest.raises(ChartError):
        parse_apple_rss(b"<html></html>", charts["apple.music_top_songs"])
    with pytest.raises(ChartError):
        parse_google_trends_rss(b"{}", charts["google_trends.trending_searches_de"])
    with pytest.raises(ChartError):
        parse_wikimedia_top(b'{"items": []}', charts["wikimedia.top_articles_dewiki"])


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------

def _serve_wikimedia(fake_web, days: list[date]) -> None:
    for day in days:
        fake_web.add(WIKI_URL.format(day=day), _wikimedia_day(day, [f"Artikel {day.day}", "Halloween", "Oktoberfest",
                                                                     "Tatort"]))


def test_live_chart_is_stored_only_when_it_changes(make_ctx, fake_web, tmp_path) -> None:
    fake_web.add(APPLE_URL, _apple(["A", "B"]))
    fake_web.add(TRENDS_URL, _fixture("google_trends.xml"), content_type="application/rss+xml")
    _serve_wikimedia(fake_web, [FIRST_RUN - timedelta(days=n) for n in (1, 2, 3)])
    collector = ChartsCollector(CHARTS_FIXTURE)
    entry = CollectorEntry(first_run=FIRST_RUN)

    result, entry = _collect(make_ctx, entry, collector)
    assert result.counters["charts_ok"] == 3
    result, entry = _collect(make_ctx, entry, collector, started=RUN_STARTED + timedelta(hours=6))
    assert result.counters.get("lists_changed", 0) == 0

    fake_web.add(APPLE_URL, _apple(["B", "A"]))
    _collect(make_ctx, entry, collector, started=RUN_STARTED + timedelta(hours=12))
    fake_web.add(APPLE_URL, _apple(["C"]))
    _collect(make_ctx, entry, collector, started=RUN_STARTED + timedelta(days=1))

    songs = [record for record in read_dataset(tmp_path / "data", "chart_snapshots")
             if record["chart_id"] == "apple.music_top_songs"]
    assert [(record["record_id"], record["revision"]) for record in songs] == [
        ("chart:apple.music_top_songs:2026-10-28", 0), ("chart:apple.music_top_songs:2026-10-28", 1),
        ("chart:apple.music_top_songs:2026-10-29", 0)]
    assert [entry["title"] for entry in songs[1]["entries"]] == ["B", "A"]
    first = songs[0]
    assert first["period"] == "live" and first["country"] == "DE" and first["entry_count"] == 2
    assert first["license"] == "Copyright Apple Inc.; chart positions and metadata only"
    assert first["source_id"] == "apple.music_top_songs"


def test_unchanged_files_are_not_downloaded_again(make_ctx, fake_web) -> None:
    fake_web.add(APPLE_URL, _apple(["A"]), headers={"etag": '"v1"'})
    collector = ChartsCollector(CHARTS_FIXTURE)
    entry = CollectorEntry(first_run=FIRST_RUN)

    _result, entry = _collect(make_ctx, entry, collector)
    assert entry.cursor["apple.music_top_songs"]["etag"] == '"v1"'

    fake_web.add(APPLE_URL, b"", status=304)
    result, _entry = _collect(make_ctx, entry, collector, started=RUN_STARTED + timedelta(hours=6))
    assert result.counters["not_modified"] == 1
    sent = [headers for url, headers in zip(fake_web.requested, fake_web.request_headers) if url == APPLE_URL]
    assert sent[-1]["if-none-match"] == '"v1"'


def test_daily_list_fetches_every_day_since_the_last_one(make_ctx, fake_web, tmp_path) -> None:
    days = [FIRST_RUN - timedelta(days=n) for n in (3, 2, 1)]
    _serve_wikimedia(fake_web, days)
    collector = ChartsCollector(CHARTS_FIXTURE)
    entry = CollectorEntry(first_run=FIRST_RUN)

    _collect(make_ctx, entry, collector)
    records = [record for record in read_dataset(tmp_path / "data", "chart_snapshots")
               if record["chart_id"] == "wikimedia.top_articles_dewiki"]
    assert [record["chart_date"] for record in records] == [day.isoformat() for day in days]
    assert all(record["entry_count"] == 3 for record in records)

    _serve_wikimedia(fake_web, [FIRST_RUN])
    fake_web.requested.clear()
    _collect(make_ctx, entry, collector, started=RUN_STARTED + timedelta(days=1))
    requested = [url for url in fake_web.requested if "wikimedia.org" in url]
    assert requested == [WIKI_URL.format(day=FIRST_RUN)]


def test_daily_list_that_is_not_published_yet_is_tried_again(make_ctx, fake_web, tmp_path) -> None:
    _serve_wikimedia(fake_web, [FIRST_RUN - timedelta(days=3), FIRST_RUN - timedelta(days=2)])
    collector = ChartsCollector(CHARTS_FIXTURE)

    result, entry = _collect(make_ctx, CollectorEntry(first_run=FIRST_RUN), collector)
    assert "wikimedia.top_articles_dewiki" not in result.counters["failed"]

    _serve_wikimedia(fake_web, [FIRST_RUN - timedelta(days=1)])
    _collect(make_ctx, entry, collector, started=RUN_STARTED + timedelta(hours=6))
    dates = [record["chart_date"] for record in read_dataset(tmp_path / "data", "chart_snapshots")
             if record["chart_id"] == "wikimedia.top_articles_dewiki"]
    assert dates == ["2026-10-25", "2026-10-26", "2026-10-27"]


def test_list_with_an_unexpected_title_is_not_stored(make_ctx, fake_web, tmp_path) -> None:
    data = _apple(["A"])
    data["feed"]["title"] = "Something else"
    fake_web.add(APPLE_URL, data)
    fake_web.add(TRENDS_URL, _fixture("google_trends.xml"), content_type="application/rss+xml")
    _serve_wikimedia(fake_web, [FIRST_RUN - timedelta(days=n) for n in (1, 2, 3)])

    result, _entry = _collect(make_ctx, CollectorEntry(first_run=FIRST_RUN), ChartsCollector(CHARTS_FIXTURE))

    assert "apple.music_top_songs" in result.counters["failed"]
    assert result.error is None
    assert not [record for record in read_dataset(tmp_path / "data", "chart_snapshots")
                if record["chart_id"] == "apple.music_top_songs"]


def test_blocked_chart_is_paused_and_charts_with_an_interval_wait(make_ctx, fake_web) -> None:
    fake_web.add(APPLE_URL, "Forbidden", status=403)
    fake_web.add(TRENDS_URL, _fixture("google_trends.xml"), content_type="application/rss+xml")
    _serve_wikimedia(fake_web, [FIRST_RUN - timedelta(days=n) for n in (1, 2, 3)])
    collector = ChartsCollector(CHARTS_FIXTURE)

    result, entry = _collect(make_ctx, CollectorEntry(first_run=FIRST_RUN), collector)
    assert result.counters["failed"]["apple.music_top_songs"].startswith("blocked")
    assert entry.cursor["apple.music_top_songs"]["blocked_until"] == "2026-11-27"

    fake_web.requested.clear()
    result, entry = _collect(make_ctx, entry, collector, started=RUN_STARTED + timedelta(days=1))
    assert APPLE_URL not in fake_web.requested
    assert TRENDS_URL not in fake_web.requested
    assert result.counters["charts_not_due"] == 2

    fake_web.requested.clear()
    _collect(make_ctx, entry, collector, started=RUN_STARTED + timedelta(days=2))
    assert TRENDS_URL in fake_web.requested


def test_all_charts_failing_is_an_error_and_removed_charts_are_not_read(make_ctx, fake_web) -> None:
    for day in (FIRST_RUN - timedelta(days=n) for n in (1, 2, 3)):
        fake_web.add(WIKI_URL.format(day=day), "error", status=500)

    result, _entry = _collect(make_ctx, CollectorEntry(first_run=FIRST_RUN), ChartsCollector(CHARTS_FIXTURE))

    assert result.error == "all 3 charts that were read failed"
    assert "wikimedia.removed_chart" not in result.cursor
    assert not any("en.wikipedia.org" in url for url in fake_web.requested)


def test_dry_run_writes_nothing(make_ctx, fake_web, tmp_path) -> None:
    fake_web.add(APPLE_URL, _apple(["A"]))

    _collect(make_ctx, CollectorEntry(first_run=FIRST_RUN), ChartsCollector(CHARTS_FIXTURE), dry_run=True)

    assert not (tmp_path / "data" / "chart_snapshots").exists()
