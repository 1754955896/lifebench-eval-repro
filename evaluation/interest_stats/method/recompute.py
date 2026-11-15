#!/usr/bin/env python3
"""Recompute interest drift from saved inputs and event labels.

The original three evaluators used an LLM to assign one category to each
observation. Their saved ``event_extractions`` are those fixed assignments;
everything calculated here is deterministic and needs no API or raw dataset.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path


DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def read_json(path: Path):
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def canonical_sha256(value) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)


def summarize(values: list[float]) -> dict:
    if not values:
        return {"count": 0, "mean": None, "median": None, "stddev": None, "p25": None, "p75": None}
    return {
        "count": len(values),
        "mean": round(statistics.fmean(values), 6),
        "median": round(statistics.median(values), 6),
        "stddev": round(statistics.pstdev(values), 6),
        "p25": round(percentile(values, 0.25), 6),
        "p75": round(percentile(values, 0.75), 6),
        "min": round(min(values), 6),
        "max": round(max(values), 6),
    }


def calculate(events: list[dict], labels: list[dict]) -> dict:
    """Join by event_id, then calculate daily sets and consecutive-day drift."""
    if not isinstance(events, list) or not isinstance(labels, list):
        raise ValueError("Inputs and extractions must be JSON arrays")
    event_ids = [event["event_id"] for event in events]
    label_ids = [item["event_id"] for item in labels]
    if len(event_ids) != len(set(event_ids)) or len(label_ids) != len(set(label_ids)):
        raise ValueError("Duplicate event_id in input or extractions")
    if set(event_ids) != set(label_ids):
        raise ValueError("Input and extraction event_id sets differ")
    by_id = {item["event_id"]: item for item in labels}
    daily_counts: dict[str, Counter] = defaultdict(Counter)
    daily_events: Counter = Counter()
    for event in events:
        date = event["date"]
        dt.date.fromisoformat(date)
        daily_events[date] += 1
        item = by_id[event["event_id"]]
        if item.get("date") and item["date"] != date:
            raise ValueError(f"Date differs for event {event['event_id']}")
        if item.get("timestamp") and item["timestamp"][:10] != date:
            raise ValueError(f"Timestamp differs for event {event['event_id']}")
        signal, category = item["interest_signal"], item["category"]
        if not isinstance(signal, bool) or (signal and category == "None") or (not signal and category != "None"):
            raise ValueError(f"Invalid interest signal/category for {event['event_id']}")
        if signal:
            daily_counts[date][category] += 1
        else:
            daily_counts[date]  # Preserve days with no interest category.

    profiles = [
        {"date": date, "event_count": daily_events[date],
         "interest_event_count": sum(daily_counts[date].values()),
         "interest_categories": dict(daily_counts[date].most_common())}
        for date in sorted(daily_events)
    ]
    series = []
    skipped_both_empty = 0
    dates = sorted(daily_events)
    for previous, current in zip(dates, dates[1:]):
        if (dt.date.fromisoformat(current) - dt.date.fromisoformat(previous)).days != 1:
            continue
        left, right = set(daily_counts[previous]), set(daily_counts[current])
        union = left | right
        if not union:
            skipped_both_empty += 1
            continue
        series.append({
            "previous_date": previous, "date": current,
            "previous_categories": sorted(left), "categories": sorted(right),
            "drift": round(1 - len(left & right) / len(union), 6),
        })
    values = [pair["drift"] for pair in series]
    monthly: dict[str, list[float]] = defaultdict(list)
    for pair in series:
        monthly[pair["date"][:7]].append(pair["drift"])
    return {
        "coverage": {"event_count": len(events), "day_count": len(dates),
                     "interest_event_count": sum(p["interest_event_count"] for p in profiles)},
        "daily_profiles": profiles,
        "interest_drift": {"summary": summarize(values),
                           "skipped_both_empty_pairs": skipped_both_empty,
                           "monthly_summary": {month: summarize(v) for month, v in sorted(monthly.items())},
                           "series": series},
    }


def check_saved(row: dict, events: list[dict], saved: dict, computed: dict) -> None:
    provenance = saved["provenance"]
    expected_hash = provenance.get("evaluated_events_sha256", provenance.get("evaluated_logs_sha256"))
    if canonical_sha256(events) != expected_hash:
        raise ValueError("Normalized input SHA-256 differs from historical report")
    if computed["interest_drift"] != saved["interest_drift"]:
        raise ValueError("Recomputed interest drift differs from historical report")
    for field in ("day_count", "interest_event_count"):
        saved_field = "interest_log_count" if field == "interest_event_count" and row["dataset"] != "lifebench" else field
        if computed["coverage"][field] != saved["coverage"][saved_field]:
            raise ValueError(f"Coverage {field} differs from historical report")
    if len(computed["daily_profiles"]) != len(saved["daily_profiles"]):
        raise ValueError("Daily profile count differs from historical report")
    for actual, expected in zip(computed["daily_profiles"], saved["daily_profiles"]):
        for field in ("date", "event_count", "interest_event_count", "interest_categories"):
            if actual[field] != expected[field]:
                raise ValueError(f"Daily profile {actual['date']} field {field} differs")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR, help="Directory with manifest, input and output")
    parser.add_argument("--output", type=Path, help="Optional JSON file containing recomputed values and daily series")
    args = parser.parse_args()
    root = args.data_dir.resolve()
    rows = read_json(root / "manifest.json")
    result = []
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        events = read_json(root / row["normalized_input"])
        saved = read_json(root / row["coarse_report"])
        if len(events) != row["event_count"] or len(events) != len(saved["event_extractions"]):
            raise ValueError(f"Event count differs for {row['dataset']}/{row['sample']}")
        computed = calculate(events, saved["event_extractions"])
        try:
            check_saved(row, events, saved, computed)
        except ValueError as exc:
            raise ValueError(f"{row['dataset']}/{row['sample']}: {exc}") from exc
        mean = computed["interest_drift"]["summary"]["mean"]
        if mean is not None:
            grouped[row["dataset"]].append(mean)
        result.append({"dataset": row["dataset"], "sample": row["sample"], **computed})
        print(f"{row['dataset']:10} {row['sample']:12} pairs={len(computed['interest_drift']['series']):2} mean={mean}  MATCH")
    group_summary = {dataset: summarize(means) for dataset, means in sorted(grouped.items())}
    print("Group summaries (unweighted mean of per-person drift means):")
    for dataset, summary in group_summary.items():
        print(f"{dataset:10} n={summary['count']} mean={summary['mean']} stddev={summary['stddev']}")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps({"samples": result, "group_summary": group_summary}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
