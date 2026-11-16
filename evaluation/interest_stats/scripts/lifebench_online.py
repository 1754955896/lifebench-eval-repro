"""This module generates interest drift results."""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import hashlib
import json
import math
import os
import random
import re
import statistics
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG = REPO_ROOT / "config/config.json"
PROMPT_VERSION = "lifebench-interest-v1"


INTEREST_CATEGORIES: Mapping[str, str] = {
    "Sports & Fitness": "sports, exercise, training, outdoor fitness",
    "Arts & Culture": "art, music, literature, photography, crafts, museums",
    "Entertainment & Media": "film, television, games, online media, fandom",
    "Technology": "technology, electronics, software, science, digital products",
    "Food & Cooking": "food preferences, cooking, restaurants, beverages",
    "Travel & Outdoors": "travel, sightseeing, camping, nature, non-routine outings",
    "Shopping & Fashion": "shopping preferences, clothing, beauty, consumer goods",
    "Finance": "investing, saving, personal finance, markets",
    "Education & Learning": "voluntary study, courses, reading, skill development",
    "Career & Professional": "professional interests, career development, work expertise",
    "Health & Wellness": "health management, medicine, sleep, mental well-being",
    "Social & Relationships": "friendship, dating, social groups, shared activities",
    "Family & Parenting": "family-oriented interests, parenting, kinship activities",
    "Home & Lifestyle": "home improvement, pets, gardening, lifestyle preferences",
    "Civic & Public Affairs": "news, public affairs, volunteering, community participation",
    "Other": "a genuine interest that does not fit another category",
    "None": "routine or obligation with no reliable stable-interest signal",
}

DATE_RE = re.compile(r"(?P<date>\d{4}-\d{2}-\d{2})")
CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
PRINT_LOCK = threading.Lock()


class EvaluationError(RuntimeError):
    pass


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Use DeepSeek to extract LifeBench interests and calculate daily 1-Jaccard drift."
    )
    parser.add_argument("--input", type=Path, required=True, help="Target daily_event.json")
    parser.add_argument("--output", type=Path, required=True, help="Final JSON report path")
    parser.add_argument("--cache", type=Path, help="Extraction cache path")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="LifeBench config.json")
    parser.add_argument("--base-url", help="Override llm.base_url")
    parser.add_argument("--model", help="Override llm.default_model")
    parser.add_argument("--from-date", required=True, help="Inclusive YYYY-MM-DD filter")
    parser.add_argument("--to-date", required=True, help="Inclusive YYYY-MM-DD filter")
    parser.add_argument("--limit", type=int, help="Evaluate only the first N events (smoke tests)")
    parser.add_argument("--batch-size", type=int, default=16, help="Events per DeepSeek request")
    parser.add_argument("--max-batch-chars", type=int, default=30000, help="Approximate prompt payload cap")
    parser.add_argument("--max-workers", type=int, default=4, help="Concurrent DeepSeek requests")
    parser.add_argument("--timeout", type=float, default=120.0, help="HTTP timeout in seconds")
    parser.add_argument("--max-retries", type=int, default=4, help="Retries per failed batch")
    parser.add_argument("--refresh", action="store_true", help="Ignore cached extractions")
    parser.add_argument("--dry-run", action="store_true", help="Validate and estimate requests without API calls")
    parser.add_argument("--verbose", action="store_true", help="Print retry details")
    args = parser.parse_args(argv)

    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be positive")
    if args.batch_size <= 0 or args.max_batch_chars <= 0:
        parser.error("--batch-size and --max-batch-chars must be positive")
    if args.max_workers <= 0 or args.max_retries <= 0 or args.timeout <= 0:
        parser.error("--max-workers, --max-retries, and --timeout must be positive")
    for field in ("from_date", "to_date"):
        value = getattr(args, field)
        if value:
            try:
                dt.date.fromisoformat(value)
            except ValueError:
                parser.error(f"--{field.replace('_', '-')} must be YYYY-MM-DD")
    if args.from_date and args.to_date and args.from_date > args.to_date:
        parser.error("--from-date must not be later than --to-date")
    return args


def read_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as stream:
            return json.load(stream)
    except FileNotFoundError as exc:
        raise EvaluationError(f"File not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise EvaluationError(f"Invalid JSON in {path}: {exc}") from exc


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def sha256_json(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def extract_event_date(event: Mapping[str, Any]) -> str:
    raw_date = event.get("date")
    candidates = raw_date if isinstance(raw_date, list) else [raw_date]
    for candidate in candidates:
        match = DATE_RE.search(str(candidate or ""))
        if match:
            value = match.group("date")
            try:
                dt.date.fromisoformat(value)
            except ValueError:
                continue
            return value
    raise EvaluationError(f"Event {event.get('event_id')!r} has no parseable date: {raw_date!r}")


def normalize_events(raw: Any, from_date: Optional[str], to_date: Optional[str]) -> List[Dict[str, Any]]:
    if not isinstance(raw, list):
        raise EvaluationError("daily_event.json must contain a top-level JSON array")
    normalized: List[Dict[str, Any]] = []
    seen_ids = set()
    for index, event in enumerate(raw):
        if not isinstance(event, dict):
            raise EvaluationError(f"Event at index {index} is not a JSON object")
        event_id = str(event.get("event_id", "")).strip()
        if not event_id:
            raise EvaluationError(f"Event at index {index} has no event_id")
        if event_id in seen_ids:
            raise EvaluationError(f"Duplicate event_id: {event_id}")
        seen_ids.add(event_id)
        date = extract_event_date(event)
        if from_date and date < from_date:
            continue
        if to_date and date > to_date:
            continue
        normalized.append(
            {
                "event_id": event_id,
                "date": date,
                "name": str(event.get("name", "")).strip(),
                "event_type": str(event.get("type", "Other")).strip() or "Other",
                "description": str(event.get("description", "")).strip(),
                "participants": [
                    str(item.get("name", "")).strip()
                    for item in event.get("participant", [])
                    if isinstance(item, dict) and item.get("name")
                ],
                "location": str(event.get("location", "")).strip(),
            }
        )
    normalized.sort(key=lambda item: (item["date"], numeric_sort_key(item["event_id"])))
    return normalized


def numeric_sort_key(value: str) -> Tuple[int, Any]:
    try:
        return (0, int(value))
    except ValueError:
        return (1, value)


def event_fingerprint(event: Mapping[str, Any], model: str) -> str:
    return sha256_json(
        {
            "prompt_version": PROMPT_VERSION,
            "model": model,
            "taxonomy": list(INTEREST_CATEGORIES),
            "event": event,
        }
    )


def load_runtime_config(path: Path, base_url_override: Optional[str], model_override: Optional[str]) -> Dict[str, str]:
    data: Dict[str, Any] = {}
    if path.exists():
        loaded = read_json(path)
        if not isinstance(loaded, dict):
            raise EvaluationError(f"Config must be a JSON object: {path}")
        data = loaded.get("llm", {}) or {}
        if not isinstance(data, dict):
            raise EvaluationError(f"llm config must be an object: {path}")

    api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("LLM_API_KEY") or str(data.get("api_key", "")).strip()
    base_url = base_url_override or os.environ.get("DEEPSEEK_BASE_URL") or os.environ.get("LLM_BASE_URL") or str(
        data.get("base_url", "https://api.deepseek.com")
    )
    model = model_override or os.environ.get("DEEPSEEK_MODEL") or os.environ.get("LLM_DEFAULT_MODEL") or str(
        data.get("default_model", "deepseek-v4-flash")
    )
    return {
        "api_key": api_key,
        "base_url": base_url.rstrip("/"),
        "model": model,
    }


def default_paths(args: argparse.Namespace) -> Tuple[Path, Path]:
    output = args.output
    cache = args.cache or output.with_name(f"{output.stem}.cache.json")
    return output.resolve(), cache.resolve()


def load_cache(path: Path) -> Dict[str, Dict[str, Any]]:
    if not path.exists():
        return {}
    raw = read_json(path)
    if not isinstance(raw, dict) or raw.get("schema_version") != 1 or not isinstance(raw.get("items"), dict):
        raise EvaluationError(f"Unsupported cache format: {path}")
    return raw["items"]


def save_cache(path: Path, items: Mapping[str, Mapping[str, Any]]) -> None:
    write_json_atomic(
        path,
        {
            "schema_version": 1,
            "prompt_version": PROMPT_VERSION,
            "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "items": dict(items),
        },
    )


def compact_event(event: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "event_id": event["event_id"],
        "date": event["date"],
        "name": event["name"],
        "type": event["event_type"],
        "description": event["description"],
        "participants": event["participants"],
        "location": event["location"],
    }


def make_batches(events: Sequence[Mapping[str, Any]], batch_size: int, max_chars: int) -> List[List[Mapping[str, Any]]]:
    batches: List[List[Mapping[str, Any]]] = []
    current: List[Mapping[str, Any]] = []
    current_chars = 0
    for event in events:
        size = len(json.dumps(compact_event(event), ensure_ascii=False))
        if current and (len(current) >= batch_size or current_chars + size > max_chars):
            batches.append(current)
            current = []
            current_chars = 0
        current.append(event)
        current_chars += size
    if current:
        batches.append(current)
    return batches


def build_prompt(batch: Sequence[Mapping[str, Any]]) -> str:
    taxonomy = "\n".join(f"- {name}: {description}" for name, description in INTEREST_CATEGORIES.items())
    example = {
        "results": [
            {
                "event_id": "example-1",
                "interest_signal": True,
                "category": "Sports & Fitness",
                "keywords": ["running", "marathon", "training"],
                "confidence": 0.94,
            }
        ]
    }
    return f"""You are a behavioral-data analyst. Classify each LifeBench event independently.

Goal: reproduce the interest-extraction step used by OmniBehavior's LoCoMo comparison. Identify a
stable or evolving personal interest, not merely the physical action occurring in the event.

Rules:
1. Return exactly one result for every event_id and preserve event_id verbatim.
2. Choose exactly one category from the closed taxonomy below.
3. Set interest_signal=false and category="None" for routine obligations with no reliable preference
   signal (sleeping, commuting, ordinary meals, hygiene, generic work execution).
4. Repeated voluntary choices, hobbies, preferences, learning goals, social-group participation, and
   personally meaningful health/career pursuits can be interest signals.
5. Provide zero to three concise keywords grounded only in the event. Do not infer unsupported facts.
6. confidence must be a JSON number from 0 to 1.
7. Output raw valid JSON only, with no markdown or explanation.

Closed taxonomy:
{taxonomy}

Example JSON shape:
{json.dumps(example, ensure_ascii=False)}

Events:
{json.dumps([compact_event(item) for item in batch], ensure_ascii=False)}
"""


def deepseek_chat_json(
    prompt: str,
    runtime: Mapping[str, str],
    timeout: float,
    max_retries: int,
    verbose: bool,
) -> Tuple[Dict[str, Any], Dict[str, int]]:
    endpoint = f"{runtime['base_url']}/chat/completions"
    body = {
        "model": runtime["model"],
        "messages": [
            {
                "role": "system",
                "content": "Return strictly valid JSON for behavioral interest extraction.",
            },
            {"role": "user", "content": prompt},
        ],
        "response_format": {"type": "json_object"},
        "thinking": {"type": "disabled"},
        "temperature": 0,
        "max_tokens": 4096,
        "stream": False,
    }
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    last_error: Optional[BaseException] = None

    for attempt in range(1, max_retries + 1):
        request = urllib.request.Request(
            endpoint,
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {runtime['api_key']}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                envelope = json.loads(response.read().decode("utf-8"))
            choice = envelope["choices"][0]
            if choice.get("finish_reason") == "length":
                raise EvaluationError("DeepSeek response was truncated (finish_reason=length)")
            content = choice["message"].get("content") or ""
            content = CODE_FENCE_RE.sub("", content.strip())
            if not content:
                raise EvaluationError("DeepSeek returned empty content")
            parsed = json.loads(content)
            usage = envelope.get("usage") or {}
            return parsed, {
                "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
                "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
                "total_tokens": int(usage.get("total_tokens", 0) or 0),
            }
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            last_error = EvaluationError(f"DeepSeek HTTP {exc.code}: {detail}")
            retryable = exc.code == 429 or 500 <= exc.code < 600
            if not retryable:
                raise last_error
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError, IndexError, EvaluationError) as exc:
            last_error = exc

        if attempt < max_retries:
            delay = min(30.0, 1.5 * (2 ** (attempt - 1))) + random.random()
            if verbose:
                with PRINT_LOCK:
                    print(f"Retry {attempt}/{max_retries} after {type(last_error).__name__}: {last_error}", file=sys.stderr)
            time.sleep(delay)
    raise EvaluationError(f"DeepSeek request failed after {max_retries} attempts: {last_error}")


def normalize_keywords(raw: Any) -> Tuple[List[str], Optional[Dict[str, Any]]]:


    values = [raw] if isinstance(raw, str) else raw if isinstance(raw, list) else []
    cleaned = list(dict.fromkeys(value.strip() for value in values
                                 if isinstance(value, str) and value.strip()))[:3]
    audit = None if raw == cleaned else {
        "original": raw, "normalized": cleaned,
        "policy": "strip, discard nonstrings/empty values, deduplicate, keep first three; string becomes one keyword",
    }
    return cleaned, audit


def validate_batch_result(
    raw: Mapping[str, Any], batch: Sequence[Mapping[str, Any]], model: str
) -> List[Dict[str, Any]]:
    results = raw.get("results") if isinstance(raw, dict) else None
    if not isinstance(results, list):
        raise EvaluationError("DeepSeek JSON must contain a results array")
    expected = {item["event_id"]: item for item in batch}
    validated: Dict[str, Dict[str, Any]] = {}
    for item in results:
        if not isinstance(item, dict):
            raise EvaluationError("Every DeepSeek result must be an object")
        event_id = str(item.get("event_id", ""))
        if event_id not in expected:
            raise EvaluationError(f"Unexpected event_id in DeepSeek result: {event_id!r}")
        if event_id in validated:
            raise EvaluationError(f"Duplicate event_id in DeepSeek result: {event_id}")
        signal = item.get("interest_signal")
        if not isinstance(signal, bool):
            raise EvaluationError(f"interest_signal must be boolean for event {event_id}")
        category = str(item.get("category", ""))
        if category not in INTEREST_CATEGORIES:
            raise EvaluationError(f"Unknown category {category!r} for event {event_id}")
        if not signal:
            category = "None"
        elif category == "None":
            raise EvaluationError(f"Interest event {event_id} cannot use category None")
        keywords, keyword_audit = normalize_keywords(item.get("keywords", []))
        try:
            confidence = float(item.get("confidence"))
        except (TypeError, ValueError) as exc:
            raise EvaluationError(f"Invalid confidence for event {event_id}") from exc
        if not 0 <= confidence <= 1:
            raise EvaluationError(f"confidence outside [0, 1] for event {event_id}")
        validated[event_id] = {
            "event_id": event_id,
            "event_hash": event_fingerprint(expected[event_id], model),
            "date": expected[event_id]["date"],
            "interest_signal": signal,
            "category": category,
            "keywords": keywords,
            "confidence": round(confidence, 6),
        }
        if keyword_audit is not None:
            validated[event_id]["keyword_normalization"] = keyword_audit
    missing = set(expected) - set(validated)
    if missing:
        raise EvaluationError(f"DeepSeek omitted event_ids: {sorted(missing, key=numeric_sort_key)}")
    return [validated[item["event_id"]] for item in batch]


def extract_batch(
    batch: Sequence[Mapping[str, Any]],
    runtime: Mapping[str, str],
    timeout: float,
    max_retries: int,
    verbose: bool,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:


    last_error: Optional[BaseException] = None
    for attempt in range(1, max_retries + 1):
        try:
            raw, usage = deepseek_chat_json(build_prompt(batch), runtime, timeout, max_retries, verbose)
            if runtime.get("response_dir"):
                batch_id = sha256_json([event["event_id"] for event in batch])[:16]
                write_json_atomic(
                    Path(runtime["response_dir"]) / f"{batch_id}_{time.time_ns()}_attempt{attempt}.json",
                    {"model": runtime["model"], "event_ids": [e["event_id"] for e in batch],
                     "response": raw, "usage": usage},
                )
            return validate_batch_result(raw, batch, runtime["model"]), usage
        except EvaluationError as exc:
            last_error = exc
            if attempt < max_retries:
                if verbose:
                    with PRINT_LOCK:
                        print(f"Schema retry {attempt}/{max_retries}: {exc}", file=sys.stderr)
                time.sleep(min(10.0, attempt * 1.5))
    raise EvaluationError(f"Batch validation failed after {max_retries} attempts: {last_error}")


def jaccard_drift(left: Iterable[str], right: Iterable[str]) -> Optional[float]:
    left_set, right_set = set(left), set(right)
    union = left_set | right_set
    if not union:
        return None
    return 1.0 - len(left_set & right_set) / len(union)


def percentile(values: Sequence[float], fraction: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)


def summarize_values(values: Sequence[float]) -> Dict[str, Any]:
    if not values:
        return {"count": 0, "mean": None, "median": None, "stddev": None, "p25": None, "p75": None}
    return {
        "count": len(values),
        "mean": round(statistics.fmean(values), 6),
        "median": round(statistics.median(values), 6),
        "stddev": round(statistics.pstdev(values), 6),
        "p25": round(percentile(values, 0.25) or 0.0, 6),
        "p75": round(percentile(values, 0.75) or 0.0, 6),
        "min": round(min(values), 6),
        "max": round(max(values), 6),
    }


def calculate_drift_series(daily_sets: Mapping[str, Iterable[str]]) -> Tuple[List[Dict[str, Any]], int]:
    dates = sorted(daily_sets)
    series: List[Dict[str, Any]] = []
    skipped_empty_pairs = 0
    for previous, current in zip(dates, dates[1:]):

        if (dt.date.fromisoformat(current) - dt.date.fromisoformat(previous)).days != 1:
            continue
        left, right = sorted(set(daily_sets[previous])), sorted(set(daily_sets[current]))
        drift = jaccard_drift(left, right)
        if drift is None:
            skipped_empty_pairs += 1
            continue
        series.append(
            {
                "previous_date": previous,
                "date": current,
                "previous_categories": left,
                "categories": right,
                "drift": round(drift, 6),
            }
        )
    return series, skipped_empty_pairs


def normalize_event_type(value: str) -> str:
    compact = re.sub(r"\s+", "", value).lower().replace("&", "and")
    aliases = {
        "familyandlivingsituation": "Family & Living Situation",
        "personallife": "Personal Life",
        "unexpectedevents": "Unexpected Events",
    }
    return aliases.get(compact, value.strip() or "Other")


def build_report(
    events: Sequence[Mapping[str, Any]],
    extractions: Sequence[Mapping[str, Any]],
    input_path: Path,
    model: str,
    usage: Mapping[str, int],
) -> Dict[str, Any]:
    extraction_by_id = {item["event_id"]: item for item in extractions}
    daily_interest_counts: Dict[str, Counter] = defaultdict(Counter)
    daily_type_counts: Dict[str, Counter] = defaultdict(Counter)
    category_counts: Counter = Counter()
    keyword_counts: Counter = Counter()
    signal_count = 0

    detailed = []
    for event in events:
        result = extraction_by_id[event["event_id"]]
        event_type = normalize_event_type(event["event_type"])
        daily_type_counts[event["date"]][event_type] += 1
        if result["interest_signal"]:
            signal_count += 1
            category_counts[result["category"]] += 1
            daily_interest_counts[event["date"]][result["category"]] += 1
            keyword_counts.update(result["keywords"])
        else:

            daily_interest_counts[event["date"]]
        detailed.append(
            {
                "event_id": event["event_id"],
                "date": event["date"],
                "name": event["name"],
                "event_type": event_type,
                "interest_signal": result["interest_signal"],
                "category": result["category"],
                "keywords": result["keywords"],
                "confidence": result["confidence"],
            }
        )
        if "keyword_normalization" in result:
            detailed[-1]["keyword_normalization"] = result["keyword_normalization"]

    interest_sets = {date: set(counts) for date, counts in daily_interest_counts.items()}
    type_sets = {date: set(counts) for date, counts in daily_type_counts.items()}
    interest_series, interest_empty = calculate_drift_series(interest_sets)
    type_series, type_empty = calculate_drift_series(type_sets)
    interest_values = [item["drift"] for item in interest_series]
    type_values = [item["drift"] for item in type_series]

    month_values: Dict[str, List[float]] = defaultdict(list)
    for item in interest_series:
        month_values[item["date"][:7]].append(item["drift"])

    return {
        "schema_version": 1,
        "method": {
            "name": "OmniBehavior-inspired consecutive-day interest drift",
            "formula": "1 - |C_t intersection C_(t-1)| / |C_t union C_(t-1)|",
            "unit": "calendar-consecutive day pair",
            "empty_policy": "skip pairs where both category sets are empty; one-empty pairs have drift 1",
            "prompt_version": PROMPT_VERSION,
            "official_reproduction": False,
        },
        "provenance": {
            "evaluated_events_sha256": sha256_json(list(events)),
            "model": model,
        },
        "coverage": {
            "event_count": len(events),
            "day_count": len(daily_interest_counts),
            "first_date": min(daily_interest_counts) if daily_interest_counts else None,
            "last_date": max(daily_interest_counts) if daily_interest_counts else None,
            "interest_event_count": signal_count,
            "interest_signal_rate": round(signal_count / len(events), 6) if events else None,
        },
        "interest_drift": {
            "summary": summarize_values(interest_values),
            "skipped_both_empty_pairs": interest_empty,
            "monthly_summary": {month: summarize_values(values) for month, values in sorted(month_values.items())},
            "series": interest_series,
        },
        "event_type_drift_audit": {
            "description": "Deterministic drift over LifeBench's original event type sets; no LLM extraction.",
            "summary": summarize_values(type_values),
            "skipped_both_empty_pairs": type_empty,
            "series": type_series,
        },
        "distributions": {
            "interest_categories": dict(category_counts.most_common()),
            "top_keywords": dict(keyword_counts.most_common(100)),
            "event_types": dict(Counter(normalize_event_type(event["event_type"]) for event in events).most_common()),
        },
        "daily_profiles": [
            {
                "date": date,
                "event_count": sum(daily_type_counts[date].values()),
                "interest_event_count": sum(daily_interest_counts[date].values()),
                "interest_categories": dict(daily_interest_counts[date].most_common()),
                "event_types": dict(daily_type_counts[date].most_common()),
            }
            for date in sorted(daily_interest_counts)
        ],
        "event_extractions": detailed,
        "keyword_normalization_audit": {
            "repaired_event_count": sum("keyword_normalization" in item for item in detailed),
            "note": "Auxiliary keywords only; interest_signal, category taxonomy and drift formula are unchanged.",
        },
    }


def run(args: argparse.Namespace) -> int:
    input_path = args.input.resolve()
    raw = read_json(input_path)
    events = normalize_events(raw, args.from_date, args.to_date)
    if args.limit is not None:
        events = events[: args.limit]
    if not events:
        raise EvaluationError("No events remain after filtering")

    runtime = load_runtime_config(args.config.resolve(), args.base_url, args.model)
    output_path, cache_path = default_paths(args)
    cache_items = {} if args.refresh else load_cache(cache_path)
    cached_results: Dict[str, Dict[str, Any]] = {}
    missing_events = []
    for event in events:
        cached = cache_items.get(event["event_id"])
        expected_hash = event_fingerprint(event, runtime["model"])
        if cached and cached.get("event_hash") == expected_hash:
            cached_results[event["event_id"]] = cached
        else:
            missing_events.append(event)
    batches = make_batches(missing_events, args.batch_size, args.max_batch_chars)

    print(f"Input: {input_path}")
    print(f"Events: {len(events)} (cached {len(cached_results)}, missing {len(missing_events)})")
    print(f"DeepSeek model: {runtime['model']}")
    print(f"Planned API requests: {len(batches)}")
    if args.dry_run:
        print("Dry run: no API request or output/cache file was written.")
        return 0
    if missing_events and not runtime["api_key"]:
        raise EvaluationError(
            "DeepSeek API key is missing. Set DEEPSEEK_API_KEY or llm.api_key in config/config.json."
        )

    usage = Counter()
    completed_batches = 0
    if batches:
        runtime["response_dir"] = str(output_path.with_name(output_path.stem + ".responses"))
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.max_workers) as executor:
            future_map = {
                executor.submit(
                    extract_batch,
                    batch,
                    runtime,
                    args.timeout,
                    args.max_retries,
                    args.verbose,
                ): batch
                for batch in batches
            }
            try:
                for future in concurrent.futures.as_completed(future_map):
                    batch_results, batch_usage = future.result()
                    for item in batch_results:
                        cache_items[item["event_id"]] = item
                        cached_results[item["event_id"]] = item
                    usage.update(batch_usage)
                    completed_batches += 1
                    save_cache(cache_path, cache_items)
                    print(f"Completed batches: {completed_batches}/{len(batches)}", flush=True)
            except Exception:
                for future in future_map:
                    future.cancel()
                raise

    ordered_results = [cached_results[event["event_id"]] for event in events]
    report = build_report(events, ordered_results, input_path, runtime["model"], usage)
    write_json_atomic(output_path, report)
    mean = report["interest_drift"]["summary"]["mean"]
    print(f"Interest drift mean: {mean}")
    print(f"Report: {output_path}")
    print(f"Cache: {cache_path}")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        return run(parse_args(argv))
    except EvaluationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted; completed batches remain in the cache.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
