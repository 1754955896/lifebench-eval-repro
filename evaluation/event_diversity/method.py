"""This module runs the evaluation."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

import numpy as np
from scipy.stats import entropy

from tools.evaluation_common.config import resolve_method_options
from tools.evaluation_common.data_loader import discover_persons
from tools.evaluation_common.runner import ensure_output_dir, write_metrics

from evaluation.event_diversity.labels import (
    OFFICIAL_EVENT_TYPES, EventTypeError, normalize_event_type,
)

METHOD_NAME = "event_diversity"

DEFAULT_OPTIONS = {
    "dry_run": False,
}

REQUIRED_FILES = ("daily_event.json",)


def _type_entries(data):


    entries = []
    diagnostics = []
    for event_index, item in enumerate(data):
        if not isinstance(item, dict):
            diagnostics.append({
                "event_index": event_index,
                "raw": repr(item),
                "reason": "daily_event 顶层条目不是 JSON 对象",
            })
            continue
        if "type" not in item:
            diagnostics.append({
                "event_index": event_index,
                "raw": None,
                "reason": "缺少 type 字段",
            })
            continue
        entries.append({"event_index": event_index, "raw": item.get("type")})
    return entries, diagnostics


def analyze_records(data, source: str | None = None) -> dict:


    if not isinstance(data, list) or not data:
        raise ValueError("daily_event 必须是非空 JSON 数组")

    raw_entries, extraction_diagnostics = _type_entries(data)
    diagnostics = list(extraction_diagnostics)
    valid_entries = []
    for entry in raw_entries:
        try:
            normalized = normalize_event_type(entry["raw"])
        except (TypeError, EventTypeError) as exc:
            diagnostics.append({
                "event_index": entry["event_index"],
                "raw": entry["raw"],
                "reason": str(exc),
            })
            continue
        valid_entries.append({**entry, "normalized": normalized})
    if not valid_entries:
        raise ValueError(
            "未找到任何可归一到官方九类的 type；输入诊断: "
            + json.dumps(diagnostics[:20], ensure_ascii=False))

    normalized = [entry["normalized"] for entry in valid_entries]
    counter = Counter(normalized)
    counts = np.array([counter[name] for name in counter], dtype=float)
    total = counts.sum()
    probs = counts / total
    K = len(counts)
    H = entropy(probs)
    H_norm = H / np.log(K) if K > 1 else 0.0
    simpson = 1.0 - np.sum(probs ** 2)
    K_eff = np.exp(H)

    raw_counter = Counter(entry["raw"] for entry in raw_entries
                          if isinstance(entry["raw"], str))
    variant_entries = [
        {"event_index": entry["event_index"], "raw": entry["raw"],
         "normalized": entry["normalized"]}
        for entry in valid_entries
        if entry["raw"] != entry["normalized"]
    ]
    variant_labels = sorted({entry["raw"] for entry in variant_entries})
    invalid_type_count = len(diagnostics) - len(extraction_diagnostics)

    result = {
        "method": METHOD_NAME,
        "status": "incomplete" if diagnostics else "complete",
        "source": source,
        "total_entries": len(data),
        "typed_entries": len(raw_entries),
        "total_events": int(total),
        "num_types": K,
        "normalized_entropy": round(float(H_norm), 4),
        "simpson_index": round(float(simpson), 4),
        "effective_num_types": round(float(K_eff), 2),
        "type_distribution": {
            name: {
                "count": counter[name],
                "ratio": round(counter[name] / total, 4),
            }
            for name in counter
        },
        "official_event_types": list(OFFICIAL_EVENT_TYPES),
        "raw_type_distribution": {
            raw: count for raw, count in sorted(raw_counter.items())
        },
        "normalized_variant_events": len(variant_entries),
        "normalized_variant_labels": variant_labels,
        "variant_events": variant_entries,
        "invalid_type_event_count": invalid_type_count,
        "input_diagnostics": diagnostics,
    }
    return result


def evaluate_file(file_path, output_dir=None, *, options=None) -> dict:

    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    result = analyze_records(data, source=os.path.abspath(file_path))
    if output_dir:
        ensure_output_dir(output_dir)
        result["metrics_path"] = write_metrics(output_dir, result)
    return result


def evaluate(data_dir, output_dir, *, options=None, client=None,
             persons=None) -> dict:
    opts = resolve_method_options(METHOD_NAME, DEFAULT_OPTIONS, options)
    dry_run = bool(opts.get("dry_run"))
    person_dirs = discover_persons(data_dir, REQUIRED_FILES)
    if persons:
        wanted = set(persons)
        person_dirs = [(n, p) for n, p in person_dirs if n in wanted]
    if not person_dirs:
        raise ValueError(f"{data_dir} 下未发现 daily_event.json")
    ensure_output_dir(output_dir)

    entries = {}
    failures = []
    totals = {
        "total_entries": 0,
        "typed_entries": 0,
        "total_events": 0,
        "normalized_variant_events": 0,
        "invalid_type_event_count": 0,
    }
    type_counts = Counter()
    for name, person_dir in person_dirs:
        person_out = os.path.join(output_dir, name)
        ensure_output_dir(person_out)
        file_path = os.path.join(person_dir, "daily_event.json")
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            metrics = analyze_records(data, source=os.path.abspath(file_path))
            metrics["person"] = name
            status = metrics.get("status", "complete")
            if dry_run and status == "complete":
                status = "dry_run"
            metrics["status"] = status
            for key in totals:
                value = metrics.get(key)
                if isinstance(value, int):
                    totals[key] += value
            for type_name, info in metrics["type_distribution"].items():
                type_counts[type_name] += info["count"]
        except Exception as exc:
            metrics = {
                "method": METHOD_NAME,
                "status": "error",
                "person": name,
                "error": f"{type(exc).__name__}: {exc}",
            }
            failures.append(name)
            status = "error"
        metrics_path = write_metrics(person_out, metrics)
        entries[name] = {
            "status": status,
            "metrics_path": metrics_path,
            "total_events": metrics.get("total_events"),
            "num_types": metrics.get("num_types"),
            "normalized_variant_events":
                metrics.get("normalized_variant_events"),
            "error": metrics.get("error"),
        }

    person_statuses = [entry["status"] for entry in entries.values()]
    if failures or "incomplete" in person_statuses:
        status = "incomplete"
    elif dry_run:
        status = "dry_run"
    else:
        status = "complete"
    root_metrics = {
        "method": METHOD_NAME,
        "status": status,
        "persons": entries,
        "aggregate_type_counts": {
            name: type_counts.get(name, 0) for name in OFFICIAL_EVENT_TYPES
        },
        "counts": totals,
        "notes": ("逐人 metrics 保留熵/Simpson/有效类型数；根目录只汇总"
                  "计数，不平均各人物比例"),
    }
    metrics_path = write_metrics(output_dir, root_metrics)
    return {
        "method": METHOD_NAME,
        "status": status,
        "dry_run": dry_run,
        "persons": entries,
        "failed_persons": failures,
        "metrics_path": metrics_path,
        "output_dir": os.path.abspath(output_dir),
    }


def _build_parser():
    parser = argparse.ArgumentParser(
        description="事件类型多样性评估（官方九类归一化后计算）")
    parser.add_argument("--data-dir", default=None,
                        help="单人物目录或包含人物子目录的数据集目录")
    parser.add_argument("--file-path", default=None,
                        help="直接分析单个 daily_event.json（兼容原入口）")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--dry-run", action="store_true", default=None)
    return parser


def main(argv=None):
    args = _build_parser().parse_args(argv)
    try:
        if args.file_path:
            result = evaluate_file(args.file_path, args.output_dir)
            print(f"{METHOD_NAME}: {result['status']} -> {args.output_dir}")
            print(f"事件总数: {result['total_events']}")
            print(f"事件类型数: {result['num_types']}")
            print(f"归一化 Shannon 熵: {result['normalized_entropy']}")
            print(f"Simpson 指数: {result['simpson_index']}")
            print(f"有效事件类型数: {result['effective_num_types']}")
            if result["status"] != "complete":
                for diag in result.get("input_diagnostics", [])[:20]:
                    print(f"输入诊断: event_index={diag['event_index']} "
                          f"raw={diag['raw']!r} reason={diag['reason']}",
                          file=sys.stderr)
                return 1
            return 0
        if not args.data_dir:
            print("错误: 必须提供 --data-dir 或 --file-path", file=sys.stderr)
            return 2
        result = evaluate(args.data_dir, args.output_dir,
                          options={"dry_run": args.dry_run})
        print(f"{METHOD_NAME}: {result['status']} -> {result['output_dir']}")
        return 0 if result["status"] in ("complete", "dry_run") else 1
    except Exception as exc:
        print(f"错误: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
