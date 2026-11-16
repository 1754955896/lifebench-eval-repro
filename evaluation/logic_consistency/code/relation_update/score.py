"""This module calculates evaluation scores."""

from __future__ import annotations

from pathlib import Path

from tools.evaluation_common.config import resolve_method_options
from tools.evaluation_common.model_adapter import ModelClient

from .method import DEFAULT_OPTIONS, _run_person
from .selector import SelectorPromptManager


def score(final_labels: dict[str, int]) -> float | None:
    conform = final_labels.get("conform", 0)
    nonconform = final_labels.get("nonconform", 0)
    denominator = conform + nonconform
    return conform / denominator if denominator else None


def run(person: str, person_dir: str, output_dir: str, options: dict,
        client=None) -> dict:

    opts = resolve_method_options("relation_update", DEFAULT_OPTIONS)
    opts.update({
        "start_date": options.get("start_date"),
        "end_date": options.get("end_date"),
        "dry_run": bool(options.get("dry_run")),
    })
    for key in ("model", "max_context_tokens", "max_output_tokens",
                "max_model_calls", "max_retries", "resume",
                "selector_prompt", "writer_prompt"):
        value = options.get("relation_" + key)
        if value is not None:
            opts[key] = value
    if client is None and not opts["dry_run"]:
        client = ModelClient(model=opts["model"],
                             max_retries=int(opts["max_retries"]))
    prompts = SelectorPromptManager(opts["selector_prompt"],
                                    opts["writer_prompt"])
    result = _run_person(person, person_dir, str(Path(output_dir)),
                         opts, client, prompts)
    metrics = result["metrics"]
    result["score"] = (score(metrics.get("final_labels") or {})
                       if result["status"] == "complete" else None)
    return result
