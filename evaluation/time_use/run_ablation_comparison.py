"""This module runs a time use comparison."""

from __future__ import annotations

import argparse
import copy
from datetime import date
import hashlib
import json
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from evaluation.time_use.engine import (CATEGORIES, PROMPT_VERSION, prepare_days, aggregate_days,
                                        validate_allocations, summarize_day)
from evaluation.time_use.method import evaluate
from tools.evaluation_common.storage import write_json

NAMES = ("original", "no_planning", "simulation_baseline")
LABELS = dict(zip(NAMES, ("原始版", "去规划版", "模拟基线")))
ACCOUNTING = ("recorded_minutes", "inferred_sleep_minutes", "ambiguous_minutes", "unobserved_minutes")


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original-dir", type=Path, default=REPO / "data/lifebench_10people/fenghaoran")
    parser.add_argument("--no-planning-dir", type=Path, default=REPO / "data/ablation_fenghaoran_2025/no_planning/fenghaoran")
    parser.add_argument("--simulation-baseline-dir", type=Path, default=REPO / "data/ablation_fenghaoran_2025/simulation_baseline/fenghaoran")
    parser.add_argument("--person", default="fenghaoran")
    parser.add_argument("--start-date", "--from-date", type=date.fromisoformat, default=date(2025, 1, 1))
    parser.add_argument("--end-date", "--to-date", type=date.fromisoformat, default=date(2025, 12, 31))
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--sleep-policy", choices=("none", "night-gap"), default="night-gap")
    parser.add_argument("--reuse-results-dir", type=Path,
                        help="Add inferred sleep to an existing none-policy comparison locally, without LLM requests")
    parser.add_argument("--model")
    parser.add_argument("--max-workers", type=int, default=8)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--max-output-tokens", type=int, default=8192)
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--dry-run", action="store_true", help="Check all inputs without API calls or output writes")
    return parser


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def make_plan(args):
    if args.start_date > args.end_date:
        raise ValueError("start-date must not exceed end-date")
    if min(args.max_workers, args.attempts, args.timeout, args.max_output_tokens) <= 0:
        raise ValueError("Workers, attempts, timeout and output tokens must be positive")
    plans = {}

    for name in NAMES:
        folder = getattr(args, name + "_dir").resolve()
        if not (folder / "daily_event.json").is_file():
            folder = folder / args.person
        events = read_json(folder / "daily_event.json")
        if not isinstance(events, list):
            raise ValueError(f"{name}: daily_event.json must be an array")
        persona = read_json(folder / "persona.json") if (folder / "persona.json").exists() else {}
        if not isinstance(persona, dict):
            raise ValueError(f"{name}: persona.json must be an object")
        days, diagnostics = prepare_days(events, start_date=str(args.start_date),
                                         end_date=str(args.end_date), sleep_policy=args.sleep_policy)
        plans[name] = {
            "folder": str(folder), "requested_dates": list(days), "requested_days": len(days),
            "days_requiring_llm": sum(any(s["source"] == "recorded" for s in d["segments"]) for d in days.values()),
            "empty_days": [day for day, d in days.items() if not d["segments"]],
            "invalid_interval_count": len(diagnostics), "input_diagnostics": diagnostics,
            "accounting_on_requested_days": {k: round(sum(d[k] for d in days.values()), 6) for k in ACCOUNTING},
        }
    return plans


def build_comparison(plans, groups, daily, args):
    common = sorted(set.intersection(*(set(daily[n]) for n in NAMES)))
    requested = plans[NAMES[0]]["requested_days"]
    comparison = {"start_date": str(args.start_date), "end_date": str(args.end_date),
                  "sleep_policy": args.sleep_policy, "requested_days": requested,
                  "common_successful_dates": common, "common_successful_days": len(common),
                  "processing_complete": all(len(daily[n]) == requested for n in NAMES),
                  "groups": {},
                  "notes": "Comparison means use identical successful dates across all three groups, including category zeros. Unknown and overlapping time are not allocated. Inferred sleep is a hypothesis. Category shares use allocated time, not 24 hours."}
    for name in NAMES:
        metrics = groups[name]
        aggregate = aggregate_days({d: daily[name][d] for d in common}, requested_days=requested)
        comparison["groups"][name] = {
            "source": plans[name]["folder"], "status": metrics.get("status"),
            "analyzed_days": len(daily[name]), "failed_days": metrics.get("failed_days", []),
            "error": metrics.get("error"), "input_plan": plans[name],
            "model": metrics.get("model"), "prompt_version": metrics.get("prompt_version"),
            "all_successful_days": aggregate_days(daily[name], requested_days=requested),
            "common_days": aggregate,
            "common_daily_accounting_minutes": {
                k: round(aggregate["totals_on_analyzed_days"][k] / len(common), 4) if common else None
                for k in ACCOUNTING},
        }
    return comparison


def markdown(report):
    lines = ["# 三组数据的时长分配对比", "",
             f"日期：{report['start_date']} 至 {report['end_date']}；睡眠策略：`{report['sleep_policy']}`。",
             f"请求 {report['requested_days']} 天；三组共同成功分析 {report['common_successful_days']} 天。", "",
             "日均时长单位为小时，分母为三组共同成功日期数，包括某类别为0的日期。", "",
             "| 活动领域 | 原始版 | 去规划版 | 模拟基线 |", "|---|---:|---:|---:|"]
    def fmt(value):
        return "—" if value is None else f"{value:.3f}"
    for category in CATEGORIES:
        values = [report["groups"][n]["common_days"]["category_distribution"][category]["daily_average_hours"] for n in NAMES]
        lines.append(f"| {category} | " + " | ".join(map(fmt, values)) + " |")
    lines += ["", "以下为共同成功日期的日均核算（小时）；推算睡眠已包含在生理必需活动中，不重复相加。", "",
              "| 核算项 | 原始版 | 去规划版 | 模拟基线 |", "|---|---:|---:|---:|"]
    for key, label in zip(ACCOUNTING, ("明确记录且无重叠", "推算睡眠（已计入六类）", "重叠歧义（未分配）", "未记录（未分配）")):
        values = [report["groups"][n]["common_daily_accounting_minutes"][key] for n in NAMES]
        lines.append(f"| {label} | " + " | ".join(fmt(v / 60 if v is not None else None) for v in values) + " |")
    lines += ["", "## 处理与输入诊断", ""]
    for name in NAMES:
        group = report["groups"][name]
        plan = group["input_plan"]
        lines.append(f"- {LABELS[name]}：成功 {group['analyzed_days']}/{report['requested_days']} 天，状态 `{group['status']}`；无效区间 {plan['invalid_interval_count']} 条。")
        if group["error"]:
            lines.append(f"  错误：{group['error']}")
    lines += ["", "六类时长与歧义、未知时间合计为24小时。推算睡眠是分析假设。存在输入重叠或无效区间时，即使每天都已处理，单组状态仍可能为 incomplete；失败日期不会当作0计入均值。", "",
              "完整 JSON 另含六类参与率、参与日平均时长、日间方差，以及各组自身成功日期的汇总。", ""]
    return "\n".join(lines)


def add_sleep_locally(args, plans, output):

    source = args.reuse_results_dir.resolve()
    if args.sleep_policy != "night-gap":
        raise ValueError("--reuse-results-dir requires --sleep-policy night-gap")
    if source == output or source in output.parents or output in source.parents:
        raise ValueError("Source and destination directories must be separate")
    report = read_json(source / "comparison.json")
    if (report["sleep_policy"] != "none" or report["start_date"] != str(args.start_date)
            or report["end_date"] != str(args.end_date)):
        raise ValueError("Source must use none policy and the same date range")
    groups, daily, destinations = {}, {}, {}
    def segment_key(segment):
        return json.dumps({k: v for k, v in segment.items() if k != "segment_id"}, sort_keys=True, ensure_ascii=False)

    for name in NAMES:
        folder = Path(plans[name]["folder"])
        old_person = source / name / folder.name
        metrics = read_json(old_person / "metrics.json")
        if (metrics.get("source_sha256") != hashlib.sha256((folder / "daily_event.json").read_bytes()).hexdigest()
                or metrics.get("prompt_version") != PROMPT_VERSION
                or metrics.get("options", {}).get("sleep_policy") != "none"
                or metrics.get("requested_dates") != plans[name]["requested_dates"]):
            raise ValueError(f"{name}: source events, prompt, policy or dates do not match")
        if args.model and args.model != metrics.get("model", {}).get("model"):
            raise ValueError("Offline reuse preserves the source model; cannot change --model")
        persona = read_json(folder / "persona.json") if (folder / "persona.json").exists() else {}
        persona_hash = hashlib.sha256(json.dumps(persona, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        if metrics.get("source_persona_sha256") != persona_hash:
            raise ValueError(f"{name}: persona changed")
        days, _ = prepare_days(read_json(folder / "daily_event.json"), start_date=str(args.start_date),
                               end_date=str(args.end_date), sleep_policy="night-gap")
        old_days = read_json(old_person / "daily_results.json")
        daily[name] = {}
        for day, previous in old_days.items():
            if day not in days:
                raise ValueError(f"{name}: unexpected cached date {day}")
            validated = validate_allocations({"segments": previous["allocations"]}, previous["segments"])
            by_id = {row["segment_id"]: row for row in validated}
            by_content = {segment_key(s): by_id[s["segment_id"]] for s in previous["segments"]}
            recorded = [s for s in days[day]["segments"] if s["source"] == "recorded"]
            if (len(by_content) != len(previous["segments"]) or
                    set(by_content) != {segment_key(s) for s in recorded}):
                raise ValueError(f"{name} {day}: recorded segments changed")
            allocations = []
            for segment in days[day]["segments"]:
                if segment["source"] == "recorded":
                    allocation = copy.deepcopy(by_content[segment_key(segment)])
                    allocation["segment_id"] = segment["segment_id"]
                else:
                    allocation = {"segment_id": segment["segment_id"], "minutes": {
                        c: segment["duration_minutes"] if c == CATEGORIES[0] else 0 for c in CATEGORIES}}
                allocations.append(allocation)
            validated = validate_allocations({"segments": allocations}, days[day]["segments"])
            daily[name][day] = summarize_day(days[day], validated)
            if abs(sum(daily[name][day]["categories"][c]["total_minutes"] for c in CATEGORIES)
                   + days[day]["ambiguous_minutes"] + days[day]["unobserved_minutes"] - 1440) > .0001:
                raise ValueError(f"{name} {day}: accounting does not sum to 1440 minutes")
        groups[name] = {**metrics, **aggregate_days(daily[name], requested_days=len(days)),
                        "options": {**metrics["options"], "sleep_policy": "night-gap"},
                        "postprocessing": {"source_directory": str(old_person), "llm_calls": 0,
                                           "operation": "Preserve recorded allocations; add night-gap sleep locally"},
                        "accounting_on_requested_days": plans[name]["accounting_on_requested_days"]}
        destinations[name] = output / name / folder.name
    identities = [groups[n]["model"] for n in NAMES]
    if any(identity != identities[0] for identity in identities[1:]):
        raise ValueError("Source groups use different model identities")
    for name in NAMES:
        write_json(str(destinations[name] / "daily_results.json"), daily[name])
        write_json(str(destinations[name] / "metrics.json"), groups[name])
        write_json(str(destinations[name] / "failures.json"), groups[name].get("failed_days", []))
    return groups, daily


def run(args, *, client=None):
    plans = make_plan(args)
    for name, plan in plans.items():
        print(f"{name}: days={plan['requested_days']}, LLM days<={plan['days_requiring_llm']}, "
              f"invalid intervals={plan['invalid_interval_count']}, empty days={len(plan['empty_days'])}", flush=True)
        print("  accounting (minutes): " + json.dumps(plan["accounting_on_requested_days"]), flush=True)
    if args.dry_run:
        return {"status": "dry_run", "plans": plans}
    output = args.output_dir or REPO / f"output/time_use/ablation_comparison_{args.start_date:%Y%m%d}_{args.end_date:%Y%m%d}_{args.sleep_policy}"
    output = output.resolve()
    if args.reuse_results_dir:
        groups, daily = add_sleep_locally(args, plans, output)
        report = build_comparison(plans, groups, daily, args)
        report["postprocessing"] = {"source_directory": str(args.reuse_results_dir.resolve()), "llm_calls": 0}
        write_json(str(output / "comparison.json"), report)
        (output / "comparison.md").write_text(markdown(report), encoding="utf-8")
        print(f"Sleep added locally, no LLM calls. Report: {output / 'comparison.md'}", flush=True)
        return report
    options = {k: getattr(args, k) for k in ("sleep_policy", "model", "max_workers", "attempts", "timeout", "max_output_tokens", "resume")}
    options.update(start_date=str(args.start_date), end_date=str(args.end_date), dry_run=False)
    groups, daily = {}, {n: {} for n in NAMES}
    account_stopped = False
    for name in NAMES:
        if account_stopped:
            groups[name] = {"status": "not_started", "error": "Account/authentication failure stopped further groups"}
            continue
        try:
            root = evaluate(plans[name]["folder"], str(output / name), options=options, client=client)
            entry = next(iter(root["persons"].values()))
            metrics_path = Path(entry["metrics_path"])
            metrics = read_json(metrics_path)
            groups[name] = metrics

            if "category_distribution" in metrics:
                daily[name] = read_json(metrics_path.parent / "daily_results.json")
            errors = [metrics.get("error") or ""] + [f["error"] for f in metrics.get("failed_days", [])]
            account_stopped = any(marker in " ".join(errors).lower() for marker in (
                "402", "insufficient_balance", "insufficient_quota", "401", "authenticationerror"))
        except Exception as exc:
            groups[name] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
    report = build_comparison(plans, groups, daily, args)
    write_json(str(output / "comparison.json"), report)
    (output / "comparison.md").write_text(markdown(report), encoding="utf-8")
    print(f"Common successful days: {report['common_successful_days']}/{report['requested_days']}", flush=True)
    print(f"Report: {output / 'comparison.md'}", flush=True)
    return report


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        result = run(args)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr, flush=True)
        return 1
    return 0 if args.dry_run or result["processing_complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
