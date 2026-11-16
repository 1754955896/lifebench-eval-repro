"""This module validates evaluation inputs."""

from __future__ import annotations

from dataclasses import dataclass


ROLE_NOISE_TARGET = "noise_target"
ROLE_SOURCE_RESOLVED = "source_resolved"
ROLE_SOURCE_PARTIAL = "source_partial"
ROLE_SOURCE_UNRESOLVED = "source_unresolved"
ROLE_INVALID_LINEAGE = "invalid_lineage"
ROLE_CROSS_DAY_CONTEXT = "cross_day_context"

LINEAGE_ROLES = frozenset({
    ROLE_NOISE_TARGET, ROLE_SOURCE_RESOLVED, ROLE_SOURCE_PARTIAL,
    ROLE_SOURCE_UNRESOLVED, ROLE_INVALID_LINEAGE, ROLE_CROSS_DAY_CONTEXT,
})


PERSONA_SECTIONS = [
    "identity", "health", "work", "addresses", "lifestyle", "economy",
    "belief", "hobbies_goals", "personality", "relationships",
    "education_experience",
]
PERSONA_SECTION_SET = frozenset(PERSONA_SECTIONS)

PERSONA_SECTION_FIELDS = {
    "identity": [
        "name", "gender", "age", "birth", "nationality", "birth_place",
        "memory_date", "description",
    ],
    "health": ["body", "healthy_desc"],
    "work": ["job", "occupation", "workplace", "work_desc"],
    "addresses": ["home_address", "workplace"],
    "lifestyle": ["lifestyle_desc", "favorite_foods"],
    "economy": ["economic_desc", "salary"],
    "belief": ["belief"],
    "hobbies_goals": ["hobbies", "aim"],
    "personality": ["personality"],
    "relationships": ["family", "relation"],
    "education_experience": ["education", "experience_desc"],
}

KNOWN_PERSONA_FIELDS = frozenset(
    f for fs in PERSONA_SECTION_FIELDS.values() for f in fs)


INPUT_ERROR_MISSING_ATOMIC_ID = "MISSING_ATOMIC_ID"
INPUT_ERROR_NULL_ATOMIC_ID = "NULL_ATOMIC_ID"
INPUT_ERROR_ATOMIC_ID_NOT_LIST = "ATOMIC_ID_NOT_LIST"
INPUT_ERROR_EMPTY_STRING_IN_ATOMIC_ID = "EMPTY_STRING_IN_ATOMIC_ID"
INPUT_ERROR_MISSING_EVENT_ID = "MISSING_EVENT_ID"
INPUT_ERROR_MISSING_DATE = "MISSING_DATE"
INPUT_ERROR_EMPTY_DATE = "EMPTY_DATE"
INPUT_ERROR_BAD_DATE_ENTRY = "BAD_DATE_ENTRY"
INPUT_ERROR_UNPARSEABLE_DATE = "UNPARSEABLE_DATE"

INPUT_ERROR_CODES = frozenset({
    INPUT_ERROR_MISSING_ATOMIC_ID, INPUT_ERROR_NULL_ATOMIC_ID,
    INPUT_ERROR_ATOMIC_ID_NOT_LIST, INPUT_ERROR_EMPTY_STRING_IN_ATOMIC_ID,
    INPUT_ERROR_MISSING_EVENT_ID, INPUT_ERROR_MISSING_DATE,
    INPUT_ERROR_EMPTY_DATE, INPUT_ERROR_BAD_DATE_ENTRY,
    INPUT_ERROR_UNPARSEABLE_DATE,
})


@dataclass(frozen=True)
class Interval:


    start: object
    end: object
    start_raw: str
    end_raw: str
    end_hour_marker: bool = False
    implicit_cross_midnight: bool = False

    @property
    def cross_day(self) -> bool:
        return self.end.date() > self.start.date()

    @property
    def invalid_duration(self) -> bool:
        return self.end < self.start

    def to_dict(self):
        return {
            "start": self.start_raw,
            "end": self.end_raw,
            "end_hour_marker": self.end_hour_marker,
            "implicit_cross_midnight": self.implicit_cross_midnight,
        }


@dataclass
class InputEventCheck:


    event_id: object
    errors: list
    is_noise_candidate: bool = False


def check_daily_event(event) -> InputEventCheck:


    errors = []
    eid = event.get("event_id") if isinstance(event, dict) else None
    if not isinstance(event, dict):
        return InputEventCheck(event_id=None,
                               errors=[INPUT_ERROR_MISSING_EVENT_ID])
    if eid is None or isinstance(eid, (list, dict)):
        errors.append(INPUT_ERROR_MISSING_EVENT_ID)
    if "date" not in event or not isinstance(event.get("date"), list):
        errors.append(INPUT_ERROR_MISSING_DATE)
    else:
        if not event["date"]:
            errors.append(INPUT_ERROR_EMPTY_DATE)
        elif not all(isinstance(d, str) and d for d in event["date"]):
            errors.append(INPUT_ERROR_BAD_DATE_ENTRY)

    is_noise = False
    if "atomic_id" not in event:
        errors.append(INPUT_ERROR_MISSING_ATOMIC_ID)
    elif event["atomic_id"] is None:
        errors.append(INPUT_ERROR_NULL_ATOMIC_ID)
    elif not isinstance(event["atomic_id"], list):
        errors.append(INPUT_ERROR_ATOMIC_ID_NOT_LIST)
    elif event["atomic_id"]:
        if any(not isinstance(x, str) or x == "" for x in event["atomic_id"]):
            errors.append(INPUT_ERROR_EMPTY_STRING_IN_ATOMIC_ID)
    else:
        is_noise = True

    return InputEventCheck(event_id=eid, errors=errors,
                           is_noise_candidate=is_noise)


def check_persona(persona) -> list:

    warnings = []
    if not isinstance(persona, dict):
        return ["persona 必须是 JSON 对象"]
    for key in persona:
        if key not in KNOWN_PERSONA_FIELDS:
            warnings.append(
                f"persona 顶层字段 {key!r} 未映射到 persona_core 或任何 section")
    return warnings


def check_event_tree(tree) -> list:

    warnings = []
    if not isinstance(tree, list):
        return ["event_tree 必须是 JSON 数组"]
    seen = set()
    for node in tree:
        _walk_tree_node(node, "$", seen, warnings)
    return warnings


def _walk_tree_node(node, path, seen, warnings):
    if not isinstance(node, dict):
        warnings.append(f"{path}: 节点必须是对象")
        return
    nid = node.get("event_id")
    if nid is None or isinstance(nid, (list, dict)):
        warnings.append(f"{path}: event_id 缺失或类型非法")
    else:
        key = str(nid)
        if key in seen:
            warnings.append(f"{path}: event_id {key!r} 重复")
        seen.add(key)
    for i, sub in enumerate(node.get("subevent") or []):
        _walk_tree_node(sub, f"{path}.subevent[{i}]", seen, warnings)


__all__ = [
    "ROLE_NOISE_TARGET", "ROLE_SOURCE_RESOLVED", "ROLE_SOURCE_PARTIAL",
    "ROLE_SOURCE_UNRESOLVED", "ROLE_INVALID_LINEAGE", "ROLE_CROSS_DAY_CONTEXT",
    "LINEAGE_ROLES",
    "PERSONA_SECTIONS", "PERSONA_SECTION_SET", "PERSONA_SECTION_FIELDS",
    "KNOWN_PERSONA_FIELDS",
    "INPUT_ERROR_MISSING_ATOMIC_ID", "INPUT_ERROR_NULL_ATOMIC_ID",
    "INPUT_ERROR_ATOMIC_ID_NOT_LIST", "INPUT_ERROR_EMPTY_STRING_IN_ATOMIC_ID",
    "INPUT_ERROR_MISSING_EVENT_ID", "INPUT_ERROR_MISSING_DATE",
    "INPUT_ERROR_EMPTY_DATE", "INPUT_ERROR_BAD_DATE_ENTRY",
    "INPUT_ERROR_UNPARSEABLE_DATE", "INPUT_ERROR_CODES",
    "Interval", "InputEventCheck", "check_daily_event", "check_persona",
    "check_event_tree",
]
