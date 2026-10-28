"""Tests of the run summary of the notification workflow."""
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_summary.py"
_spec = importlib.util.spec_from_file_location("run_summary", SCRIPT)
assert _spec is not None and _spec.loader is not None
run_summary = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_summary)

ENTRY = {
    "started": "2026-10-28T02:17:00+00:00", "finished": "2026-10-28T02:31:00+00:00", "mode": "normal",
    "requests": 420,
    "collectors": {
        "wikipedia_pageviews": {"status": "not_due"},
        "charts": {"status": "ok", "records": {"chart_snapshots": 9},
                   "counters": {"charts_read": 12, "charts_ok": 11, "lists_changed": 9, "charts_not_due": 0,
                                "charts_failed": 1, "failed": {"wikimedia.top_articles_germany": "HTTP 500"}}},
        "feeds": {"status": "ok", "records": {"news_items": 180, "press_releases": 1, "text_annotations": 25},
                  "counters": {"items_new": 181, "sources_ok": 22, "annotated": 25, "beyond_cap": 156,
                               "not_annotated": 0, "gemini_model": "gemini-2.5-flash",
                               "gemini_usage": {"calls": 3, "prompt_tokens": 9_000, "output_tokens": 10_500,
                                                "thinking_tokens": 0, "total_tokens": 19_500}}},
        "entities": {"status": "failed", "error": "RegistryError: duplicate ids"},
    },
}


def write_log(tmp_path: Path, *entries: dict) -> Path:
    path = tmp_path / "run_log.jsonl"
    path.write_text("".join(json.dumps(entry) + "\n" for entry in entries), encoding="utf-8")
    return path


def test_gemini_cost_uses_the_list_prices_of_the_model() -> None:
    usage = {"prompt_tokens": 1_000_000, "output_tokens": 600_000, "thinking_tokens": 400_000}
    assert run_summary.gemini_cost("gemini-2.5-flash", usage) == pytest.approx(0.30 + 2.50)
    assert run_summary.gemini_cost("gemini-2.5-flash-lite", usage) == pytest.approx(0.10 + 0.40)
    assert run_summary.gemini_cost("other-model", usage) is None


def test_summary_lists_collectors_charts_feeds_and_gemini_cost() -> None:
    text = run_summary.build_summary(ENTRY, "failure", "https://github.com/run/1", "owner")

    assert text.startswith("@owner German pop culture pipeline: **failure**")
    assert "duration 14 min" in text
    assert "| `wikipedia_pageviews` | not_due |  |" in text
    assert "| `feeds` | ok | news_items 180, press_releases 1, text_annotations 25 |" in text
    assert "| `entities` | failed (RegistryError: duplicate ids) |  |" in text
    assert "**Charts:** 12 read, 9 changed lists stored, 0 not due, 1 failed." in text
    assert "- `wikimedia.top_articles_germany`: HTTP 500" in text
    assert "25 annotated, 156 stored without labels" in text
    assert "estimated cost $0.03 at the list prices of gemini-2.5-flash" in text


def test_window_summarizes_the_runs_of_the_last_day(tmp_path: Path) -> None:
    earlier = dict(ENTRY, started="2026-10-27T20:17:00+00:00")
    old = dict(ENTRY, started="2026-10-26T20:17:00+00:00")
    log = write_log(tmp_path, old, earlier, ENTRY)
    since = datetime(2026, 10, 27, 2, 17, tzinfo=timezone.utc)

    window = run_summary.entries_since(log, since)
    text = run_summary.build_summary(ENTRY, "success", None, None, window, 24)

    assert len(window) == 2
    assert "**Last 24 hours:** 2 runs, 2 with failed collectors; Gemini 6 calls, estimated cost $0.06." in text
    assert "chart_snapshots 18" in text


def test_entry_of_an_earlier_run_is_ignored(tmp_path: Path) -> None:
    log = write_log(tmp_path, ENTRY)

    assert run_summary.last_entry(log) == ENTRY
    assert run_summary.last_entry(log, datetime(2026, 10, 29, tzinfo=timezone.utc)) is None
    assert run_summary.last_entry(tmp_path / "missing.jsonl") is None
    assert "wrote no run log entry" in run_summary.build_summary(None, "failure", None, None)


def test_main_writes_the_summary_file(tmp_path: Path) -> None:
    log = write_log(tmp_path, {"started": "2026-10-27T21:17:00+00:00", "mode": "normal", "collectors": {}}, ENTRY)
    output = tmp_path / "summary.md"

    assert run_summary.main(["--run-log", str(log), "--output", str(output), "--conclusion", "success",
                             "--not-before", "2026-10-28T02:16:30Z", "--window-hours", "24"]) == 0
    text = output.read_text(encoding="utf-8")
    assert "German pop culture pipeline: **success**" in text
    assert "**Last 24 hours:** 2 runs" in text
