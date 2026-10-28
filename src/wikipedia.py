"""
Resolves titles of the German Wikipedia to existing articles and their Wikidata ids.

The MediaWiki API (de.wikipedia.org) follows normalizations and redirects
and returns the Wikidata id of every article. Disambiguation pages are not
articles. The resolution serves two purposes:
- the entity registry configures article titles; the pageviews collector
  resolves them to the current title before it requests the views;
- the annotation returns names of public figures; a name counts as a
  public figure only if it is the title of (or redirects to) an article of the
  German Wikipedia, and the person is stored by the Wikidata id of that
  article. Other names are counted, not stored.
"""
import logging
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any, Optional

from web import WebClient

logger = logging.getLogger(__name__)

WIKIPEDIA_API_URL = "https://de.wikipedia.org/w/api.php"
WIKIPEDIA_PROJECT = "de.wikipedia"
# Titles per MediaWiki API request (the limit for clients without bot rights)
API_BATCH_SIZE = 50
# Characters that MediaWiki does not allow in titles; "|" would also split the titles parameter
INVALID_TITLE_CHARS = frozenset("#<>[]{}|")
MAX_TITLE_CHARS = 255


@dataclass(frozen=True)
class Article:
    """An existing article of the German Wikipedia.

    Attributes:
        title (str): Current title of the article.
        wikidata_qid (Optional[str]): Wikidata id of the article, if it has one.
    """
    title: str
    wikidata_qid: Optional[str]


def _batches(values: Sequence[str], size: int) -> Iterator[Sequence[str]]:
    """Yields consecutive slices of at most ``size`` values."""
    for start in range(0, len(values), size):
        yield values[start:start + size]


def is_valid_title(title: str) -> bool:
    """Tells whether a text can be a Wikipedia title.

    Args:
        title: The text.

    Returns:
        bool: False for empty or long texts and for texts with characters that titles cannot contain.
    """
    stripped = title.strip()
    return bool(stripped) and len(stripped) <= MAX_TITLE_CHARS and not (set(stripped) & INVALID_TITLE_CHARS)


def parse_query(data: dict[str, Any], titles: Sequence[str], allow_fragments: bool) -> dict[str, Article]:
    """Reads the response of a MediaWiki query for titles with page properties.

    Args:
        data: The JSON response (``formatversion=2``).
        titles: The requested titles.
        allow_fragments: Whether a redirect to a section of another article counts as a match.

    Returns:
        dict[str, Article]: Article by requested title, for titles that lead to an article that
        exists and is not a disambiguation page.
    """
    query = data.get("query", {})
    mapping = {title: title for title in titles}
    excluded: set[str] = set()
    for step in ("normalized", "redirects"):
        renames: dict[str, str] = {}
        to_sections: set[str] = set()
        for item in query.get(step, []):
            if "from" not in item or "to" not in item:
                continue
            renames[item["from"]] = item["to"]
            if item.get("tofragment"):
                to_sections.add(item["from"])
        if not allow_fragments:
            excluded.update(original for original, current in mapping.items() if current in to_sections)
        mapping = {original: renames.get(current, current) for original, current in mapping.items()}
    pages: dict[str, dict[str, Any]] = {}
    for page in query.get("pages", []):
        if page.get("missing") or page.get("invalid") or "title" not in page:
            continue
        properties = page.get("pageprops") or {}
        if "disambiguation" in properties:
            continue
        pages[page["title"]] = properties
    resolved: dict[str, Article] = {}
    for original, current in mapping.items():
        if original in excluded or current not in pages:
            continue
        qid = pages[current].get("wikibase_item")
        resolved[original] = Article(current, qid if isinstance(qid, str) else None)
    return resolved


def resolve_titles(web: WebClient, titles: Iterable[str], allow_fragments: bool = True) -> dict[str, Article]:
    """Resolves titles to the existing articles of the German Wikipedia.

    Args:
        web: The HTTP client.
        titles: Candidate titles; invalid titles are skipped.
        allow_fragments: Whether a redirect to a section of another article counts as a match; False
            for names of persons, whose redirect to a section (e.g. of their band) is not their own article.

    Returns:
        dict[str, Article]: Article by candidate title, for the titles that resolve.

    Raises:
        FetchError: If a request fails.
    """
    unique = list(dict.fromkeys(title.strip() for title in titles if is_valid_title(title)))
    resolved: dict[str, Article] = {}
    for batch in _batches(unique, API_BATCH_SIZE):
        data = web.get(WIKIPEDIA_API_URL, respect_robots=False, params={
            "action": "query", "titles": "|".join(batch), "redirects": "1", "prop": "pageprops",
            "ppprop": "wikibase_item|disambiguation", "format": "json", "formatversion": "2",
        }).json()
        resolved.update(parse_query(data, batch, allow_fragments))
    return resolved
