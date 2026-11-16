"""This module stores evaluation outputs."""

from __future__ import annotations

import json
import os
import threading


def read_json(path: str, default=None):

    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path: str, obj) -> None:

    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def append_jsonl(path: str, record: dict) -> None:

    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_jsonl(path: str) -> list:

    if not os.path.exists(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


class PersonOutputStore:


    def __init__(self, out_dir: str):
        self.out_dir = out_dir
        os.makedirs(out_dir, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, name: str) -> str:
        return os.path.join(self.out_dir, name)

    def append_jsonl(self, name: str, record: dict):
        with self._lock:
            with open(self._path(name), "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def write_json(self, name: str, obj):
        with self._lock:
            with open(self._path(name), "w", encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False, indent=2)

    def read_json(self, name: str, default=None):
        return read_json(self._path(name), default=default)

    def read_jsonl(self, name: str) -> list:
        return read_jsonl(self._path(name))

    def has_results(self) -> bool:

        path = self._path("events.jsonl")
        return os.path.exists(path) and os.path.getsize(path) > 0

    def has_file(self, name: str) -> bool:
        return os.path.exists(self._path(name))

    def load_completed(self, signature: dict, *,
                       final_labels=("reasonable", "unreasonable"),
                       schema_version=None) -> set:


        completed = set()
        path = self._path("events.jsonl")
        if not os.path.exists(path):
            return completed
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if schema_version is not None \
                        and rec.get("schema_version") != schema_version:
                    continue
                if (isinstance(rec.get("date"), str)
                        and isinstance(rec.get("event_id"), str)
                        and rec.get("final_label") in final_labels
                        and rec.get("evaluation_signature") == signature):
                    completed.add((rec["date"], rec["event_id"]))
        return completed


__all__ = [
    "PersonOutputStore", "read_json", "write_json", "append_jsonl",
    "read_jsonl",
]
