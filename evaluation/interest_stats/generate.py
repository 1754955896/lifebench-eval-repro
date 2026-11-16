"""This module generates interest drift reports from normalized inputs."""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from .method.recompute import calculate
from .scripts import lifebench_online as core


DATA_DIR = Path(__file__).resolve().parent / "data"
PROMPT_VERSIONS = {
    "lifebench": core.PROMPT_VERSION,
    "mempal": "mempal-log-interest-v1",
    "omni": "omni-behavior-interest-v1",
}


def compact_log(event: Mapping[str, Any]) -> dict:
    return {
        "event_id": event["event_id"],
        "timestamp": event["timestamp"],
        "content": event["description"],
    }


def event_fingerprint(event: Mapping[str, Any], dataset: str, model: str) -> str:
    if dataset == "lifebench":
        return core.event_fingerprint(event, model)
    return core.sha256_json({
        "prompt_version": PROMPT_VERSIONS[dataset],
        "model": model,
        "taxonomy": list(core.INTEREST_CATEGORIES),
        "log": compact_log(event),
    })


def make_batches(events: list[dict], dataset: str, batch_size: int, max_chars: int) -> list[list[dict]]:
    if dataset == "lifebench":
        return core.make_batches(events, batch_size, max_chars)
    batches: list[list[dict]] = []
    current: list[dict] = []
    current_chars = 0
    for event in events:
        size = len(json.dumps(compact_log(event), ensure_ascii=False))
        if current and (len(current) >= batch_size or current_chars + size > max_chars):
            batches.append(current)
            current = []
            current_chars = 0
        current.append(event)
        current_chars += size
    if current:
        batches.append(current)
    return batches


def build_log_prompt(batch: list[dict], dataset: str) -> str:
    taxonomy = "\n".join(f"- {name}: {description}" for name, description in core.INTEREST_CATEGORIES.items())
    if dataset == "mempal":
        opening = "You are analyzing timestamped personal behavior logs from the Mem-PAL dataset."
        rule5 = ("5. Judge the user's behavior only. Do not treat app recommendations, received notifications, or\n"
                 "   another person's preference as the user's interest unless the user adopts or acts on it.")
        subject = "logs"
    else:
        opening = "You are analyzing dated personal behavior records from an authentic short-video platform dataset."
        rule5 = ("5. Judge the user's behavior only. Do not treat app recommendations, received notifications,\n"
                 "   advertisements, or another person's preference as the user's interest unless the user adopts or\n"
                 "   acts on it.")
        subject = "records"
    return f"""{opening}

Classify each {subject[:-1]} independently. Identify a stable or evolving personal interest or preference,
not merely the literal action, an obligation, or incidental context.

Rules:
1. Return exactly one result for every event_id and preserve event_id verbatim.
2. Choose exactly one category from the closed taxonomy.
3. Use interest_signal=false and category="None" for routine or obligatory behavior without a
   reliable preference signal, including generic commuting, ordinary work execution, reminders,
   basic purchases, medication instructions, and one-off logistics.
4. Voluntary repeated choices, hobbies, media/content choices, learning goals, personally meaningful
   health or career pursuits, relationship-oriented activities, and explicit preferences may be signals.
{rule5}
6. Provide zero to three concise keywords grounded in the {subject[:-1]}. Do not infer unsupported facts.
7. confidence must be a JSON number from 0 to 1.
8. The {subject} may be Chinese or English. Output category names exactly as written below.
9. Output raw valid JSON only as {{"results": [...]}} with no markdown or explanation.

Closed taxonomy:
{taxonomy}

Required result fields:
event_id, interest_signal, category, keywords, confidence

Logs:
{json.dumps([compact_log(item) for item in batch], ensure_ascii=False)}
"""


def validate_log_result(raw: Any, batch: list[dict], dataset: str, model: str) -> list[dict]:
    results = raw.get("results") if isinstance(raw, dict) else None
    if not isinstance(results, list):
        raise core.EvaluationError("Model response must contain a results array")
    expected = {event["event_id"]: event for event in batch}
    validated = {}
    for item in results:
        if not isinstance(item, dict):
            raise core.EvaluationError("Every model result must be an object")
        event_id = str(item.get("event_id", ""))
        if event_id not in expected or event_id in validated:
            raise core.EvaluationError(f"Unexpected or duplicate event_id: {event_id!r}")
        signal = item.get("interest_signal")
        category = str(item.get("category", ""))
        if not isinstance(signal, bool) or category not in core.INTEREST_CATEGORIES:
            raise core.EvaluationError(f"Invalid interest signal or category for {event_id}")
        if signal and category == "None":
            raise core.EvaluationError(f"Interest event {event_id} cannot use category None")
        if not signal:
            category = "None"
        keywords = item.get("keywords", [])
        if not isinstance(keywords, list) or not all(isinstance(keyword, str) for keyword in keywords):
            raise core.EvaluationError(f"Invalid keywords for {event_id}")
        try:
            confidence = float(item.get("confidence"))
        except (TypeError, ValueError) as exc:
            raise core.EvaluationError(f"Invalid confidence for {event_id}") from exc
        if not 0 <= confidence <= 1:
            raise core.EvaluationError(f"Confidence outside [0, 1] for {event_id}")
        event = expected[event_id]
        validated[event_id] = {
            "event_id": event_id,
            "event_hash": event_fingerprint(event, dataset, model),
            "date": event["date"],
            "interest_signal": signal,
            "category": category,
            "keywords": [keyword.strip() for keyword in keywords[:3] if keyword.strip()],
            "confidence": round(confidence, 6),
        }
    if set(validated) != set(expected):
        raise core.EvaluationError("Model response omitted event ids")
    return [validated[event["event_id"]] for event in batch]


def extract_batch(batch: list[dict], dataset: str, runtime: Mapping[str, str], args: argparse.Namespace) -> tuple[list[dict], dict]:
    prompt = core.build_prompt(batch) if dataset == "lifebench" else build_log_prompt(batch, dataset)
    last_error = None
    for attempt in range(1, args.max_retries + 1):
        try:
            raw, usage = core.deepseek_chat_json(prompt, runtime, args.timeout, args.max_retries, args.verbose)
            labels = (core.validate_batch_result(raw, batch, runtime["model"]) if dataset == "lifebench"
                      else validate_log_result(raw, batch, dataset, runtime["model"]))
            return labels, usage
        except core.EvaluationError as exc:
            last_error = exc
            if attempt < args.max_retries:
                if args.verbose:
                    print(f"Schema retry {attempt}/{args.max_retries}: {exc}", file=sys.stderr)
                time.sleep(min(10.0, attempt * 1.5))
    if len(batch) > 1:
        midpoint = len(batch) // 2
        left, left_usage = extract_batch(batch[:midpoint], dataset, runtime, args)
        right, right_usage = extract_batch(batch[midpoint:], dataset, runtime, args)
        return left + right, dict(Counter(left_usage) + Counter(right_usage))
    raise core.EvaluationError(f"Batch validation failed: {last_error}")


def build_log_report(events: list[dict], labels: list[dict], dataset: str, sample: str, model: str) -> dict:
    by_id = {item["event_id"]: item for item in labels}
    detailed = []
    categories = Counter()
    keywords = Counter()
    splits = Counter()
    for event in events:
        label = by_id[event["event_id"]]
        splits[event["source_split"]] += 1
        if label["interest_signal"]:
            categories[label["category"]] += 1
            keywords.update(label["keywords"])
        detailed.append({
            "event_id": event["event_id"],
            "timestamp": event["timestamp"],
            "source_split": event["source_split"],
            "source_sample_id": event["source_sample_id"],
            "content": event["description"],
            "interest_signal": label["interest_signal"],
            "category": label["category"],
            "keywords": label["keywords"],
            "confidence": label["confidence"],
        })
    calculated = calculate(events, detailed)
    dates = [profile["date"] for profile in calculated["daily_profiles"]]
    signal_count = calculated["coverage"]["interest_event_count"]
    scope = ("timestamped user logs only; dialogue/topics/labels excluded" if dataset == "mempal"
             else "dated platform behavior records; one record per observation")
    return {
        "schema_version": 1,
        "method": {
            "name": "Mem-PAL consecutive-day interest drift" if dataset == "mempal" else "OmniBehavior consecutive-day interest drift",
            "formula": "1 - |C_t intersection C_(t-1)| / |C_t union C_(t-1)|",
            "unit": "calendar-consecutive day pair",
            "input_scope": scope,
            "empty_policy": "skip both-empty pairs; one-empty pairs have drift 1",
            "prompt_version": PROMPT_VERSIONS[dataset],
            "official_reproduction": False,
        },
        "provenance": {
            "user_id": sample,
            "evaluated_logs_sha256": core.sha256_json(events),
            "model": model,
        },
        "coverage": {
            "log_count": len(events),
            "day_count": len(dates),
            "first_date": min(dates),
            "last_date": max(dates),
            "source_split_counts": dict(splits),
            "interest_log_count": signal_count,
            "interest_signal_rate": round(signal_count / len(events), 6),
        },
        "interest_drift": calculated["interest_drift"],
        "distributions": {
            "interest_categories": dict(categories.most_common()),
            "top_keywords": dict(keywords.most_common(100)),
        },
        "daily_profiles": calculated["daily_profiles"],
        "event_extractions": detailed,
    }


def select_rows(data_dir: Path, dataset: str, sample: str | None) -> list[dict]:
    rows = core.read_json(data_dir / "manifest.json")
    selected = [row for row in rows if (dataset == "all" or row["dataset"] == dataset)
                and (sample is None or row["sample"] == sample)]
    if not selected:
        raise core.EvaluationError(f"No input found for dataset={dataset}, sample={sample}")
    return selected


def run_one(row: dict, data_dir: Path, runtime: Mapping[str, str], args: argparse.Namespace) -> None:
    dataset, sample = row["dataset"], row["sample"]
    input_path = data_dir / row["normalized_input"]
    events = core.read_json(input_path)
    if not isinstance(events, list) or len(events) != row["event_count"] or not events:
        raise core.EvaluationError(f"Invalid event count for {dataset}/{sample}")
    seen = set()
    for event in events:
        if not isinstance(event, dict) or not isinstance(event.get("event_id"), str):
            raise core.EvaluationError(f"Invalid event in {dataset}/{sample}")
        if event["event_id"] in seen:
            raise core.EvaluationError(f"Duplicate event_id in {dataset}/{sample}")
        seen.add(event["event_id"])
        dt.date.fromisoformat(event["date"])
        if dataset != "lifebench":
            compact_log(event)
            event["source_split"]
            event["source_sample_id"]
    output = args.output_dir / dataset / f"{sample}.json"
    cache_path = args.cache_dir / dataset / f"{sample}.json"
    cache_items = {} if args.refresh else core.load_cache(cache_path)
    labels = {}
    missing = []
    for event in events:
        cached = cache_items.get(event["event_id"])
        if cached and cached.get("event_hash") == event_fingerprint(event, dataset, runtime["model"]):
            labels[event["event_id"]] = cached
        else:
            missing.append(event)
    batches = make_batches(missing, dataset, args.batch_size, args.max_batch_chars)
    print(f"{dataset}/{sample}: {len(events)} events, {len(labels)} cached, {len(batches)} requests")
    if args.dry_run:
        return
    if missing and not runtime["api_key"]:
        raise core.EvaluationError("Set llm.api_key in config/config.json or DEEPSEEK_API_KEY")
    usage = Counter()
    if batches:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.max_workers) as executor:
            futures = {executor.submit(extract_batch, batch, dataset, runtime, args): batch for batch in batches}
            try:
                for future in concurrent.futures.as_completed(futures):
                    extracted, batch_usage = future.result()
                    for item in extracted:
                        cache_items[item["event_id"]] = item
                        labels[item["event_id"]] = item
                    usage.update(batch_usage)
                    core.write_json_atomic(cache_path, {
                        "schema_version": 1,
                        "prompt_version": PROMPT_VERSIONS[dataset],
                        "items": cache_items,
                    })
            except Exception:
                for future in futures:
                    future.cancel()
                raise
    ordered = [labels[event["event_id"]] for event in events]
    report = (core.build_report(events, ordered, input_path, runtime["model"], usage)
              if dataset == "lifebench" else build_log_report(events, ordered, dataset, sample, runtime["model"]))
    core.write_json_atomic(output, report)
    print(f"{dataset}/{sample}: mean={report['interest_drift']['summary']['mean']} -> {output}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", choices=["all", *PROMPT_VERSIONS], default="all")
    parser.add_argument("--sample")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--output-dir", type=Path, default=Path("output/interest_stats/online"))
    parser.add_argument("--cache-dir", type=Path, default=Path("output/interest_stats/cache"))
    parser.add_argument("--config", type=Path, default=core.DEFAULT_CONFIG)
    parser.add_argument("--base-url")
    parser.add_argument("--model")
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--max-batch-chars", type=int, default=30000)
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--max-retries", type=int, default=4)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    if args.sample and args.dataset == "all":
        parser.error("--sample requires a specific --dataset")
    for field in ("batch_size", "max_batch_chars", "max_workers", "timeout", "max_retries"):
        if getattr(args, field) <= 0:
            parser.error(f"--{field.replace('_', '-')} must be positive")
    try:
        rows = select_rows(args.data_dir, args.dataset, args.sample)
        runtime = core.load_runtime_config(args.config, args.base_url, args.model)
        for row in rows:
            run_one(row, args.data_dir, runtime, args)
    except (core.EvaluationError, ValueError, KeyError, TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
