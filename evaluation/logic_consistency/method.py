"""旧时空逻辑校验（Legacy）统一入口与 CLI。

以 ``logic_run_new.py`` 当前有效链路为基础，移除了顶层执行副作用；
地图能力经 ``tools.geo``、模型能力经 ``tools.llm``。本方法保留历史
统计公式与已知限制，不宣称修复既有算法口径。
"""
from __future__ import annotations

import argparse
import os
import sys

from tools.evaluation_common.config import resolve_method_options
from tools.evaluation_common.data_loader import discover_persons
from tools.evaluation_common.runner import (
    ensure_output_dir, write_metrics,
)

from evaluation.logic_consistency.runner import (
    PROMPT_DEFAULTS, run_logic_check,
)

METHOD_NAME = "logic_consistency"

DEFAULT_OPTIONS = {
    "model": "deepseek-chat",
    "num_workers": 10,
    "start_date": None,
    "end_date": None,
    "timeout_seconds": 3600,
    "dry_run": False,
    **PROMPT_DEFAULTS,
}

REQUIRED_FILES = ("daily_event.json", "persona.json")


def _run_person(name, person_dir, person_out, opts, client, geo_client):
    event_file = os.path.join(person_dir, "daily_event.json")
    person_file = os.path.join(person_dir, "persona.json")
    options = {k: opts.get(k) for k in (
        "model", "num_workers", "timeout_seconds",
        "verify_1", "verify_2", "verify_3", "events_extraction",
        "locations_extraction", "people_extraction", "event_check")}
    metrics = run_logic_check(
        person_file, event_file, person_out,
        start_date=opts.get("start_date"), end_date=opts.get("end_date"),
        options=options, llm=client, geo_client=geo_client,
        dry_run=bool(opts.get("dry_run")))
    metrics.setdefault("person", name)
    if metrics.get("dry_run"):
        status = "dry_run"
    else:
        status = metrics.get("status", "complete")
    metrics["status"] = status
    metrics_path = write_metrics(person_out, metrics)
    return {"status": status, "dry_run": status == "dry_run",
            "metrics": metrics, "metrics_path": metrics_path}


def evaluate(data_dir, output_dir, *, options=None, client=None,
             geo_client=None, persons=None) -> dict:
    """评估单个人物或数据集目录下的旧时空逻辑。

    ``client`` 为旧 ``Deepseek.ask(prompt)`` 形状的假对象（测试注入）；
    ``geo_client`` 为提供 ``geocode``/``route`` 的假对象（测试注入）。
    """
    opts = resolve_method_options(METHOD_NAME, DEFAULT_OPTIONS, options)
    person_dirs = discover_persons(data_dir, REQUIRED_FILES)
    if persons:
        wanted = set(persons)
        person_dirs = [(n, p) for n, p in person_dirs if n in wanted]
    if not person_dirs:
        raise ValueError(
            f"{data_dir} 下未发现同时包含 "
            f"{', '.join(REQUIRED_FILES)} 的人物目录")
    ensure_output_dir(output_dir)

    entries = []
    failures = []
    for name, person_dir in person_dirs:
        person_out = os.path.join(output_dir, name)
        ensure_output_dir(person_out)
        try:
            result = _run_person(name, person_dir, person_out, opts,
                                 client, geo_client)
        except Exception as exc:
            entries.append({
                "person": name,
                "output_dir": person_out,
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
            })
            failures.append(name)
            continue
        entries.append({
            "person": name,
            "output_dir": person_out,
            "status": result["status"],
            "dry_run": result["dry_run"],
            "metrics_path": result["metrics_path"],
        })
        if result["status"] not in ("complete", "dry_run"):
            failures.append(name)

    status = "incomplete" if failures else "complete"
    if entries and all(e.get("status") == "dry_run" for e in entries):
        status = "dry_run"
    root_metrics = {
        "method": METHOD_NAME,
        "status": status,
        "legacy": True,
        "persons": entries,
        "failed_persons": failures,
        "notes": ("旧时空校验为 legacy 兼容迁移；逐人 metrics 保留原"
                  "离散标签与日志聚合，未修正历史口径"),
    }
    metrics_path = write_metrics(output_dir, root_metrics)
    return {
        "method": METHOD_NAME,
        "status": status,
        "dry_run": status == "dry_run",
        "persons": entries,
        "failed_persons": failures,
        "metrics_path": metrics_path,
        "output_dir": os.path.abspath(output_dir),
    }


def _build_parser():
    parser = argparse.ArgumentParser(
        description="旧时空逻辑校验（Legacy；保留历史统计公式与限制）")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--num-workers", type=int, default=None)
    parser.add_argument("--timeout-seconds", type=float, default=None)
    parser.add_argument("--verify-1", default=None)
    parser.add_argument("--verify-2", default=None)
    parser.add_argument("--verify-3", default=None)
    parser.add_argument("--events-extraction", default=None)
    parser.add_argument("--locations-extraction", default=None)
    parser.add_argument("--people-extraction", default=None)
    parser.add_argument("--event-check", default=None)
    parser.add_argument("--persons", nargs="*", default=None)
    parser.add_argument("--dry-run", action="store_true", default=None)
    return parser


def main(argv=None):
    args = _build_parser().parse_args(argv)
    option_map = {
        "model": args.model,
        "num_workers": args.num_workers,
        "start_date": args.start_date,
        "end_date": args.end_date,
        "timeout_seconds": args.timeout_seconds,
        "verify_1": args.verify_1,
        "verify_2": args.verify_2,
        "verify_3": args.verify_3,
        "events_extraction": args.events_extraction,
        "locations_extraction": args.locations_extraction,
        "people_extraction": args.people_extraction,
        "event_check": args.event_check,
        "dry_run": args.dry_run,
    }
    try:
        result = evaluate(args.data_dir, args.output_dir,
                          options=option_map, persons=args.persons)
    except Exception as exc:
        print(f"错误: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    for entry in result["persons"]:
        if entry.get("status") == "error":
            print(f"[FAILED] {entry['person']}: {entry['error']}",
                  file=sys.stderr)
    print(f"{METHOD_NAME}: {result['status']} -> {result['output_dir']}")
    return 0 if result["status"] in ("complete", "dry_run") else 1


if __name__ == "__main__":
    sys.exit(main())
