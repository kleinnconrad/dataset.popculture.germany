"""Tests of the source configuration and its lifecycle keys."""
from datetime import date
from pathlib import Path

import pytest

from sources import CONFIG_PATH, SourceHealth, SourceHealthStore, load_config

ENTRY = """\
[[outlet]]
id = "news_example"
name = "News Example"
homepage = "https://news.example/"
outlet_type = "national_daily"
owner_group = "Example Group"
paywall_model = "mixed"
feeds = ["https://news.example/rss"]
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "sources.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_lifecycle_keys_and_exclusions_are_read(tmp_path) -> None:
    text = (ENTRY + "added = 2026-10-25\nremoved = 2027-02-01\nremoved_reason = \"blocked\"\nkeep = true\n\n"
            "[[excluded]]\nid = \"zeit\"\nname = \"ZEIT ONLINE\"\nhomepage = \"https://www.zeit.de/\"\n"
            "status = \"tdm_reserved\"\nreason = \"TDMRep\"\nchecked = 2026-10-25\n")

    config = load_config(write(tmp_path, text))

    outlet = config.outlets[0]
    assert outlet.added == date(2026, 10, 25) and outlet.removed == date(2027, 2, 1)
    assert outlet.removed_reason == "blocked" and outlet.keep is True
    assert not outlet.active and config.active_sources == ()
    assert outlet.audience == "general" and outlet.focus is None
    assert config.excluded[0].status == "tdm_reserved"
    assert not config.excluded[0].rechecked


def test_unchecked_candidates_with_attributes_are_rechecked(tmp_path) -> None:
    text = ENTRY + ("\n[[excluded]]\nid = \"bunte\"\nname = \"Bunte\"\nhomepage = \"https://bunte.example/\"\n"
                    "kind = \"outlet\"\noutlet_type = \"celebrity_magazine\"\nowner_group = \"Burda\"\n"
                    "focus = \"Celebrities and royals\"\naudience = \"women\"\npaywall_model = \"free\"\n"
                    "status = \"unchecked\"\nreason = \"not checked yet\"\nchecked = 2026-10-28\n\n"
                    "[[excluded]]\nid = \"blog\"\nname = \"Blog\"\nhomepage = \"https://blog.example/\"\n"
                    "status = \"no_feed\"\nreason = \"no feed\"\nchecked = 2026-10-25\n")

    candidate, without_kind = load_config(write(tmp_path, text)).excluded

    assert candidate.rechecked and candidate.focus == "Celebrities and royals" and candidate.audience == "women"
    assert not without_kind.rechecked
    with pytest.raises(ValueError, match="focus"):
        load_config(write(tmp_path, text.replace('"Celebrities and royals"', '"Gossip"')))


@pytest.mark.parametrize("line", ['added = "2026-10-25"', "added = 2026-10-25T10:00:00Z"])
def test_lifecycle_dates_must_be_dates(tmp_path, line: str) -> None:
    with pytest.raises(ValueError, match="must be a date"):
        load_config(write(tmp_path, ENTRY + line + "\n"))


def test_unknown_values_and_duplicate_ids_are_rejected(tmp_path) -> None:
    excluded = ("\n[[excluded]]\nid = \"{id}\"\nname = \"X\"\nhomepage = \"https://x.example/\"\n"
                "status = \"{status}\"\nreason = \"r\"\nchecked = 2026-10-25\n")
    with pytest.raises(ValueError, match="status"):
        load_config(write(tmp_path, ENTRY + excluded.format(id="x", status="unknown")))
    with pytest.raises(ValueError, match="Duplicate"):
        load_config(write(tmp_path, ENTRY + excluded.format(id="news_example", status="blocked")))
    with pytest.raises(ValueError, match="audience"):
        load_config(write(tmp_path, ENTRY + "audience = \"teens\"\n"))
    with pytest.raises(ValueError, match="actor_type"):
        load_config(write(tmp_path, "[[press_source]]\nid = \"p\"\nname = \"P\"\nhomepage = \"https://p.example/\"\n"
                                    "actor_type = \"ministry\"\nfeeds = []\n"))


def test_removed_sources_have_the_status_removed(tmp_path) -> None:
    config = load_config(write(tmp_path, ENTRY + "removed = 2027-02-01\n"))
    health = SourceHealthStore(tmp_path / "sources.json", {"news_example": SourceHealth(status="blocked")})

    assert health.status_of(config.outlets[0]) == "removed"


def test_broken_sources_are_retried_once_a_week(tmp_path) -> None:
    health = SourceHealthStore(tmp_path / "sources.json", {})
    day = date(2026, 10, 28)
    for offset in range(7):
        health.record_failure("news_example", day, "HTTP 500")
    assert health.get("news_example").status == "broken"
    assert not health.is_due("news_example", day.replace(day=30))
    assert health.is_due("news_example", date(2026, 11, 4))
    health.record_success("news_example", date(2026, 11, 4), 3)
    assert health.get("news_example").status == "active"


def test_repository_configuration_records_its_checks() -> None:
    config = load_config(CONFIG_PATH)

    assert all(source.added is not None for source in config.sources)
    assert {entry.id for entry in config.excluded} >= {"stern", "zeit", "vogue", "watson"}
    assert all(entry.kind is not None for entry in config.excluded if entry.status == "unchecked")
    hosts = [source.homepage for source in config.active_sources]
    assert len(hosts) == len(set(hosts))
