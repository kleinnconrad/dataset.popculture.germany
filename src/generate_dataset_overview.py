"""
Writes the dataset overview into the README.

The overview replaces the text between the markers DATASET_OVERVIEW_START and
DATASET_OVERVIEW_END: record counts per dataset, the outcome of the latest
run, the latest list of every chart with its first entries, the entities with
the most Wikipedia views of the last seven days, the status and output of
every news and press-release source, the annotation coverage, the main fields,
opinions, people and products of the pop culture items of the last 30 days,
and the fields of every dataset with their descriptions from ``schema.py`` and
their fill rates. Long tables are placed in collapsible sections.

Usage:
    uv run python src/generate_dataset_overview.py [--data-dir data] [--readme README.md]
"""
import argparse
import json
import logging
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from chart_catalog import CONFIG_PATH as CHARTS_PATH
from chart_catalog import ChartConfigError, load_chart_config
from entities import CONFIG_PATH as ENTITIES_PATH
from entities import RegistryError, load_registry
from schema import DATASET_MODELS
from sources import CONFIG_PATH, SourceHealthStore, load_config
from state import RUN_LOG_FILE
from storage import iter_dataset_files, load_records

logger = logging.getLogger(__name__)

START_MARKER = "<!-- DATASET_OVERVIEW_START -->"
END_MARKER = "<!-- DATASET_OVERVIEW_END -->"
RECENT_WINDOW_DAYS = 30
PAGEVIEW_WINDOW_DAYS = 7
TOP_ROWS = 10
CHART_PREVIEW_ENTRIES = 3
ITEM_DATASETS = ("news_items", "press_releases")


@dataclass
class DatasetData:
    """The stored records of one dataset.

    Attributes:
        files (int): Number of partition files.
        records (list[dict]): All records in chronological order of the files.
    """
    files: int
    records: list[dict[str, Any]]


def load_datasets(data_dir: Path) -> dict[str, DatasetData]:
    """Reads all records of all datasets.

    Args:
        data_dir: Root folder of the datasets.

    Returns:
        dict[str, DatasetData]: Records per dataset, in the order of ``DATASET_MODELS``.
    """
    datasets = {}
    for dataset in DATASET_MODELS:
        files = list(iter_dataset_files(data_dir, dataset))
        records: list[dict[str, Any]] = []
        for path in files:
            records.extend(load_records(path))
        datasets[dataset] = DatasetData(files=len(files), records=records)
    return datasets


def _description(dataset: str) -> str:
    """Returns the first line of the docstring of a dataset's record model."""
    doc = DATASET_MODELS[dataset].__doc__ or ""
    return doc.strip().splitlines()[0] if doc.strip() else ""


def _cell(text: Optional[str]) -> str:
    """Escapes text for a Markdown table cell."""
    return (text or "").replace("|", "\\|").replace("\n", " ")


def _share(part: int, total: int) -> str:
    """Formats a share with its count, e.g. ``95.0% (190)``."""
    return f"{part / total:.1%} ({part:,})" if total else "0.0% (0)"


def dataset_lines(datasets: dict[str, DatasetData]) -> list[str]:
    """Builds the table of the datasets.

    Args:
        datasets: Records per dataset.

    Returns:
        list[str]: Markdown lines.
    """
    lines = ["| Dataset | Records | Items | Files | Observed | Content |", "|---|---|---|---|---|---|"]
    for dataset, data in datasets.items():
        observed = sorted(str(r.get("observed_at"))[:10] for r in data.records if r.get("observed_at"))
        items = len({record.get("record_id") for record in data.records})
        span = f"{observed[0]} to {observed[-1]}" if observed else "none"
        lines.append(f"| `{dataset}` | {len(data.records):,} | {items:,} | {data.files} | {span} | "
                     f"{_cell(_description(dataset))} |")
    return lines


def _is_filled(value: Any) -> bool:
    """Tells whether a field has a value: not null and not an empty text, list or object."""
    return value is not None and value != "" and value != [] and value != {}


def _distinct(values: list[Any]) -> int:
    """Counts the distinct values of a field: list elements and object keys are counted individually."""
    distinct: set[str] = set()
    for value in values:
        if isinstance(value, list):
            distinct.update(json.dumps(item, sort_keys=True, ensure_ascii=False) for item in value)
        elif isinstance(value, dict):
            distinct.update(str(key) for key in value)
        elif value is not None:
            distinct.add(json.dumps(value, ensure_ascii=False))
    return len(distinct)


def field_lines(datasets: dict[str, DatasetData]) -> list[str]:
    """Builds one collapsible field table per dataset with records.

    Args:
        datasets: Records per dataset.

    Returns:
        list[str]: Markdown lines; empty if no dataset has records.
    """
    lines: list[str] = []
    for dataset, data in datasets.items():
        if not data.records:
            continue
        total = len(data.records)
        lines.extend([
            "<details>",
            f"<summary><code>{dataset}</code>: {len(DATASET_MODELS[dataset].model_fields)} fields</summary>",
            "",
            "| Field | Description | Filled | Distinct values |",
            "|---|---|---|---|",
        ])
        for name, field in DATASET_MODELS[dataset].model_fields.items():
            values = [record.get(name) for record in data.records]
            filled = sum(1 for value in values if _is_filled(value))
            lines.append(f"| `{name}` | {_cell(field.description)} | {_share(filled, total)} | {_distinct(values):,} |")
        lines.extend(["", "</details>", ""])
    return lines


def chart_lines(charts_path: Path, datasets: dict[str, DatasetData]) -> list[str]:
    """Summarizes the charts: stored lists and the first entries of the latest list of every chart.

    Args:
        charts_path: The chart configuration.
        datasets: Records per dataset.

    Returns:
        list[str]: Markdown lines.
    """
    try:
        charts = load_chart_config(charts_path).active_charts
    except (FileNotFoundError, ChartConfigError) as e:
        return [f"- Chart configuration not readable: {_cell(str(e))[:200]}"]
    latest: dict[str, dict[str, Any]] = {}
    counts: Counter[str] = Counter()
    for record in datasets["chart_snapshots"].records:
        chart_id = str(record.get("chart_id"))
        counts[chart_id] += 1
        key = (str(record.get("chart_date")), int(record.get("revision") or 0), str(record.get("observed_at")))
        current = latest.get(chart_id)
        if current is None or key >= (str(current.get("chart_date")), int(current.get("revision") or 0),
                                      str(current.get("observed_at"))):
            latest[chart_id] = record
    lines = [
        f"- **Active charts:** {len(charts)}; with stored lists: {sum(1 for chart in charts if chart.id in latest)}",
        "",
        "| Chart | Lists | Latest list | First entries |",
        "|---|---|---|---|",
    ]
    for chart in charts:
        record = latest.get(chart.id)
        if record is None:
            lines.append(f"| {_cell(chart.name)} | 0 | none | |")
            continue
        entries = record.get("entries") or []
        preview = "; ".join(
            f"{entry.get('rank')}. {entry.get('title')}" + (f" ({entry['creator']})" if entry.get("creator") else "")
            for entry in entries[:CHART_PREVIEW_ENTRIES])
        lines.append(f"| {_cell(chart.name)} | {counts[chart.id]:,} | {record.get('chart_date')} | {_cell(preview)} |")
    return lines


def pageview_lines(entities_path: Path, datasets: dict[str, DatasetData], now: datetime) -> list[str]:
    """Summarizes the entity registry and the entities with the most views in the last days.

    Args:
        entities_path: The entity registry.
        datasets: Records per dataset.
        now: Current UTC time.

    Returns:
        list[str]: Markdown lines.
    """
    try:
        entities = [entity for entity in load_registry(entities_path) if entity.active]
    except (FileNotFoundError, RegistryError) as e:
        return [f"- Entity registry not readable: {_cell(str(e))[:200]}"]
    names = {entity.id: entity.name for entity in entities}
    types = Counter(entity.entity_type for entity in entities)
    lines = [f"- **Registered entities:** {len(entities):,} ("
             + ", ".join(f"{name.replace('_', ' ')} {count}" for name, count in types.most_common()) + ")"]
    views: dict[str, dict[str, int]] = defaultdict(dict)
    for record in datasets["wikipedia_pageviews"].records:
        for day, count in (record.get("daily_views") or {}).items():
            views[str(record.get("entity_id"))][str(day)] = int(count)
    if not views:
        return lines
    last_day = max(day for series in views.values() for day in series)
    first_day = (datetime.fromisoformat(last_day) - timedelta(days=PAGEVIEW_WINDOW_DAYS - 1)).date().isoformat()
    totals = Counter({entity_id: sum(count for day, count in series.items() if first_day <= day <= last_day)
                      for entity_id, series in views.items()})
    lines.extend(["", f"**Most viewed entities, {first_day} to {last_day}**", "",
                  "| Entity | Views |", "|---|---|"])
    lines.extend(f"| {_cell(names.get(entity_id, entity_id))} | {count:,} |"
                 for entity_id, count in totals.most_common(TOP_ROWS))
    return lines


def source_lines(state_dir: Path, config_path: Path, datasets: dict[str, DatasetData], now: datetime) -> list[str]:
    """Summarizes the status of the sources and builds a collapsible table per source.

    Args:
        state_dir: Folder of the state files.
        config_path: The source configuration.
        datasets: Records per dataset.
        now: Current UTC time.

    Returns:
        list[str]: Markdown lines.
    """
    if not config_path.exists():
        return ["- No sources configured."]
    config = load_config(config_path)
    health = SourceHealthStore.load(state_dir)
    items = [record for dataset in ITEM_DATASETS for record in datasets[dataset].records]
    annotated = {record.get("item_id") for record in datasets["text_annotations"].records}
    by_source: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        by_source.setdefault(str(item.get("source_id")), []).append(item)
    cutoff = (now - timedelta(days=RECENT_WINDOW_DAYS)).isoformat()
    lines = []
    tables: list[str] = []
    groups = (("News outlets", config.outlets, "outlet_type"),
              ("Press-release sources", config.press_sources, "actor_type"))
    for label, sources, type_attribute in groups:
        counts = Counter(health.status_of(source) for source in sources)
        text = ", ".join(f"{count} {status}" for status, count in sorted(counts.items())) or "none"
        lines.append(f"- **{label}:** {len(sources)} configured ({text})")
        tables.extend([
            "<details>",
            f"<summary>{label}: items per source</summary>",
            "",
            "| Source | Type | Status | Items | Last 30 days | Annotated | With lead text | Latest publication |",
            "|---|---|---|---|---|---|---|---|",
        ])
        for source in sorted(sources, key=lambda s: s.name.casefold()):
            source_items = by_source.get(source.id, [])
            recent = sum(1 for item in source_items if str(item.get("observed_at", "")) >= cutoff)
            with_labels = sum(1 for item in source_items if item.get("record_id") in annotated)
            with_lead = sum(1 for item in source_items if item.get("content_basis") == "lead_text")
            published = [str(item["published_at"])[:10] for item in source_items if item.get("published_at")]
            observed = [str(item["observed_at"])[:10] for item in source_items if item.get("observed_at")]
            latest = max(published or observed, default="none")
            tables.append(
                f"| {_cell(source.name)} | {getattr(source, type_attribute).replace('_', ' ')} | "
                f"{health.status_of(source)} | {len(source_items):,} | {recent:,} | "
                f"{_share(with_labels, len(source_items))} | {_share(with_lead, len(source_items))} | {latest} |"
            )
        tables.extend(["", "</details>", ""])
    return [*lines, "", *tables]


def _top_table(title: str, header: str, counts: Counter[str], total: int) -> list[str]:
    """Builds a table of the most frequent values with their share of the items."""
    if not counts:
        return []
    lines = ["", f"**{title}**", "", f"| {header} | Items | Share |", "|---|---|---|"]
    lines.extend(f"| {_cell(value)} | {count:,} | {count / total:.1%} |" for value, count in counts.most_common(TOP_ROWS))
    return lines


def annotation_lines(datasets: dict[str, DatasetData], now: datetime) -> list[str]:
    """Summarizes the annotation coverage and the pop culture items of the last 30 days.

    Args:
        datasets: Records per dataset.
        now: Current UTC time.

    Returns:
        list[str]: Markdown lines.
    """
    items = [record for dataset in ITEM_DATASETS for record in datasets[dataset].records]
    annotations = datasets["text_annotations"].records
    if not items:
        return ["- No news articles or press releases yet."]
    annotated_ids = {record.get("item_id") for record in annotations}
    annotated = sum(1 for item in items if item.get("record_id") in annotated_ids)
    basis = Counter(item.get("content_basis") for item in items)
    lines = [
        f"- **Annotated items:** {annotated:,} of {len(items):,} ({annotated / len(items):.1%})",
        "- **Text seen by the model:** " + ", ".join(f"{name} {count:,}" for name, count in basis.most_common()),
    ]
    cutoff = (now - timedelta(days=RECENT_WINDOW_DAYS)).isoformat()
    recent = [record for record in annotations
              if record.get("is_pop_culture") and str(record.get("observed_at", "")) >= cutoff]
    if not recent:
        return lines
    total = len(recent)
    lines.append(f"- **Pop culture items of the last {RECENT_WINDOW_DAYS} days:** {total:,}")
    domains = Counter(str(record.get("domain")) for record in recent)
    people: Counter[str] = Counter()
    products: Counter[str] = Counter()
    advice: Counter[str] = Counter()
    stances: dict[str, Counter[str]] = defaultdict(Counter)
    for record in recent:
        people.update({str(person.get("wikipedia_title")) for person in record.get("people") or []})
        products.update({str(product.get("name")) for product in record.get("products") or []})
        advice.update(set(record.get("lifestyle_advice") or []))
        for stance in record.get("stances") or []:
            stances[str(stance.get("target"))][str(stance.get("stance"))] += 1
    lines.extend(_top_table("Main fields", "Field", domains, total))
    lines.extend(_top_table("People named most often", "Person or group", people, total))
    lines.extend(_top_table("Brands and products named most often", "Brand or product", products, total))
    lines.extend(_top_table("Lifestyle advice", "Topic", advice, total))
    if stances:
        ranked = sorted(stances.items(), key=lambda pair: sum(pair[1].values()), reverse=True)[:TOP_ROWS]
        lines.extend(["", "**Opinions: targets named most often**", "",
                      "| Target | Mentions | Favorable | Unfavorable | Neutral or mixed |", "|---|---|---|---|---|"])
        for target, counts in ranked:
            mentions = sum(counts.values())
            other = mentions - counts["Favorable"] - counts["Unfavorable"]
            lines.append(f"| {_cell(target)} | {mentions:,} | {counts['Favorable']:,} | {counts['Unfavorable']:,} | "
                         f"{other:,} |")
    return lines


def last_run_lines(state_dir: Path) -> list[str]:
    """Summarizes the latest run from the run log.

    Args:
        state_dir: Folder of the state files.

    Returns:
        list[str]: Markdown lines.
    """
    path = state_dir / RUN_LOG_FILE
    if not path.exists():
        return ["- No run yet."]
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    entry = json.loads(lines[-1])
    rows = [f"- **Latest run:** {entry.get('started', '?')[:16].replace('T', ' ')} UTC ({entry.get('mode')})"]
    for name, outcome in entry.get("collectors", {}).items():
        records = ", ".join(f"{dataset} {count:,}" for dataset, count in outcome.get("records", {}).items())
        error = f"; {outcome['error']}" if outcome.get("error") else ""
        rows.append(f"  - `{name}`: {outcome.get('status')}{' (' + records + ')' if records else ''}{error}")
    return rows


def build_overview(data_dir: Path, config_path: Path = CONFIG_PATH, now: Optional[datetime] = None,
                   charts_path: Path = CHARTS_PATH, entities_path: Path = ENTITIES_PATH) -> str:
    """Builds the Markdown of the overview.

    Args:
        data_dir: Root folder of the datasets.
        config_path: The source configuration.
        now: Current UTC time; defaults to now.
        charts_path: The chart configuration.
        entities_path: The entity registry.

    Returns:
        str: The overview without the markers.
    """
    now = now or datetime.now(timezone.utc)
    datasets = load_datasets(data_dir)
    state_dir = data_dir / "state"
    fields = field_lines(datasets)
    parts = [
        f"**Last updated:** {now:%Y-%m-%d %H:%M} UTC",
        "",
        *dataset_lines(datasets),
        "",
        "**Runs**",
        "",
        *last_run_lines(state_dir),
        "",
        "**Charts**",
        "",
        *chart_lines(charts_path, datasets),
        "",
        "**Entities and Wikipedia views**",
        "",
        *pageview_lines(entities_path, datasets, now),
        "",
        "**Sources**",
        "",
        *source_lines(state_dir, config_path, datasets, now),
        "**Annotations**",
        "",
        *annotation_lines(datasets, now),
    ]
    if fields:
        parts.extend(["", "**Fields** (Filled: records with a value that is not null and not empty)", "", *fields])
    return "\n".join(parts).rstrip()


def update_readme(readme: Path, overview: str) -> bool:
    """Replaces the text between the markers of the README.

    Args:
        readme: The README file.
        overview: The new overview.

    Returns:
        bool: True if the README changed.

    Raises:
        ValueError: If the markers are missing.
    """
    text = readme.read_text(encoding="utf-8")
    start = text.find(START_MARKER)
    end = text.find(END_MARKER)
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"{readme} has no overview markers")
    updated = text[: start + len(START_MARKER)] + "\n" + overview + "\n" + text[end:]
    if updated == text:
        return False
    readme.write_text(updated, encoding="utf-8", newline="\n")
    return True


def main(argv: Optional[list[str]] = None) -> int:
    """Writes the overview into the README.

    Args:
        argv: Arguments without the program name; None reads ``sys.argv``.

    Returns:
        int: 0 on success, 1 if the README has no markers.
    """
    parser = argparse.ArgumentParser(description="Writes the dataset overview into the README.")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--readme", type=Path, default=Path("README.md"))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    try:
        changed = update_readme(args.readme, build_overview(args.data_dir))
    except ValueError as e:
        logger.error("%s", e)
        return 1
    logger.info("README overview %s.", "updated" if changed else "unchanged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
