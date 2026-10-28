"""Tests of the monthly source review with a fake web."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from xml.sax.saxutils import escape

from conftest import CHARTS_FIXTURE, ENTITIES_FIXTURE, FIXTURES_DIR, SOURCES_FIXTURE
from settings import Settings
from source_review import (
    Change,
    NewEntry,
    apply_changes,
    build_report,
    evaluate_candidate,
    feed_links,
    probe,
    reuse_excerpts,
    review_source,
    run_review,
)
from sources import SourceHealth, load_config
from wikipedia import WIKIPEDIA_API_URL

NOW = datetime(2026, 10, 28, 6, 17, tzinfo=timezone.utc)
RECENT = NOW - timedelta(days=2)


def rss(titles: list[str], published: datetime = RECENT) -> str:
    items = "".join(f"<item><title>{escape(title)}</title><link>https://x.example/{n}</link>"
                    f"<pubDate>{format_datetime(published)}</pubDate></item>" for n, title in enumerate(titles))
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>{items}</channel></rss>'


def serve_active_sources(fake_web) -> None:
    fake_web.add("https://news.example/kultur/rss.xml", rss(["Konzert"]), content_type="application/rss+xml")
    fake_web.add("https://news.example/alles/rss.xml", rss(["Alt"], NOW - timedelta(days=400)),
                 content_type="application/rss+xml")
    fake_web.add("https://news.example/news-sitemap.xml", "", status=301,
                 headers={"location": "https://news.example/sitemap-neu.xml"})
    fake_web.add("https://news.example/sitemap-neu.xml",
                 '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>',
                 content_type="application/xml")
    fake_web.add("https://news.example/robots.txt", "# Text and data mining reserved (§ 44b UrhG)\nUser-agent: *\n",
                 content_type="text/plain")
    fake_web.add("https://music.example/feed/", "", status=301, headers={"location": "https://music.example/rss/"})
    fake_web.add("https://music.example/rss/", rss(["Album"]), content_type="application/rss+xml")
    fake_web.add("https://games.example/feed/", rss(["Studie"]), content_type="application/rss+xml")


def serve_candidate(fake_web, imprint: str) -> None:
    fake_web.add("https://candidate.example/", '<html><head><link rel="alternate" type="application/rss+xml" '
                 'href="/feed/"></head><body><a href="/impressum/">Impressum</a></body></html>')
    fake_web.add("https://candidate.example/feed/", rss(["Promi heiratet", "Star trennt sich", "Neue Show"]),
                 content_type="application/rss+xml")
    fake_web.add("https://candidate.example/impressum/", f"<html><body><p>{imprint}</p></body></html>")


def test_review_source_proposes_feed_and_page_fetching_changes(fake_web) -> None:
    serve_active_sources(fake_web)
    outlet = load_config(SOURCES_FIXTURE).outlets[0]

    changes, notes = review_source(fake_web.client(), outlet, SourceHealth(), items=5, now=NOW)

    actions = {(change.action, change.old) for change in changes}
    assert ("remove_feed", "https://news.example/alles/rss.xml") in actions
    assert ("set_fetch_pages", None) in actions
    assert any("has no items" in note for note in notes)


def test_review_source_follows_permanent_redirects_and_removes_blocked_sources(fake_web) -> None:
    serve_active_sources(fake_web)
    config = load_config(SOURCES_FIXTURE)
    magazine = config.outlets[1]

    changes, _notes = review_source(fake_web.client(), magazine, SourceHealth(), items=5, now=NOW)
    assert [(c.action, c.old, c.new) for c in changes] == [
        ("replace_feed", "https://music.example/feed/", "https://music.example/rss/")]

    blocked = SourceHealth(status="blocked", last_error="HTTP 403")
    changes, _notes = review_source(fake_web.client(), magazine, blocked, items=5, now=NOW)
    assert changes[0].action == "remove_source" and "HTTP 403" in changes[0].reason


def test_source_without_items_for_90_days_is_removed(fake_web) -> None:
    serve_active_sources(fake_web)
    press = load_config(SOURCES_FIXTURE).press_sources[0]

    changes, _notes = review_source(fake_web.client(), press, SourceHealth(), items=0, now=NOW + timedelta(days=95))

    assert changes[0].action == "remove_source" and "no pop culture items" in changes[0].reason


def test_candidate_is_admitted_with_the_clauses_of_its_imprint(fake_web) -> None:
    serve_candidate(fake_web, "Die Inhalte dürfen ohne vorherige schriftliche Zustimmung nicht genutzt werden. "
                              "Wir behalten uns das Text- und Data-Mining nach § 44b UrhG vor.")
    candidate = load_config(SOURCES_FIXTURE).excluded[0]

    changes, notes, excerpts = evaluate_candidate(fake_web.client(), candidate, NOW)

    assert [change.action for change in changes] == ["drop_exclusion", "add_source"]
    entry = changes[1].entry
    assert entry is not None and entry.table == "outlet"
    assert entry.fields["feeds"] == ["https://candidate.example/feed/"]
    assert entry.fields["fetch_pages"] is False
    assert entry.fields["focus"] == "Celebrities and royals"
    assert excerpts and "Zustimmung" in excerpts[0]
    assert notes == []


def test_candidate_without_a_feed_gets_a_new_status_and_unreachable_ones_stay(fake_web) -> None:
    fake_web.add("https://candidate.example/", "<html><body>Keine Feeds</body></html>")
    candidate = load_config(SOURCES_FIXTURE).excluded[0]

    changes, _notes, _excerpts = evaluate_candidate(fake_web.client(), candidate, NOW)
    assert [(c.action, c.status) for c in changes] == [("update_exclusion", "no_feed")]

    no_feed = replace(candidate, status="no_feed")
    changes, notes, _excerpts = evaluate_candidate(fake_web.client(), no_feed, NOW)
    assert changes == [] and "still no_feed" in notes[0]

    fake_web.fail("https://candidate.example/")
    changes, notes, _excerpts = evaluate_candidate(fake_web.client(), candidate, NOW)
    assert changes == [] and "not checked" in notes[0]


def test_apply_changes_edits_entries_and_keeps_the_rest_of_the_text() -> None:
    text = SOURCES_FIXTURE.read_text(encoding="utf-8")
    config = load_config(SOURCES_FIXTURE)
    new = NewEntry("outlet", {"id": "candidate_outlet", "name": "Candidate Outlet",
                              "homepage": "https://candidate.example/", "outlet_type": "celebrity_magazine",
                              "owner_group": "Candidate Media", "focus": "Celebrities and royals", "audience": None,
                              "paywall_model": "free", "feeds": ["https://candidate.example/feed/"],
                              "fetch_pages": False, "added": NOW.date()}, "admitted")
    changes = [
        Change("remove_feed", "general_daily", "newest item from 2025-09-01", old="https://news.example/alles/rss.xml"),
        Change("set_fetch_pages", "general_daily", "TDM statement"),
        Change("replace_feed", "music_magazine", "moved permanently", old="https://music.example/feed/",
               new="https://music.example/rss/"),
        Change("remove_source", "games_association", "no working feed"),
        Change("drop_exclusion", "candidate_outlet", "admitted"),
        Change("add_source", "candidate_outlet", "1 feeds", entry=new),
        Change("update_exclusion", "blocked_outlet", "homepage blocked again", status="blocked"),
    ]

    changed = apply_changes(text, config, changes, NOW.date())
    result = load_config_text(changed)

    outlets = {outlet.id: outlet for outlet in result.outlets}
    assert outlets["general_daily"].feeds == ("https://news.example/kultur/rss.xml",)
    assert outlets["general_daily"].fetch_pages is False
    assert outlets["music_magazine"].feeds == ("https://music.example/rss/",)
    assert outlets["candidate_outlet"].focus == "Celebrities and royals"
    assert result.press_sources[0].removed == NOW.date()
    assert [entry.id for entry in result.excluded] == ["blocked_outlet"]
    assert result.excluded[0].reason == "homepage blocked again"
    assert "# source review 2026-10-28: feed https://news.example/alles/rss.xml removed" in changed
    assert "# Test configuration of the feeds collector" in changed
    assert apply_changes(text, config, [], NOW.date()) == text


def load_config_text(text: str):
    from source_review import validate_config_text
    return validate_config_text(text)


def test_reuse_excerpts_and_feed_links() -> None:
    excerpts = reuse_excerpts("Impressum. " * 40 + "Die Aufnahme in Online-Dienste ist nur mit Zustimmung erlaubt.")
    assert len(excerpts) == 1 and "Online-Dienste" in excerpts[0]
    links = feed_links('<link rel="alternate" type="application/rss+xml" href="/rss.xml">'
                       '<a href="/comments/feed/">x</a><a href="/news/feed/">y</a>', "https://a.example/")
    assert links == ["https://a.example/rss.xml", "https://a.example/news/feed/"]


def test_run_review_checks_sources_candidates_charts_entities_and_providers(fake_web, tmp_path) -> None:
    serve_active_sources(fake_web)
    serve_candidate(fake_web, "Alle Rechte vorbehalten.")
    fake_web.add("https://rss.marketingtools.apple.com/api/v2/de/music/most-played/100/songs.json",
                 (FIXTURES_DIR / "charts" / "apple_songs.json").read_bytes(), content_type="application/json")
    fake_web.add("https://policies.google.com/terms", "<html><body>Bedingungen</body></html>")
    fake_web.add(WIKIPEDIA_API_URL, {"query": {"redirects": [{"from": "Knossi", "to": "Jens Knossalla"}],
                                               "pages": [{"title": "Helene Fischer"}, {"title": "Bundeswehr"},
                                                         {"title": "Jens Knossalla"},
                                                         {"title": "Gibt es nicht (Fernsehsendung)", "missing": True}]}})
    config_path = tmp_path / "sources.toml"
    config_path.write_text(SOURCES_FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    settings = replace(Settings(), output_dir=tmp_path / "data")

    text, result = run_review(settings, config_path, fake_web.client(), NOW, charts_path=CHARTS_FIXTURE,
                              entities_path=ENTITIES_FIXTURE)
    report = build_report(result, load_config(config_path), NOW)

    assert result.checked_sources == 3 and result.rechecked == 1
    assert "candidate_outlet" in {outlet.id for outlet in load_config_text(text).outlets}
    assert "### Additions" in report and "no clause on reuse found in the imprint" in report
    assert "| `apple.music_top_songs` | ok | 3 | Top-Titel | Housewife;" in report
    assert "| `google_trends.trending_searches_de` | failed:" in report
    assert "| `google_trends` | https://policies.google.com/terms | HTTP 200 |" in report
    assert "`knossi`: 'Knossi' redirects to 'Jens Knossalla'" in report
    assert "`unknown_show`: no German Wikipedia article" in report


def test_probe_describes_feeds_and_pages(fake_web) -> None:
    serve_candidate(fake_web, "Die Inhalte dürfen nicht in Datenbanken aufgenommen werden.")

    report = probe(fake_web.client(), ["https://candidate.example/", "https://candidate.example/feed/"], NOW)

    assert "- **robots.txt:** allows the URL (no robots.txt)" in report
    assert "- **Feed links:** https://candidate.example/feed/" in report
    assert "Datenbanken" in report
    assert "- **Feed:** 3 items, newest 48 h old" in report
