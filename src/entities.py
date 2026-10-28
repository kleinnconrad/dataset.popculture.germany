"""
The registry of tracked entities (``config/entities.toml``).

The registry lists people, groups, shows, platforms, brands, events,
institutions and concepts of pop culture and everyday life whose attention
the pipeline measures with the daily views of their German Wikipedia
articles. The entries are the stable backbone of the dataset: the Wikimedia
API keeps the views of every day, so the series can be compared over many
years. Concepts such as marriage, the Bundeswehr or veganism stand for the
topics on which the annotation records the opinions of pop culture media.

Every entry names the title of its German Wikipedia article; the pageviews
collector resolves it through redirects and records the Wikidata id. An
entity that is no longer collected keeps its entry with ``removed`` and
``removed_reason``.
"""
import re
import tomllib
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional, get_args

from schema import EntityType, PopDomain
from wikipedia import is_valid_title

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "entities.toml"

ID_PATTERN = re.compile(r"^[a-z0-9_]+$")
KNOWN_KEYS = frozenset({"id", "name", "type", "domain", "wikipedia", "added", "removed", "removed_reason"})


class RegistryError(ValueError):
    """The entity registry contains invalid or duplicate entries."""


@dataclass(frozen=True)
class Entity:
    """A tracked entity.

    Attributes:
        id (str): Stable id, e.g. ``helene_fischer``.
        name (str): Name of the entity.
        entity_type (str): Type (see ``schema.EntityType``).
        domain (str): Field of pop culture or society (see ``schema.PopDomain``).
        wikipedia_title (str): Title of the German Wikipedia article.
        added (date): Day on which the entity was added.
        removed (Optional[date]): Day on which the entity was removed.
        removed_reason (Optional[str]): Why the entity was removed.
    """
    id: str
    name: str
    entity_type: str
    domain: str
    wikipedia_title: str
    added: date
    removed: Optional[date] = None
    removed_reason: Optional[str] = None

    @property
    def active(self) -> bool:
        """True if the entity is collected, i.e. it was not removed."""
        return self.removed is None


def _date(raw: dict[str, Any], key: str) -> Optional[date]:
    """Reads an optional TOML date."""
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, date) or isinstance(value, datetime):
        raise ValueError(f"{key} must be a date (YYYY-MM-DD)")
    return value


def parse_entity(raw: dict[str, Any]) -> Entity:
    """Validates one ``[[entity]]`` entry.

    Args:
        raw: The entry as read from TOML.

    Returns:
        Entity: The entity.

    Raises:
        ValueError: If a field is missing, unknown or invalid.
    """
    unknown = sorted(set(raw) - KNOWN_KEYS)
    if unknown:
        raise ValueError(f"unknown keys: {', '.join(unknown)}")
    for key in ("id", "name", "type", "domain", "wikipedia"):
        if not isinstance(raw.get(key), str) or not raw[key].strip():
            raise ValueError(f"{key} must be a non-empty string")
    if not ID_PATTERN.match(raw["id"]):
        raise ValueError("id must consist of lower-case letters, digits and underscores")
    if raw["type"] not in get_args(EntityType):
        raise ValueError(f"unknown type {raw['type']!r}")
    if raw["domain"] not in get_args(PopDomain):
        raise ValueError(f"unknown domain {raw['domain']!r}")
    if not is_valid_title(raw["wikipedia"]):
        raise ValueError(f"wikipedia {raw['wikipedia']!r} is not a valid title")
    added = _date(raw, "added")
    if added is None:
        raise ValueError("added is missing")
    removed = _date(raw, "removed")
    removed_reason = raw.get("removed_reason")
    if (removed is None) != (removed_reason is None):
        raise ValueError("removed and removed_reason must be given together")
    return Entity(id=raw["id"], name=raw["name"].strip(), entity_type=raw["type"], domain=raw["domain"],
                  wikipedia_title=raw["wikipedia"].strip(), added=added, removed=removed,
                  removed_reason=removed_reason)


def load_registry(path: Path = CONFIG_PATH) -> list[Entity]:
    """Reads and validates the entity registry.

    Args:
        path: The registry file.

    Returns:
        list[Entity]: The entities in file order, including removed ones.

    Raises:
        RegistryError: If an entry is invalid, or an id or article title is used twice.
        FileNotFoundError: If the file does not exist.
    """
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    entities: list[Entity] = []
    problems: list[str] = []
    for position, raw in enumerate(data.get("entity", []), start=1):
        try:
            entities.append(parse_entity(raw))
        except ValueError as e:
            problems.append(f"entity {position} ({raw.get('id', '?')}): {e}")
    for label, values in (("ids", [e.id for e in entities]), ("titles", [e.wikipedia_title for e in entities])):
        duplicates = sorted(key for key, count in Counter(values).items() if count > 1)
        if duplicates:
            problems.append(f"duplicate {label}: {', '.join(duplicates)}")
    if problems:
        raise RegistryError(f"{path}: " + "; ".join(problems))
    return entities


def active_entities(path: Path = CONFIG_PATH) -> list[Entity]:
    """Returns the entities that are collected.

    Args:
        path: The registry file.

    Returns:
        list[Entity]: The active entities in file order.
    """
    return [entity for entity in load_registry(path) if entity.active]
