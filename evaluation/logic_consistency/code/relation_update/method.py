"""This module runs the evaluation."""

from __future__ import annotations

import argparse
import os
import sys

from tools.evaluation_common.config import resolve_method_options
from tools.evaluation_common.data_loader import discover_persons
from tools.evaluation_common.model_adapter import ModelClient
from tools.evaluation_common.runner import (
    ensure_output_dir, run_over_persons, write_metrics,
)
from tools.evaluation_common.storage import read_json

from evaluation.logic_consistency.code.relation_update.pipeline import (
    DEFAULT_OUTPUT_ROOT, PIPELINE_VERSION, evaluate_person_relations,
)
from evaluation.logic_consistency.code.relation_update.selector import SelectorPromptManager
from evaluation.logic_consistency.code.relation_update.status import run_complete as _run_complete

METHOD_NAME = "relation_update"
PROMPT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")

DEFAULT_OPTIONS = {
    "model": "deepseek-flash",
    "selector_prompt": os.path.join(PROMPT_DIR, "relation_selector_v1.txt"),
    "writer_prompt": os.path.join(PROMPT_DIR, "relation_writer_v1.txt"),
    "start_date": None,
    "end_date": None,
    "max_context_tokens": 24000,
    "max_output_tokens": 8192,
    "max_model_calls": None,
    "max_retries": 3,
    "resume": False,
    "dry_run": False,
}

REQUIRED_FILES = ("daily_event.json", "event_tree.json", "persona.json")


def _run_person(name, person_dir, person_out, opts, client, prompts):
    event_file = os.path.join(person_dir, "daily_event.json")
    outline_file = os.path.join(person_dir, "event_tree.json")
    persona_file = os.path.join(person_dir, "persona.json")
    run_opts = dict(opts)
    report = evaluate_person_relations(
        name, event_file, outline_file, persona_file, person_out,
        run_opts, client=client, prompts=prompts)
    signature = report.get("evaluation_signature")
    summary = read_json(os.path.join(person_out, "summary.json"))
    if report.get("dry_run"):
        status = "dry_run"
    elif report.get("budget_exhausted"):
        status = "incomplete"
    elif signature is not None and not _run_complete(person_out, signature):
        status = "incomplete"
    else:
        status = "complete"
    metrics = {
        "method": METHOD_NAME,
        "status": status,
        "pipeline_version": PIPELINE_VERSION,
        "person": report.get("person"),
        "evaluated_events": report.get("evaluated"),
        "dry_run": bool(report.get("dry_run")),
        "budget_exhausted": bool(report.get("budget_exhausted")),
    }
    if isinstance(summary, dict):
        metrics.update(summary)
        metrics["status"] = status
    if report.get("dry_run"):
        metrics.update(report)
    return {"status": status, "dry_run": bool(report.get("dry_run")),
            "metrics": metrics}


def evaluate(data_dir, output_dir, *, options=None, client=None,
             persons=None) -> dict:

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
    if not opts.get("dry_run") and client is None:
        client = ModelClient(model=opts["model"],
                             max_retries=int(opts["max_retries"]))
    prompts = SelectorPromptManager(opts["selector_prompt"],
                                    opts["writer_prompt"])

    batch = run_over_persons(
        person_dirs, output_dir,
        lambda name, pdir, out: _run_person(
            name, pdir, out, opts, client, prompts))

    per_person = {}
    for entry in batch["persons"]:
        metrics = entry.get("metrics") or {}
        per_person[entry.get("person")] = {
            key: metrics.get(key) for key in (
                "status", "evaluated_events", "relation_rate",
                "final_labels", "initial_relation_count",
                "final_relation_count", "budget_exhausted", "error")
        }
    metrics_path = write_metrics(output_dir, {
        "method": METHOD_NAME,
        "status": batch["status"],
        "pipeline_version": PIPELINE_VERSION,
        "model": opts.get("model"),
        "output_dir": os.path.abspath(output_dir),
        "persons": per_person,
        "notes": ("逐人输出标签计数与关系率；不跨人物合并比例，"
                  "也不定义统一通过阈值"),
    })
    return {
        "method": METHOD_NAME,
        "status": batch["status"],
        "dry_run": batch["status"] == "dry_run",
        "persons": batch["persons"],
        "failed_persons": batch["failed_persons"],
        "metrics_path": metrics_path,
        "output_dir": os.path.abspath(output_dir),
    }


def _build_parser():
    parser = argparse.ArgumentParser(
        description="人际关系合理性评估（动态关系上下文维护）")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--model", default=None)
    parser.add_argument("--selector-prompt", default=None)
    parser.add_argument("--writer-prompt", default=None)
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--max-context-tokens", type=int, default=None)
    parser.add_argument("--max-output-tokens", type=int, default=None)
    parser.add_argument("--max-model-calls", type=int, default=None)
    parser.add_argument("--max-retries", type=int, default=None)
    parser.add_argument("--persons", nargs="*", default=None)
    parser.add_argument("--resume", action="store_true", default=None)
    parser.add_argument("--dry-run", action="store_true", default=None)
    return parser


def main(argv=None):
    args = _build_parser().parse_args(argv)
    options = {
        "model": args.model,
        "selector_prompt": args.selector_prompt,
        "writer_prompt": args.writer_prompt,
        "start_date": args.start_date,
        "end_date": args.end_date,
        "max_context_tokens": args.max_context_tokens,
        "max_output_tokens": args.max_output_tokens,
        "max_model_calls": args.max_model_calls,
        "max_retries": args.max_retries,
        "resume": args.resume,
        "dry_run": args.dry_run,
    }
    try:
        result = evaluate(args.data_dir, args.output_dir,
                          options=options, persons=args.persons)
    except Exception as exc:
        print(f"错误: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    for entry in result["persons"]:
        if entry.get("status") == "error":
            print(f"[FAILED] {entry['person']}: {entry['error']}",
                  file=sys.stderr)
        elif entry.get("status") == "incomplete":
            print(f"[INCOMPLETE] {entry['person']} 存在未解决运行错误或预算"
                  "耗尽，请汇报后用 --resume 续跑", file=sys.stderr)
    print(f"{METHOD_NAME}: {result['status']} -> {result['output_dir']}")
    return 0 if result["status"] in ("complete", "dry_run") else 1


if __name__ == "__main__":
    sys.exit(main())
