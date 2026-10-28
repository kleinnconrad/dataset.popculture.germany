"""
Builds a table of weekly indicators of German pop culture from the datasets.

The table is long: one row per ISO week, source, variable and key, with the
value and the number of observations it is based on. It is derived data for
analysis and for the correlation with the other datasets, which aggregate to
the same weeks (``YYYY-Www``); it is written to ``features/`` and not committed.

- ``annotations``: the labels of the pop culture items, dated by the
  publication of the item (or its observation): shares of fields, tones and
  forms, mentions and balances of opinions per target (also of celebrities and
  influencers only), lifestyle advice, forms of family, values, generations,
  platforms, products, people and works;
- ``items``: the news items and press releases per outlet type and audience;
- ``charts``: the positions in the latest list of every chart in the week and
  the days on which an entry was in a list;
- ``pageviews``: the Wikipedia views per registered entity.

A balance is (favorable - unfavorable) / mentions, from -1 to 1.

Usage:
    uv run python scripts/build_weekly.py [--data-dir data] [--output features/weekly_indicators.csv]
"""
import argparse
import csv
import json
import logging
import sys
from collections import Counter, defaultdict
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

COLUMNS = ("week", "source", "variable", "key", "value", "n")
ITEM_DATASETS = ("news_items", "press_releases")
# Rows per week of the variables with many keys (products, people, works)
TOP_KEYS = 25
CHART_TOP = 10
CELEBRITY_HOLDER = "Celebrity or influencer"
# Yes/no labels whose share of the pop culture items is reported
FLAGS = ("purchase_recommendation", "sponsored", "reports_controversy")

Row = tuple[str, str, str, str, float, int]


def iso_week(day: date) -> str:
    """Returns the ISO week of a day, e.g. ``2026-W44``."""
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def _day(value: Any) -> Optional[date]:
    """Parses the day of an ISO date or time; None if it cannot be parsed."""
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def read_dataset(data_dir: Path, dataset: str) -> Iterator[dict[str, Any]]:
    """Yields the records of a dataset in the order of its files.

    Args:
        data_dir: Root folder of the datasets.
        dataset: Name of the dataset.

    Yields:
        dict: Each record.
    """
    for path in sorted((data_dir / dataset).glob("year=*/month=*/*.json")):
        yield from json.loads(path.read_text(encoding="utf-8"))


def _balance(counts: Counter[str]) -> float:
    """Returns (favorable - unfavorable) / mentions."""
    total = sum(counts.values())
    return (counts["Favorable"] - counts["Unfavorable"]) / total if total else 0.0


def _tone_balance(counts: Counter[str]) -> float:
    """Returns (positive - negative) / mentions."""
    total = sum(counts.values())
    return (counts["Positive"] - counts["Negative"]) / total if total else 0.0


def annotation_rows(data_dir: Path) -> list[Row]:
    """Aggregates the annotations of pop culture items per ISO week.

    Args:
        data_dir: Root folder of the datasets.

    Returns:
        list[Row]: The rows of the source ``annotations``.
    """
    published: dict[str, Optional[date]] = {}
    for dataset in ITEM_DATASETS:
        for item in read_dataset(data_dir, dataset):
            published[str(item.get("record_id"))] = _day(item.get("published_at")) or _day(item.get("observed_at"))
    weeks: dict[str, list[dict[str, Any]]] = defaultdict(list)
    annotated: Counter[str] = Counter()
    for record in read_dataset(data_dir, "text_annotations"):
        day = published.get(str(record.get("item_id"))) or _day(record.get("observed_at"))
        if day is None:
            continue
        week = iso_week(day)
        annotated[week] += 1
        if record.get("is_pop_culture"):
            weeks[week].append(record)
    rows: list[Row] = []
    for week in sorted(annotated):
        items = weeks.get(week, [])
        n = len(items)
        rows.append((week, "annotations", "annotated_items", "", float(annotated[week]), annotated[week]))
        rows.append((week, "annotations", "pop_culture_items", "", float(n), annotated[week]))
        if not n:
            continue
        shares: dict[str, Counter[str]] = defaultdict(Counter)
        flags: Counter[str] = Counter()
        counts: dict[str, Counter[str]] = defaultdict(Counter)
        stances: dict[str, Counter[str]] = defaultdict(Counter)
        celebrity_stances: dict[str, Counter[str]] = defaultdict(Counter)
        family: dict[str, Counter[str]] = defaultdict(Counter)
        for record in items:
            shares["domain_share"][str(record.get("domain"))] += 1
            shares["tone_share"][str(record.get("overall_tone"))] += 1
            shares["article_type_share"][str(record.get("article_type"))] += 1
            flags.update(flag for flag in FLAGS if record.get(flag))
            counts["lifestyle_advice"].update(set(record.get("lifestyle_advice") or []))
            counts["promoted_value"].update(set(record.get("promoted_values") or []))
            counts["generation"].update(set(record.get("generations") or []))
            counts["platform"].update(set(record.get("platforms") or []))
            counts["origin_country"].update(set(record.get("content_origin_countries") or []))
            products = record.get("products") or []
            counts["product_category"].update({str(product.get("category")) for product in products})
            counts["product"].update({str(product.get("name")) for product in products})
            counts["product_recommended"].update({str(product.get("name")) for product in products
                                                  if product.get("role") in ("Recommended", "Advertised")})
            counts["person"].update({f"{person.get('wikidata_qid')} {person.get('wikipedia_title')}"
                                     for person in record.get("people") or []})
            counts["work"].update({f"{work.get('kind')}: {work.get('title')}" for work in record.get("works") or []})
            for stance in record.get("stances") or []:
                stances[str(stance.get("target"))][str(stance.get("stance"))] += 1
                if stance.get("holder") == CELEBRITY_HOLDER:
                    celebrity_stances[str(stance.get("target"))][str(stance.get("stance"))] += 1
            for form, tone in (record.get("family_forms") or {}).items():
                family[str(form)][str(tone)] += 1
        for variable, counter in shares.items():
            rows.extend((week, "annotations", variable, key, count / n, n) for key, count in sorted(counter.items()))
        rows.extend((week, "annotations", f"{flag}_share", "", flags[flag] / n, n) for flag in FLAGS)
        for variable, counter in counts.items():
            limit = TOP_KEYS if variable in ("product", "product_recommended", "person", "work") else None
            rows.extend((week, "annotations", variable, key, float(count), n)
                        for key, count in counter.most_common(limit))
        for target, counter in sorted(stances.items()):
            mentions = sum(counter.values())
            rows.append((week, "annotations", "stance_mentions", target, float(mentions), n))
            rows.append((week, "annotations", "stance_balance", target, _balance(counter), mentions))
        for target, counter in sorted(celebrity_stances.items()):
            rows.append((week, "annotations", "stance_balance_celebrities", target, _balance(counter),
                         sum(counter.values())))
        for form, counter in sorted(family.items()):
            mentions = sum(counter.values())
            rows.append((week, "annotations", "family_form_mentions", form, float(mentions), n))
            rows.append((week, "annotations", "family_form_tone_balance", form, _tone_balance(counter), mentions))
    return rows


def item_rows(data_dir: Path) -> list[Row]:
    """Counts the news items and press releases per ISO week, outlet type and audience.

    Args:
        data_dir: Root folder of the datasets.

    Returns:
        list[Row]: The rows of the source ``items``.
    """
    counts: dict[str, Counter[tuple[str, str]]] = defaultdict(Counter)
    for dataset in ITEM_DATASETS:
        for item in read_dataset(data_dir, dataset):
            day = _day(item.get("published_at")) or _day(item.get("observed_at"))
            if day is None:
                continue
            week = iso_week(day)
            if dataset == "news_items":
                counts[week][("outlet_type", str(item.get("outlet_type")))] += 1
                counts[week][("audience", str(item.get("audience")))] += 1
            else:
                counts[week][("actor_type", str(item.get("actor_type")))] += 1
    rows: list[Row] = []
    for week in sorted(counts):
        rows.extend((week, "items", f"items_by_{variable}", key, float(count), count)
                    for (variable, key), count in sorted(counts[week].items()))
    return rows


def chart_rows(data_dir: Path) -> list[Row]:
    """Takes the latest list of every chart in each ISO week and the days on which entries were listed.

    Args:
        data_dir: Root folder of the datasets.

    Returns:
        list[Row]: The rows of the source ``charts``.
    """
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    presence: dict[tuple[str, str], dict[str, set[date]]] = defaultdict(lambda: defaultdict(set))
    for record in read_dataset(data_dir, "chart_snapshots"):
        day = _day(record.get("chart_date"))
        if day is None:
            continue
        key = (iso_week(day), str(record.get("chart_id")))
        order = (str(record.get("chart_date")), int(record.get("revision") or 0), str(record.get("observed_at")))
        current = latest.get(key)
        if current is None or order >= (str(current.get("chart_date")), int(current.get("revision") or 0),
                                        str(current.get("observed_at"))):
            latest[key] = record
        for entry in record.get("entries") or []:
            presence[key][_entry_key(entry)].add(day)
    rows: list[Row] = []
    for (week, chart_id), record in sorted(latest.items()):
        for entry in (record.get("entries") or [])[:CHART_TOP]:
            name = _entry_key(entry)
            rows.append((week, "charts", "chart_rank", f"{chart_id} | {name}", float(entry.get("rank") or 0), 1))
            rows.append((week, "charts", "chart_days_listed", f"{chart_id} | {name}",
                         float(len(presence[(week, chart_id)][name])), 7))
    return rows


def _entry_key(entry: dict[str, Any]) -> str:
    """Returns the name of a chart entry: the title and, if given, the creator."""
    title = str(entry.get("title"))
    return f"{title} – {entry['creator']}" if entry.get("creator") else title


def pageview_rows(data_dir: Path) -> list[Row]:
    """Sums the Wikipedia views per ISO week and entity.

    Args:
        data_dir: Root folder of the datasets.

    Returns:
        list[Row]: The rows of the source ``pageviews``.
    """
    views: dict[tuple[str, str], dict[str, int]] = defaultdict(dict)
    for record in read_dataset(data_dir, "wikipedia_pageviews"):
        for day_text, count in (record.get("daily_views") or {}).items():
            day = _day(day_text)
            if day is not None and isinstance(count, int):
                views[(iso_week(day), str(record.get("entity_id")))][day_text] = count
    return [(week, "pageviews", "pageviews", entity_id, float(sum(days.values())), len(days))
            for (week, entity_id), days in sorted(views.items())]


def build_rows(data_dir: Path) -> list[Row]:
    """Builds all rows of the weekly table.

    Args:
        data_dir: Root folder of the datasets.

    Returns:
        list[Row]: The rows, sorted by week, source, variable and key.
    """
    rows = [*annotation_rows(data_dir), *item_rows(data_dir), *chart_rows(data_dir), *pageview_rows(data_dir)]
    return sorted(rows, key=lambda row: (row[0], row[1], row[2], row[3]))


def write_csv(rows: list[Row], output: Path) -> None:
    """Writes the rows as CSV with a header.

    Args:
        rows: The rows.
        output: The CSV file; its folder is created if needed.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        writer.writerows((week, source, variable, key, f"{value:.6g}", n) for week, source, variable, key, value, n
                         in rows)


def main(argv: Optional[list[str]] = None) -> int:
    """Writes the weekly table.

    Args:
        argv: Arguments without the program name; None reads ``sys.argv``.

    Returns:
        int: 0 on success, 1 if the data folder does not exist.
    """
    parser = argparse.ArgumentParser(description="Builds the weekly indicators of German pop culture.")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--output", type=Path, default=Path("features") / "weekly_indicators.csv")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    if not args.data_dir.exists():
        logger.error("Data folder %s does not exist.", args.data_dir)
        return 1
    started = datetime.now()
    rows = build_rows(args.data_dir)
    write_csv(rows, args.output)
    weeks = sorted({row[0] for row in rows})
    logger.info("Wrote %d rows for %d weeks (%s to %s) to %s in %.1f s.", len(rows), len(weeks),
                weeks[0] if weeks else "-", weeks[-1] if weeks else "-", args.output,
                (datetime.now() - started).total_seconds())
    return 0


if __name__ == "__main__":
    sys.exit(main())
