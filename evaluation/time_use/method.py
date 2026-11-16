"""This module runs the evaluation."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import date
import hashlib
import json
from pathlib import Path
import sys
import threading
import time

from tools.evaluation_common.config import resolve_method_options
from tools.evaluation_common.data_loader import discover_persons
from tools.evaluation_common.model_adapter import parse_model_json
from tools.evaluation_common.storage import write_json
from tools.evaluation_common.runner import write_metrics
from .engine import (PROMPT_VERSION, RULES, SYSTEM_PROMPT, prepare_days, build_messages,
                     validate_allocations, summarize_day, aggregate_days)

METHOD_NAME = "time_use"
DEFAULT_OPTIONS = {"dry_run": False, "resume": True, "model": None, "temperature": 0.0,
                   "max_workers": 8, "attempts": 3, "timeout": 180.0, "max_output_tokens": 8192,
                   "sleep_policy": "none", "start_date": None, "end_date": None}


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


class SharedClient:
    def __init__(self, options):
        self.options = options

    def chat(self, messages, max_tokens):
        from tools.llm import chat_completion
        response = chat_completion(messages, model=self.options["model"],
                                   temperature=self.options["temperature"], max_tokens=max_tokens,
                                   response_format={"type": "json_object"},
                                   timeout=self.options["timeout"], sdk_max_retries=0)
        return {"content": response.content, "finish_reason": response.finish_reason,
                "model_returned": response.model_returned, "usage": response.usage}


def _analyze_day(prepared, persona, directory, opts, client, identity, stop):
    day = prepared["date"]
    if not prepared["segments"]:
        raise ValueError("No unambiguous time to classify; unknown days are not zero-activity observations")
    messages = build_messages(prepared, persona)
    signature = _hash({"messages": messages, "prepared": prepared, "identity": identity,
                       "sleep_policy": opts["sleep_policy"], "version": PROMPT_VERSION})
    checkpoint = directory / "days" / f"{day}.json"
    if opts["resume"] and checkpoint.exists():
        saved = _read(checkpoint)
        if saved.get("signature") != signature:
            raise ValueError("Checkpoint inputs/model changed: use another output directory or --no-resume")

        allocations = validate_allocations({"segments": saved["result"]["allocations"]}, prepared["segments"])
        return summarize_day(prepared, allocations), True

    inferred = [s for s in prepared["segments"] if s["source"] == "inferred_sleep"]
    from .engine import CATEGORIES
    fixed = [{"segment_id": s["segment_id"], "minutes": {
        c: s["duration_minutes"] if c == CATEGORIES[0] else 0 for c in CATEGORIES}} for s in inferred]
    recorded = {**prepared, "segments": [s for s in prepared["segments"] if s["source"] == "recorded"]}
    for attempt in range(1, opts["attempts"] + 1):
        if stop.is_set():
            raise RuntimeError("Not attempted: account/authentication failure stopped further requests")
        try:
            if recorded["segments"]:
                response = client.chat(build_messages(recorded, persona), opts["max_output_tokens"])
                write_json(str(directory / "responses" / f"{day}.attempt{attempt}.json"), response)
                if response.get("finish_reason") == "length":
                    raise ValueError("Model response truncated; increase max_output_tokens")
                parsed = parse_model_json(response["content"])
                allocations = validate_allocations(parsed, recorded["segments"])
            else:
                allocations = []
            allocations = validate_allocations({"segments": allocations + fixed}, prepared["segments"])
            result = summarize_day(prepared, allocations)
            write_json(str(checkpoint), {"signature": signature, "result": result})
            return result, False
        except Exception as exc:
            text = str(exc).lower()
            if any(marker in text for marker in ("402", "insufficient_balance", "insufficient_quota", "401", "authenticationerror")):
                stop.set()
                raise
            if attempt == opts["attempts"]:
                raise
            print(f"  {day}: retry {attempt}/{opts['attempts']} ({type(exc).__name__})", flush=True)
            time.sleep(min(attempt, 3))


def _run_person(name, folder, destination, opts, client, identity):
    events = _read(folder / "daily_event.json")
    persona = _read(folder / "persona.json") if (folder / "persona.json").exists() else {}
    if not isinstance(persona, dict):
        raise ValueError("persona.json must be an object")
    days, diagnostics = prepare_days(events, start_date=opts["start_date"], end_date=opts["end_date"],
                                     sleep_policy=opts["sleep_policy"])
    source = str((folder / "daily_event.json").resolve())
    source_hash = hashlib.sha256((folder / "daily_event.json").read_bytes()).hexdigest()
    manifest = {"method": METHOD_NAME, "person": name, "source": source, "source_sha256": source_hash,
                "source_persona_sha256": _hash(persona), "prompt_version": PROMPT_VERSION,
                "prompt_sha256": _hash([SYSTEM_PROMPT, RULES]), "model": identity,
                "options": opts, "requested_dates": list(days), "input_diagnostics": diagnostics}
    destination.mkdir(parents=True, exist_ok=True)
    if opts["dry_run"]:
        write_json(str(destination / "plan.json"), manifest)
        first = next((d for d in days.values() if d["segments"]), next(iter(days.values())))
        write_json(str(destination / "prompt_preview.json"), build_messages(first, persona))
        metrics = {**manifest, "status": "dry_run", "requested_days": len(days),
                   "days_requiring_llm": sum(any(s["source"] == "recorded" for s in d["segments"]) for d in days.values()),
                   "accounting": {k: round(sum(d[k] for d in days.values()), 6) for k in (
                       "recorded_minutes", "inferred_sleep_minutes", "ambiguous_minutes", "unobserved_minutes")}}
        print(f"{name}: dry-run, {len(days)} days, {len(diagnostics)} invalid interval diagnostics", flush=True)
        return metrics
    previous_path = destination / "manifest.json"
    run_signature = _hash({k: manifest[k] for k in ("source_sha256", "source_persona_sha256", "prompt_sha256", "model", "requested_dates")}
                          | {"sleep_policy": opts["sleep_policy"]})
    if opts["resume"] and previous_path.exists() and _read(previous_path).get("signature") != run_signature:
        raise ValueError("Existing run has different inputs/model/dates/policy; use another output directory or --no-resume")
    write_json(str(previous_path), {**manifest, "signature": run_signature, "status": "running"})
    write_json(str(destination / "input_accounting.json"), {
        day: {k: v for k, v in prepared.items() if k != "segments"} for day, prepared in days.items()})
    results, failures, resumed = {}, [], 0
    stop = threading.Event()
    print(f"{name}: {len(days)} days, workers={opts['max_workers']}, sleep={opts['sleep_policy']}, model={opts['model']}", flush=True)
    with ThreadPoolExecutor(max_workers=opts["max_workers"]) as pool:
        pending = {pool.submit(_analyze_day, prepared, persona, destination, opts, client, identity, stop): day
                   for day, prepared in days.items()}
        finished = 0
        while pending:
            done, _ = wait(pending, timeout=30, return_when=FIRST_COMPLETED)
            if not done:
                print(f"{name}: waiting for model; finished {finished}/{len(days)}, successful {len(results)}", flush=True)
            for future in done:
                day = pending.pop(future)
                finished += 1
                try:
                    result, cached = future.result()
                    results[day] = result
                    resumed += int(cached)
                    print(f"[{finished}/{len(days)}] {name} {day}: done" + (" [cached]" if cached else ""), flush=True)
                except Exception as exc:
                    failures.append({"date": day, "error": f"{type(exc).__name__}: {exc}"})
                    print(f"[{finished}/{len(days)}] {name} {day}: FAILED: {exc}", flush=True)
    results = dict(sorted(results.items()))
    metrics = {**manifest, **aggregate_days(results, requested_days=len(days)),
               "status": "incomplete" if failures or diagnostics or any(d["ambiguous_minutes"] for d in days.values()) else "complete",
               "resumed_days": resumed, "failed_days": failures,
               "accounting_on_requested_days": {k: round(sum(d[k] for d in days.values()), 6) for k in (
                   "recorded_minutes", "inferred_sleep_minutes", "ambiguous_minutes", "unobserved_minutes")},
               "notes": "Category estimates cover non-overlapping recorded time plus explicitly enabled inferred sleep. Unobserved and ambiguous minutes are not allocated. Means use successful days including category zeros."}
    write_json(str(destination / "daily_results.json"), results)
    write_json(str(destination / "failures.json"), failures)
    write_json(str(previous_path), {**manifest, "signature": run_signature, "status": metrics["status"]})
    return metrics


def evaluate(data_dir, output_dir, *, options=None, client=None, persons=None):
    opts = resolve_method_options(METHOD_NAME, DEFAULT_OPTIONS, options)
    from tools.llm import DEFAULT_MODEL
    from tools.llm.llm_call import BASE_URL
    opts["model"] = opts["model"] or DEFAULT_MODEL
    if opts["max_workers"] < 1 or opts["attempts"] < 1 or opts["timeout"] <= 0 or opts["max_output_tokens"] < 1:
        raise ValueError("Workers, attempts, timeout and output tokens must be positive")
    for key in ("start_date", "end_date"):
        if opts[key] is not None:
            opts[key] = str(date.fromisoformat(str(opts[key])))
    identity = {"model": opts["model"], "temperature": opts["temperature"], "base_url": BASE_URL,
                "max_output_tokens": opts["max_output_tokens"],
                "client": getattr(client, "cache_identity", type(client).__name__) if client else "tools.llm"}
    found = discover_persons(data_dir, ("daily_event.json",))
    if persons:
        found = [(n, p) for n, p in found if n in set(persons)]
    if not found:
        raise ValueError("No matching person directories containing daily_event.json")
    entries, successful = {}, []
    for name, path in found:
        person_out = Path(output_dir) / name
        try:
            metrics = _run_person(name, Path(path), person_out, opts, client or SharedClient(opts), identity)
        except Exception as exc:
            metrics = {"method": METHOD_NAME, "person": name, "status": "error", "error": f"{type(exc).__name__}: {exc}"}
        metric_path = write_metrics(str(person_out), metrics)
        entries[name] = {"status": metrics["status"], "metrics_path": metric_path,
                         "analyzed_days": metrics.get("analyzed_days"), "error": metrics.get("error")}
        if "category_distribution" in metrics:
            successful.append(metrics)
    status = "incomplete" if any(e["status"] not in ("complete", "dry_run") for e in entries.values()) else ("dry_run" if opts["dry_run"] else "complete")
    from .engine import CATEGORIES
    root = {"method": METHOD_NAME, "status": status, "persons": entries,
            "aggregate_category_minutes": {c: round(sum(m["category_distribution"][c]["total_minutes"] for m in successful), 6) for c in CATEGORIES},
            "aggregate_analyzed_person_days": sum(m["analyzed_days"] for m in successful),
            "notes": "Root aggregates minutes and person-days; individual daily distributions and participation metrics are in person outputs."}
    path = write_metrics(output_dir, root)
    return {**root, "metrics_path": path, "output_dir": str(Path(output_dir).resolve())}


def _build_parser():
    parser = argparse.ArgumentParser(description="每日六领域时间利用统计（LLM复合活动分配，程序核算时长）")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--persons", nargs="+")
    parser.add_argument("--start-date", type=date.fromisoformat)
    parser.add_argument("--end-date", type=date.fromisoformat)
    parser.add_argument("--sleep-policy", choices=("none", "night-gap"))
    parser.add_argument("--model")
    parser.add_argument("--max-workers", type=int)
    parser.add_argument("--attempts", type=int)
    parser.add_argument("--timeout", type=float)
    parser.add_argument("--max-output-tokens", type=int)
    parser.add_argument("--dry-run", action="store_true", default=None)
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=None)
    return parser


def main(argv=None):
    args = _build_parser().parse_args(argv)
    try:
        opts = {k: v for k, v in vars(args).items() if k not in ("data_dir", "output_dir", "persons") and v is not None}
        result = evaluate(args.data_dir, args.output_dir, options=opts, persons=args.persons)
        print(f"{METHOD_NAME}: {result['status']} -> {result['output_dir']}")
        return 0 if result["status"] in ("complete", "dry_run") else 1
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
