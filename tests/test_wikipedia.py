"""Tests of the resolution of German Wikipedia titles."""
from wikipedia import WIKIPEDIA_API_URL, is_valid_title, parse_query, resolve_titles

ANSWER = {"batchcomplete": True, "query": {
    "normalized": [{"fromencoded": False, "from": "iPhone", "to": "IPhone"}],
    "redirects": [{"from": "Knossi", "to": "Jens Knossalla"},
                  {"from": "Bill Kaulitz", "to": "Tokio Hotel", "tofragment": "Besetzung"}],
    "pages": [
        {"ns": 0, "title": "IPhone", "pageprops": {"wikibase_item": "Q2766"}},
        {"ns": 0, "title": "Jens Knossalla", "pageprops": {"wikibase_item": "Q18222351"}},
        {"ns": 0, "title": "Tokio Hotel", "pageprops": {"wikibase_item": "Q386216"}},
        {"ns": 0, "title": "Joyn", "pageprops": {"wikibase_item": "Q1", "disambiguation": ""}},
        {"ns": 0, "title": "Gibt es nicht", "missing": True},
    ],
}}
TITLES = ["iPhone", "Knossi", "Bill Kaulitz", "Joyn", "Gibt es nicht"]


def test_redirects_and_normalizations_are_followed() -> None:
    resolved = parse_query(ANSWER, TITLES, allow_fragments=True)

    assert resolved["iPhone"].title == "IPhone" and resolved["iPhone"].wikidata_qid == "Q2766"
    assert resolved["Knossi"].title == "Jens Knossalla"
    assert resolved["Bill Kaulitz"].title == "Tokio Hotel"
    assert "Joyn" not in resolved and "Gibt es nicht" not in resolved


def test_redirects_to_a_section_do_not_count_for_people() -> None:
    resolved = parse_query(ANSWER, TITLES, allow_fragments=False)

    assert "Bill Kaulitz" not in resolved
    assert resolved["Knossi"].wikidata_qid == "Q18222351"


def test_resolve_titles_skips_invalid_titles_and_sends_one_request_per_batch(fake_web) -> None:
    fake_web.add(WIKIPEDIA_API_URL, ANSWER)

    resolved = resolve_titles(fake_web.client(), ["Knossi", "Knossi", "A|B", "", "x" * 300])

    assert list(resolved) == ["Knossi"]
    assert len([url for url in fake_web.requested if url.startswith(WIKIPEDIA_API_URL)]) == 1
    assert "robots.txt" not in " ".join(fake_web.requested)


def test_valid_titles() -> None:
    assert is_valid_title("Ich bin ein Star – Holt mich hier raus!")
    assert not is_valid_title("Tom [Rapper]") and not is_valid_title("  ")
