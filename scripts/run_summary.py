"""
Writes a Markdown summary of the latest pipeline run for the notification comment.

The summary is built from the last line of ``data/state/run_log.jsonl``: the
status and records of each collector, the changed and failed charts, the
counts of the feeds collector and the Gemini tokens with an estimate of
their cost; with ``--window-hours``, also the runs, failures, records and
Gemini cost of the last hours, for the daily notification of the runs every
six hours. The notification workflow
posts it as a commit comment that mentions the repository owner, so that
GitHub sends an email.

Usage:
    python scripts/run_summary.py --run-log data/state/run_log.jsonl --output summary.md
        [--conclusion success] [--run-url URL] [--mention USER] [--not-before TIMESTAMP] [--window-hours 24]
"""
import argparse
import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# List prices of the paid tier in USD per million tokens (input, output including thinking), as of
# October 2026. Update them when Google changes the prices or the pipeline uses another model.
GEMINI_PRICES_USD_PER_M: dict[str, tuple[float, float]] = {
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-flash-lite": (0.10, 0.40),
}


def last_entry(run_log: Path, not_before: Optional[datetime] = None) -> Optional[dict[str, Any]]:
    """Returns the last entry of the run log.

    Args:
        run_log: Path of ``run_log.jsonl``.
        not_before: If given, an entry that started earlier belongs to a previous run and is ignored.

    Returns:
        Optional[dict[str, Any]]: The entry, or None if the file is missing or has no entry of this run.
    """
    if not run_log.exists():
        return None
    lines = [line for line in run_log.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines:
        return None
    entry = json.loads(lines[-1])
    if not_before is not None and datetime.fromisoformat(entry["started"]) < not_before:
        return None
    return entry


def entries_since(run_log: Path, since: datetime) -> list[dict[str, Any]]:
    """Returns the entries of the run log that started at or after a time.

    Args:
        run_log: Path of ``run_log.jsonl``.
        since: Start of the window.

    Returns:
        list[dict[str, Any]]: The entries in the order of the log; empty if the file is missing.
    """
    if not run_log.exists():
        return []
    entries = [json.loads(line) for line in run_log.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [entry for entry in entries if datetime.fromisoformat(entry["started"]) >= since]


def window_lines(entries: list[dict[str, Any]], hours: int) -> list[str]:
    """Summarizes the runs of a time window: runs, failed collectors, records and Gemini tokens.

    Args:
        entries: The run log entries of the window.
        hours: Length of the window, for the heading.

    Returns:
        list[str]: Markdown lines; empty without entries.
    """
    if not entries:
        return []
    failed = [entry for entry in entries
              if any(outcome.get("status") in ("failed", "skipped") for outcome in entry.get("collectors", {}).values())]
    records: dict[str, int] = {}
    cost = 0.0
    calls = 0
    for entry in entries:
        for outcome in entry.get("collectors", {}).values():
            for dataset, count in outcome.get("records", {}).items():
                records[dataset] = records.get(dataset, 0) + count
        counters = entry.get("collectors", {}).get("feeds", {}).get("counters", {})
        usage = counters.get("gemini_usage") or {}
        calls += usage.get("calls", 0)
        cost += gemini_cost(counters.get("gemini_model"), usage) or 0.0
    lines = ["", f"**Last {hours} hours:** {len(entries)} runs, {len(failed)} with failed collectors; Gemini "
                 f"{calls} calls, estimated cost ${cost:.2f}."]
    if records:
        lines.append("Records: " + ", ".join(f"{dataset} {count:,}" for dataset, count in sorted(records.items())))
    return lines


def gemini_cost(model: Optional[str], usage: dict[str, Any]) -> Optional[float]:
    """Estimates the cost of the Gemini tokens of a run.

    Args:
        model: The Gemini model id.
        usage: Token counts as written by the feeds collector.

    Returns:
        Optional[float]: The cost in USD, or None if no price is known for the model.
    """
    prices = GEMINI_PRICES_USD_PER_M.get(model or "")
    if prices is None:
        return None
    input_price, output_price = prices
    output_tokens = usage.get("output_tokens", 0) + usage.get("thinking_tokens", 0)
    return (usage.get("prompt_tokens", 0) * input_price + output_tokens * output_price) / 1_000_000


def _duration_minutes(entry: dict[str, Any]) -> Optional[float]:
    """Returns the duration of a run in minutes, or None if it has no end time."""
    if not entry.get("finished"):
        return None
    started = datetime.fromisoformat(entry["started"])
    finished = datetime.fromisoformat(entry["finished"])
    return (finished - started).total_seconds() / 60


def build_summary(entry: Optional[dict[str, Any]], conclusion: str, run_url: Optional[str],
                  mention: Optional[str], window: Optional[list[dict[str, Any]]] = None,
                  window_hours: int = 24) -> str:
    """Builds the Markdown of the notification.

    Args:
        entry: The run log entry of the run; None if the run wrote none.
        conclusion: Conclusion of the workflow run (success, failure, ...).
        run_url: URL of the workflow run.
        mention: GitHub user to mention, so that GitHub sends an email.
        window: Run log entries of the last ``window_hours`` hours, summarized after the run; None leaves the
            summary out.
        window_hours: Length of the window.

    Returns:
        str: The Markdown text.
    """
    greeting = f"@{mention} " if mention else ""
    lines = [f"{greeting}German pop culture pipeline: **{conclusion}**"]
    if run_url:
        lines.append(f"Workflow run: {run_url}")
    lines.append("")
    if entry is None:
        lines.append("The run wrote no run log entry; see the workflow log.")
        return "\n".join(lines) + "\n"

    duration = _duration_minutes(entry)
    lines.append(f"- **Mode:** {entry.get('mode')}")
    lines.append(f"- **Started:** {entry['started'][:16].replace('T', ' ')} UTC"
                 + (f", duration {duration:.0f} min" if duration is not None else ""))
    lines.append(f"- **HTTP requests:** {entry.get('requests', 0):,}")
    lines.extend(["", "| Collector | Status | Records |", "|---|---|---|"])
    collectors = entry.get("collectors", {})
    for name, outcome in collectors.items():
        records = ", ".join(f"{dataset} {count:,}" for dataset, count in outcome.get("records", {}).items())
        error = f" ({outcome['error']})" if outcome.get("error") else ""
        lines.append(f"| `{name}` | {outcome.get('status')}{error} | {records} |")

    charts = collectors.get("charts", {}).get("counters", {})
    if charts:
        lines.extend(["", f"**Charts:** {charts.get('charts_read', 0)} read, {charts.get('lists_changed', 0):,} "
                          f"changed lists stored, {charts.get('charts_not_due', 0)} not due, "
                          f"{charts.get('charts_failed', 0)} failed."])
        for chart_id, message in (charts.get("failed") or {}).items():
            lines.append(f"- `{chart_id}`: {message}")
    counters = collectors.get("feeds", {}).get("counters", {})
    if counters:
        lines.extend(["", f"**Feeds:** {counters.get('items_new', 0):,} new items from "
                          f"{counters.get('sources_ok', 0)} sources; {counters.get('annotated', 0):,} annotated, "
                          f"{counters.get('beyond_cap', 0) + counters.get('not_annotated', 0):,} stored without labels."])
    usage = counters.get("gemini_usage")
    if usage:
        model = counters.get("gemini_model")
        cost = gemini_cost(model, usage)
        cost_text = (f"estimated cost ${cost:.2f} at the list prices of {model}" if cost is not None
                     else f"no price known for {model}")
        lines.append(f"**Gemini:** {usage.get('calls', 0)} calls; {usage.get('prompt_tokens', 0):,} input, "
                     f"{usage.get('output_tokens', 0):,} output and {usage.get('thinking_tokens', 0):,} "
                     f"thinking tokens; {cost_text}.")
    if window is not None:
        lines.extend(window_lines(window, window_hours))
    return "\n".join(lines) + "\n"


def parse_args(argv: Optional[list[str]]) -> argparse.Namespace:
    """Parses the command-line arguments.

    Args:
        argv: Arguments without the program name; None reads ``sys.argv``.

    Returns:
        argparse.Namespace: The parsed arguments.
    """
    parser = argparse.ArgumentParser(description="Writes a Markdown summary of the latest pipeline run.")
    parser.add_argument("--run-log", type=Path, required=True, help="path of run_log.jsonl")
    parser.add_argument("--output", type=Path, required=True, help="path of the Markdown file to write")
    parser.add_argument("--conclusion", default="completed", help="conclusion of the workflow run")
    parser.add_argument("--run-url", help="URL of the workflow run")
    parser.add_argument("--mention", help="GitHub user to mention")
    parser.add_argument("--not-before", type=datetime.fromisoformat,
                        help="start of the workflow run (ISO 8601); older run log entries are ignored")
    parser.add_argument("--window-hours", type=int,
                        help="also summarize all runs of this many hours before the latest run")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    """Writes the summary file.

    Args:
        argv: Arguments without the program name; None reads ``sys.argv``.

    Returns:
        int: 0 on success, 1 if the run log cannot be read.
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(name)s - %(message)s")
    args = parse_args(argv)
    try:
        entry = last_entry(args.run_log, args.not_before)
    except (ValueError, KeyError) as e:
        logger.error("Run log %s cannot be read: %s", args.run_log, e)
        return 1
    window = None
    if args.window_hours and entry is not None:
        since = datetime.fromisoformat(entry["started"]) - timedelta(hours=args.window_hours)
        window = entries_since(args.run_log, since)
    args.output.write_text(build_summary(entry, args.conclusion, args.run_url, args.mention, window,
                                         args.window_hours or 24), encoding="utf-8")
    logger.info("Summary written to %s.", args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
