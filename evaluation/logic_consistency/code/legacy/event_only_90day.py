"""Compare event_consistency_rate on the same 90 source days for two datasets.

Run from the repository root with ``python -m evaluation.logic_consistency.code.legacy.event_only_90day``.
Only DeepSeek is used; this script does not call geocoding or route APIs.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

from evaluation.logic_consistency.code.internal.model import Deepseek


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
DEFAULT_BASELINE = ROOT / "data/lifebench_10people/fenghaoran/daily_event.json"
DEFAULT_OBJECTIVE = ROOT / "data/ablation_fenghaoran_2025/objective_only/fenghaoran/daily_event.json"
EVENT_PROMPT = (HERE.parent / "prompts/events.txt").read_text(encoding="utf-8")
CHECK_PROMPT = (HERE.parent / "prompts/check.txt").read_text(encoding="utf-8")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
EVENT_RE = re.compile(r"\(([^()]+?),\s*(\d{4}-\d{2}-\d{2})(?:-([早中晚全]))?\)")


def load_days(path: Path) -> dict[str, list[dict]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"Expected a daily_event.json array: {path}")
    days = defaultdict(list)
    for item in data:
        if not isinstance(item, dict):
            continue
        for interval in item.get("date", []):
            if not isinstance(interval, str) or len(interval) < 10:
                continue
            day = interval[:10]
            if not DATE_RE.fullmatch(day):
                continue
            days[day].append({interval.replace("至", "-", 1): {
                "description": item.get("description") or "",
                "location": item.get("location") or "",
            }})
    return dict(days)


def sample_days(common: list[str], n: int, seed: int) -> list[str]:
    """Month-stratified, reproducible sample with near-proportional quotas."""
    if len(common) < n:
        raise ValueError(f"Only {len(common)} shared days; need {n}")
    groups = defaultdict(list)
    for day in common:
        groups[day[:7]].append(day)
    ideal = {month: n * len(days) / len(common) for month, days in groups.items()}
    quota = {month: int(value) for month, value in ideal.items()}
    remaining = n - sum(quota.values())
    for month in sorted(groups, key=lambda m: (-(ideal[m] - quota[m]), m))[:remaining]:
        quota[month] += 1
    rng = random.Random(seed)
    return sorted(day for month in sorted(groups)
                  for day in rng.sample(groups[month], quota[month]))


def ask_retry(client: Deepseek, prompt: str, attempts: int = 3) -> str:
    last = None
    for attempt in range(attempts):
        try:
            return client.ask(prompt)
        except Exception as exc:
            last = exc
            if attempt + 1 < attempts:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"Model request failed after {attempts} attempts: {last}")


def extract(client: Deepseek, source_day: str, story: list[dict]) -> list[dict]:
    # The legacy extraction prompt expects the original date-tagged event list.
    response = ask_retry(client, EVENT_PROMPT.format(str({source_day: story})))
    match = re.search(r"<events>(.*?)</events>", response, re.S | re.I)
    if not match:
        raise ValueError("Missing <events> block")
    body = match.group(1).strip()
    if not body or "<none>" in body.lower():
        return []
    parsed = []
    seen = set()
    pairs = EVENT_RE.findall(body)
    for event, target_day, period in pairs:
        event = event.strip()
        if not event or target_day == source_day:
            continue
        date.fromisoformat(target_day)
        key = (event, target_day, period)
        if key not in seen:
            seen.add(key)
            parsed.append({"event": event, "target_day": target_day, "period": period})
    if not pairs:
        raise ValueError("No valid event/date pairs in nonempty extraction")
    return parsed


def check(client: Deepseek, event: str, story: list[dict]) -> str:
    response = ask_retry(client, CHECK_PROMPT.format(event, str(story)))
    match = re.search(r"<results>\s*(Yes|No)\s*</results>", response, re.I | re.S)
    if not match:
        raise ValueError("Missing single Yes/No result")
    return match.group(1).lower()


def assess(client: Deepseek, row: dict, days: dict[str, list[dict]]) -> dict:
    target = row["target_day"]
    result = dict(row)
    if target not in days:
        result.update(status="unknown", reason="target_day_missing")
        return result
    try:
        if check(client, row["event"], days[target]) == "yes":
            result.update(status="correct", evidence_day=target)
            return result
        anchor = date.fromisoformat(target)
        nearby = []
        for offset in (-2, -1, 1, 2):
            day = (anchor + timedelta(days=offset)).isoformat()
            if day in days and check(client, row["event"], days[day]) == "yes":
                nearby.append(day)
        if nearby:
            result.update(status="suspected", evidence_days=nearby)
        elif all((anchor + timedelta(days=o)).isoformat() in days for o in range(-2, 3)):
            result.update(status="wrong", reason="not_found_within_two_days")
        else:
            result.update(status="unknown", reason="incomplete_two_day_window")
    except Exception as exc:
        result.update(status="unknown", reason=f"check_error: {exc}")
    return result


def evaluate_version(label: str, days: dict[str, list[dict]], selected: list[str],
                     client: Deepseek, workers: int, output: Path) -> dict:
    """Append durable per-day records; resume skips completed source days."""
    output.mkdir(parents=True, exist_ok=True)
    records_dir = output / label / "days"
    records_dir.mkdir(parents=True, exist_ok=True)
    scoped_days = {day: days[day] for day in selected}
    for index, source_day in enumerate(selected, 1):
        record_path = records_dir / f"{source_day}.json"
        if record_path.exists():
            previous = json.loads(record_path.read_text(encoding="utf-8"))
            if "extraction_error" not in previous:
                continue
        try:
            items = extract(client, source_day, days[source_day])
        except Exception as exc:
            record = {"source_day": source_day, "extraction_error": str(exc), "results": []}
        else:
            rows = [{"source_day": source_day, **item} for item in items]
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = [executor.submit(assess, client, row, scoped_days) for row in rows]
                results = [future.result() for future in futures]
            record = {"source_day": source_day, "extracted": len(items), "results": results}
        tmp = record_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(record_path)
        print(f"{label}: {index}/{len(selected)} {source_day} ({len(record['results'])} checked)", flush=True)
    records = [json.loads((records_dir / f"{day}.json").read_text(encoding="utf-8"))
               for day in selected]
    counts = Counter(row["status"] for record in records for row in record["results"])
    denom = counts["correct"] + counts["wrong"]
    result = {
        "version": label, "sample_days": len(selected),
        "extraction_error_days": sum("extraction_error" in r for r in records),
        "counts": dict(counts),
        "event_consistency_rate": counts["correct"] / denom if denom else None,
        "denominator": denom,
        "definition": "correct / (correct + wrong); suspected and unknown excluded",
    }
    (output / label / "metrics.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Event-only 90-day baseline/objective comparison")
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--objective", type=Path, default=DEFAULT_OBJECTIVE)
    parser.add_argument("--output", type=Path, default=ROOT / "output/event_consistency_90day")
    parser.add_argument("--start-date", default="2025-09-10")
    parser.add_argument("--end-date", default="2025-12-08")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--seed", type=int, default=2025)
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--model", default="deepseek-chat")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.days < 1 or args.workers < 1:
        parser.error("--days and --workers must be positive")
    baseline, objective = load_days(args.baseline), load_days(args.objective)
    common = sorted(day for day in baseline.keys() & objective.keys()
                    if args.start_date <= day <= args.end_date)
    selected = sample_days(common, args.days, args.seed)
    print(f"Selected {len(selected)} shared source days from {len(common)} eligible days")
    if args.dry_run:
        print(json.dumps({"sampled_days": selected}, ensure_ascii=False, indent=2))
        return
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = {"baseline": str(args.baseline), "objective": str(args.objective),
                "range": [args.start_date, args.end_date], "seed": args.seed,
                "sampled_days": selected, "model": args.model}
    manifest_path = args.output / "manifest.json"
    if manifest_path.exists():
        prior = json.loads(manifest_path.read_text(encoding="utf-8"))
        if prior != manifest:
            raise ValueError("Output directory contains a different run; choose another --output")
    else:
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    client = Deepseek(model=args.model)
    results = [evaluate_version("baseline", baseline, selected, client, args.workers, args.output),
               evaluate_version("objective_only", objective, selected, client, args.workers, args.output)]
    summary = {"sampled_days": selected, "results": results}
    (args.output / "comparison.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
