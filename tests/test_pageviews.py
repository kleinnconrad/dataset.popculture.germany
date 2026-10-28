"""Tests of the Wikipedia pageviews of the registered entities with a fake web."""
from datetime import timedelta

from collectors.pageviews import PageviewsCollector
from conftest import ENTITIES_FIXTURE, RUN_STARTED, read_dataset
from state import CollectorEntry
from wikipedia import WIKIPEDIA_API_URL

BASE = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/de.wikipedia.org/all-access/user"


def mediawiki_answer() -> dict:
    return {"query": {
        "redirects": [{"from": "Knossi", "to": "Jens Knossalla"}],
        "pages": [{"title": "Helene Fischer", "pageprops": {"wikibase_item": "Q1"}},
                  {"title": "Bundeswehr", "pageprops": {"wikibase_item": "Q2"}},
                  {"title": "Jens Knossalla", "pageprops": {"wikibase_item": "Q3"}},
                  {"title": "Gibt es nicht (Fernsehsendung)", "missing": True}],
    }}


def views(days: list[str]) -> dict:
    return {"items": [{"timestamp": day.replace("-", "") + "00", "views": 100 + index}
                      for index, day in enumerate(days)]}


def test_collector_fetches_the_days_since_the_last_stored_day(make_ctx, fake_web, tmp_path) -> None:
    fake_web.add(WIKIPEDIA_API_URL, mediawiki_answer())
    fake_web.add(f"{BASE}/Helene_Fischer/daily/20150701/20261027", views(["2026-10-26", "2026-10-27"]))
    fake_web.add(f"{BASE}/Bundeswehr/daily/20150701/20261027", views(["2026-10-27"]))
    fake_web.add(f"{BASE}/Jens_Knossalla/daily/20150701/20261027", views(["2026-10-27"]))
    ctx = make_ctx()
    entry = CollectorEntry(first_run=ctx.today)

    result = PageviewsCollector(ENTITIES_FIXTURE).collect(ctx, entry)

    assert result.counters["records"] == 3 and result.counters["unresolved"] == ["unknown_show"]
    records = {record["entity_id"]: record for record in read_dataset(tmp_path / "data", "wikipedia_pageviews")}
    assert records["helene_fischer"]["daily_views"] == {"2026-10-26": 100, "2026-10-27": 101}
    assert records["helene_fischer"]["wikidata_qid"] == "Q1"
    assert records["knossi"]["wikipedia_title"] == "Jens Knossalla"
    assert records["bundeswehr"]["domain"] == "Politics and society"
    assert records["helene_fischer"]["backfilled"] is False
    assert "old_entity" not in records

    # A week later only the days after the last stored day are requested
    fake_web.requested.clear()
    fake_web.add(f"{BASE}/Helene_Fischer/daily/20261028/20261103", views(["2026-10-28"]))
    later = make_ctx(started=RUN_STARTED + timedelta(days=7))
    PageviewsCollector(ENTITIES_FIXTURE).collect(later, entry)
    assert any("Helene_Fischer/daily/20261028/20261103" in url for url in fake_web.requested)
