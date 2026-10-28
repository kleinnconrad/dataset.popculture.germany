"""Tests of the Gemini annotation with a fake client."""
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import Any, Callable

import pytest
from google.genai import types

from annotation import (
    BATCH_SIZE,
    MAX_MENTIONS,
    AnnotationInput,
    CallBudgetExceededError,
    GeminiAnnotator,
    build_prompt,
    clean_annotation,
    clean_country_codes,
    clean_name,
    extraction_hash,
    keep_known,
)
from schema import ItemAnnotation, PopDomain
from settings import Settings


def label(number: int, **overrides: Any) -> dict[str, Any]:
    """Returns a valid model answer for one item."""
    base = {
        "item_number": number, "is_pop_culture": True, "domain": "Music",
        "domains_secondary": ["Celebrities and royals"], "issue_detail": "Neues Album", "article_type": "News",
        "works": [{"title": "Zeitreise", "kind": "Album", "role": "Subject", "tone": "Positive"}],
        "people": [{"name": "Helene Fischer", "role": "Subject", "tone": "Positive"},
                   {"name": "Max Mustermann", "role": "Commentator", "tone": "Neutral"}],
        "products": [{"name": "Stanley Cup", "category": "Home and furniture", "role": "Recommended"}],
        "platforms": ["Spotify", "TikTok"], "purchase_recommendation": True, "sponsored": False,
        "lifestyle_advice": ["Fitness and exercise"],
        "family_forms": [{"form": "Married couple with children", "tone": "Positive"}],
        "stances": [{"target": "Bundeswehr and military", "stance": "Unfavorable", "holder": "Celebrity or influencer"}],
        "promoted_values": ["Achievement", "Hedonism"], "generations": ["Generation Z"],
        "content_origin_countries": ["DE"], "overall_tone": "Positive", "reports_controversy": False,
        "upcoming_events": [{"event_type": "Concert or tour", "date": "2026-12-01"}],
        "communication_type": "Not applicable",
    }
    base.update(overrides)
    return base


def response(items: list[dict[str, Any]], finish: Any = types.FinishReason.STOP) -> SimpleNamespace:
    """Builds a fake GenerateContentResponse."""
    return SimpleNamespace(
        text=json.dumps({"items": items}), candidates=[SimpleNamespace(finish_reason=finish)],
        prompt_feedback=None, usage_metadata=None,
    )


class FakeClient:
    """Fake genai client whose responses are produced by a function of the prompt."""

    def __init__(self, answer: Callable[[str], SimpleNamespace]) -> None:
        self.prompts: list[str] = []
        self.models = SimpleNamespace(generate_content=self._generate)
        self._answer = answer

    def _generate(self, model: str, contents: str, config: Any) -> SimpleNamespace:
        self.prompts.append(contents)
        return self._answer(contents)


def items(count: int) -> list[AnnotationInput]:
    return [AnnotationInput(kind="news article", source="Music Magazine (music magazine)",
                            published="2026-10-27", title=f"Titel {n}", teaser="Teaser") for n in range(count)]


def annotator(answer: Callable[[str], SimpleNamespace], **overrides: Any) -> tuple[GeminiAnnotator, FakeClient]:
    client = FakeClient(answer)
    settings = Settings(**overrides)
    return GeminiAnnotator(settings, client=client, sleep=lambda s: None, clock=lambda: 0.0), client


def test_annotate_aligns_labels_with_items_by_number() -> None:
    ann, _ = annotator(lambda prompt: response([label(2, domain="Film and cinema"), label(1)]))
    labels = ann.annotate(items(2))

    assert labels[0] is not None and labels[0].domain == "Music"
    assert labels[1] is not None and labels[1].domain == "Film and cinema"


def test_annotate_returns_none_for_items_missing_in_the_response() -> None:
    ann, _ = annotator(lambda prompt: response([label(1)]))
    labels = ann.annotate(items(3))
    assert labels[0] is not None and labels[1] is None and labels[2] is None


def test_unusable_response_is_split_until_single_items() -> None:
    def answer(prompt: str) -> SimpleNamespace:
        if prompt.count("\nKind:") > 1:
            return response([], finish=types.FinishReason.MAX_TOKENS)
        return response([label(1)])

    ann, client = annotator(answer)
    labels = ann.annotate(items(4))

    assert all(result is not None for result in labels)
    assert len(client.prompts) == 1 + 2 + 4


def test_call_budget_stops_the_annotation() -> None:
    ann, _ = annotator(lambda prompt: response([label(1)]), max_gemini_calls_per_run=1)
    ann.annotate(items(1))
    with pytest.raises(CallBudgetExceededError):
        ann.annotate(items(1))


def test_call_budget_holds_across_threads() -> None:
    ann, client = annotator(lambda prompt: response([label(1)]), max_gemini_calls_per_run=3)

    def call(_: int) -> bool:
        try:
            ann.annotate(items(1))
        except CallBudgetExceededError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(call, range(8)))

    assert sum(results) == 3
    assert len(client.prompts) == 3 and ann.usage.calls == 3


def test_annotate_rejects_oversized_batches() -> None:
    ann, _ = annotator(lambda prompt: response([]))
    with pytest.raises(ValueError):
        ann.annotate(items(BATCH_SIZE + 1))


def test_keep_known_maps_to_the_vocabulary_and_drops_other_values() -> None:
    assert keep_known(["music", "Rockets", "Music", " Film and  cinema "], PopDomain) == ["Music", "Film and cinema"]


def test_clean_country_codes() -> None:
    assert clean_country_codes(["de", "UK", "EL", "Germany", "DE", "kr "]) == ["DE", "GB", "GR", "KR"]


def test_clean_name_drops_implausible_names() -> None:
    assert clean_name('  „Helene   Fischer“ ') == "Helene Fischer"
    assert clean_name("X") == ""
    assert clean_name("a" * 200) == ""


def test_clean_annotation_normalizes_mentions_opinions_and_press_fields() -> None:
    raw = ItemAnnotation.model_validate(label(
        1,
        domain="music",
        domains_secondary=["Music", "TV and streaming", "tv and streaming", "Comedy and satire", "Space"],
        works=[{"title": " Zeitreise ", "kind": "Album", "role": "Subject", "tone": "Positive"},
               {"title": "zeitreise", "kind": "Album", "role": "Mentioned", "tone": "Neutral"},
               {"title": "Neue Show", "kind": "Hologram", "role": "Hyped", "tone": "Positive"}],
        people=[{"name": "Helene Fischer", "role": "Subject", "tone": "Positive"},
                {"name": "helene fischer", "role": "Mentioned", "tone": "Neutral"},
                {"name": "X", "role": "Subject", "tone": "Neutral"}],
        products=[{"name": "Labubu", "category": "Toys", "role": "Hyped"}],
        platforms=["Spotify", "MySpace", "tiktok"],
        lifestyle_advice=["Sleep", "Levitation"],
        family_forms=[{"form": "Single parent", "tone": "Positive"}, {"form": "single parent", "tone": "Negative"},
                      {"form": "Clan", "tone": "Neutral"}],
        stances=[{"target": "Bundeswehr and military", "stance": "Unfavorable", "holder": "Celebrity or influencer"},
                 {"target": "bundeswehr and military", "stance": "Favorable", "holder": "Celebrity or influencer"},
                 {"target": "Marriage", "stance": "Favorable", "holder": "Some guy"},
                 {"target": "Pineapple pizza", "stance": "Favorable", "holder": "Author or outlet"}],
        promoted_values=["Achievement", "Hedonism", "Tradition", "Security", "Joy"],
        generations=["Generation Z", "Zoomers"],
        content_origin_countries=["de", "USA", "KR"],
        upcoming_events=[{"event_type": "Concert or tour", "date": "2026-12-01"},
                         {"event_type": "Concert or tour", "date": "next week"},
                         {"event_type": "Festival", "date": "2020-01-01"},
                         {"event_type": "Party", "date": "2026-11-06"}],
        communication_type="Market figures or study", issue_detail="  Neues   Album ",
    ))

    news = clean_annotation(raw, "news article", "2026-10-27")
    press = clean_annotation(raw, "press release", "2026-10-27")

    assert news.domain == "Music"
    assert news.domains_secondary == ["TV and streaming", "Comedy and satire"]
    assert [(work.title, work.kind, work.role) for work in news.works] == [
        ("Zeitreise", "Album", "Subject"), ("Neue Show", "Other", "Mentioned")]
    assert [person.name for person in news.people] == ["Helene Fischer"]
    assert [(product.name, product.category, product.role) for product in news.products] == [
        ("Labubu", "Other", "Mentioned")]
    assert news.platforms == ["Spotify", "TikTok"]
    assert news.lifestyle_advice == ["Sleep"]
    assert [(form.form, form.tone) for form in news.family_forms] == [("Single parent", "Positive")]
    assert [(s.target, s.stance, s.holder) for s in news.stances] == [
        ("Bundeswehr and military", "Unfavorable", "Celebrity or influencer"), ("Marriage", "Favorable", "Other")]
    assert news.promoted_values == ["Achievement", "Hedonism", "Tradition"]
    assert news.generations == ["Generation Z"]
    assert news.content_origin_countries == ["DE", "KR"]
    assert [(event.event_type, event.date) for event in news.upcoming_events] == [("Concert or tour", "2026-12-01")]
    assert news.issue_detail == "Neues Album"
    assert news.communication_type == "Not applicable"
    assert press.communication_type == "Market figures or study"


def test_clean_annotation_cuts_long_lists() -> None:
    raw = ItemAnnotation.model_validate(label(1, works=[
        {"title": f"Song {n}", "kind": "Song", "role": "Mentioned", "tone": "Neutral"} for n in range(30)]))

    assert len(clean_annotation(raw, "news article", None).works) == MAX_MENTIONS


def test_build_prompt_numbers_items_and_marks_missing_text() -> None:
    prompt = build_prompt([AnnotationInput(kind="press release", source="game (industry association)",
                                           published=None, title="T")])
    assert "Item 1" in prompt and "Kind: press release" in prompt
    assert "Published: (unknown)" in prompt and "Teaser: (none)" in prompt


def test_extraction_hash_changes_with_the_model() -> None:
    assert extraction_hash(Settings()) != extraction_hash(Settings(gemini_model="other-model"))
