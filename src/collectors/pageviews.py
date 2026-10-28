"""
Collects the daily views of the German Wikipedia articles of the registered entities.

The entity registry (``config/entities.toml``) names the article of every
entity. The collector resolves the titles through the MediaWiki API
(following redirects, so that a renamed article is still found) and fetches
for every entity the days since the last stored day from the Wikimedia
Pageviews API (CC0). A new entity gets the days from HISTORY_START.

The pageviews are the stable backbone of the dataset: the API keeps the views
of every day since July 2015, so the series do not depend on the day of the
run. The collector runs once a week. At the run deadline it stops; the
remaining articles follow in the next run.
"""
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

from collectors.base import Collector, CollectorResult, RunContext, series_start
from entities import CONFIG_PATH, Entity, active_entities
from schema import PageviewRecord
from state import CollectorEntry, is_backfilled
from web import FetchError, NotFoundError
from wikipedia import WIKIPEDIA_PROJECT, Article, resolve_titles

logger = logging.getLogger(__name__)

DATASET = "wikipedia_pageviews"
SOURCE_ID = "wikimedia_pageviews"
LICENSE = "CC0-1.0"
PAGEVIEWS_URL = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/de.wikipedia.org/"
                 "all-access/user/{title}/daily/{start}/{end}")
# First day fetched for a new entity: the first day with data in the Wikimedia Pageviews API
HISTORY_START = date(2015, 7, 1)
EMIT_EVERY = 100


def _article_url(title: str, start: date, end: date) -> str:
    """Builds the Pageviews API URL of an article and a day range."""
    encoded = quote(title.replace(" ", "_"), safe="")
    return PAGEVIEWS_URL.format(title=encoded, start=f"{start:%Y%m%d}", end=f"{end:%Y%m%d}")


def fetch_daily_views(ctx: RunContext, title: str, start: date, end: date) -> dict[date, int]:
    """Fetches the daily views of an article.

    Args:
        ctx: The run context.
        title: Article title.
        start: First day.
        end: Last day.

    Returns:
        dict[date, int]: Views by day; days without data are missing.
    """
    try:
        data = ctx.web.get(_article_url(title, start, end), respect_robots=False).json()
    except NotFoundError:
        return {}
    views: dict[date, int] = {}
    for item in data.get("items", []):
        timestamp = str(item.get("timestamp", ""))
        try:
            day = datetime.strptime(timestamp[:8], "%Y%m%d").date()
        except ValueError:
            continue
        if isinstance(item.get("views"), int):
            views[day] = item["views"]
    return views


class PageviewsCollector(Collector):
    """Writes the new daily views of the articles of the registered entities."""
    name = "wikipedia_pageviews"
    datasets = (DATASET,)
    min_days_between_runs = 6

    def __init__(self, registry_path: Path = CONFIG_PATH) -> None:
        """Initializes the collector.

        Args:
            registry_path: The entity registry.
        """
        self._registry_path = registry_path

    def collect(self, ctx: RunContext, entry: CollectorEntry) -> CollectorResult:
        """Fetches the days since the last stored day for every entity and emits one record per entity.

        Args:
            ctx: The run context.
            entry: Run history of this collector.

        Returns:
            CollectorResult: Counters with the entities whose article was not found; the progress per
            entity is kept in the seen store.
        """
        entities = active_entities(self._registry_path)
        resolved: dict[str, Article] = resolve_titles(ctx.web, [entity.wikipedia_title for entity in entities])
        seen = ctx.seen(DATASET)
        end = ctx.today - timedelta(days=1)
        records: list[PageviewRecord] = []
        counters: dict[str, Any] = {"entities": len(entities), "records": 0, "days": 0, "failed": 0}
        unresolved = [entity.id for entity in entities if entity.wikipedia_title not in resolved]
        if unresolved:
            logger.warning("No German Wikipedia article for %d entities: %s", len(unresolved), ", ".join(unresolved))
            counters["unresolved"] = unresolved
        complete = True
        targets: list[tuple[Entity, Article]] = [(entity, resolved[entity.wikipedia_title]) for entity in entities
                                                 if entity.wikipedia_title in resolved]
        for position, (entity, article) in enumerate(targets):
            if ctx.past_deadline():
                counters["deferred"] = len(targets) - position
                complete = False
                break
            start = series_start(seen.get(entity.id), HISTORY_START)
            if start > end:
                continue
            try:
                views = fetch_daily_views(ctx, article.title, start, end)
            except FetchError as e:
                logger.warning("Pageviews of %s failed: %s", article.title, e)
                counters["failed"] += 1
                continue
            if not views:
                continue
            first, last = min(views), max(views)
            revision = seen.mark(entity.id, article.title, ctx.today, data={"last_day": last.isoformat()})
            records.append(PageviewRecord(
                **ctx.envelope(
                    DATASET, f"pageviews:{entity.id}:{first.isoformat()}:{last.isoformat()}", revision=revision,
                    source_id=SOURCE_ID, source_url=_article_url(article.title, first, last), license=LICENSE,
                    backfilled=is_backfilled(first, entry.first_run),
                ),
                entity_id=entity.id, entity_type=entity.entity_type, domain=entity.domain,
                wikipedia_project=WIKIPEDIA_PROJECT, wikipedia_title=article.title,
                wikidata_qid=article.wikidata_qid, daily_views=dict(sorted(views.items())),
            ))
            counters["days"] += len(views)
            if len(records) >= EMIT_EVERY:
                ctx.emit(DATASET, records)
                counters["records"] += len(records)
                records = []
        ctx.emit(DATASET, records)
        counters["records"] += len(records)
        logger.info("Pageviews: %d records with %d days for %d entities.", counters["records"], counters["days"],
                    len(targets))
        return CollectorResult(counters=counters, complete=complete)
