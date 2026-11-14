"""Run the existing interest extractor on a baseline calendar window.

Default: September 19--December 18, 2025, inclusive (91 calendar days).
The historical 90d filename is retained for command compatibility.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, timedelta
import importlib.util
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[2]
CORE_PATH = Path(__file__).resolve().parent / "eval/drift_eval/lifebench_interest_drift_eval.py"
spec = importlib.util.spec_from_file_location("baseline_90d_interest_core", CORE_PATH)
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=REPO / "data/ablation_fenghaoran_2025/simulation_baseline")
    parser.add_argument("--person", default="fenghaoran")
    parser.add_argument("--start-date", type=date.fromisoformat, default=date(2025, 9, 19),
                        help="First calendar day; default: 2025-09-19")
    parser.add_argument("--days", type=int, default=91,
                        help="Inclusive calendar-day count, default 91; use 90 for exactly 90 days")
    parser.add_argument("--end-date", type=date.fromisoformat,
                        help="Inclusive end date; overrides --days")
    parser.add_argument("--output-dir", type=Path, default=REPO / "output/interest_stats/simulation_baseline_90d")
    parser.add_argument("--config", type=Path, default=REPO / "config/config.json")
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument("--max-workers", type=int, default=8)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    return parser


def run(args):
    folder = args.data_dir.resolve()
    source = folder / "daily_event.json"
    if not source.exists():
        source = folder / args.person / "daily_event.json"
    events = core.normalize_events(core.read_json(source), None, None)
    if not events:
        raise core.EvaluationError("No events available")
    if args.days < 1:
        raise core.EvaluationError("--days must be positive")
    first = args.start_date
    last = args.end_date or first + timedelta(days=args.days - 1)
    if last < first:
        raise core.EvaluationError("--end-date must not precede --start-date")
    calendar_days = (last - first).days + 1
    selected = [e for e in events if str(first) <= e["date"] <= str(last)]
    counts = Counter(e["date"] for e in selected)
    expected = {str(first + timedelta(days=i)) for i in range(calendar_days)}
    missing = sorted(expected - counts.keys())
    if missing:
        raise core.EvaluationError(f"{calendar_days}-day window is incomplete; missing dates: {missing}")
    stem = f"{args.person}_{first:%Y%m%d}_{last:%Y%m%d}"
    output = args.output_dir.resolve()
    report_path = output / f"{stem}.json"
    cache_path = output / f"{stem}.cache.json"
    argv = ["--input", str(source), "--from-date", str(first), "--to-date", str(last),
            "--output", str(report_path), "--cache", str(cache_path), "--config", str(args.config.resolve()),
            "--max-workers", str(args.max_workers), "--batch-size", str(args.batch_size),
            "--timeout", str(args.timeout), "--max-retries", str(args.max_retries), "--verbose"]
    for flag, value in (("--model", args.model), ("--base-url", args.base_url)):
        if value:
            argv += [flag, value]
    if args.dry_run:
        argv.append("--dry-run")
    if args.refresh:
        argv.append("--refresh")
    print(f"Window: {first} to {last}; {calendar_days} calendar days, {len(selected)} events, {calendar_days - 1} adjacent pairs", flush=True)
    result = core.run(core.parse_args(argv))
    if result or args.dry_run:
        return result
    report = core.read_json(report_path)
    if report["coverage"]["day_count"] != calendar_days or report["coverage"]["event_count"] != len(selected):
        raise core.EvaluationError("Report coverage differs from selected window")
    summary = {
        "source": str(source), "start_date": str(first), "end_date": str(last),
        "calendar_days": calendar_days, "possible_adjacent_pairs": calendar_days - 1, "event_count": len(selected),
        "daily_event_counts": dict(sorted(counts.items())),
        "model": report["provenance"]["model"], "method": report["method"],
        "interest_drift": report["interest_drift"]["summary"],
        "skipped_both_empty_pairs": report["interest_drift"]["skipped_both_empty_pairs"],
        "interest_signal_rate": report["coverage"]["interest_signal_rate"],
        "category_distribution": report["distributions"]["interest_categories"],
        "keyword_normalization_audit": report.get("keyword_normalization_audit"),
        "report_path": str(report_path), "cache_path": str(cache_path),
        "note": "Existing macro taxonomy and extractor unchanged; event type drift is not interest drift. A higher drift is not a quality score.",
    }
    core.write_json_atomic(output / f"{stem}.summary.json", summary)
    print(f"{calendar_days}-day interest drift mean: {summary['interest_drift']['mean']}; valid pairs: {summary['interest_drift']['count']}", flush=True)
    return 0


def main(argv=None):
    try:
        return run(build_parser().parse_args(argv))
    except (core.EvaluationError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted; completed batches remain cached. Rerun the same command to resume.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
