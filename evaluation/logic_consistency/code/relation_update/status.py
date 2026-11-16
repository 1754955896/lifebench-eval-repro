"""This module reports evaluation status."""

from __future__ import annotations

import json
import os

_ERROR_TYPES = {"api_error", "model_output_error", "output_truncated",
                "patch_rejected"}


def run_complete(out_dir: str, signature: dict) -> bool:


    path = os.path.join(out_dir, "errors.jsonl")
    if not os.path.exists(path):
        return True
    completed = set()
    events_path = os.path.join(out_dir, "relation_events.jsonl")
    if os.path.exists(events_path):
        with open(events_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("evaluation_signature") == signature:
                    completed.add((rec.get("date"), rec.get("event_id")))
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("error_type") not in _ERROR_TYPES:
                continue
            if rec.get("evaluation_signature") != signature:
                continue
            event_ids = list(rec.get("event_ids") or [])
            for cid in rec.get("case_ids") or []:
                parts = str(cid).split(":")
                if len(parts) == 3:
                    event_ids.append(parts[1])
            for eid in event_ids:
                if (rec.get("date"), eid) not in completed:
                    return False
    return True


__all__ = ["run_complete"]
