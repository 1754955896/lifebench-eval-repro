"""Compare original, no-planning and simulation-baseline interest drift."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, timedelta
import hashlib
import importlib.util
from pathlib import Path
import statistics
import sys

REPO = Path(__file__).resolve().parents[2]
SUITE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("ablation_interest_core", SUITE / "eval/drift_eval/lifebench_interest_drift_eval.py")
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)
NAMES = ("original", "no_planning", "simulation_baseline")


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original-dir", type=Path, default=REPO / "data/lifebench_10people/fenghaoran")
    parser.add_argument("--no-planning-dir", type=Path, default=REPO / "data/ablation_fenghaoran_2025/no_planning")
    parser.add_argument("--simulation-baseline-dir", type=Path, default=REPO / "data/ablation_fenghaoran_2025/simulation_baseline")
    parser.add_argument("--person", default="fenghaoran")
    parser.add_argument("--from-date", type=date.fromisoformat, default=date(2025, 9, 10))
    parser.add_argument("--to-date", type=date.fromisoformat, default=date(2025, 12, 8))
    parser.add_argument("--output-dir", type=Path, default=REPO / "output/interest_stats/ablation_comparison_20250910_20251208")
    parser.add_argument("--config", type=Path, default=REPO / "config/config.json")
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument("--max-workers", type=int, default=8, help="Concurrent requests within each dataset; datasets run sequentially")
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--refresh", action="store_true", help="Ignore all previous extraction caches and re-extract all three datasets")
    return parser


def seed_paths(person):
    """Only content/model/prompt-matched entries can be copied from these."""
    return {
        "original": [REPO / "results/interest_stats/original/interest_drift.cache.json"],
        "no_planning": [REPO / "results/interest_stats/no_planning/interest_drift.cache.json"],
        "simulation_baseline": [REPO / "results/interest_stats/simulation_baseline/interest_drift.cache.json"],
    }


def make_plan(args):
    if args.from_date > args.to_date:
        raise core.EvaluationError("--from-date must not exceed --to-date")
    if min(args.max_workers, args.batch_size, args.timeout, args.max_retries) <= 0:
        raise core.EvaluationError("Workers, batch size, timeout and retries must be positive")
    expected = {str(args.from_date + timedelta(days=i)) for i in range((args.to_date - args.from_date).days + 1)}
    runtime = core.load_runtime_config(args.config.resolve(), args.base_url, args.model)
    seeds = seed_paths(args.person)
    plans = []
    for name in NAMES:
        folder = getattr(args, name + "_dir").resolve()
        source = folder / "daily_event.json"
        if not source.exists():
            source = folder / args.person / "daily_event.json"
        events = core.normalize_events(core.read_json(source), str(args.from_date), str(args.to_date))
        counts = Counter(e["date"] for e in events)
        missing = sorted(expected - counts.keys())
        if missing:
            raise core.EvaluationError(f"{name}: missing calendar dates {missing}; all inputs checked before API calls")
        if source == args.output_dir.resolve() or source in args.output_dir.resolve().parents:
            raise core.EvaluationError("Output cannot replace an input file")
        target = args.output_dir.resolve() / name
        cache_path = target / "interest_drift.cache.json"
        cached = {} if args.refresh else core.load_cache(cache_path)
        seed_count = 0
        if not args.refresh:
            for path in seeds[name]:
                if not path.exists() or path.resolve() == cache_path:
                    continue
                candidates = core.load_cache(path)
                for event in events:
                    key, fingerprint = event["event_id"], core.event_fingerprint(event, runtime["model"])
                    if cached.get(key, {}).get("event_hash") == fingerprint:
                        continue
                    item = candidates.get(key)
                    if item and item.get("event_hash") == fingerprint:
                        cached[key] = item
                        seed_count += 1
        reusable = {e["event_id"]: cached[e["event_id"]] for e in events
                    if cached.get(e["event_id"], {}).get("event_hash") == core.event_fingerprint(e, runtime["model"])}
        missing_events = [e for e in events if e["event_id"] not in reusable]
        plans.append({"name": name, "source": source, "events": events, "daily_counts": dict(sorted(counts.items())),
                      "target": target, "cache_path": cache_path, "cached": reusable, "seeded": seed_count,
                      "estimated_batches": len(core.make_batches(missing_events, args.batch_size, 30000))})
    return plans, runtime


def run(args):
    plans, runtime = make_plan(args)
    days = (args.to_date - args.from_date).days + 1
    print(f"Window: {args.from_date} to {args.to_date}; {days} calendar days, {days - 1} possible adjacent pairs", flush=True)
    print(f"Model: {runtime['model']}; prompt: {core.PROMPT_VERSION}; groups run sequentially, workers per group: {args.max_workers}", flush=True)
    for p in plans:
        print(f"{p['name']}: events={len(p['events'])}, reusable={len(p['cached'])}, "
              f"seeded={p['seeded']}, estimated API batches={p['estimated_batches']}", flush=True)
    if args.dry_run:
        print("Dry run: all three inputs checked; no network calls, cache copies or output writes.", flush=True)
        return 0
    summary = {
        "status": "running", "from_date": str(args.from_date), "to_date": str(args.to_date),
        "calendar_days": days, "possible_adjacent_pairs": days - 1,
        "model": runtime["model"], "prompt_version": core.PROMPT_VERSION,
        "formula": "1 - Jaccard(adjacent daily interest-category sets)",
        "note": "One primary category per event. Higher drift is not a quality score. Cached extractions may originate from earlier runs with the same recorded model name.",
        "datasets": {}, "failures": [],
    }
    output = args.output_dir.resolve()
    core.write_json_atomic(output / "comparison.json", summary)
    for p in plans:
        name = p["name"]
        print(f"\n=== {name} ===", flush=True)
        try:
            if p["cached"]:
                core.save_cache(p["cache_path"], p["cached"])
            report_path = p["target"] / "interest_drift.json"
            argv = ["--input", str(p["source"]), "--output", str(report_path), "--cache", str(p["cache_path"]),
                    "--from-date", str(args.from_date), "--to-date", str(args.to_date),
                    "--config", str(args.config.resolve()), "--model", runtime["model"],
                    "--base-url", runtime["base_url"], "--max-workers", str(args.max_workers),
                    "--batch-size", str(args.batch_size), "--timeout", str(args.timeout),
                    "--max-retries", str(args.max_retries), "--verbose"]
            if args.refresh:
                argv.append("--refresh")
            if core.run(core.parse_args(argv)):
                raise core.EvaluationError(f"{name} evaluator returned an error")
            report = core.read_json(report_path)
            if (report["coverage"]["day_count"] != days or report["coverage"]["event_count"] != len(p["events"])
                    or report["provenance"]["evaluated_events_sha256"] != core.sha256_json(p["events"])):
                raise core.EvaluationError("Report coverage/source hash differs from selected input")
            summary["datasets"][name] = {
                "source": str(p["source"]), "source_file_sha256": hashlib.sha256(p["source"].read_bytes()).hexdigest(),
                "report_path": str(report_path), "cache_path": str(p["cache_path"]),
                "coverage": report["coverage"], "drift": report["interest_drift"]["summary"],
                "skipped_both_empty_pairs": report["interest_drift"]["skipped_both_empty_pairs"],
                "daily_mean_interest_categories": round(statistics.mean(len(d["interest_categories"]) for d in report["daily_profiles"]), 6),
                "category_counts": report["distributions"]["interest_categories"],
                "category_day_counts": {c: sum(c in d["interest_categories"] for d in report["daily_profiles"])
                                        for c in report["distributions"]["interest_categories"]},
                "keyword_normalization_audit": report.get("keyword_normalization_audit"),
                "cache_reused_at_start": len(p["cached"]), "cache_seeded_at_start": p["seeded"],
            }
        except Exception as exc:
            summary["failures"].append({"dataset": name, "error": f"{type(exc).__name__}: {exc}"})
            print(f"FAILED {name}: {exc}", file=sys.stderr, flush=True)
            # Stop expensive work on account/access errors; a rerun reuses completed batches.
            if any(s in str(exc) for s in ("HTTP 401", "HTTP 402", "HTTP 403", "API key is missing")):
                break
        finally:
            core.write_json_atomic(output / "comparison.json", summary)
    summary["status"] = "complete" if len(summary["datasets"]) == 3 and not summary["failures"] else "incomplete"
    core.write_json_atomic(output / "comparison.json", summary)
    lines = ["# 三组兴趣漂移比较", "", f"窗口：{args.from_date} 至 {args.to_date}，{days}天；状态：{summary['status']}。", "",
             f"模型：{runtime['model']}；提示词：{core.PROMPT_VERSION}。", "",
             "| 数据组 | 事件数 | 有效日对 | 平均漂移 | 中位数 | 标准差 | 每日兴趣类别数 |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for name, item in summary["datasets"].items():
        d = item["drift"]
        lines.append(f"| {name} | {item['coverage']['event_count']} | {d['count']} | {d['mean']} | {d['median']} | {d['stddev']} | {item['daily_mean_interest_categories']} |")
    lines += ["", "三组使用同一窗口、类别表和公式。当前每条事件抽取一个主要兴趣类别；兴趣漂移越大不等于数据质量越高。", "",
              "默认复用内容、模型名称、提示词版本和类别表哈希一致的缓存。若要求全部重新抽取，使用 --refresh。"]
    for failure in summary["failures"]:
        lines += ["", f"失败：{failure['dataset']}：{failure['error']}"]
    (output / "comparison.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n" + "\n".join(lines), flush=True)
    print(f"Output: {output}", flush=True)
    return 0 if summary["status"] == "complete" else 1


def main(argv=None):
    try:
        return run(build_parser().parse_args(argv))
    except (core.EvaluationError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted; rerun the same command to reuse completed extraction caches.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
