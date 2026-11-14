"""Run unchanged legacy event/person checkers on the same 90 days, without maps.

This is a thin orchestration entry point. It uses the original prompts, response
parsers, EventAnalyzeProcess and RelationAnalyzeProcessor without changing them.
The original event window and log-rate semantics are intentionally preserved.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

from evaluation.logic_consistency.internal import processors
from evaluation.logic_consistency.internal.messages import EventMessage, PersonaMessage, PromptMessage
from evaluation.logic_consistency.internal.model import Deepseek
from evaluation.logic_consistency.internal.tasks import Task
from evaluation.logic_consistency.runner import adapt_daily_event, adapt_persona


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEFAULT_MANIFEST = ROOT / "results/logic_consistency/manifest.json"
DEFAULT_BASELINE = ROOT / "data/lifebench_10people/fenghaoran"
DEFAULT_OBJECTIVE = ROOT / "data/ablation_fenghaoran_2025/objective_only/fenghaoran"


def load_calendar(person_dir: Path, selected: list[str]) -> dict[str, Task]:
    persona = json.loads((person_dir / "persona.json").read_text(encoding="utf-8"))
    events = json.loads((person_dir / "daily_event.json").read_text(encoding="utf-8"))
    persona_message = PersonaMessage(adapt_persona(persona))
    daily = EventMessage(adapt_daily_event(events)).simplied_message
    missing = [day for day in selected if day not in daily]
    if missing:
        raise ValueError(f"Missing {len(missing)} selected dates: {missing[:5]}")
    return {day: Task(day, daily[day], persona_message) for day in selected}


def extract_legacy(client: Deepseek, task: Task, prompt: PromptMessage,
                   tag: str) -> list:
    # The regexes below are copied from the old APIExtractProcessor.run_task.
    response = client.ask(prompt.write_prompt(task.event))
    if tag == "events":
        text = re.search(r"<events>(.*?)</events>", response, re.S).group(1).strip()
        return [{"事件": event, "时间": event_time}
                for event, event_time in re.findall(r"\((.*?),\s*(.*?)\)", text)]
    match = re.search(r"<names>(.*?)</names>", response)
    return match.group(1).split() if match else []


def summarize(records: list[dict], selected: list[str], version: str) -> dict:
    event = Counter(prefix for row in records for prefix in row.get("event_prefixes", []))
    person = Counter(prefix for row in records for prefix in row.get("person_prefixes", []))
    event_den = event["<事件安排正确>"] + event["<事件安排错误>"]
    person_den = person["<人物正确>"] + person["<人物错误>"]
    return {
        "version": version, "selected_days": len(selected),
        "completed_days": sum("error" not in row for row in records),
        "failed_days": [row["source_day"] for row in records if "error" in row],
        "event_counts": dict(event),
        "person_counts": dict(person),
        "event_consistency_rate": event["<事件安排正确>"] / event_den if event_den else None,
        "event_denominator": event_den,
        "person_consistency_rate": person["<人物正确>"] / person_den if person_den else None,
        "person_denominator": person_den,
        "note": "Unmodified legacy checker semantics; suspected events are outside the event denominator.",
    }


def evaluate_version(version: str, person_dir: Path, selected: list[str],
                     client: Deepseek, output: Path) -> dict:
    calendar = load_calendar(person_dir, selected)
    records_dir = output / version / "days"
    records_dir.mkdir(parents=True, exist_ok=True)
    event_prompt = PromptMessage((HERE / "prompts/events.txt").read_text(encoding="utf-8"))
    people_prompt = PromptMessage((HERE / "prompts/people.txt").read_text(encoding="utf-8"))
    processors.RelationAnalyzeProcessor.relations = None
    processors.EventAnalyzeProcess.calendar = None
    processors.EventAnalyzeProcess.deepseek = None
    processors.EventAnalyzeProcess.events_api = None
    processors.EventAnalyzeProcess.check_prompt = None
    relation_checker = processors.RelationAnalyzeProcessor("person-only")
    event_checker = processors.EventAnalyzeProcess(
        "event-only", None, client, calendar,
        SimpleNamespace(event_check=str(HERE / "prompts/check.txt")))

    for index, day in enumerate(selected, 1):
        path = records_dir / f"{day}.json"
        if path.exists() and "error" not in json.loads(path.read_text(encoding="utf-8")):
            continue
        task = calendar[day]
        try:
            task.events = extract_legacy(client, task, event_prompt, "events")
            task.relations = extract_legacy(client, task, people_prompt, "people")
            relation_checker.task = (task,)
            relation_checker.run_task(lambda _: None)
            event_checker.task = (task,)
            event_checker.run_task(lambda _: None)
            record = {
                "source_day": day,
                "extracted_events": task.events,
                "extracted_people": task.relations,
                "event_results": [r.to_dict() for r in task.analyze_for_events],
                "person_results": [r.to_dict() for r in task.res_for_relations],
                "event_prefixes": [r.prefix for r in task.analyze_for_events],
                "person_prefixes": [r.prefix for r in task.res_for_relations],
            }
        except Exception as exc:
            record = {"source_day": day, "error": f"{type(exc).__name__}: {exc}"}
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        print(f"{version}: {index}/{len(selected)} {day}"
              f" events={len(record.get('event_prefixes', []))}"
              f" people={len(record.get('person_prefixes', []))}"
              f" error={'error' in record}", flush=True)

    records = [json.loads((records_dir / f"{day}.json").read_text(encoding="utf-8"))
               for day in selected]
    metrics = summarize(records, selected, version)
    (output / version / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    return metrics


def main() -> None:
    p = argparse.ArgumentParser(description="Legacy event/person scores only, no maps")
    p.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    p.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    p.add_argument("--objective", type=Path, default=DEFAULT_OBJECTIVE)
    p.add_argument("--output", type=Path, default=ROOT / "output/logic_consistency_two_metrics_legacy90")
    p.add_argument("--model", default="deepseek-chat")
    p.add_argument("--version", choices=("baseline", "objective_only", "both"), default="both")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    selected = json.loads(args.manifest.read_text(encoding="utf-8"))["sampled_days"]
    if len(selected) != 90 or len(set(selected)) != 90:
        raise ValueError("Manifest must contain exactly 90 distinct dates")
    versions = (("baseline", args.baseline), ("objective_only", args.objective))
    if args.version != "both":
        versions = tuple(item for item in versions if item[0] == args.version)
    for label, path in versions:
        load_calendar(path, selected)
        print(f"{label}: 90 dates available", flush=True)
    if args.dry_run:
        return
    args.output.mkdir(parents=True, exist_ok=True)
    client = Deepseek(model=args.model)
    results = [evaluate_version(label, path, selected, client, args.output)
               for label, path in versions]
    if args.version == "both":
        (args.output / "comparison.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
