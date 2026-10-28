"""Tests of the feeds collector with a fake web and a fake annotator."""
import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from typing import Any, Optional
from xml.sax.saxutils import escape

import httpx
import pytest

from annotation import AnnotationInput, CallBudgetExceededError, clean_annotation
from collectors.base import CollectorSkipped, RunContext
from collectors.feeds import Candidate, FeedsCollector, interleave, looks_pop_culture, parse_feed, parse_news_sitemap
from conftest import RUN_STARTED, SOURCES_FIXTURE, read_dataset
from schema import ItemAnnotation
from settings import Settings
from sources import SourceHealthStore, load_config
from state import CollectorEntry
from urls import section_tokens
from test_annotation import label
from wikipedia import WIKIPEDIA_API_URL

SOURCES = SOURCES_FIXTURE
RECENT = RUN_STARTED - timedelta(hours=5)
MEDIAWIKI_ANSWER = {"batchcomplete": True, "query": {"pages": [
    {"ns": 0, "title": "Helene Fischer", "pageprops": {"wikibase_item": "Q1"}},
    {"ns": 0, "title": "Max Mustermann", "missing": True},
]}}


def rss(items: list[tuple[str, str, Optional[datetime], str]]) -> str:
    """Builds an RSS feed from (title, link, published, description) tuples."""
    entries = "".join(
        f"<item><title>{escape(title)}</title><link>{escape(link)}</link>"
        + (f"<pubDate>{format_datetime(published)}</pubDate>" if published else "")
        + f"<description>{escape(description)}</description></item>"
        for title, link, published, description in items
    )
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>{entries}</channel></rss>'


def news_sitemap(items: list[tuple[str, str, datetime, str]]) -> str:
    """Builds a Google News sitemap from (title, link, published, keywords) tuples."""
    urls = "".join(
        f"<url><loc>{escape(link)}</loc><news:news><news:publication><news:name>N</news:name>"
        f"<news:language>de</news:language></news:publication>"
        f"<news:publication_date>{published.isoformat()}</news:publication_date>"
        f"<news:title>{escape(title)}</news:title><news:keywords>{escape(keywords)}</news:keywords></news:news></url>"
        for title, link, published, keywords in items
    )
    return ('<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
            'xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">' + urls + "</urlset>")


class FakeAnnotator:
    """Annotator double that labels every item and records the inputs."""
    model = "fake-model"
    extraction_hash = "0123456789abcdef"

    def __init__(self, stop_after: Optional[int] = None) -> None:
        self.inputs: list[AnnotationInput] = []
        self.usage = type("Usage", (), {"calls": 0})()
        self._stop_after = stop_after

    def annotate(self, items: list[AnnotationInput]) -> list[Optional[ItemAnnotation]]:
        if self._stop_after is not None and len(self.inputs) >= self._stop_after:
            raise CallBudgetExceededError("MAX_GEMINI_CALLS_PER_RUN (1) reached")
        self.inputs.extend(items)
        return [clean_annotation(ItemAnnotation.model_validate(label(n)), item.kind, item.published)
                for n, item in enumerate(items, start=1)]


def collector(annotator: Any, config_path: Any = SOURCES) -> FeedsCollector:
    return FeedsCollector(annotator, config_path=config_path)


def setup_sources(fake_web: Any) -> None:
    fake_web.add("https://news.example/kultur/rss.xml", rss([
        ("Helene Fischer kündigt neues Album an", "https://news.example/kultur/fischer-1.html?utm_source=rss",
         RECENT, "Die Sängerin geht auf Tour."),
        ("Alter Artikel", "https://news.example/kultur/alt.html", RUN_STARTED - timedelta(days=10), "alt"),
    ]), content_type="application/rss+xml")
    fake_web.add("https://news.example/alles/rss.xml", rss([
        ("Bundestag beschließt Haushalt", "https://news.example/politik/haushalt.html", RECENT, "Der Etat steht."),
        ("Rapper wirbt für die Bundeswehr", "https://news.example/politik/rapper.html", RECENT, "Kampagne"),
    ]), content_type="application/rss+xml")
    fake_web.add("https://news.example/news-sitemap.xml", news_sitemap([
        ("Helene Fischer kündigt neues Album an", "https://news.example/kultur/fischer-1.html", RECENT, "Musik"),
        ("Bundesliga: Die Stars des Spieltags", "https://news.example/sport/bundesliga.html", RECENT, "Fußball"),
        ("Die besten Tipps für den Herbsturlaub", "https://news.example/reise/herbst.html", RECENT, "Urlaub"),
    ]), content_type="application/xml")
    fake_web.add_fixture("https://news.example/kultur/fischer-1.html", "pages/news_article.html")
    fake_web.add("https://music.example/feed/", rss([
        ("Neue Platte der Woche", "https://music.example/news/platte", RECENT, ""),
    ]), content_type="application/rss+xml")
    fake_web.add("https://games.example/feed/", rss([
        ("Games-Markt wächst um 6 Prozent", "https://games.example/presse/markt", RECENT, "Umsatz steigt"),
    ]), content_type="application/rss+xml")
    fake_web.add(WIKIPEDIA_API_URL, MEDIAWIKI_ANSWER)


def test_parse_feed_and_sitemap() -> None:
    outlet = load_config(SOURCES).outlets[0]
    feed = parse_feed(rss([("Titel", "https://news.example/a.html", RECENT, "<p>Teaser</p>")]).encode(),
                      outlet, "https://news.example/kultur/rss.xml")
    assert feed[0].title == "Titel" and feed[0].teaser == "Teaser" and feed[0].from_pop_feed
    assert feed[0].published_at == RECENT
    general = parse_feed(rss([("Titel", "https://news.example/a.html", RECENT, "")]).encode(),
                         outlet, "https://news.example/alles/rss.xml")
    assert not general[0].from_pop_feed

    sitemap, children = parse_news_sitemap(news_sitemap([("T", "https://news.example/x.html", RECENT,
                                                          "Musik, Tour")]).encode(),
                                           outlet, "https://news.example/news-sitemap.xml")
    assert children == [] and sitemap[0].keywords == ["Musik", "Tour"] and sitemap[0].channel == "news_sitemap"


def test_sitemap_with_entity_declarations_is_rejected() -> None:
    outlet = load_config(SOURCES).outlets[0]
    bomb = ('<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">]>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>&lol;</loc></url></urlset>')
    with pytest.raises(ValueError):
        parse_news_sitemap(bomb.encode(), outlet, "https://news.example/news-sitemap.xml")


def test_central_european_zone_abbreviations_are_not_read_as_utc() -> None:
    outlet = load_config(SOURCES).outlets[0]
    feed = ('<rss version="2.0"><channel><item><title>T</title><link>https://news.example/kultur/a</link>'
            "<pubDate>Mon, 26 Oct 2026 10:00:00 CET</pubDate></item></channel></rss>")

    candidate = parse_feed(feed.encode(), outlet, "https://news.example/kultur/rss.xml")[0]

    assert candidate.published_at == datetime(2026, 10, 26, 9, 0, tzinfo=timezone.utc)


def test_looks_pop_culture_filters_general_outlet_items_only() -> None:
    config = load_config(SOURCES)
    outlet, magazine, press = config.outlets[0], config.outlets[1], config.press_sources[0]

    def candidate(source: Any, url: str, title: str) -> Candidate:
        return Candidate(source=source, url=url, key=url, title=title, teaser="", published_at=None, channel="rss")

    assert looks_pop_culture(candidate(outlet, "https://news.example/kultur/x.html", "Irgendwas"))
    assert looks_pop_culture(candidate(outlet, "https://news.example/politik/x.html", "Rapper wirbt für die Bundeswehr"))
    assert not looks_pop_culture(candidate(outlet, "https://news.example/politik/x.html", "Bundestag beschließt Etat"))
    assert not looks_pop_culture(candidate(outlet, "https://news.example/sport/x.html", "Die Stars des Spieltags"))
    assert not looks_pop_culture(candidate(outlet, "https://news.example/panorama/x.html", "Mann mit 3 Promille"))
    assert not looks_pop_culture(candidate(outlet, "https://news.example/panorama/leute-aus-dem-zug-geschleudert-1.html",
                                           "Zugunglück in Spanien"))
    assert not looks_pop_culture(candidate(outlet, "https://news.example/panorama/x.html", "Polizei sichert den Tatort"))
    assert looks_pop_culture(candidate(outlet, "https://news.example/panorama/x.html", "„Tatort“ aus Münster"))
    assert looks_pop_culture(candidate(outlet, "https://news.example/panorama/x.html", "Tatort-Kommissarin hört auf"))
    assert looks_pop_culture(candidate(magazine, "https://music.example/news/x", "Neue Platte"))
    assert looks_pop_culture(candidate(press, "https://games.example/presse/x", "Jahresbilanz"))


def test_interleave_takes_turns() -> None:
    assert list(interleave([["a1", "a2", "a3"], ["b1"], ["c1", "c2"]])) == ["a1", "b1", "c1", "a2", "c2", "a3"]


def test_collect_writes_items_annotations_and_resolves_people(fake_web, make_ctx) -> None:
    setup_sources(fake_web)
    ctx = make_ctx()
    annotator = FakeAnnotator()

    result = collector(annotator).collect(ctx, CollectorEntry(first_run=ctx.today))

    news = read_dataset(ctx.settings.output_dir, "news_items")
    press = read_dataset(ctx.settings.output_dir, "press_releases")
    annotations = read_dataset(ctx.settings.output_dir, "text_annotations")
    assert sorted(item["title"] for item in news) == ["Die besten Tipps für den Herbsturlaub",
                                                      "Helene Fischer kündigt neues Album an",
                                                      "Neue Platte der Woche", "Rapper wirbt für die Bundeswehr"]
    assert [item["title"] for item in press] == ["Games-Markt wächst um 6 Prozent"]
    assert press[0]["actor_type"] == "industry_association" and press[0]["jurisdiction"] == "DE"
    assert len(annotations) == 5
    fischer = next(item for item in news if item["title"].startswith("Helene"))
    assert fischer["channel"] == "rss" and fischer["content_basis"] == "lead_text"
    assert fischer["outlet_focus"] is None and fischer["audience"] == "general"
    magazine = next(item for item in news if item["outlet_id"] == "music_magazine")
    assert magazine["outlet_focus"] == "Music" and magazine["audience"] == "youth"
    assert magazine["content_basis"] == "title_only"
    annotation = next(a for a in annotations if a["item_id"] == fischer["record_id"])
    assert annotation["people"] == [{"wikidata_qid": "Q1", "wikipedia_title": "Helene Fischer", "role": "Subject",
                                     "tone": "Positive"}]
    assert annotation["unresolved_person_count"] == 1
    assert annotation["works"] == [{"title": "Zeitreise", "kind": "Album", "role": "Subject", "tone": "Positive"}]
    assert annotation["products"] == [{"name": "Stanley Cup", "category": "Home and furniture",
                                       "role": "Recommended"}]
    assert annotation["stances"] == [{"target": "Bundeswehr and military", "stance": "Unfavorable",
                                      "holder": "Celebrity or influencer"}]
    assert annotation["family_forms"] == {"Married couple with children": "Positive"}
    assert annotation["upcoming_events"] == [{"event_type": "Concert or tour", "date": "2026-12-01"}]
    assert annotation["license"].startswith("Derived data of this project")
    assert result.counters["annotated"] == 5 and result.error is None
    assert result.counters["people_resolution_failed"] == 0
    holiday = next(item for item in news if item["title"].startswith("Die besten"))
    assert holiday["channel"] == "news_sitemap" and holiday["keywords"] == ["Urlaub"]
    press_input = next(item for item in annotator.inputs if item.kind == "press release")
    assert press_input.source == "Games Association (industry association)"
    assert sum(1 for url in fake_web.requested if url.startswith(WIKIPEDIA_API_URL)) == 1


def test_failed_people_resolution_counts_the_people_as_unresolved(fake_web, make_ctx) -> None:
    setup_sources(fake_web)
    fake_web.add(WIKIPEDIA_API_URL, "error", status=500)
    ctx = make_ctx()

    result = collector(FakeAnnotator()).collect(ctx, CollectorEntry(first_run=ctx.today))

    annotations = read_dataset(ctx.settings.output_dir, "text_annotations")
    assert all(a["people"] == [] and a["unresolved_person_count"] == 2 for a in annotations)
    assert result.counters["people_resolution_failed"] == 1
    assert result.error is None


def test_second_run_skips_known_items(fake_web, make_ctx) -> None:
    setup_sources(fake_web)
    ctx = make_ctx()
    collector(FakeAnnotator()).collect(ctx, CollectorEntry(first_run=ctx.today))
    annotator = FakeAnnotator()

    result = collector(annotator).collect(make_ctx(), CollectorEntry(first_run=ctx.today))

    assert annotator.inputs == []
    assert result.counters["items_selected"] == 0


def test_blocked_source_is_marked_and_skipped(fake_web, make_ctx) -> None:
    setup_sources(fake_web)
    fake_web.add("https://games.example/feed/", "Forbidden", status=403)
    ctx = make_ctx()

    collector(FakeAnnotator()).collect(ctx, CollectorEntry(first_run=ctx.today))

    health = SourceHealthStore.load(ctx.settings.state_dir)
    assert health.get("games_association").status == "blocked"
    assert not health.is_due("games_association", ctx.today + timedelta(days=30))
    assert read_dataset(ctx.settings.output_dir, "press_releases") == []


def test_tdm_reservation_excludes_the_source(fake_web, make_ctx) -> None:
    setup_sources(fake_web)
    fake_web.add("https://news.example/.well-known/tdmrep.json", [{"location": "/", "tdm-reservation": 1}])
    ctx = make_ctx()

    collector(FakeAnnotator()).collect(ctx, CollectorEntry(first_run=ctx.today))

    health = SourceHealthStore.load(ctx.settings.state_dir)
    assert health.get("general_daily").status == "tdm_reserved"
    news = read_dataset(ctx.settings.output_dir, "news_items")
    assert all(item["outlet_id"] != "general_daily" for item in news)


def test_budget_stop_writes_items_without_annotations(fake_web, make_ctx) -> None:
    setup_sources(fake_web)
    ctx = make_ctx()

    result = collector(FakeAnnotator(stop_after=0)).collect(ctx, CollectorEntry(first_run=ctx.today))

    assert len(read_dataset(ctx.settings.output_dir, "news_items")) == 4
    assert read_dataset(ctx.settings.output_dir, "text_annotations") == []
    assert result.counters["not_annotated"] == 5
    assert result.error is None


def test_cap_annotates_press_releases_first_and_stores_the_rest_without_labels(fake_web, make_ctx) -> None:
    setup_sources(fake_web)
    ctx = make_ctx(max_annotations_per_run=2)
    annotator = FakeAnnotator()

    result = collector(annotator).collect(ctx, CollectorEntry(first_run=ctx.today))

    assert [item.kind for item in annotator.inputs].count("press release") == 1
    assert len(annotator.inputs) == 2
    assert len(read_dataset(ctx.settings.output_dir, "news_items")) == 4
    assert len(read_dataset(ctx.settings.output_dir, "text_annotations")) == 2
    assert result.counters["items_selected"] == 2 and result.counters["beyond_cap"] == 3


def test_items_after_the_run_deadline_are_stored_without_labels(fake_web, tmp_path, monkeypatch) -> None:
    setup_sources(fake_web)
    now = [RUN_STARTED]
    serve = fake_web.handler

    def slow_page(request: httpx.Request) -> httpx.Response:
        # The first article page takes longer than the run deadline
        if request.url.path == "/kultur/fischer-1.html":
            now[0] += timedelta(minutes=30)
        return serve(request)

    monkeypatch.setattr(fake_web, "handler", slow_page)
    settings = replace(Settings(), output_dir=tmp_path / "data")
    ctx = RunContext(settings, fake_web.client(), RUN_STARTED, clock=lambda: now[0])
    annotator = FakeAnnotator()

    result = collector(annotator).collect(ctx, CollectorEntry(first_run=ctx.today))

    assert not result.complete
    assert result.counters["annotated"] + result.counters["not_annotated"] == 5
    assert len(read_dataset(ctx.settings.output_dir, "news_items")) == 4
    assert len(read_dataset(ctx.settings.output_dir, "press_releases")) == 1
    assert "https://news.example/reise/herbst.html" not in fake_web.requested


class ParallelAnnotator(FakeAnnotator):
    """Annotator double whose first two calls wait for each other, so that they must run at the same time."""

    def __init__(self) -> None:
        super().__init__()
        self._barrier = threading.Barrier(2, timeout=5)
        self._lock = threading.Lock()
        self._calls = 0

    def annotate(self, items: list[AnnotationInput]) -> list[Optional[ItemAnnotation]]:
        with self._lock:
            self._calls += 1
            call = self._calls
        if call <= 2:
            self._barrier.wait()
        return super().annotate(items)


def test_batches_are_annotated_in_parallel_and_written_in_order(fake_web, make_ctx) -> None:
    titles = [f"Games-Studie {n}" for n in range(25)]
    fake_web.add("https://games.example/feed/", rss([
        (title, f"https://games.example/presse/{n}", RECENT - timedelta(minutes=n), "Text")
        for n, title in enumerate(titles)
    ]), content_type="application/rss+xml")
    fake_web.add(WIKIPEDIA_API_URL, MEDIAWIKI_ANSWER)
    ctx = make_ctx(gemini_concurrency=3)

    result = collector(ParallelAnnotator()).collect(ctx, CollectorEntry(first_run=ctx.today))

    assert result.counters["annotated"] == 25
    assert [item["title"] for item in read_dataset(ctx.settings.output_dir, "press_releases")] == titles
    assert len(read_dataset(ctx.settings.output_dir, "text_annotations")) == 25
    # The names of a run are resolved once and then taken from the cache
    assert sum(1 for url in fake_web.requested if url.startswith(WIKIPEDIA_API_URL)) == 1


def test_removed_sources_are_not_read(fake_web, make_ctx) -> None:
    setup_sources(fake_web)
    ctx = make_ctx()

    collector(FakeAnnotator()).collect(ctx, CollectorEntry(first_run=ctx.today))

    assert "https://gone.example/feed/" not in fake_web.requested


def test_dry_run_reads_sources_without_writing(fake_web, make_ctx) -> None:
    setup_sources(fake_web)
    ctx = make_ctx(dry_run=True)

    result = collector(None).collect(ctx, CollectorEntry(first_run=ctx.today))

    assert result.counters["items_selected"] == 5
    assert not (ctx.settings.output_dir / "news_items").exists()
    assert "https://news.example/kultur/fischer-1.html" not in fake_web.requested


def test_missing_annotator_skips_the_collector(make_ctx) -> None:
    with pytest.raises(CollectorSkipped):
        collector(None).collect(make_ctx(), CollectorEntry(first_run=RUN_STARTED.date()))


def test_section_tokens_ignore_the_words_of_slugs() -> None:
    assert section_tokens("https://www.bild.de/unterhaltung/leute/star-heiratet-heimlich-123.bild.html") == {
        "unterhaltung", "leute"}
    assert section_tokens("https://www.focus.de/kultur/kino_tv/neue-serie-startet_id_123.html") == {
        "kultur", "kino", "tv"}
    assert "leben" not in section_tokens("https://taz.de/Wie-wir-leben-wollen/!6123456/")
