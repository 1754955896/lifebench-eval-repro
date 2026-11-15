"""旧时空逻辑校验（Legacy）统一入口与 CLI。

以 ``logic_run_new.py`` 当前有效链路为基础，移除了顶层执行副作用；
地图能力经 ``tools.geo``、模型能力经 ``tools.llm``。本方法保留历史
统计公式与已知限制，不宣称修复既有算法口径。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from statistics import fmean

from tools.evaluation_common.config import resolve_method_options
from tools.evaluation_common.data_loader import discover_persons
from tools.evaluation_common.runner import (
    ensure_output_dir, write_metrics,
)

from evaluation.logic_consistency.code.combine import (
    DEFAULT_SCORES, SCORERS, combine, select_scores,
)

METHOD_NAME = "logic_consistency"
PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
PROMPT_DEFAULTS = {
    "verify_1": str(PROMPT_DIR / "verify_1.txt"),
    "verify_2": str(PROMPT_DIR / "verify_2.txt"),
    "verify_3": str(PROMPT_DIR / "verify_3.txt"),
    "events_extraction": str(PROMPT_DIR / "events.txt"),
    "locations_extraction": str(PROMPT_DIR / "locations.txt"),
    "people_extraction": str(PROMPT_DIR / "people.txt"),
    "event_check": str(PROMPT_DIR / "check.txt"),
}

DEFAULT_OPTIONS = {
    "model": "deepseek-chat",
    "num_workers": 10,
    "start_date": None,
    "end_date": None,
    "timeout_seconds": 3600,
    "dry_run": False,
    "scoring_methods": DEFAULT_SCORES,
    **PROMPT_DEFAULTS,
}

REQUIRED_FILES = ("daily_event.json", "persona.json")


def _run_person(name, person_dir, person_out, opts, client, geo_client,
                relation_client):
    from evaluation.logic_consistency.code.relation_update.score import run as run_relation_update

    legacy_selected = any(method != "relation_update"
                          for method in opts["scoring_methods"])
    if legacy_selected:
        from evaluation.logic_consistency.code.runner import run_logic_check
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
    else:
        metrics = {"method": METHOD_NAME, "dry_run": bool(opts.get("dry_run"))}
    metrics.setdefault("person", name)
    if metrics.get("dry_run"):
        status = "dry_run"
    else:
        status = metrics.get("status", "complete")
    metrics["status"] = status
    relation_result = None
    if "relation_update" in opts["scoring_methods"] and status in ("complete", "dry_run"):
        relation_dir = os.path.join(person_out, "relation_update")
        ensure_output_dir(relation_dir)
        relation_result = run_relation_update(
            name, person_dir, relation_dir, opts, relation_client)
        metrics["relation_update"] = {
            "status": relation_result["status"],
            "output_dir": relation_dir,
            "metrics": relation_result["metrics"],
        }
        if relation_result["status"] not in ("complete", "dry_run"):
            status = "incomplete"
            metrics["status"] = status
    if status == "dry_run":
        metrics["scoring"] = {"selected_methods": list(opts["scoring_methods"]),
                              "method_scores": {}, "missing_methods": [],
                              "final_score": None}
    else:
        metrics["scoring"] = combine(
            metrics.get("log_aggregation", {}).get("counts", {}),
            opts["scoring_methods"],
            {"relation_update": relation_result["score"]}
            if relation_result is not None else None,
        )
    if status == "complete" and metrics["scoring"]["missing_methods"]:
        status = "incomplete"
        metrics["status"] = status
    metrics_path = write_metrics(person_out, metrics)
    return {"status": status, "dry_run": status == "dry_run",
            "metrics": metrics, "metrics_path": metrics_path}


def evaluate(data_dir, output_dir, *, options=None, client=None,
             geo_client=None, relation_client=None, persons=None) -> dict:
    """评估单个人物或数据集目录下的旧时空逻辑。

    ``client`` 为旧 ``Deepseek.ask(prompt)`` 形状的假对象（测试注入）；
    ``geo_client`` 为提供 ``geocode``/``route`` 的假对象（测试注入）。
    """
    opts = resolve_method_options(METHOD_NAME, DEFAULT_OPTIONS, options)
    opts["scoring_methods"] = select_scores(opts.get("scoring_methods"))
    person_dirs = discover_persons(data_dir, REQUIRED_FILES)
    if persons:
        wanted = set(persons)
        person_dirs = [(n, p) for n, p in person_dirs if n in wanted]
    if "relation_update" in opts["scoring_methods"]:
        missing_trees = [name for name, path in person_dirs if not
                         os.path.isfile(os.path.join(path, "event_tree.json"))]
        if missing_trees:
            raise ValueError("relation_update 需要 event_tree.json：" +
                             ", ".join(missing_trees))
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
                                 client, geo_client, relation_client)
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
            "scoring": result["metrics"]["scoring"],
        })
        if result["status"] not in ("complete", "dry_run") or (
                result["status"] == "complete" and
                result["metrics"]["scoring"]["missing_methods"]):
            failures.append(name)

    status = "incomplete" if failures else "complete"
    if entries and all(e.get("status") == "dry_run" for e in entries):
        status = "dry_run"
    person_scores = [e.get("scoring", {}).get("final_score") for e in entries]
    final_score = (fmean(person_scores) if status == "complete" and
                   person_scores and all(score is not None for score in person_scores)
                   else None)
    root_metrics = {
        "method": METHOD_NAME,
        "status": status,
        "legacy": any(method != "relation_update"
                      for method in opts["scoring_methods"]),
        "selected_methods": list(opts["scoring_methods"]),
        "final_score": final_score,
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
        "selected_methods": list(opts["scoring_methods"]),
        "final_score": final_score,
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
    parser.add_argument("--scores", nargs="+", choices=tuple(SCORERS),
                        default=None, metavar="METHOD",
                        help="Scoring methods: event person location path relation_update (default: original four)")
    parser.add_argument("--relation-model", default=None)
    parser.add_argument("--relation-max-model-calls", type=int, default=None)
    parser.add_argument("--relation-max-retries", type=int, default=None)
    parser.add_argument("--relation-resume", action="store_true", default=None)
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
        "scoring_methods": args.scores,
        "relation_model": args.relation_model,
        "relation_max_model_calls": args.relation_max_model_calls,
        "relation_max_retries": args.relation_max_retries,
        "relation_resume": args.relation_resume,
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
    print(f"{METHOD_NAME}: {result['status']} final_score={result['final_score']} "
          f"-> {result['output_dir']}")
    return 0 if result["status"] in ("complete", "dry_run") else 1


if __name__ == "__main__":
    sys.exit(main())
