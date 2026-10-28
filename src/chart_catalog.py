"""
The configuration of the charts and rankings (``config/charts.toml``).

Charts are the volatile sources of the dataset: lists that their sources
overwrite with every update (the current Apple charts, the trending searches
of Google) or publish per day (the most-read Wikipedia articles). The pipeline stores what such a list
showed at every observation in ``chart_snapshots``, so that the history of
the lists is kept.

The file has three kinds of entries:
- ``[[provider]]``: an organization that publishes charts, with the address
  of its terms and the terms that every record names in its ``license`` field;
- ``[[chart]]``: one list, with its provider, format (the parser), address,
  period and the kind of its entries;
- ``[[excluded]]``: candidates that were considered and not admitted, with
  the day of the decision and the reason.
"""
import re
import tomllib
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional, get_args

from schema import ChartDomain, ChartFormat, ChartPeriod, EntryKind

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "charts.toml"

ID_PATTERN = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
COUNTRY_PATTERN = re.compile(r"^[A-Z]{2}$")
PROVIDER_KEYS = frozenset({"id", "name", "homepage", "terms_url", "license", "added"})
CHART_KEYS = frozenset({
    "id", "name", "provider", "domain", "format", "url", "country", "period", "entry_kind", "max_entries",
    "metric_name", "check_days", "respect_robots", "expect", "history_days", "options", "added", "removed",
    "removed_reason",
})
EXCLUDED_KEYS = frozenset({"id", "name", "url", "checked", "reason"})
# Formats whose address contains the placeholders of a day
DAY_FORMATS = frozenset({"wikimedia_top", "wikimedia_top_country"})
DAY_PLACEHOLDERS = ("{year}", "{month}", "{day}")
DEFAULT_MAX_ENTRIES = 100
DEFAULT_HISTORY_DAYS = 7


class ChartConfigError(ValueError):
    """The chart configuration contains invalid or duplicate entries."""


@dataclass(frozen=True)
class Provider:
    """An organization that publishes charts.

    Attributes:
        id (str): Stable id, the first part of the ids of its charts.
        name (str): Name of the provider.
        homepage (str): Homepage URL.
        terms_url (str): Address of the terms of use.
        license (str): Terms that the records of its charts name in ``license``.
        added (date): Day on which the provider was added.
    """
    id: str
    name: str
    homepage: str
    terms_url: str
    license: str
    added: date


@dataclass(frozen=True)
class Chart:
    """One chart or ranking that the pipeline observes.

    Attributes:
        id (str): Stable id ``<provider>.<chart>``.
        name (str): Name of the chart.
        provider (str): Provider id.
        domain (str): Field of pop culture (see ``schema.ChartDomain``).
        format (str): Parser of the chart (see ``schema.ChartFormat``).
        url (str): Address of the list; daily lists contain {year}, {month} and {day}.
        period (str): live or day (see ``schema.ChartPeriod``).
        entry_kind (str): Kind of the entries (see ``schema.EntryKind``).
        added (date): Day on which the chart was added; start of collection.
        country (Optional[str]): Country of the chart (ISO 3166-1 alpha-2); None for a language edition.
        max_entries (int): Number of entries stored at most.
        metric_name (Optional[str]): Meaning of the metric of the entries.
        check_days (int): Days between two checks; 0 checks in every run.
        respect_robots (bool): False only for documented APIs, which are called under their terms.
        expect (Optional[str]): Regular expression that the title of the list at the source must match;
            a list with another title is not stored, so that a changed address cannot write other data under the id.
        history_days (int): Days fetched for a daily list when it is new.
        options (dict): Format options, e.g. the Wikipedia edition of a top list.
        removed (Optional[date]): Day on which the chart was removed.
        removed_reason (Optional[str]): Why the chart was removed.
    """
    id: str
    name: str
    provider: str
    domain: str
    format: str
    url: str
    period: str
    entry_kind: str
    added: date
    country: Optional[str] = None
    max_entries: int = DEFAULT_MAX_ENTRIES
    metric_name: Optional[str] = None
    check_days: int = 0
    respect_robots: bool = True
    expect: Optional[str] = None
    history_days: int = DEFAULT_HISTORY_DAYS
    options: dict[str, Any] = field(default_factory=dict)
    removed: Optional[date] = None
    removed_reason: Optional[str] = None

    @property
    def active(self) -> bool:
        """True if the chart is collected, i.e. it was not removed."""
        return self.removed is None

    def url_for(self, day: date) -> str:
        """Returns the address of the list of a day.

        Args:
            day: The day of a daily list; ignored for other charts.

        Returns:
            str: The address with the placeholders filled in.
        """
        if self.format not in DAY_FORMATS:
            return self.url
        return self.url.format(year=f"{day:%Y}", month=f"{day:%m}", day=f"{day:%d}")


@dataclass(frozen=True)
class ExcludedChart:
    """A chart candidate that was considered and not admitted.

    Attributes:
        id (str): Id of the candidate.
        name (str): Name of the candidate.
        url (str): Address that was considered.
        checked (date): Day of the decision.
        reason (str): Why the candidate is not collected.
    """
    id: str
    name: str
    url: str
    checked: date
    reason: str


@dataclass(frozen=True)
class ChartConfig:
    """All entries of ``config/charts.toml``.

    Attributes:
        providers (tuple[Provider, ...]): The providers.
        charts (tuple[Chart, ...]): The charts in file order, including removed ones.
        excluded (tuple[ExcludedChart, ...]): Candidates that were not admitted.
    """
    providers: tuple[Provider, ...] = ()
    charts: tuple[Chart, ...] = ()
    excluded: tuple[ExcludedChart, ...] = ()

    @property
    def active_charts(self) -> tuple[Chart, ...]:
        """The charts that are collected, in file order."""
        return tuple(chart for chart in self.charts if chart.active)

    def provider(self, provider_id: str) -> Provider:
        """Returns a provider by id.

        Raises:
            KeyError: If the provider is unknown.
        """
        for provider in self.providers:
            if provider.id == provider_id:
                return provider
        raise KeyError(provider_id)


def _date(raw: dict[str, Any], key: str, required: bool = False) -> Optional[date]:
    """Reads a TOML date."""
    value = raw.get(key)
    if value is None:
        if required:
            raise ValueError(f"{key} is missing")
        return None
    if not isinstance(value, date) or isinstance(value, datetime):
        raise ValueError(f"{key} must be a date (YYYY-MM-DD)")
    return value


def _text(raw: dict[str, Any], key: str, required: bool = True) -> Optional[str]:
    """Reads a string."""
    value = raw.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value.strip()


def _int(raw: dict[str, Any], key: str, default: int, minimum: int) -> int:
    """Reads an integer of at least ``minimum``."""
    value = raw.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ValueError(f"{key} must be an integer of at least {minimum}")
    return value


def _check(value: Optional[str], key: str, vocabulary: object) -> str:
    """Raises ValueError if a value is not in its vocabulary."""
    allowed = get_args(vocabulary)
    if value not in allowed:
        raise ValueError(f"{key} {value!r} is not one of {', '.join(allowed)}")
    return value


def parse_provider(raw: dict[str, Any]) -> Provider:
    """Validates one ``[[provider]]`` entry.

    Args:
        raw: The entry as read from TOML.

    Returns:
        Provider: The provider.

    Raises:
        ValueError: If a field is missing, unknown or invalid.
    """
    unknown = sorted(set(raw) - PROVIDER_KEYS)
    if unknown:
        raise ValueError(f"unknown keys: {', '.join(unknown)}")
    provider_id = _text(raw, "id") or ""
    if not re.fullmatch(r"[a-z0-9_]+", provider_id):
        raise ValueError("id must consist of lower-case letters, digits and underscores")
    added = _date(raw, "added", required=True)
    assert added is not None
    return Provider(id=provider_id, name=_text(raw, "name") or "", homepage=_text(raw, "homepage") or "",
                    terms_url=_text(raw, "terms_url") or "", license=_text(raw, "license") or "", added=added)


def parse_chart(raw: dict[str, Any], providers: set[str]) -> Chart:
    """Validates one ``[[chart]]`` entry.

    Args:
        raw: The entry as read from TOML.
        providers: Ids of the providers of the file.

    Returns:
        Chart: The chart.

    Raises:
        ValueError: If a field is missing, unknown or invalid.
    """
    unknown = sorted(set(raw) - CHART_KEYS)
    if unknown:
        raise ValueError(f"unknown keys: {', '.join(unknown)}")
    chart_id = _text(raw, "id") or ""
    if not ID_PATTERN.match(chart_id):
        raise ValueError("id must have the form <provider>.<chart> in lower case")
    provider = _text(raw, "provider") or ""
    if provider not in providers:
        raise ValueError(f"provider {provider!r} has no [[provider]] entry")
    if not chart_id.startswith(f"{provider}."):
        raise ValueError(f"id must start with the provider {provider!r}")
    chart_format = _check(_text(raw, "format"), "format", ChartFormat)
    period = _check(_text(raw, "period"), "period", ChartPeriod)
    url = _text(raw, "url") or ""
    if not url.startswith("https://"):
        raise ValueError("url must start with https://")
    if chart_format in DAY_FORMATS:
        if period != "day" or not all(placeholder in url for placeholder in DAY_PLACEHOLDERS):
            raise ValueError(f"a {chart_format} chart has period day and the placeholders {', '.join(DAY_PLACEHOLDERS)}")
    elif period == "day" or "{" in url:
        raise ValueError("only the formats of daily lists have period day and placeholders in the url")
    country = _text(raw, "country", required=False)
    if country is not None and not COUNTRY_PATTERN.match(country):
        raise ValueError("country must be an ISO 3166-1 alpha-2 code")
    expect = _text(raw, "expect", required=False)
    if expect:
        re.compile(expect)
    respect_robots = raw.get("respect_robots", True)
    if not isinstance(respect_robots, bool):
        raise ValueError("respect_robots must be true or false")
    options = raw.get("options", {})
    if not isinstance(options, dict):
        raise ValueError("options must be a table")
    added = _date(raw, "added", required=True)
    removed = _date(raw, "removed")
    removed_reason = raw.get("removed_reason")
    if (removed is None) != (removed_reason is None):
        raise ValueError("removed and removed_reason must be given together")
    assert added is not None
    return Chart(
        id=chart_id, name=_text(raw, "name") or "", provider=provider,
        domain=_check(_text(raw, "domain"), "domain", ChartDomain), format=chart_format, url=url, period=period,
        entry_kind=_check(_text(raw, "entry_kind"), "entry_kind", EntryKind), added=added, country=country,
        max_entries=_int(raw, "max_entries", DEFAULT_MAX_ENTRIES, 1),
        metric_name=_text(raw, "metric_name", required=False), check_days=_int(raw, "check_days", 0, 0),
        respect_robots=respect_robots, expect=expect, history_days=_int(raw, "history_days", DEFAULT_HISTORY_DAYS, 1),
        options=dict(options), removed=removed, removed_reason=removed_reason,
    )


def parse_excluded(raw: dict[str, Any]) -> ExcludedChart:
    """Validates one ``[[excluded]]`` entry.

    Args:
        raw: The entry as read from TOML.

    Returns:
        ExcludedChart: The entry.

    Raises:
        ValueError: If a field is missing or unknown.
    """
    unknown = sorted(set(raw) - EXCLUDED_KEYS)
    if unknown:
        raise ValueError(f"unknown keys: {', '.join(unknown)}")
    checked = _date(raw, "checked", required=True)
    assert checked is not None
    return ExcludedChart(id=_text(raw, "id") or "", name=_text(raw, "name") or "", url=_text(raw, "url") or "",
                         checked=checked, reason=_text(raw, "reason") or "")


def load_chart_config(path: Path = CONFIG_PATH) -> ChartConfig:
    """Reads and validates the chart configuration.

    Args:
        path: The configuration file.

    Returns:
        ChartConfig: The providers, charts (including removed ones) and excluded candidates.

    Raises:
        ChartConfigError: If an entry is invalid or an id is duplicated.
        FileNotFoundError: If the file does not exist.
    """
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    problems: list[str] = []
    providers: list[Provider] = []
    for position, raw in enumerate(data.get("provider", []), start=1):
        try:
            providers.append(parse_provider(raw))
        except ValueError as e:
            problems.append(f"provider {position} ({raw.get('id', '?')}): {e}")
    provider_ids = {provider.id for provider in providers}
    charts: list[Chart] = []
    for position, raw in enumerate(data.get("chart", []), start=1):
        try:
            charts.append(parse_chart(raw, provider_ids))
        except ValueError as e:
            problems.append(f"chart {position} ({raw.get('id', '?')}): {e}")
    excluded: list[ExcludedChart] = []
    for position, raw in enumerate(data.get("excluded", []), start=1):
        try:
            excluded.append(parse_excluded(raw))
        except ValueError as e:
            problems.append(f"excluded {position} ({raw.get('id', '?')}): {e}")
    ids = [*(p.id for p in providers), *(c.id for c in charts), *(e.id for e in excluded)]
    duplicates = sorted(key for key, count in Counter(ids).items() if count > 1)
    if duplicates:
        problems.append(f"duplicate ids: {', '.join(duplicates)}")
    if problems:
        raise ChartConfigError(f"{path}: " + "; ".join(problems))
    return ChartConfig(providers=tuple(providers), charts=tuple(charts), excluded=tuple(excluded))
