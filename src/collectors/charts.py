"""
Stores the charts and rankings of ``config/charts.toml`` as snapshots in ``chart_snapshots``.

Most charts are volatile: the Apple charts of music, podcasts, apps and
books and the trending searches of Google show only their current state and
overwrite it with every update. The collector reads every chart that is due, parses it into
ranked entries and compares their hash with the previous observation. Only a
changed list writes a record, so the records of a chart are the history of
what it showed, observed at the time of the run (``observed_at``).

Two periods are handled:
- ``live``: the current list, dated by the day of observation. A change later
  on the same day (e.g. seen by a manual run) writes the next revision of that
  day's record.
- ``day``: the list of a past day (the most-read Wikipedia articles). Every
  day since the last stored day is fetched; a new chart starts
  ``history_days`` days before the run. A day that the source reports as
  missing is tried again in later runs for MISSING_RETRY_DAYS days. A backfill
  run fetches the days again that an older version of the parser read
  (PARSER_VERSIONS); a list that changes with the new version is stored as the
  next revision of its day.

Live charts are read with conditional requests (ETag, Last-Modified), so that
an unchanged list is not downloaded again. A chart that fails is counted and
listed in the run log; the other charts continue. A chart whose host refuses
automated requests is not requested again for BLOCKED_RETRY_DAYS days.
"""
import json
import logging
import re
import xml.etree.ElementTree as ElementTree
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

from defusedxml import ElementTree as SafeElementTree

from chart_catalog import CONFIG_PATH, Chart, Provider, load_chart_config
from collectors.base import Collector, CollectorResult, RunContext
from content import clean_text, parse_datetime
from schema import ChartEntry, ChartSnapshotRecord
from state import CollectorEntry, content_hash, is_backfilled
from web import BlockedError, FetchError, NotFoundError

logger = logging.getLogger(__name__)

DATASET = "chart_snapshots"
# Key of the seen store of a live chart: hash of its latest list, with the day and revision of its record
LATEST_KEY = "latest"
# Key of the seen store of a daily list: the last day that was fetched
DAYS_KEY = "days"
REQUEST_TIMEOUT_S = 120.0
BLOCKED_RETRY_DAYS = 30
# Days of a daily list fetched per run; a backfill run fetches up to BACKFILL_DAYS days back
MAX_DAYS_PER_RUN = 60
BACKFILL_DAYS = 365
# A daily list that is missing for one of the last days is not published yet and is tried again in the next run
PUBLICATION_DELAY_DAYS = 3
# An older day that the source reports as missing (HTTP 404, also transiently) is tried again in later runs
# until it is this many days old
MISSING_RETRY_DAYS = 30
# Version of the parser of a daily format; a backfill run fetches the days that an older version parsed again,
# and a list that changes with the new version is stored as the next revision of its day
PARSER_VERSIONS = {"wikimedia_top": 2, "wikimedia_top_country": 2}
MAX_TITLE_CHARS = 300
MAX_DETAIL_CHARS = 300
MAX_FAILURES_LISTED = 20
# Pages of a Wikipedia edition that are not articles. A namespace prefix is followed by the title without a space
# ("Spezial:Suche", "Benutzer_Diskussion:X", "Speciale:Ricerca"); article titles with a colon have a space after it
# ("Star_Wars:_Episode_I") or start with a digit ("16:9"). Main pages without a prefix are listed by name, technical
# entries are recognised by their file extension (wiki.phtml).
WIKIPEDIA_NAMESPACE_PATTERN = re.compile(r"^[^\W\d_][^:]*:[^_\s]")
WIKIPEDIA_MAIN_PAGES = frozenset({
    "-", "Main_Page", "Hauptseite", "Заглавная_страница", "Pagina_principale", "Hoofdpagina", "Anasayfa",
    "Головна_сторінка", "Hlavní_strana", "メインページ",
})
WIKIPEDIA_TECHNICAL_PATTERN = re.compile(r"\.(?:php\d?|phtml|html?)$", re.IGNORECASE)
# Projects of the list across editions that are kept; other Wikimedia projects (Wiktionary, Commons) are not
# Wikipedia articles
WIKIPEDIA_PROJECT_SUFFIX = ".wikipedia"
# Approximate search volumes of Google Trends, e.g. "2000+", "20.000+", "50K+", "1 Mio.+"
TRAFFIC_PATTERN = re.compile(r"([\d.,\s]+)\s*(k|tsd\.?|m|mio\.?)?\s*\+?", re.IGNORECASE)
TRAFFIC_FACTORS = {"k": 1_000, "tsd": 1_000, "m": 1_000_000, "mio": 1_000_000}


class ChartError(Exception):
    """The content cannot be read as the configured chart."""


@dataclass
class ChartList:
    """One list as read from a chart source.

    Attributes:
        entries (list[ChartEntry]): The positions in order of rank.
        chart_date (Optional[date]): Day the list refers to; None for a live list,
            which is dated by the day of observation.
        title (Optional[str]): Title of the list at the source, checked against ``expect``.
        source_updated (Optional[datetime]): Update time stated by the source.
    """
    entries: list[ChartEntry]
    chart_date: Optional[date] = None
    title: Optional[str] = None
    source_updated: Optional[datetime] = None


def _text(value: Any, limit: int = MAX_TITLE_CHARS) -> Optional[str]:
    """Cleans a text value of a source; empty values become None."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = str(value)
    cleaned = clean_text(value, limit)
    return cleaned or None


def _details(values: dict[str, Any]) -> dict[str, str]:
    """Keeps the non-empty values of a mapping as cleaned texts."""
    details = {}
    for key, value in values.items():
        text = _text(value, MAX_DETAIL_CHARS)
        if text is not None:
            details[key] = text
    return details


def make_entry(rank: int, title: Any, *, creator: Any = None, item_id: Any = None, url: Any = None,
               genres: Optional[list[Any]] = None, release_date: Any = None, metric: Optional[float] = None,
               details: Optional[dict[str, Any]] = None) -> Optional[ChartEntry]:
    """Builds a chart entry from the values of a source.

    Args:
        rank: Position in the list.
        title: Title of the entry.
        creator: Artist, author, developer or channel.
        item_id: Id at the source.
        url: URL at the source.
        genres: Genres or categories.
        release_date: Release date; the first ten characters are kept (YYYY-MM-DD).
        metric: Value that the source ranks by or states.
        details: Further values.

    Returns:
        Optional[ChartEntry]: The entry, or None if the title is empty.
    """
    clean_title = _text(title)
    if clean_title is None:
        return None
    release = _text(release_date, 40)
    return ChartEntry(
        rank=rank, title=clean_title, creator=_text(creator), item_id=_text(item_id, 80), url=_text(url, 500),
        genres=[genre for genre in (_text(value, 80) for value in genres or []) if genre],
        release_date=release[:10] if release and re.match(r"\d{4}-\d{2}-\d{2}", release) else None,
        metric=metric, details=_details(details or {}),
    )


def _ranked(entries: list[Optional[ChartEntry]]) -> list[ChartEntry]:
    """Drops empty entries and numbers the others from 1 in their order."""
    kept = [entry for entry in entries if entry is not None]
    return [entry.model_copy(update={"rank": rank}) for rank, entry in enumerate(kept, start=1)]


def _json(content: bytes) -> Any:
    """Parses a JSON document.

    Raises:
        ChartError: If the content is not JSON.
    """
    try:
        return json.loads(content)
    except ValueError as e:
        raise ChartError(f"not JSON: {e}") from e


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def parse_apple_rss(content: bytes, chart: Chart) -> list[ChartList]:
    """Parses a feed of the Apple RSS generator (``rss.marketingtools.apple.com``, version 2, JSON).

    Args:
        content: The JSON document.
        chart: The chart.

    Returns:
        list[ChartList]: One live list.

    Raises:
        ChartError: If the document has no feed with results.
    """
    data = _json(content)
    feed = data.get("feed") if isinstance(data, dict) else None
    if not isinstance(feed, dict) or not isinstance(feed.get("results"), list):
        raise ChartError("no feed with results")
    entries = []
    for result in feed["results"]:
        if not isinstance(result, dict):
            continue
        genres = [genre.get("name") for genre in result.get("genres") or [] if isinstance(genre, dict)]
        entries.append(make_entry(
            0, result.get("name"), creator=result.get("artistName"), item_id=result.get("id"), url=result.get("url"),
            genres=genres, release_date=result.get("releaseDate"),
            details={"content_advisory": result.get("contentAdvisoryRating")},
        ))
    return [ChartList(_ranked(entries), title=_text(feed.get("title")),
                      source_updated=parse_datetime(feed.get("updated")))]


def parse_traffic(value: Optional[str]) -> Optional[float]:
    """Parses an approximate search volume of Google Trends.

    Args:
        value: E.g. "2000+", "20.000+", "50K+" or "1 Mio.+".

    Returns:
        Optional[float]: The lower bound of the volume, or None if it cannot be read.
    """
    if not value:
        return None
    match = TRAFFIC_PATTERN.fullmatch(value.strip())
    if not match:
        return None
    digits = re.sub(r"[.,\s]", "", match.group(1))
    if not digits.isdigit():
        return None
    suffix = (match.group(2) or "").lower().rstrip(".")
    return float(int(digits) * TRAFFIC_FACTORS.get(suffix, 1))


def _local(tag: str) -> str:
    """Returns the name of an XML element without its namespace."""
    return tag.rsplit("}", 1)[-1]


def parse_google_trends_rss(content: bytes, chart: Chart) -> list[ChartList]:
    """Parses the RSS feed of the trending searches of Google Trends.

    Every item is a search term with its approximate search volume and the
    news articles that Google links to it; the first article is kept as context.

    Args:
        content: The RSS document.
        chart: The chart.

    Returns:
        list[ChartList]: One live list in the order of the feed.

    Raises:
        ChartError: If the document is not an RSS feed.
    """
    try:
        root = SafeElementTree.fromstring(content)
    except ElementTree.ParseError as e:
        raise ChartError(f"invalid RSS: {e}") from e
    channel = root.find("channel")
    if channel is None:
        raise ChartError("no RSS channel")
    entries = []
    for item in channel.findall("item"):
        values: dict[str, str] = {}
        news: list[dict[str, str]] = []
        for child in item:
            name = _local(child.tag)
            if name == "news_item":
                news.append({_local(part.tag): (part.text or "").strip() for part in child})
            elif child.text and child.text.strip():
                values.setdefault(name, child.text.strip())
        first_news = news[0] if news else {}
        started = parse_datetime(values.get("pubDate"))
        entries.append(make_entry(
            0, values.get("title"), metric=parse_traffic(values.get("approx_traffic")),
            details={"approx_traffic": values.get("approx_traffic"),
                     "started_at": started.isoformat() if started else None,
                     "news_title": first_news.get("news_item_title"),
                     "news_source": first_news.get("news_item_source"),
                     "news_count": len(news) or None},
        ))
    return [ChartList(_ranked(entries), title=_text(channel.findtext("title")))]


def _wikipedia_url(project: str, article: str) -> str:
    """Returns the URL of an article of a Wikipedia edition, e.g. project de.wikipedia."""
    return f"https://{project}.org/wiki/{quote(article, safe='')}"


def _is_article(article: str) -> bool:
    """Tells whether a page title of the Wikimedia top lists (with underscores) is an article."""
    return (article not in WIKIPEDIA_MAIN_PAGES and not WIKIPEDIA_NAMESPACE_PATTERN.match(article)
            and not WIKIPEDIA_TECHNICAL_PATTERN.search(article))


def _wikimedia_day(item: dict[str, Any]) -> Optional[date]:
    """Returns the day of an item of the Wikimedia top lists."""
    try:
        return date(int(item["year"]), int(item["month"]), int(item["day"]))
    except (KeyError, TypeError, ValueError):
        return None


def parse_wikimedia_top(content: bytes, chart: Chart) -> list[ChartList]:
    """Parses the most-viewed articles of a Wikipedia edition on a day (Wikimedia REST API).

    Main pages, pages of other namespaces (special, talk, user and project pages, files, portals)
    and technical entries are left out; the other articles are numbered from 1, and their rank in
    the source is kept in the details.

    Args:
        content: The JSON document.
        chart: The chart; ``options.project`` names the edition (default de.wikipedia).

    Returns:
        list[ChartList]: One list per day of the response.

    Raises:
        ChartError: If the document has no items.
    """
    data = _json(content)
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list) or not items:
        raise ChartError("no items")
    project = str(chart.options.get("project", "de.wikipedia"))
    lists = []
    for item in items:
        day = _wikimedia_day(item) if isinstance(item, dict) else None
        if day is None:
            continue
        entries = []
        for article in item.get("articles") or []:
            title = str(article.get("article", "")) if isinstance(article, dict) else ""
            if not title or not _is_article(title):
                continue
            entries.append(make_entry(
                0, title.replace("_", " "), url=_wikipedia_url(project, title),
                metric=float(article["views"]) if isinstance(article.get("views"), int) else None,
                details={"source_rank": article.get("rank")},
            ))
        lists.append(ChartList(_ranked(entries), chart_date=day))
    if not lists:
        raise ChartError("no item with a day")
    return lists


def parse_wikimedia_top_country(content: bytes, chart: Chart) -> list[ChartList]:
    """Parses the most-viewed articles from a country on a day, across all Wikipedia editions.

    The Wikimedia API rounds the views up to the next thousand (``views_ceil``) and
    lists only articles above a privacy threshold. Pages of other Wikimedia projects
    (Wiktionary, Commons) and pages that are not articles are left out.

    Args:
        content: The JSON document.
        chart: The chart.

    Returns:
        list[ChartList]: One list per day of the response.

    Raises:
        ChartError: If the document has no items.
    """
    data = _json(content)
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list) or not items:
        raise ChartError("no items")
    lists = []
    for item in items:
        day = _wikimedia_day(item) if isinstance(item, dict) else None
        if day is None:
            continue
        entries = []
        for article in item.get("articles") or []:
            if not isinstance(article, dict):
                continue
            title = str(article.get("article", ""))
            project = str(article.get("project", ""))
            if not title or not project.endswith(WIKIPEDIA_PROJECT_SUFFIX) or not _is_article(title):
                continue
            views = article.get("views_ceil")
            entries.append(make_entry(
                0, title.replace("_", " "), creator=project, url=_wikipedia_url(project, title),
                metric=float(views) if isinstance(views, int) else None,
                details={"source_rank": article.get("rank")},
            ))
        lists.append(ChartList(_ranked(entries), chart_date=day))
    if not lists:
        raise ChartError("no item with a day")
    return lists


PARSERS: dict[str, Callable[[bytes, Chart], list[ChartList]]] = {
    "apple_rss": parse_apple_rss,
    "google_trends_rss": parse_google_trends_rss,
    "wikimedia_top": parse_wikimedia_top,
    "wikimedia_top_country": parse_wikimedia_top_country,
}


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------

class ChartsCollector(Collector):
    """Writes a snapshot of every chart whose list changed since the previous observation."""
    name = "charts"
    datasets = (DATASET,)

    def __init__(self, config_path: Path = CONFIG_PATH) -> None:
        """Initializes the collector.

        Args:
            config_path: The chart configuration.
        """
        self._config_path = config_path

    @staticmethod
    def _due(chart: Chart, state: dict[str, Any], ctx: RunContext) -> bool:
        """Tells whether a chart is read in this run: not paused after a refusal and its interval passed."""
        blocked_until = state.get("blocked_until")
        if blocked_until and date.fromisoformat(blocked_until) > ctx.today:
            return False
        checked = state.get("checked")
        if ctx.backfill or chart.check_days == 0 or not checked:
            return True
        return (ctx.today - date.fromisoformat(checked)).days >= chart.check_days

    def _record(self, ctx: RunContext, chart: Chart, provider: Provider, chart_list: ChartList, chart_date: date,
                revision: int, url: str, first_run: date) -> ChartSnapshotRecord:
        """Builds the record of a list."""
        entries = chart_list.entries[:chart.max_entries]
        return ChartSnapshotRecord(
            **ctx.envelope(DATASET, f"chart:{chart.id}:{chart_date.isoformat()}", revision=revision,
                           source_id=chart.id, source_url=url, license=provider.license,
                           backfilled=is_backfilled(chart_date, first_run)),
            chart_id=chart.id, chart_name=chart.name, domain=chart.domain, provider=chart.provider,
            country=chart.country, period=chart.period, chart_date=chart_date,
            source_updated=chart_list.source_updated, entry_kind=chart.entry_kind, metric_name=chart.metric_name,
            entries=entries, entry_count=len(entries),
        )

    @staticmethod
    def _check_list(chart: Chart, chart_list: ChartList) -> None:
        """Raises ChartError if a list is empty or its title does not match ``expect``."""
        if not chart_list.entries:
            raise ChartError("the list has no entries")
        if chart.expect and not re.search(chart.expect, chart_list.title or ""):
            raise ChartError(f"title {chart_list.title!r} does not match {chart.expect!r}")

    def _store(self, ctx: RunContext, chart: Chart, provider: Provider, chart_list: ChartList, url: str,
               first_run: date) -> Optional[ChartSnapshotRecord]:
        """Marks a list in the seen store and returns its record, or None if it did not change.

        The entry of a daily list keeps the version of the parser that read it (``PARSER_VERSIONS``).

        Raises:
            ChartError: If the list is empty or its title does not match ``expect``.
        """
        self._check_list(chart, chart_list)
        store = ctx.seen(DATASET, chart.id)
        item_hash = content_hash([entry.model_dump(mode="json") for entry in chart_list.entries[:chart.max_entries]])
        if chart.period == "live":
            latest = store.get(LATEST_KEY)
            if latest is not None and latest.hash == item_hash:
                return None
            day = ctx.today.isoformat()
            revision = latest.data.get("revision", 0) + 1 if latest and latest.data.get("chart_date") == day else 0
            store.mark(LATEST_KEY, item_hash, ctx.today, data={"chart_date": day, "revision": revision})
            return self._record(ctx, chart, provider, chart_list, ctx.today, revision, url, first_run)
        if chart_list.chart_date is None:
            raise ChartError("the list has no date")
        key = chart_list.chart_date.isoformat()
        status = store.classify(key, item_hash)
        revision = store.mark(key, item_hash, ctx.today, data={"parser": PARSER_VERSIONS.get(chart.format, 1)})
        if status == "unchanged":
            return None
        return self._record(ctx, chart, provider, chart_list, chart_list.chart_date, revision, url, first_run)

    def _read_file(self, ctx: RunContext, chart: Chart, provider: Provider, state: dict[str, Any],
                   first_run: date, counters: dict[str, Any]) -> list[ChartSnapshotRecord]:
        """Reads a live chart with a conditional request and returns the records of changed lists."""
        headers = {}
        if state.get("etag"):
            headers["If-None-Match"] = state["etag"]
        if state.get("last_modified"):
            headers["If-Modified-Since"] = state["last_modified"]
        response = ctx.web.get(chart.url, headers=headers or None, respect_robots=chart.respect_robots,
                               timeout_s=REQUEST_TIMEOUT_S)
        if response.status == 304:
            counters["not_modified"] += 1
            return []
        records = []
        for chart_list in PARSERS[chart.format](response.content, chart):
            record = self._store(ctx, chart, provider, chart_list, response.url, first_run)
            if record is not None:
                records.append(record)
        for key, header in (("etag", "etag"), ("last_modified", "last-modified")):
            if response.headers.get(header):
                state[key] = response.headers[header]
            else:
                state.pop(key, None)
        return records

    def _read_days(self, ctx: RunContext, chart: Chart, provider: Provider, first_run: date,
                   counters: dict[str, Any]) -> tuple[list[ChartSnapshotRecord], bool]:
        """Reads the lists of the days that were missing and of the days since the last fetched day.

        A day that the source does not have (HTTP 404) is not published yet if it is one of the last
        PUBLICATION_DELAY_DAYS days: the chart stops and the next run continues from it. An older day
        that is missing is tried again in the following runs until it is MISSING_RETRY_DAYS days old,
        because the source also reports days as missing that it has. A backfill run fetches the days
        that are not stored or were read by an older version of the parser; a stored day of an older
        version that was missing when a backfill read it again is tried again like a missing day.

        Returns:
            tuple[list[ChartSnapshotRecord], bool]: The records, and False if the run deadline
            or the per-run limit of days stopped the chart before the last day.
        """
        store = ctx.seen(DATASET, chart.id)
        progress = store.get(DAYS_KEY)
        last_day = progress.data.get("last_day") if progress else None
        retry_from = ctx.today - timedelta(days=MISSING_RETRY_DAYS)
        missing = {day for day in (progress.data.get("missing") or [] if progress else [])
                   if date.fromisoformat(day) >= retry_from}
        version = PARSER_VERSIONS.get(chart.format, 1)
        end = ctx.today - timedelta(days=1)
        if ctx.backfill:
            start = ctx.today - timedelta(days=max(chart.history_days, BACKFILL_DAYS))
        elif last_day:
            start = date.fromisoformat(last_day) + timedelta(days=1)
        else:
            start = ctx.today - timedelta(days=chart.history_days)
        retried = sorted(date.fromisoformat(day) for day in missing if date.fromisoformat(day) < start)
        days = retried + [start + timedelta(days=offset) for offset in range((end - start).days + 1)]
        limit = BACKFILL_DAYS if ctx.backfill else MAX_DAYS_PER_RUN
        records: list[ChartSnapshotRecord] = []
        fetched = 0
        newest = date.fromisoformat(last_day) if last_day else None
        complete = True
        for day in days:
            if ctx.past_deadline() or fetched >= limit:
                complete = False
                break
            stored = store.get(day.isoformat())
            current = stored is not None and stored.data.get("parser", 1) == version
            if current or (stored is not None and not ctx.backfill and day.isoformat() not in missing):
                continue
            url = chart.url_for(day)
            fetched += 1
            if stored is not None:
                counters["days_parsed_again"] += 1
            elif day.isoformat() in missing:
                counters["days_retried"] += 1
            try:
                response = ctx.web.get(url, respect_robots=chart.respect_robots, timeout_s=REQUEST_TIMEOUT_S)
            except NotFoundError:
                if day >= ctx.today - timedelta(days=PUBLICATION_DELAY_DAYS):
                    break
                counters["days_missing"] += 1
                if day >= retry_from:
                    missing.add(day.isoformat())
                continue
            missing.discard(day.isoformat())
            for chart_list in PARSERS[chart.format](response.content, chart):
                record = self._store(ctx, chart, provider, chart_list, url, first_run)
                if record is not None:
                    records.append(record)
            newest = max(newest, day) if newest else day
        if newest is not None or missing or progress is not None:
            store.mark(DAYS_KEY, DAYS_KEY, ctx.today, data={
                "last_day": newest.isoformat() if newest else None, "missing": sorted(missing)})
        counters["days_fetched"] += fetched
        return records, complete

    def collect(self, ctx: RunContext, entry: CollectorEntry) -> CollectorResult:
        """Reads every due chart and writes the lists that changed.

        Args:
            ctx: The run context.
            entry: Run history of this collector; its cursor holds per chart the day of the
                last check, the validators of the conditional request and a pause after a refusal.

        Returns:
            CollectorResult: Counters with the failed charts, the new cursor, and an error if
            every chart that was read failed.
        """
        config = load_chart_config(self._config_path)
        cursor: dict[str, Any] = {key: dict(value) for key, value in entry.cursor.items() if isinstance(value, dict)}
        counters: dict[str, Any] = defaultdict(int)
        failures: dict[str, str] = {}
        complete = True
        charts = config.active_charts
        for position, chart in enumerate(charts):
            if ctx.past_deadline():
                counters["charts_not_read"] = len(charts) - position
                complete = False
                break
            state = cursor.setdefault(chart.id, {})
            if not self._due(chart, state, ctx):
                counters["charts_not_due"] += 1
                continue
            provider = config.provider(chart.provider)
            counters["charts_read"] += 1
            try:
                if chart.period == "day":
                    records, chart_complete = self._read_days(ctx, chart, provider, entry.first_run, counters)
                    complete = complete and chart_complete
                else:
                    records = self._read_file(ctx, chart, provider, state, entry.first_run, counters)
            except BlockedError as e:
                state["blocked_until"] = (ctx.today + timedelta(days=BLOCKED_RETRY_DAYS)).isoformat()
                failures[chart.id] = f"blocked: {e}"
                ctx.discard_unsaved()
                continue
            except (FetchError, ChartError) as e:
                failures[chart.id] = str(e)[:200]
                ctx.discard_unsaved()
                continue
            state.pop("blocked_until", None)
            state["checked"] = ctx.today.isoformat()
            ctx.emit(DATASET, records)
            # Also saves the progress of a chart without changed lists, before a later chart can fail
            ctx.save_seen()
            counters["charts_ok"] += 1
            counters["lists_changed"] += len(records)
            logger.info("Chart %s: %d changed lists.", chart.id, len(records))
        for chart_id, message in failures.items():
            logger.warning("Chart %s failed: %s", chart_id, message)
        counters["charts_failed"] = len(failures)
        if failures:
            counters["failed"] = dict(list(failures.items())[:MAX_FAILURES_LISTED])
        error = None
        if failures and not counters["charts_ok"]:
            error = f"all {len(failures)} charts that were read failed"
        active = {chart.id for chart in charts}
        new_cursor = {chart_id: state for chart_id, state in cursor.items() if chart_id in active}
        return CollectorResult(cursor=new_cursor, counters=dict(counters), error=error, complete=complete)
