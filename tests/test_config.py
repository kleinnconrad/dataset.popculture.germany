"""Checks of the real configuration files in config/."""
from collections import Counter
from typing import get_args

from chart_catalog import load_chart_config
from collectors.charts import PARSERS
from entities import load_registry
from schema import ChartFormat
from sources import load_config


def test_every_chart_format_has_a_parser() -> None:
    assert set(PARSERS) == set(get_args(ChartFormat))


def test_chart_config_loads_and_ids_are_unique() -> None:
    config = load_chart_config()
    assert config.active_charts
    ids = [chart.id for chart in config.charts] + [chart.id for chart in config.excluded]
    assert not [chart_id for chart_id, count in Counter(ids).items() if count > 1]
    for chart in config.charts:
        assert chart.id.split(".", 1)[0] == chart.provider
        config.provider(chart.provider)


def test_source_config_loads_and_ids_and_feeds_are_unique() -> None:
    config = load_config()
    assert config.active_sources
    ids = [source.id for source in config.sources] + [entry.id for entry in config.excluded]
    assert not [source_id for source_id, count in Counter(ids).items() if count > 1]
    feeds = [feed for source in config.sources for feed in source.feeds]
    assert not [feed for feed, count in Counter(feeds).items() if count > 1]
    for source in config.active_sources:
        assert source.feeds or getattr(source, "news_sitemap", None), source.id
    assert all(entry.reason.strip() for entry in config.excluded)


def test_entity_registry_loads() -> None:
    entities = load_registry()
    assert len(entities) >= 200
    assert all(entity.active for entity in entities)
