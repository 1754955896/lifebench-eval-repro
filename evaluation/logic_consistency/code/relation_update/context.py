"""This module prepares evaluation context."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime

from tools.evaluation_common.context import (
    _flatten_relations, prepare_events,
)
from tools.evaluation_common.input_schema import ROLE_NOISE_TARGET
from evaluation.logic_consistency.code.relation_update.schema import LIB_SCHEMA, normalize_tag


SELF_RELATION_MARKERS = ("自己", "本人", "我")


SAFE_NAME_SUFFIXES = ("教授", "老师", "医生", "律师", "先生", "女士",
                      "师傅", "经理", "主任", "院长", "校长", "同学")


KINSHIP_GROUPS = {
    "父亲": ("父亲", "爸爸", "爸"),
    "母亲": ("母亲", "妈妈", "妈"),
    "配偶": ("丈夫", "妻子", "老公", "老婆", "配偶", "爱人"),
    "姐姐": ("姐姐", "大姐"),
    "妹妹": ("妹妹", "小妹"),
    "哥哥": ("哥哥", "大哥"),
    "弟弟": ("弟弟", "小弟"),
    "爷爷": ("爷爷",),
    "奶奶": ("奶奶",),
    "外公": ("外公", "姥爷"),
    "外婆": ("外婆", "姥姥"),
    "儿子": ("儿子",),
    "女儿": ("女儿",),
}
KINSHIP_SYNONYMS = {syn: group
                    for group, syns in KINSHIP_GROUPS.items()
                    for syn in syns}


def build_initial_relation_library(persona: dict, owner: str) -> dict:


    records = []
    for idx, rel in enumerate(_flatten_relations(persona or {}), start=1):
        name = (rel.get("name") or "").strip()
        if not name:
            continue
        aliases = []
        nickname = (rel.get("nickname") or "").strip()
        if nickname and nickname != name:
            aliases.append(nickname)
        tags = []
        tag = (rel.get("relation") or "").strip()
        if tag:
            tags.append(tag)
        records.append({
            "person_id": f"rel_{idx:04d}",
            "name": name,
            "aliases": aliases,
            "relation_tags": tags,
            "social_circle": (rel.get("social circle")
                              or rel.get("social_circle") or None),
            "base_relation_description": rel.get("relation_description") or "",
            "dynamic_relation_notes": [],
            "first_seen": "initial",
            "last_seen": None,
            "interaction_days": 0,
            "update_evidence": [],
        })
    return {"schema_version": LIB_SCHEMA, "owner": owner,
            "records": records}


def library_indexes(library: dict) -> dict:

    by_name = defaultdict(list)
    by_alias = defaultdict(list)
    by_stripped = defaultdict(list)
    for rec in library["records"]:
        by_name[rec["name"]].append(rec["person_id"])
        for a in rec["aliases"]:
            by_alias[a].append(rec["person_id"])
        stripped = strip_safe_suffix(rec["name"])
        if stripped != rec["name"]:
            by_stripped[stripped].append(rec["person_id"])

    kinship_holders = defaultdict(list)
    for rec in library["records"]:
        for tag in rec["relation_tags"]:
            group = KINSHIP_SYNONYMS.get(tag)
            if group:
                kinship_holders[group].append(rec["person_id"])
    by_kinship = {}
    for group, ids in kinship_holders.items():
        if len(set(ids)) == 1:
            for syn in KINSHIP_GROUPS[group]:
                by_kinship[syn] = ids[0]
    return {"by_name": by_name, "by_alias": by_alias,
            "by_stripped": by_stripped, "by_kinship": by_kinship}


def strip_safe_suffix(name: str) -> str:

    for suffix in SAFE_NAME_SUFFIXES:
        if len(name) > len(suffix) and name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def compute_match_hints(observed_name: str, observed_relation: str | None,
                        indexes: dict) -> list:


    hints = []
    seen = set()
    candidates = [observed_name]
    stripped = strip_safe_suffix(observed_name)
    if stripped != observed_name:
        candidates.append(stripped)
    for name in candidates:
        for source in (indexes["by_name"].get(name, []),
                       indexes["by_alias"].get(name, []),
                       indexes["by_stripped"].get(name, [])):
            for pid in source:
                if pid not in seen:
                    seen.add(pid)
                    hints.append(pid)
    for text in (observed_name, observed_relation or ""):
        if text in indexes["by_kinship"]:
            pid = indexes["by_kinship"][text]
            if pid not in seen:
                seen.add(pid)
                hints.append(pid)
    return hints


def event_origin_of(atomic_id) -> str:

    return "source" if atomic_id else "noise"


def prepare_relation_events(daily_events: list, outline) -> tuple:


    parsed = prepare_events(daily_events, outline)
    parse_errors = []
    ok = []
    for pe in parsed:
        if pe["intervals"]:
            ok.append(pe)
        else:
            parse_errors.append(pe)
    return ok, parse_errors


def participants_of(event: dict) -> tuple:


    parts = event.get("participant")
    if not isinstance(parts, list) or not parts:
        return [], "participant 缺失或不是数组"
    for p in parts:
        if not isinstance(p, dict) or not isinstance(p.get("name"), str) \
                or not p["name"].strip():
            return [], "participant 元素结构非法(缺少非空 name)"
    return parts, None


def is_self_participant(participant: dict, persona: dict) -> bool:

    name = (participant.get("name") or "").strip()
    owner = (persona or {}).get("name")
    if owner and name == owner:
        return True
    rel = (participant.get("relation") or "").strip()
    if rel in SELF_RELATION_MARKERS:
        return True
    if owner and len(owner) >= 2 and len(name) == 2 \
            and name in (owner[-2:], owner[:2]):
        return True
    return False


def build_day_plan(day_events: list, date: str, library: dict,
                   persona: dict) -> list:


    ordered = sorted(day_events, key=lambda pe: pe["intervals"][0].start)
    indexes = library_indexes(library)
    plan = []
    for pe in ordered:
        earlier = [o for o in ordered
                   if o is not pe
                   and o["intervals"][0].start <= pe["intervals"][0].start
                   and o["role"] != ROLE_NOISE_TARGET]
        cases, errors = _event_cases(pe, date, indexes, persona, earlier)
        plan.append((pe, cases, earlier, errors))
    return plan


def _event_cases(pe: dict, date: str, indexes: dict, persona: dict,
                 earlier: list) -> tuple:
    event = pe["event"]
    eid = str(event.get("event_id") or "")
    participants, err = participants_of(event)
    if err:
        return [], [{"event_id": eid, "message": err}]
    allowed = [eid]
    for other in earlier:
        oid = str(other["event"]["event_id"])
        if oid not in allowed:
            allowed.append(oid)
    cases = []
    for i, p in enumerate(participants, start=1):
        name = p.get("name", "").strip()
        rel = (p.get("relation") or "").strip() or None
        cases.append({
            "case_id": f"{date}:{eid}:{i}",
            "date": date,
            "event_id": eid,
            "participant_index": i,
            "event_origin": event_origin_of(event.get("atomic_id")),
            "observed_name": name,
            "observed_relation": rel,
            "candidate_key": name,
            "match_hints": compute_match_hints(name, rel, indexes),
            "self_hint": is_self_participant(p, persona),
            "available_context_event_ids": allowed,
        })
    return cases, []


def build_relation_cases(day_events: list, date: str, library: dict,
                         persona: dict) -> tuple:

    cases, errors = [], []
    for _pe, c, _earlier, errs in build_day_plan(day_events, date,
                                                 library, persona):
        cases.extend(c)
        errors.extend(errs)
    return cases, errors


def group_relation_days(parsed: list) -> dict:

    by_day = defaultdict(list)
    for pe in parsed:
        if not pe["intervals"]:
            continue
        day = pe["intervals"][0].start.strftime("%Y-%m-%d")
        by_day[day].append(pe)
    return dict(by_day)


def record_by_id(library: dict, person_id: str) -> dict | None:
    return next((r for r in library["records"]
                 if r["person_id"] == person_id), None)


def record_by_name_or_alias(library: dict, name: str) -> list:
    out = []
    for r in library["records"]:
        if r["name"] == name or name in r["aliases"]:
            out.append(r)
    return out


def apply_keep_maintenance(library: dict, person_id: str, date: str):

    rec = record_by_id(library, person_id)
    if rec is None:
        return
    if rec["last_seen"] != date:
        rec["interaction_days"] += 1
    rec["last_seen"] = date


def next_person_id(library: dict) -> str:
    used = {r["person_id"] for r in library["records"]}
    idx = len(used) + 1
    while f"rel_{idx:04d}" in used:
        idx += 1
    return f"rel_{idx:04d}"


def apply_add_patch(library: dict, patch: dict) -> dict:

    rec = {
        "person_id": next_person_id(library),
        "name": patch["name"].strip(),
        "aliases": [a.strip() for a in patch.get("aliases_to_add") or []
                    if a.strip()],
        "relation_tags": [normalize_tag(t.strip())
                          for t in patch.get("relation_tags_to_add") or []
                          if t.strip()],
        "social_circle": patch.get("social_circle"),
        "base_relation_description": "",
        "dynamic_relation_notes": [],
        "first_seen": patch.get("_date"),
        "last_seen": patch.get("_date"),
        "interaction_days": 1,
        "update_evidence": list(patch.get("evidence_event_ids") or []),
    }
    note = (patch.get("relation_note") or "").strip()
    if note:
        rec["dynamic_relation_notes"].append(
            {"date": patch.get("_date"), "note": note})
    library["records"].append(rec)
    return rec


def apply_update_patch(library: dict, patch: dict) -> dict:

    rec = record_by_id(library, patch["target_person_id"])
    if rec is None:
        raise ValueError(f"目标 person_id 不存在: "
                         f"{patch['target_person_id']!r}")
    for alias in patch.get("aliases_to_add") or []:
        alias = alias.strip()
        if alias and alias not in rec["aliases"]:
            rec["aliases"].append(alias)
    for tag in patch.get("relation_tags_to_add") or []:
        tag = normalize_tag(tag.strip())
        if tag and tag not in rec["relation_tags"]:
            rec["relation_tags"].append(tag)
    sc = patch.get("social_circle")
    if sc and not rec.get("social_circle"):
        rec["social_circle"] = sc
    note = (patch.get("relation_note") or "").strip()
    if note:
        rec["dynamic_relation_notes"].append(
            {"date": patch.get("_date"), "note": note})
    rec["update_evidence"] = sorted(set(
        rec["update_evidence"]) | set(patch.get("evidence_event_ids") or []))

    date = patch.get("_date")
    if date:
        apply_keep_maintenance(library, rec["person_id"], date)
    return rec


def check_patch_conflicts(library: dict, patch: dict,
                          target_self_id: str | None) -> str | None:


    if patch["operation"] == "add":
        name = patch["name"].strip()
        for rec in library["records"]:
            if rec["name"] == name:
                return f"新增姓名 {name!r} 与现有对象 {rec['person_id']} 冲突"
            if name in rec["aliases"]:
                return f"新增姓名 {name!r} 与 {rec['person_id']} 别名冲突"
        for alias in patch.get("aliases_to_add") or []:
            alias = alias.strip()
            for rec in library["records"]:
                if rec["name"] == alias or alias in rec["aliases"]:
                    return (f"新增别名 {alias!r} 与现有对象 "
                            f"{rec['person_id']} 冲突")
        return None
    rec = record_by_id(library, patch["target_person_id"])
    if rec is None:
        return f"target_person_id 不存在: {patch['target_person_id']!r}"
    if patch.get("name", "").strip() != rec["name"]:
        return (f"update 补丁不得改写正式姓名 "
                f"({patch.get('name')!r} != {rec['name']!r})")
    return None


def rebuild_library_from_updates(initial: dict, updates: list,
                                 keep_events: list | None = None) -> dict:


    import copy as _copy
    library = _copy.deepcopy(initial)
    actions = []
    for upd in updates:
        patch = dict(upd["patch"])
        patch["_date"] = upd["date"]
        actions.append((upd["date"], 0, "patch", patch))
    for person_id, date in (keep_events or []):
        actions.append((date, 1, "keep", person_id))
    actions.sort(key=lambda a: (a[0], a[1]))
    for _date, _seq, kind, payload in actions:
        if kind == "patch":
            if payload.get("operation") == "add":
                stored_id = payload.get("target_person_id")
                if stored_id:
                    apply_add_patch_with_id(library, payload, stored_id)
                else:
                    apply_add_patch(library, payload)
            else:
                apply_update_patch(library, payload)
        else:
            apply_keep_maintenance(library, payload, _date)
    return library


def apply_add_patch_with_id(library: dict, patch: dict, person_id: str):

    if record_by_id(library, person_id) is not None:
        raise ValueError(f"重放 person_id 冲突: {person_id!r}")
    rec = {
        "person_id": person_id,
        "name": patch["name"].strip(),
        "aliases": [a.strip() for a in patch.get("aliases_to_add") or []
                    if a.strip()],
        "relation_tags": [normalize_tag(t.strip())
                          for t in patch.get("relation_tags_to_add") or []
                          if t.strip()],
        "social_circle": patch.get("social_circle"),
        "base_relation_description": "",
        "dynamic_relation_notes": [],
        "first_seen": patch.get("_date"),
        "last_seen": patch.get("_date"),
        "interaction_days": 1,
        "update_evidence": list(patch.get("evidence_event_ids") or []),
    }
    note = (patch.get("relation_note") or "").strip()
    if note:
        rec["dynamic_relation_notes"].append(
            {"date": patch.get("_date"), "note": note})
    library["records"].append(rec)
    return rec
