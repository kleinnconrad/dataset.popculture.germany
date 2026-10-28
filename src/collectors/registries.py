"""
Writes the entries of the chart configuration and the entity registry to their datasets.

``config/charts.toml`` and ``config/entities.toml`` are the slowly changing dimensions of the
dataset: every entry is written when it appears and again, with the next revision, when one of
its fields changes, so that the definition of a chart or entity at time t can be reconstructed
from the data alone. The collectors send no requests.
"""
import logging
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from chart_catalog import CONFIG_PATH as CHARTS_PATH
from chart_catalog import Chart, load_chart_config
from collectors.base import Collector, CollectorResult, RunContext
from entities import CONFIG_PATH as ENTITIES_PATH
from entities import Entity, load_registry
from schema import ChartCatalogRecord, EntityRecord, RecordBase
from state import CollectorEntry, content_hash

logger = logging.getLogger(__name__)

SOURCE_ID = "registry"
LICENSE = "Configuration of this project; no license granted"
REPOSITORY_URL = "https://github.com/kleinnconrad/dataset.popculture.germany/blob/main/"


def chart_fields(chart: Chart) -> dict[str, Any]:
    """Returns the dataset fields of a configured chart.

    Args:
        chart: The chart.

    Returns:
        dict: The fields of a ``ChartCatalogRecord`` without the common fields.
    """
    return {
        "chart_id": chart.id, "name": chart.name, "domain": chart.domain, "provider": chart.provider,
        "format": chart.format, "url": chart.url, "country": chart.country, "period": chart.period,
        "entry_kind": chart.entry_kind, "max_entries": chart.max_entries, "added": chart.added,
        "removed": chart.removed, "removed_reason": chart.removed_reason,
    }


def entity_fields(entity: Entity) -> dict[str, Any]:
    """Returns the dataset fields of a registry entity.

    Args:
        entity: The registry entry.

    Returns:
        dict: The fields of an ``EntityRecord`` without the common fields.
    """
    return {
        "entity_id": entity.id, "name": entity.name, "entity_type": entity.entity_type, "domain": entity.domain,
        "wikipedia_title": entity.wikipedia_title, "added": entity.added, "removed": entity.removed,
        "removed_reason": entity.removed_reason,
    }


def emit_changes(ctx: RunContext, dataset: str, items: Sequence[tuple[str, dict[str, Any]]],
                 model: Callable[..., RecordBase], source_url: str) -> dict[str, int]:
    """Emits a record for every item that is new or changed since the previous run.

    Args:
        ctx: The run context.
        dataset: Name of the dataset.
        items: Key and record fields of every item.
        model: Record model of the dataset.
        source_url: URL of the configuration file.

    Returns:
        dict[str, int]: Counts of all, new and changed items.
    """
    seen = ctx.seen(dataset)
    counters = {"items": len(items), "new": 0, "changed": 0}
    records = []
    for key, fields in items:
        item_hash = content_hash(fields)
        status = seen.classify(key, item_hash)
        if status == "unchanged":
            continue
        counters[status] += 1
        revision = seen.mark(key, item_hash, ctx.today)
        records.append(model(**ctx.envelope(dataset, f"{dataset}:{key}", revision=revision, source_id=SOURCE_ID,
                                            source_url=source_url, license=LICENSE, backfilled=False), **fields))
    ctx.emit(dataset, records)
    logger.info("%s: %d new, %d changed of %d entries.", dataset, counters["new"], counters["changed"], len(items))
    return counters


class ChartCatalogCollector(Collector):
    """Writes new and changed entries of the chart configuration."""
    name = "chart_catalog"
    datasets = ("chart_catalog",)

    def __init__(self, config_path: Path = CHARTS_PATH) -> None:
        """Initializes the collector.

        Args:
            config_path: The chart configuration.
        """
        self._config_path = config_path

    def collect(self, ctx: RunContext, entry: CollectorEntry) -> CollectorResult:
        """Emits a record for every configured chart that is new or changed.

        Args:
            ctx: The run context.
            entry: Run history of this collector.

        Returns:
            CollectorResult: Counters.
        """
        items = [(chart.id, chart_fields(chart)) for chart in load_chart_config(self._config_path).charts]
        return CollectorResult(counters=emit_changes(ctx, "chart_catalog", items, ChartCatalogRecord,
                                                     REPOSITORY_URL + "config/charts.toml"))


class EntitiesCollector(Collector):
    """Writes new and changed entries of the entity registry."""
    name = "entities"
    datasets = ("entities",)

    def __init__(self, registry_path: Path = ENTITIES_PATH) -> None:
        """Initializes the collector.

        Args:
            registry_path: The entity registry.
        """
        self._registry_path = registry_path

    def collect(self, ctx: RunContext, entry: CollectorEntry) -> CollectorResult:
        """Emits a record for every registry entity that is new or changed.

        Args:
            ctx: The run context.
            entry: Run history of this collector.

        Returns:
            CollectorResult: Counters.
        """
        items = [(entity.id, entity_fields(entity)) for entity in load_registry(self._registry_path)]
        return CollectorResult(counters=emit_changes(ctx, "entities", items, EntityRecord,
                                                     REPOSITORY_URL + "config/entities.toml"))
