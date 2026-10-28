"""
Monthly review of the data sources of the pipeline.

The review
1. checks the active news and press sources of ``config/sources.toml``: their
   health in ``state/sources.json``, their yield of pop culture items in the
   last REVIEW_WINDOW_DAYS days, every feed and news sitemap (errors, permanent
   redirects, age of the newest item) and the robots.txt of their homepage
   (crawl delay, a text-and-data-mining reservation stated in natural language);
2. checks the excluded candidates with a temporary status (no_feed, inactive,
   robots, unchecked) again: robots.txt, TDM reservation, bot protection, the
   feeds linked on the homepage and the imprint; a candidate that passes is
   proposed for admission, with the clauses of its imprint on reuse in the
   report, so that its terms are read before the pull request is merged;
3. fetches every active chart of ``config/charts.toml``, resolves the article
   titles of ``config/entities.toml`` and requests the terms pages of the chart
   providers, and reports problems (these files are changed by hand);
4. writes the proposed changes into ``config/sources.toml`` and a Markdown report.

The workflow ``source_review.yml`` opens a pull request with the changed
configuration and the report; the changes take effect when it is merged.
With ``--probe``, the review instead describes candidate URLs with the fetch
rules (robots.txt, TDM reservation, response, feed items, page links, imprint)
and changes nothing. Every request goes through ``WebClient``.

Usage:
    uv run python src/source_review.py --report PATH [--output-dir PATH] [--config PATH]
    uv run python src/source_review.py --report PATH --probe URL [URL ...]
"""
import argparse
import hashlib
import json
import logging
import re
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, Optional
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
from dotenv import load_dotenv

from chart_catalog import CONFIG_PATH as CHARTS_PATH
from chart_catalog import ChartConfigError, load_chart_config
from collectors.charts import PARSERS, ChartError
from collectors.feeds import MAX_CHILD_SITEMAPS, Candidate, parse_feed, parse_news_sitemap
from entities import CONFIG_PATH as ENTITIES_PATH
from entities import RegistryError, active_entities
from settings import Settings
from sources import (
    CONFIG_PATH,
    ExcludedSource,
    Outlet,
    PressSource,
    Source,
    SourceConfig,
    SourceHealth,
    SourceHealthStore,
    load_config,
)
from web import (
    MAX_CRAWL_DELAY_S,
    TDM_STATEMENT_PATTERN,
    BlockedError,
    FetchError,
    NotFoundError,
    RobotsDisallowedError,
    RobotsRules,
    WebClient,
)
from wikipedia import resolve_titles

logger = logging.getLogger(__name__)

# Sources without any pop culture item in this window are proposed for removal (unless keep = true)
REVIEW_WINDOW_DAYS = 90
# A feed whose newest item is older than this is stale
STALE_FEED_DAYS = 180
# Article pages are fetched only from hosts with at most this crawl delay, so that a run stays short
MAX_PAGE_CRAWL_DELAY_S = 10.0
MAX_FEEDS_PER_SOURCE = 3
MAX_FEEDS_CHECKED_PER_CANDIDATE = 8
MAX_FEED_LINKS_PER_PAGE = 20
MAX_ADDITIONS_PER_REVIEW = 5
# A candidate outlet needs MIN_RECENT_OUTLET_ITEMS items in RECENT_DAYS days, a press source one item in
# PRESS_RECENT_DAYS days; items without a publication time count as recent
RECENT_DAYS = 30
MIN_RECENT_OUTLET_ITEMS = 3
PRESS_RECENT_DAYS = 60
# Requests stop after this time, so that the workflow ends within its timeout
REVIEW_TIME_BUDGET_S = 20 * 60
# Notes and excerpts beyond these numbers are counted, not listed, so that the pull request stays short
MAX_REPORT_NOTES = 40
MAX_EXCERPTS = 3
EXCERPT_CHARS = 220
ITEM_DATASETS = ("news_items", "press_releases")
FEED_TYPES = ("application/rss+xml", "application/atom+xml")
# Link addresses that look like a feed or an overview page of feeds, e.g. /rss.xml, /service/rss-feeds/, /feed/
FEED_LINK_PATTERN = re.compile(r"(?<![a-z])(rss|atom)(?![a-z])|feed(?!back)|\.xml(?:\?|$)", re.IGNORECASE)
IMPRINT_LINK_PATTERN = re.compile(r"impressum|imprint", re.IGNORECASE)
# Clauses of an imprint on the reuse of content; they are shown in the report and decide nothing by themselves
REUSE_CLAUSES = re.compile(
    r"datenbank|online-dienst|maschinenlesbar|reproduz|data[\s-]*mining|§\s*44\s*b|scraping|crawler|"
    r"ohne (?:vorherige )?(?:ausdrückliche )?(?:schriftliche )?(?:genehmigung|zustimmung|erlaubnis)",
    re.IGNORECASE,
)
# Exclusion statuses that every review checks again
RECHECKED_STATUSES = frozenset({"no_feed", "inactive", "robots", "unchecked"})

ChangeAction = Literal["remove_source", "remove_feed", "replace_feed", "remove_sitemap", "set_fetch_pages",
                       "add_source", "drop_exclusion", "update_exclusion"]


@dataclass(frozen=True)
class NewEntry:
    """A new entry of the configuration.

    Attributes:
        table (str): outlet or press_source.
        fields (dict[str, Any]): Keys and values in the order of the entry; None values are left out.
        comment (Optional[str]): Comment line above the entry.
    """
    table: str
    fields: dict[str, Any]
    comment: Optional[str] = None


@dataclass(frozen=True)
class Change:
    """A proposed change of the configuration.

    Attributes:
        action (str): What to change (see ``ChangeAction``).
        source_id (str): Id of the source or exclusion.
        reason (str): Why; shown in the pull request and in the configuration.
        old (Optional[str]): Feed or sitemap URL that is removed or replaced.
        new (Optional[str]): Feed URL that replaces ``old``.
        entry (Optional[NewEntry]): The new entry of ``add_source``.
        status (Optional[str]): New status of ``update_exclusion``.
    """
    action: ChangeAction
    source_id: str
    reason: str
    old: Optional[str] = None
    new: Optional[str] = None
    entry: Optional[NewEntry] = None
    status: Optional[str] = None


@dataclass(frozen=True)
class FeedCheck:
    """Result of fetching a feed or news sitemap.

    Attributes:
        url (str): The URL.
        outcome (str): ok, not_found, disallowed, blocked, error or invalid.
        detail (str): Description of a failure.
        entries (tuple[Candidate, ...]): The items of the feed.
        newest (Optional[datetime]): Publication time of the newest item.
        moved_to (Optional[str]): New URL after permanent redirects.
    """
    url: str
    outcome: Literal["ok", "not_found", "disallowed", "blocked", "error", "invalid"]
    detail: str = ""
    entries: tuple[Candidate, ...] = ()
    newest: Optional[datetime] = None
    moved_to: Optional[str] = None


@dataclass
class ReviewResult:
    """Outcome of a review.

    Attributes:
        changes (list[Change]): Proposed changes of ``config/sources.toml``.
        notes (list[str]): Observations without a change, e.g. temporary errors.
        excerpts (dict[str, list[str]]): Clauses of the imprint on reuse, per admitted candidate.
        chart_rows (list[str]): Report rows of the charts.
        entity_issues (list[str]): Entities whose article is missing or renamed.
        provider_rows (list[str]): Report rows of the terms pages of the chart providers.
        checked_sources (int): Active sources that were checked.
        rechecked (int): Excluded candidates that were checked again.
    """
    changes: list[Change] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    excerpts: dict[str, list[str]] = field(default_factory=dict)
    chart_rows: list[str] = field(default_factory=list)
    entity_issues: list[str] = field(default_factory=list)
    provider_rows: list[str] = field(default_factory=list)
    checked_sources: int = 0
    rechecked: int = 0


class _Budget:
    """The time budget of the requests of a review."""

    def __init__(self, seconds: float) -> None:
        self._end = time.monotonic() + seconds

    @property
    def exhausted(self) -> bool:
        """True if the review should send no further requests."""
        return time.monotonic() >= self._end


# ---------------------------------------------------------------------------
# Feeds and pages
# ---------------------------------------------------------------------------

def count_items(data_dir: Path, since: date) -> Counter[str]:
    """Counts the stored news items and press releases per source, observed on or after a day.

    Args:
        data_dir: Root folder of the datasets.
        since: First day to count.

    Returns:
        Counter[str]: Records per source id.
    """
    counts: Counter[str] = Counter()
    cutoff = since.isoformat()
    for dataset in ITEM_DATASETS:
        for path in sorted((data_dir / dataset).glob("year=*/month=*/*.json")):
            # Files are partitioned by the day of the observing run: <dataset>_YYYY-MM-DD.json
            if path.stem.rsplit("_", 1)[-1] < cutoff:
                continue
            for record in json.loads(path.read_text(encoding="utf-8")):
                counts[str(record.get("source_id"))] += 1
    return counts


def check_feed(web: WebClient, url: str, source: Source, sitemap: bool = False) -> FeedCheck:
    """Fetches and parses a feed or news sitemap.

    Args:
        web: The HTTP client.
        url: URL of the feed or sitemap.
        source: The source the items belong to.
        sitemap: True for a news sitemap; the first child sitemaps of an index are read as well.

    Returns:
        FeedCheck: The outcome and the items.
    """
    try:
        response = web.get(url)
    except RobotsDisallowedError:
        return FeedCheck(url, "disallowed", "disallowed by robots.txt")
    except NotFoundError as e:
        return FeedCheck(url, "not_found", e.reason)
    except BlockedError as e:
        return FeedCheck(url, "blocked", e.reason)
    except FetchError as e:
        return FeedCheck(url, "error", e.reason)
    try:
        if sitemap and isinstance(source, Outlet):
            entries, children = parse_news_sitemap(response.content, source, url)
            for child in children[:MAX_CHILD_SITEMAPS]:
                try:
                    entries.extend(parse_news_sitemap(web.get(child).content, source, child)[0])
                except (FetchError, ValueError) as e:
                    logger.info("Child sitemap %s failed: %s", child, e)
        else:
            entries = parse_feed(response.content, source, url)
            if not entries and "html" in response.content_type:
                raise ValueError("not a feed (HTML page)")
    except ValueError as e:
        return FeedCheck(url, "invalid", str(e)[:160])
    dates = [entry.published_at for entry in entries if entry.published_at is not None]
    moved_to = response.url if response.moved_permanently and response.url != url else None
    return FeedCheck(url, "ok", entries=tuple(entries), newest=max(dates, default=None), moved_to=moved_to)


def feed_links(html: str, base_url: str) -> list[str]:
    """Returns the feeds that a page links: the RSS and Atom links of its head, then the
    links whose address looks like a feed (FEED_LINK_PATTERN).

    Args:
        html: The page.
        base_url: URL of the page, for relative links.

    Returns:
        list[str]: Absolute http(s) URLs without duplicates, at most MAX_FEED_LINKS_PER_PAGE.
    """
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for tag in soup.find_all("link", href=True):
        rel = tag.get("rel") or []
        rels = [value.lower() for value in (rel if isinstance(rel, list) else [rel])]
        if "alternate" in rels and str(tag.get("type") or "").lower() in FEED_TYPES:
            links.append(urljoin(base_url, str(tag["href"])))
    for tag in soup.find_all("a", href=True):
        url = urljoin(base_url, str(tag["href"]).strip())
        parts = urlsplit(url)
        target = f"{parts.path}?{parts.query}" if parts.query else parts.path
        if parts.scheme in ("http", "https") and FEED_LINK_PATTERN.search(target) and "comments" not in target:
            links.append(url.split("#", 1)[0])
    return list(dict.fromkeys(links))[:MAX_FEED_LINKS_PER_PAGE]


def imprint_link(html: str, base_url: str) -> Optional[str]:
    """Returns the address of the imprint linked on a page, if any.

    Args:
        html: The page.
        base_url: URL of the page, for relative links.

    Returns:
        Optional[str]: The absolute URL of the first link whose address or text names the imprint.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all("a", href=True):
        if IMPRINT_LINK_PATTERN.search(str(tag["href"])) or IMPRINT_LINK_PATTERN.search(tag.get_text(" ")):
            return urljoin(base_url, str(tag["href"]).strip())
    return None


def page_text(html: str) -> str:
    """Returns the visible text of a page with collapsed whitespace."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return " ".join(soup.get_text(" ").split())


def reuse_excerpts(text: str) -> list[str]:
    """Returns the passages of a text that contain a clause on the reuse of content.

    Args:
        text: Text of an imprint or terms page.

    Returns:
        list[str]: Up to MAX_EXCERPTS passages of about EXCERPT_CHARS characters around each match.
    """
    excerpts: list[str] = []
    last_end = -1
    for match in REUSE_CLAUSES.finditer(text):
        if match.start() < last_end:
            continue
        start = max(0, match.start() - EXCERPT_CHARS // 2)
        last_end = min(len(text), match.end() + EXCERPT_CHARS // 2)
        excerpts.append(("..." if start else "") + text[start:last_end] + "...")
        if len(excerpts) >= MAX_EXCERPTS:
            break
    return excerpts


def lacks_yield(source: Source, items: int, today: date) -> bool:
    """Tells whether a source is proposed for removal because it yielded no pop culture item.

    Args:
        source: The source.
        items: Its records in the last REVIEW_WINDOW_DAYS days.
        today: Day of the review.

    Returns:
        bool: True if the source was added at least REVIEW_WINDOW_DAYS days ago, has no
        records in that window and is not marked with ``keep = true``.
    """
    if source.keep or source.added is None or items:
        return False
    return (today - source.added).days >= REVIEW_WINDOW_DAYS


# ---------------------------------------------------------------------------
# Active sources
# ---------------------------------------------------------------------------

def review_source(web: WebClient, source: Source, health: SourceHealth, items: int,
                  now: datetime) -> tuple[list[Change], list[str]]:
    """Checks one active source.

    A source is proposed for removal if it is blocked or TDM reserved, has
    no working feed, is broken and still fails, requires a crawl delay above
    MAX_CRAWL_DELAY_S or yielded no pop culture item in REVIEW_WINDOW_DAYS days.
    Otherwise single feeds are removed or replaced, and page fetching is
    turned off where robots.txt states a TDM reservation or a long crawl delay.

    Args:
        web: The HTTP client.
        source: The source.
        health: Its health from ``state/sources.json``.
        items: Its records in the last REVIEW_WINDOW_DAYS days.
        now: Time of the review (UTC).

    Returns:
        tuple[list[Change], list[str]]: The proposed changes and notes.
    """
    if health.status in ("blocked", "tdm_reserved"):
        # These hosts are not requested again
        reason = f"{health.status.replace('_', ' ')} since {health.last_attempt or 'unknown'}"
        if health.last_error:
            reason += f": {health.last_error}"
        return [Change("remove_source", source.id, reason)], []
    if lacks_yield(source, items, now.date()):
        return [Change("remove_source", source.id,
                       f"no pop culture items in the last {REVIEW_WINDOW_DAYS} days")], []

    changes: list[Change] = []
    notes: list[str] = []
    checks = [check_feed(web, url, source) for url in source.feeds]
    sitemap_check = None
    if isinstance(source, Outlet) and source.news_sitemap:
        sitemap_check = check_feed(web, source.news_sitemap, source, sitemap=True)
    all_checks = [*checks, *([sitemap_check] if sitemap_check else [])]
    blocked = next((check for check in all_checks if check.outcome == "blocked"), None)
    if blocked is not None:
        return [Change("remove_source", source.id, f"blocked: {blocked.detail} ({blocked.url})")], []

    stale_before = now - timedelta(days=STALE_FEED_DAYS)
    dropped: list[str] = []
    for check in all_checks:
        is_sitemap = check is sitemap_check
        label = "news sitemap" if is_sitemap else "feed"
        reason: Optional[str] = None
        if check.outcome in ("not_found", "disallowed"):
            reason = check.detail
        elif check.outcome == "ok" and check.newest is not None and check.newest < stale_before:
            reason = f"newest item from {check.newest.date().isoformat()}"
        if reason is not None:
            changes.append(Change("remove_sitemap" if is_sitemap else "remove_feed", source.id, reason, old=check.url))
            dropped.append(f"{label} {check.url}: {reason}")
        elif check.outcome == "ok" and check.moved_to and not is_sitemap:
            changes.append(Change("replace_feed", source.id, "moved permanently", old=check.url, new=check.moved_to))
        elif check.outcome in ("error", "invalid"):
            notes.append(f"`{source.id}`: {label} {check.url}: {check.detail}")
        elif check.outcome == "ok" and not check.entries:
            notes.append(f"`{source.id}`: {label} {check.url} has no items")
    if all_checks and len(dropped) == len(all_checks):
        return [Change("remove_source", source.id, "no working feed: " + "; ".join(dropped))], notes
    working = any(check.outcome == "ok" for check in all_checks)
    if health.status == "broken":
        if not working:
            reason = f"broken since {health.last_success or 'the first run'}: {health.last_error or 'feeds fail'}"
            return [Change("remove_source", source.id, reason)], notes
        notes.append(f"`{source.id}`: broken in sources.json, but a feed works again")

    try:
        rules: Optional[RobotsRules] = web.robots_rules(source.homepage)
    except FetchError as e:
        notes.append(f"`{source.id}`: robots.txt of the homepage not checked ({e.reason})")
        rules = None
    delay = rules.crawl_delay if rules else None
    if delay is not None and delay > MAX_CRAWL_DELAY_S:
        return [Change("remove_source", source.id, f"robots.txt crawl delay {delay:g} s, more than "
                                                    f"{MAX_CRAWL_DELAY_S:g} s")], notes
    if source.fetch_pages and rules is not None and rules.tdm_statement:
        changes.append(Change("set_fetch_pages", source.id,
                              "robots.txt states a text-and-data-mining reservation in natural language"))
    elif source.fetch_pages and delay is not None and delay > MAX_PAGE_CRAWL_DELAY_S:
        changes.append(Change("set_fetch_pages", source.id, f"robots.txt crawl delay {delay:g} s"))
    return changes, notes


# ---------------------------------------------------------------------------
# Excluded candidates
# ---------------------------------------------------------------------------

def _candidate_source(entry: ExcludedSource) -> Optional[Source]:
    """Builds a source object from the attributes of an excluded candidate, or None if attributes are missing."""
    if entry.kind == "outlet":
        if not (entry.outlet_type and entry.owner_group and entry.paywall_model):
            return None
        return Outlet(id=entry.id, name=entry.name, homepage=entry.homepage, outlet_type=entry.outlet_type,
                      owner_group=entry.owner_group, paywall_model=entry.paywall_model, feeds=entry.feeds,
                      home_state=entry.home_state, focus=entry.focus, audience=entry.audience or "general")
    if entry.kind == "press_source":
        if not (entry.actor_type and entry.jurisdiction):
            return None
        return PressSource(id=entry.id, name=entry.name, homepage=entry.homepage, actor_type=entry.actor_type,
                           jurisdiction=entry.jurisdiction, feeds=entry.feeds)
    return None


def _recent_items(check: FeedCheck, since: datetime) -> int:
    """Counts the items of a feed published since a time; items without a time count as recent."""
    return sum(1 for item in check.entries if item.published_at is None or item.published_at >= since)


def _exclusion(entry: ExcludedSource, status: str, reason: str) -> tuple[list[Change], list[str], list[str]]:
    """Returns the outcome of a candidate that is not admitted.

    A new status is written into the configuration with its reason and the day of the check; an
    unchanged status leaves the entry as it is and only adds a note, so that the configuration does
    not change with every review.
    """
    if status == entry.status:
        return [], [f"`{entry.id}`: still {status}: {reason}"], []
    return [Change("update_exclusion", entry.id, reason, status=status)], [], []


def _transient(entry: ExcludedSource, reason: str) -> tuple[list[Change], list[str], list[str]]:
    """Returns the outcome of a candidate that could not be checked; the entry stays as it is."""
    return [], [f"`{entry.id}`: not checked: {reason}"], []


def _new_entry(entry: ExcludedSource, feeds: list[str], fetch_pages: bool, today: date, comment: str) -> NewEntry:
    """Builds the configuration entry of an admitted candidate."""
    if entry.kind == "outlet":
        fields: dict[str, Any] = {
            "id": entry.id, "name": entry.name, "homepage": entry.homepage, "outlet_type": entry.outlet_type,
            "owner_group": entry.owner_group, "home_state": entry.home_state, "focus": entry.focus,
            "audience": entry.audience, "paywall_model": entry.paywall_model, "feeds": feeds,
            "fetch_pages": fetch_pages, "added": today,
        }
        return NewEntry("outlet", fields, comment)
    fields = {
        "id": entry.id, "name": entry.name, "homepage": entry.homepage, "actor_type": entry.actor_type,
        "jurisdiction": entry.jurisdiction, "feeds": feeds, "fetch_pages": fetch_pages, "added": today,
    }
    return NewEntry("press_source", fields, comment)


def evaluate_candidate(web: WebClient, entry: ExcludedSource, now: datetime
                       ) -> tuple[list[Change], list[str], list[str]]:
    """Checks an excluded candidate with a temporary status again.

    The candidate is admitted if robots.txt allows its homepage and feeds with a
    crawl delay of at most MAX_CRAWL_DELAY_S, no machine-readable TDM reservation
    applies, the host does not refuse automated requests, and up to
    MAX_FEEDS_PER_SOURCE feeds (configured or linked on the homepage) have recent
    items. Page fetching is turned off if robots.txt or the imprint states a TDM
    reservation in natural language or the crawl delay exceeds MAX_PAGE_CRAWL_DELAY_S.

    Args:
        web: The HTTP client.
        entry: The excluded candidate.
        now: Time of the review (UTC).

    Returns:
        tuple[list[Change], list[str], list[str]]: The changes (admission and removal of the
        exclusion, or the updated exclusion), notes, and the clauses of the imprint on reuse.
    """
    source = _candidate_source(entry)
    if source is None:
        return [], [f"`{entry.id}`: kind or attributes missing; not checked"], []
    try:
        rules = web.robots_rules(entry.homepage)
    except FetchError as e:
        return _transient(entry, e.reason)
    delay = rules.crawl_delay if rules else None
    if delay is not None and delay > MAX_CRAWL_DELAY_S:
        return _exclusion(entry, "robots", f"robots.txt crawl delay {delay:g} s")
    if web.tdm_reserved(entry.homepage):
        return _exclusion(entry, "tdm_reserved", "machine-readable text-and-data-mining reservation")
    try:
        homepage = web.get(entry.homepage)
    except RobotsDisallowedError:
        return _exclusion(entry, "robots", "robots.txt disallows the homepage")
    except BlockedError as e:
        return _exclusion(entry, "blocked", f"homepage blocked ({e.reason})")
    except FetchError as e:
        return _transient(entry, f"homepage unreachable ({e.reason})")
    if web.tdm_reserved(homepage.url, homepage):
        return _exclusion(entry, "tdm_reserved", "machine-readable text-and-data-mining reservation")
    html = homepage.text if "html" in homepage.content_type else ""
    candidates = list(dict.fromkeys([*entry.feeds, *feed_links(html, homepage.url)]))
    checks = [check_feed(web, url, source) for url in candidates[:MAX_FEEDS_CHECKED_PER_CANDIDATE]]
    if any(check.outcome == "blocked" for check in checks):
        return _exclusion(entry, "blocked", "a feed is blocked (HTTP 401/403 or challenge)")
    working = [check for check in checks if check.outcome == "ok" and check.entries]
    if not working:
        details = "; ".join(f"{check.url}: {check.outcome}" for check in checks) or "no feed link on the homepage"
        return _exclusion(entry, "no_feed", f"no readable feed ({details[:240]})")
    window = PRESS_RECENT_DAYS if entry.kind == "press_source" else RECENT_DAYS
    minimum = 1 if entry.kind == "press_source" else MIN_RECENT_OUTLET_ITEMS
    since = now - timedelta(days=window)
    ranked = sorted(working, key=lambda check: _recent_items(check, since), reverse=True)
    chosen = [check for check in ranked if _recent_items(check, since)][:MAX_FEEDS_PER_SOURCE]
    recent = sum(_recent_items(check, since) for check in chosen)
    if recent < minimum:
        return _exclusion(entry, "inactive", f"{recent} items in the last {window} days, fewer than {minimum}")
    excerpts: list[str] = []
    tdm_in_imprint = False
    imprint = imprint_link(html, homepage.url) if html else None
    notes: list[str] = []
    if imprint:
        try:
            text = page_text(web.get(imprint).text)
            excerpts = reuse_excerpts(text)
            tdm_in_imprint = bool(TDM_STATEMENT_PATTERN.search(text))
        except FetchError as e:
            notes.append(f"`{entry.id}`: imprint {imprint} not read ({e.reason})")
    else:
        notes.append(f"`{entry.id}`: no imprint link on the homepage; read the terms before merging")
    tdm_statement = bool(rules and rules.tdm_statement) or tdm_in_imprint
    fetch_pages = not tdm_statement and not (delay is not None and delay > MAX_PAGE_CRAWL_DELAY_S)
    feeds = [check.moved_to or check.url for check in chosen]
    spans = ", ".join(f"{_recent_items(check, since)} recent items" for check in chosen)
    comment = (f"source review {now.date().isoformat()}: admitted from the excluded candidates; feeds: {spans}; "
               + ("TDM reservation in natural language, therefore fetch_pages = false" if tdm_statement
                  else "no TDM reservation found"))
    new = _new_entry(entry, feeds, fetch_pages, now.date(), comment)
    reason = f"{len(feeds)} feeds with {recent} items in the last {window} days"
    return ([Change("drop_exclusion", entry.id, "admitted"), Change("add_source", entry.id, reason, entry=new)],
            notes, excerpts)


# ---------------------------------------------------------------------------
# Charts, entities and providers
# ---------------------------------------------------------------------------

def _cell(text: Any) -> str:
    """Escapes a value for a Markdown table cell."""
    return " ".join(str(text).split()).replace("|", "\\|")


def check_charts(web: WebClient, path: Path, today: date) -> list[str]:
    """Fetches every active chart and returns a report row per chart.

    Daily lists are fetched for the day three days before the review, which the sources have published.

    Args:
        web: The HTTP client.
        path: The chart configuration.
        today: Day of the review.

    Returns:
        list[str]: Markdown table rows: chart, outcome, entries, title, first entries.
    """
    rows = []
    for chart in load_chart_config(path).active_charts:
        url = chart.url_for(today - timedelta(days=3))
        try:
            response = web.get(url, respect_robots=chart.respect_robots, timeout_s=120.0)
            lists = PARSERS[chart.format](response.content, chart)
            reserved = chart.respect_robots and web.tdm_reserved(response.url, response)
        except (FetchError, ChartError) as e:
            rows.append(f"| `{chart.id}` | failed: {_cell(str(e)[:160])} | | | |")
            continue
        first = lists[-1] if lists else None
        entries = first.entries if first else []
        outcome = "TDM reserved" if reserved else ("no entries" if not entries else "ok")
        if chart.expect and first and not re.search(chart.expect, first.title or ""):
            outcome = f"title does not match {chart.expect!r}"
        rows.append(f"| `{chart.id}` | {outcome} | {len(entries)} | {_cell(first.title if first else '')} | "
                    f"{_cell('; '.join(entry.title for entry in entries[:3]))} |")
    return rows


def check_entities(web: WebClient, path: Path) -> list[str]:
    """Resolves the article titles of the active entities and returns the problems.

    Args:
        web: The HTTP client.
        path: The entity registry.

    Returns:
        list[str]: One line per entity whose article is missing, a disambiguation page or renamed.
    """
    entities = active_entities(path)
    resolved = resolve_titles(web, [entity.wikipedia_title for entity in entities])
    issues = []
    for entity in entities:
        article = resolved.get(entity.wikipedia_title)
        if article is None:
            issues.append(f"`{entity.id}`: no German Wikipedia article (or a disambiguation page) for "
                          f"{entity.wikipedia_title!r}")
        elif article.title != entity.wikipedia_title:
            issues.append(f"`{entity.id}`: {entity.wikipedia_title!r} redirects to {article.title!r}; "
                          f"set wikipedia = {article.title!r}")
    return issues


def check_providers(web: WebClient, path: Path) -> list[str]:
    """Requests the terms pages of the chart providers and returns a report row per provider.

    Args:
        web: The HTTP client.
        path: The chart configuration.

    Returns:
        list[str]: Markdown table rows: provider, terms page, outcome, hash of the text and the reuse clauses.
    """
    rows = []
    for provider in load_chart_config(path).providers:
        try:
            response = web.get(provider.terms_url)
        except FetchError as e:
            rows.append(f"| `{provider.id}` | {provider.terms_url} | {_cell(e.reason)} | | |")
            continue
        text = page_text(response.text)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        clauses = len(REUSE_CLAUSES.findall(text))
        rows.append(f"| `{provider.id}` | {provider.terms_url} | HTTP {response.status} | `{digest}` | {clauses} |")
    return rows


# ---------------------------------------------------------------------------
# Probe
# ---------------------------------------------------------------------------

def probe(web: WebClient, urls: Sequence[str], now: datetime) -> str:
    """Describes candidate URLs with the fetch rules, without changing the configuration.

    Args:
        web: The HTTP client.
        urls: The URLs.
        now: Time of the probe (UTC).

    Returns:
        str: The Markdown report.
    """
    lines = [f"## Probe of {len(urls)} URLs, {now:%Y-%m-%d %H:%M} UTC", ""]
    for url in urls:
        lines.extend([f"### {url}", ""])
        try:
            rules = web.robots_rules(url)
            allowed = web.allowed(url)
            lines.append(f"- **robots.txt:** {'allows' if allowed else 'disallows'} the URL"
                         + (f"; crawl delay {rules.crawl_delay:g} s" if rules and rules.crawl_delay else "")
                         + ("; states a TDM reservation in natural language" if rules and rules.tdm_statement else "")
                         + ("" if rules else " (no robots.txt)"))
        except FetchError as e:
            lines.append(f"- **robots.txt:** not readable ({e.reason})")
        lines.append(f"- **TDM reservation (TDMRep):** {'yes' if web.tdm_reserved(url) else 'no'}")
        try:
            response = web.get(url)
        except FetchError as e:
            lines.extend([f"- **Response:** {e.reason}", ""])
            continue
        lines.append(f"- **Response:** HTTP {response.status}, {response.content_type or 'no type'}, "
                     f"{len(response.content):,} bytes" + (f", final URL {response.url}" if response.url != url else "")
                     + (" (moved permanently)" if response.moved_permanently else ""))
        if "json" in response.content_type:
            try:
                data = response.json()
                keys = list(data)[:15] if isinstance(data, dict) else f"list of {len(data)}"
                lines.append(f"- **JSON:** {keys}")
            except ValueError:
                lines.append("- **JSON:** not valid")
        elif "html" not in response.content_type:
            dummy = Outlet(id="probe", name="probe", homepage=url, outlet_type="online_only", owner_group="",
                           paywall_model="free", feeds=())
            try:
                items = parse_feed(response.content, dummy, url)
            except ValueError:
                items = []
            dates = [item.published_at for item in items if item.published_at]
            if items:
                newest = max(dates) if dates else None
                lines.append(f"- **Feed:** {len(items)} items"
                             + (f", newest {(now - newest).total_seconds() / 3600:.0f} h old" if newest else
                                ", without publication times"))
                lines.extend(f"  - {_cell(item.title[:100])}" for item in items[:5])
        else:
            soup = BeautifulSoup(response.text, "html.parser")
            title = soup.title.get_text(" ").strip() if soup.title else ""
            lines.append(f"- **Page title:** {_cell(title[:120])}")
            links = feed_links(response.text, response.url)
            lines.append(f"- **Feed links:** {', '.join(links) or 'none'}")
            imprint = imprint_link(response.text, response.url)
            lines.append(f"- **Imprint:** {imprint or 'no link'}")
            if imprint:
                try:
                    for excerpt in reuse_excerpts(page_text(web.get(imprint).text)):
                        lines.append(f"  - {_cell(excerpt)}")
                except FetchError as e:
                    lines.append(f"  - not readable ({e.reason})")
        lines.append("")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Configuration text
# ---------------------------------------------------------------------------

_TABLE_HEADER = re.compile(r"^\[\[(outlet|press_source|excluded)\]\]$")
_ID_LINE = re.compile(r'^id\s*=\s*"([^"]+)"')


@dataclass(frozen=True)
class _Block:
    """An entry of the configuration text: the header line and the key lines up to ``end`` (exclusive)."""
    table: str
    source_id: str
    header: int
    end: int


def _blocks(lines: Sequence[str]) -> list[_Block]:
    """Finds the entries of the configuration text.

    Raises:
        ValueError: If an entry has no id line.
    """
    blocks = []
    for index, line in enumerate(lines):
        match = _TABLE_HEADER.match(line.strip())
        if not match:
            continue
        end = index + 1
        while end < len(lines) and lines[end].strip() and not lines[end].lstrip().startswith(("#", "[")):
            end += 1
        ids = [found.group(1) for found in (_ID_LINE.match(text.strip()) for text in lines[index + 1:end]) if found]
        if not ids:
            raise ValueError(f"The entry in line {index + 1} has no id")
        blocks.append(_Block(match.group(1), ids[0], index, end))
    return blocks


def toml_value(value: Any) -> str:
    """Formats a value as TOML.

    Args:
        value: A string, bool, date or a list of strings.

    Returns:
        str: The TOML literal.

    Raises:
        TypeError: For other types.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        # JSON string escapes are valid in TOML basic strings
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(toml_value(item) for item in value) + "]"
    raise TypeError(f"Cannot write {type(value).__name__} as TOML")


def _render_entry(entry: NewEntry) -> list[str]:
    """Returns the lines of a new entry, preceded by a blank line."""
    lines = ["", *([f"# {entry.comment}"] if entry.comment else []), f"[[{entry.table}]]"]
    lines.extend(f"{key} = {toml_value(value)}" for key, value in entry.fields.items() if value is not None)
    return lines


def apply_changes(text: str, config: SourceConfig, changes: Sequence[Change], today: date) -> str:
    """Writes the changes into the configuration text and validates the result.

    Unchanged entries and all comments stay as they are. A removed source
    keeps its entry and gets ``removed`` and ``removed_reason``; feed and
    page-fetching changes get a comment line above the entry; admitted
    candidates are appended to their table and their exclusion is dropped;
    a checked exclusion gets its new status, reason and day of the check.

    Args:
        text: The configuration text.
        config: The parsed configuration.
        changes: The changes.
        today: Day of the review.

    Returns:
        str: The changed text; equal to ``text`` without changes.

    Raises:
        ValueError: If a change names an unknown entry or the result is not a valid configuration.
    """
    if not changes:
        return text
    lines = text.split("\n")
    blocks = _blocks(lines)
    by_key = {(block.table == "excluded", block.source_id): block for block in blocks}
    sources = {source.id: source for source in config.sources}
    comments: dict[int, list[str]] = {}
    values: dict[int, dict[str, Optional[str]]] = {}
    dropped: set[int] = set()
    feeds: dict[str, list[str]] = {}
    appended: dict[str, list[str]] = {"outlet": [], "press_source": []}
    for change in changes:
        if change.action == "add_source":
            assert change.entry is not None
            appended[change.entry.table].extend(_render_entry(change.entry))
            continue
        excluded = change.action in ("drop_exclusion", "update_exclusion")
        block = by_key.get((excluded, change.source_id))
        if block is None:
            raise ValueError(f"No {'excluded ' if excluded else ''}entry with the id {change.source_id}")
        keys = values.setdefault(block.header, {})
        note = comments.setdefault(block.header, [])
        if change.action == "drop_exclusion":
            dropped.add(block.header)
        elif change.action == "update_exclusion":
            keys["status"] = toml_value(change.status or "unchecked")
            keys["reason"] = toml_value(change.reason)
            keys["checked"] = toml_value(today)
        elif change.action == "remove_source":
            keys["removed"] = toml_value(today)
            keys["removed_reason"] = toml_value(change.reason)
        elif change.action in ("remove_feed", "replace_feed"):
            current = feeds.setdefault(change.source_id, list(sources[change.source_id].feeds))
            if change.action == "remove_feed":
                current[:] = [url for url in current if url != change.old]
                note.append(f"# source review {today.isoformat()}: feed {change.old} removed: {change.reason}")
            else:
                current[:] = list(dict.fromkeys(change.new if url == change.old else url for url in current))
                note.append(f"# source review {today.isoformat()}: feed {change.old} moved to {change.new}")
            keys["feeds"] = toml_value(current)
        elif change.action == "remove_sitemap":
            keys["news_sitemap"] = None
            note.append(f"# source review {today.isoformat()}: news sitemap {change.old} removed: {change.reason}")
        elif change.action == "set_fetch_pages":
            keys["fetch_pages"] = "false"
            note.append(f"# source review {today.isoformat()}: fetch_pages = false: {change.reason}")

    inserts: dict[int, list[str]] = {}
    for table, new_lines in appended.items():
        if not new_lines:
            continue
        ends = [block.end for block in blocks if block.table == table]
        if not ends:
            # A table without entries gets its first entry before the excluded candidates
            ends = [min((block.header for block in blocks if block.table == "excluded"), default=len(lines))]
            new_lines = [*new_lines[1:], ""]
        inserts.setdefault(max(ends), []).extend(new_lines)

    out: list[str] = []
    block_at = {block.header: block for block in blocks}
    index = 0
    while index < len(lines):
        out.extend(inserts.get(index, []))
        block = block_at.get(index)
        if block is None:
            out.append(lines[index])
            index += 1
            continue
        if block.header in dropped:
            # Remove the blank line before the entry as well
            if out and out[-1] == "":
                out.pop()
            index = block.end
            continue
        if block.header in values:
            out.extend(comments.get(block.header, []))
            out.append(lines[index])
            keys = values[block.header]
            written: set[str] = set()
            for line in lines[index + 1:block.end]:
                key = line.split("=", 1)[0].strip()
                if key in keys:
                    written.add(key)
                    if keys[key] is not None:
                        out.append(f"{key} = {keys[key]}")
                    continue
                out.append(line)
            out.extend(f"{key} = {value}" for key, value in keys.items() if key not in written and value is not None)
            index = block.end
            continue
        out.append(lines[index])
        index += 1
    out.extend(inserts.get(len(lines), []))
    changed = "\n".join(out)
    validate_config_text(changed)
    return changed


def validate_config_text(text: str) -> SourceConfig:
    """Parses a configuration text with the rules of ``load_config``.

    Args:
        text: The configuration text.

    Returns:
        SourceConfig: The parsed configuration.

    Raises:
        ValueError: If the text is not a valid configuration.
    """
    # tomllib.TOMLDecodeError is a ValueError, as are the validation errors of load_config
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "sources.toml"
        path.write_text(text, encoding="utf-8")
        return load_config(path)


# ---------------------------------------------------------------------------
# Review
# ---------------------------------------------------------------------------

def run_review(settings: Settings, config_path: Path, web: WebClient, now: datetime,
               charts_path: Path = CHARTS_PATH, entities_path: Path = ENTITIES_PATH,
               budget_s: float = REVIEW_TIME_BUDGET_S) -> tuple[str, ReviewResult]:
    """Reviews the sources and returns the changed configuration text and the result.

    Args:
        settings: The settings; ``settings.output_dir`` holds the data and state.
        config_path: The source configuration.
        web: The HTTP client.
        now: Time of the review (UTC).
        charts_path: The chart configuration.
        entities_path: The entity registry.
        budget_s: Seconds after which no further source or candidate is checked.

    Returns:
        tuple[str, ReviewResult]: The configuration text with the changes, and the result.

    Raises:
        ValueError: If the configuration is invalid or the changes cannot be applied.
    """
    text = config_path.read_text(encoding="utf-8")
    config = load_config(config_path)
    health = SourceHealthStore.load(settings.state_dir)
    items = count_items(settings.output_dir, now.date() - timedelta(days=REVIEW_WINDOW_DAYS))
    result = ReviewResult()
    budget = _Budget(budget_s)
    for source in config.active_sources:
        if budget.exhausted:
            result.notes.append("time budget used up; the remaining sources were not checked")
            break
        changes, notes = review_source(web, source, health.get(source.id), items[source.id], now)
        result.changes.extend(changes)
        result.notes.extend(notes)
        result.checked_sources += 1
    additions = 0
    for entry in config.excluded:
        if entry.status not in RECHECKED_STATUSES or entry.kind is None:
            continue
        if budget.exhausted or additions >= MAX_ADDITIONS_PER_REVIEW:
            result.notes.append(f"`{entry.id}`: not checked (time budget or {MAX_ADDITIONS_PER_REVIEW} additions)")
            continue
        changes, notes, excerpts = evaluate_candidate(web, entry, now)
        result.rechecked += 1
        result.changes.extend(changes)
        result.notes.extend(notes)
        if any(change.action == "add_source" for change in changes):
            additions += 1
            result.excerpts[entry.id] = excerpts
    try:
        result.chart_rows = check_charts(web, charts_path, now.date())
        result.provider_rows = check_providers(web, charts_path)
    except ChartConfigError as e:
        result.notes.append(f"chart configuration not readable: {e}")
    try:
        result.entity_issues = check_entities(web, entities_path)
    except (RegistryError, FetchError) as e:
        result.notes.append(f"entity registry not checked: {e}")
    return apply_changes(text, config, result.changes, now.date()), result


def build_report(result: ReviewResult, config: SourceConfig, now: datetime) -> str:
    """Builds the Markdown report of a review, used as pull request description and job summary.

    Args:
        result: The review result.
        config: The configuration before the changes.
        now: Time of the review (UTC).

    Returns:
        str: The Markdown text.
    """
    names = {source.id: source.name for source in (*config.sources, *config.excluded)}
    by_action: dict[str, list[Change]] = {}
    for change in result.changes:
        by_action.setdefault(change.action, []).append(change)
    removals = by_action.get("remove_source", [])
    edits = [change for change in result.changes
             if change.action in ("remove_feed", "replace_feed", "remove_sitemap", "set_fetch_pages")]
    additions = by_action.get("add_source", [])
    exclusions = by_action.get("update_exclusion", [])
    lines = [
        f"## Source review {now:%Y-%m}",
        "",
        f"Checked {result.checked_sources} of {len(config.active_sources)} active sources and "
        f"{result.rechecked} excluded candidates.",
        "",
        f"**Proposed:** {len(removals)} removals, {len(edits)} changes of feeds or page fetching, "
        f"{len(additions)} additions.",
    ]
    if removals:
        lines.extend(["", "### Removals", "",
                      "The entries stay in the configuration with the date of the review as `removed`.", "",
                      "| Source | Reason |", "|---|---|"])
        lines.extend(f"| {_cell(names.get(change.source_id, change.source_id))} (`{change.source_id}`) | "
                     f"{_cell(change.reason)} |" for change in removals)
    if edits:
        lines.extend(["", "### Feeds and page fetching", "", "| Source | Change | Reason |", "|---|---|---|"])
        for change in edits:
            what = {"remove_feed": f"remove feed {change.old}", "replace_feed": f"{change.old} → {change.new}",
                    "remove_sitemap": f"remove news sitemap {change.old}",
                    "set_fetch_pages": "fetch_pages = false"}[change.action]
            lines.append(f"| `{change.source_id}` | {_cell(what)} | {_cell(change.reason)} |")
    if additions:
        lines.extend(["", "### Additions", "",
                      "Read the clauses of the imprint before merging: a source whose terms forbid the inclusion "
                      "in databases, the automated collection of content or its transfer into a machine-readable "
                      "form is not admitted (set its exclusion to `terms` instead).", ""])
        for change in additions:
            assert change.entry is not None
            entry = change.entry.fields
            lines.append(f"- **{_cell(entry['name'])}** (`{entry['id']}`): {_cell(change.reason)}; feeds "
                         f"{_cell(', '.join(entry.get('feeds', [])))}; fetch_pages = {str(entry['fetch_pages']).lower()}")
            for excerpt in result.excerpts.get(str(entry["id"]), []) or ["no clause on reuse found in the imprint"]:
                lines.append(f"  - {_cell(excerpt)}")
    if exclusions:
        lines.extend(["", "### Candidates checked again", "", "| Candidate | Status | Reason |", "|---|---|---|"])
        lines.extend(f"| {_cell(names.get(change.source_id, change.source_id))} (`{change.source_id}`) | "
                     f"{change.status} | {_cell(change.reason)} |" for change in exclusions)
    if result.chart_rows:
        lines.extend(["", "### Charts", "", "| Chart | Outcome | Entries | Title | First entries |",
                      "|---|---|---|---|---|", *result.chart_rows])
    if result.provider_rows:
        lines.extend(["", "### Terms of the chart providers", "",
                      "The hash of the text changes when the page changes; read the terms again when it differs "
                      "from the previous review.", "",
                      "| Provider | Terms page | Outcome | Hash of the text | Reuse clauses |", "|---|---|---|---|---|",
                      *result.provider_rows])
    lines.extend(["", "### Entities", ""])
    lines.extend([f"- {issue}" for issue in result.entity_issues] or ["- All article titles resolve."])
    if result.notes:
        lines.extend(["", "### Notes", ""])
        lines.extend(f"- {note}" for note in result.notes[:MAX_REPORT_NOTES])
        if len(result.notes) > MAX_REPORT_NOTES:
            lines.append(f"- {len(result.notes) - MAX_REPORT_NOTES} further notes are in the workflow log")
    lines.extend([
        "", "---",
        "Merge this pull request to apply the changes to `config/sources.toml`, or edit the file on its branch "
        "first. Changes of the charts and the entity registry are proposed here only; edit `config/charts.toml` and "
        "`config/entities.toml` by hand. The next review replaces the branch, so merge or close the pull request "
        "before then.",
    ])
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def parse_args(argv: Optional[list[str]]) -> argparse.Namespace:
    """Parses the command-line arguments.

    Args:
        argv: Arguments without the program name; None reads ``sys.argv``.

    Returns:
        argparse.Namespace: The parsed arguments.
    """
    parser = argparse.ArgumentParser(description="Reviews the data sources and proposes changes.")
    parser.add_argument("--report", type=Path, required=True, help="path of the Markdown report to write")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH, help="the source configuration to review")
    parser.add_argument("--output-dir", type=Path,
                        help="folder with the datasets and state (default: data/ in GitHub Actions, "
                             "~/Downloads/german_popculture_dataset locally)")
    parser.add_argument("--probe", nargs="+", metavar="URL",
                        help="describe these URLs with the fetch rules instead of reviewing the sources")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    """Runs the review or the probe and writes the report.

    Args:
        argv: Arguments without the program name; None reads ``sys.argv``.

    Returns:
        int: 0 on success, 1 if the settings or the configuration are invalid.
    """
    args = parse_args(argv)
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(name)s - %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    if args.probe:
        with WebClient() as web:
            args.report.write_text(probe(web, args.probe, now), encoding="utf-8")
        logger.info("Probe of %d URLs written to %s.", len(args.probe), args.report)
        return 0
    try:
        settings = Settings.from_env()
    except ValueError as e:
        logger.error("Invalid settings: %s", e)
        return 1
    if args.output_dir is not None:
        settings = replace(settings, output_dir=args.output_dir)
    try:
        config = load_config(args.config)
        with WebClient() as web:
            text, result = run_review(settings, args.config, web, now)
    except ValueError as e:
        logger.error("The review failed: %s", e)
        return 1
    if result.changes:
        args.config.write_text(text, encoding="utf-8")
    args.report.write_text(build_report(result, config, now), encoding="utf-8")
    for note in result.notes:
        logger.info("Note: %s", note)
    logger.info("Source review: %d changes, %d notes; report written to %s.", len(result.changes), len(result.notes),
                args.report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
