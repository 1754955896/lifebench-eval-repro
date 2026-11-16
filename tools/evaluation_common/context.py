"""This module prepares evaluation context."""

from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta

from tools.evaluation_common.input_schema import (
    INPUT_ERROR_UNPARSEABLE_DATE,
    PERSONA_SECTION_FIELDS,
    ROLE_INVALID_LINEAGE, ROLE_NOISE_TARGET,
    ROLE_SOURCE_PARTIAL, ROLE_SOURCE_RESOLVED, ROLE_SOURCE_UNRESOLVED,
    Interval,
)


TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}):(\d{2}):(\d{2})$")
INTERVAL_RE = re.compile(r"^(.+?)至(.+)$")


WEEKDAYS_EN = ["Monday", "Tuesday", "Wednesday", "Thursday",
               "Friday", "Saturday", "Sunday"]
WEEKDAYS_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

DESCRIPTION_EXCERPT_CHARS = 300


def parse_timestamp(ts: str):


    m = TS_RE.match(ts)
    if not m:
        raise ValueError(f"无法解析时间字符串 {ts!r}")
    day, h, mi, sec = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))
    if mi > 59 or sec > 59:
        raise ValueError(f"时间分量非法 {ts!r}")
    dt = datetime.strptime(day, "%Y-%m-%d")
    normalized = h >= 24
    if normalized:
        dt += timedelta(days=h // 24)
        h %= 24
    return dt + timedelta(hours=h, minutes=mi, seconds=sec), normalized


def parse_interval(date_str: str) -> Interval:


    if not isinstance(date_str, str):

        raise ValueError(f"时间段必须是字符串: {date_str!r}")
    m = INTERVAL_RE.match(date_str)
    if not m:
        raise ValueError(f"时间段缺少 '至' 分隔符: {date_str!r}")
    start_raw, end_raw = m.group(1), m.group(2)
    start, _ = parse_timestamp(start_raw)
    end, end_marker = parse_timestamp(end_raw)
    implicit_cross = False
    if end.date() == start.date() and end < start and not end_marker:

        end = end + timedelta(days=1)
        implicit_cross = True
    return Interval(start=start, end=end, start_raw=start_raw,
                    end_raw=end_raw, end_hour_marker=end_marker,
                    implicit_cross_midnight=implicit_cross)


def parse_event_intervals(event) -> list:


    intervals = [parse_interval(d) for d in event.get("date") or []]
    intervals.sort(key=lambda iv: iv.start)
    return intervals


def owner_date_of(intervals: list) -> str:


    return intervals[0].start.strftime("%Y-%m-%d")


def weekday_en(date_str: str) -> str:


    return WEEKDAYS_EN[datetime.strptime(date_str, "%Y-%m-%d").weekday()]


def weekday_cn(date_str: str) -> str:

    return WEEKDAYS_CN[datetime.strptime(date_str, "%Y-%m-%d").weekday()]


class OutlineIndex:


    def __init__(self):
        self.nodes = {}
        self.ancestors = {}
        self.parent = {}

    def add(self, key: str, node: dict, summary: dict, parent_key: str | None):
        self.nodes[key] = node
        self.nodes[key]["_summary"] = summary
        self.parent[key] = parent_key

    def resolve(self, atomic_id: str) -> dict | None:

        return self.nodes.get(str(atomic_id))

    def summarize(self, key: str, excerpt_chars: int = DESCRIPTION_EXCERPT_CHARS):

        node = self.nodes.get(key)
        if node is None:
            return None
        summary = dict(node["_summary"])
        return summary

    def build_ancestors(self):

        for key in self.nodes:
            chain = []
            cur = self.parent.get(key)
            while cur is not None:
                node = self.nodes.get(cur)
                if node is None:
                    break
                chain.append(dict(node["_summary"]))
                cur = self.parent.get(cur)
            self.ancestors[key] = chain


def _node_summary(node: dict, excerpt_chars: int) -> dict:

    desc = node.get("description") or ""
    participants = [
        p.get("name") for p in (node.get("participant") or [])
        if isinstance(p, dict) and p.get("name")
    ]
    truncated = len(desc) > excerpt_chars
    return {
        "event_id": node.get("event_id"),
        "event_id_type": type(node.get("event_id")).__name__,
        "name": node.get("name"),
        "type": node.get("type"),
        "date": node.get("date"),
        "location": node.get("location"),
        "participants": participants,
        "description_excerpt": desc[:excerpt_chars],
        "description_truncated": truncated,
    }


def build_outline_index(tree: list) -> OutlineIndex:


    index = OutlineIndex()

    def walk(node, parent_key):
        nid = node.get("event_id")
        if nid is None or isinstance(nid, (list, dict)):

            for sub in node.get("subevent") or []:
                walk(sub, parent_key)
            return
        key = str(nid)
        index.add(key, node, _node_summary(node, DESCRIPTION_EXCERPT_CHARS),
                  parent_key)
        for sub in node.get("subevent") or []:
            walk(sub, key)

    for node in tree:
        walk(node, None)


    for key in list(index.parent):
        if index.parent[key] is None and "-" in key:
            candidate = key.rsplit("-", 1)[0]
            if candidate in index.nodes:
                index.parent[key] = candidate
    index.build_ancestors()
    return index


def lineage_role_of(event_check, atomic_ids, outline: OutlineIndex):


    if event_check.errors:
        return ROLE_INVALID_LINEAGE, [], []
    if event_check.is_noise_candidate:
        return ROLE_NOISE_TARGET, [], []
    resolved, missing = [], []
    for aid in atomic_ids:
        key = str(aid)
        (resolved if outline.resolve(key) is not None else missing).append(aid)
    if missing and resolved:
        return ROLE_SOURCE_PARTIAL, resolved, missing
    if missing:
        return ROLE_SOURCE_UNRESOLVED, resolved, missing
    return ROLE_SOURCE_RESOLVED, resolved, missing


def build_lineage_audit(daily_events: list, outline: OutlineIndex) -> dict:


    counts = defaultdict(int)
    events = []
    input_errors = []
    distinct_ids = set()
    missing_ids = set()
    events_with_missing = 0

    for event in daily_events:
        check = _check_event(event)
        eid = str(check.event_id) if check.event_id is not None else None
        if check.errors:
            counts[ROLE_INVALID_LINEAGE] += 1
            input_errors.append({"event_id": check.event_id,
                                 "codes": check.errors})
            events.append({"event_id": check.event_id, "role": ROLE_INVALID_LINEAGE,
                           "resolved": [], "missing": [], "errors": check.errors})
            continue
        atomic_ids = event["atomic_id"]
        role, resolved, missing = lineage_role_of(check, atomic_ids, outline)
        counts[role] += 1
        for aid in atomic_ids:
            distinct_ids.add(str(aid))
        for aid in missing:
            missing_ids.add(str(aid))
        if missing:
            events_with_missing += 1
        events.append({"event_id": check.event_id, "role": role,
                       "resolved": resolved, "missing": missing, "errors": []})

    return {
        "counts": dict(counts),
        "atomic_id_stats": {
            "distinct_nonempty_ids": len(distinct_ids),
            "missing_in_outline": len(missing_ids),
            "events_with_missing_ids": events_with_missing,
        },
        "events": events,
        "input_errors": input_errors,
    }


def _check_event(event):

    from tools.evaluation_common.input_schema import check_daily_event
    return check_daily_event(event)


PERSONA_CORE_ORDER = [
    ("name", "姓名"), ("gender", "性别"), ("age", "年龄"), ("birth", "生日"),
    ("nationality", "民族"), ("education", "学历"), ("job", "职业"),
    ("occupation", "工作单位"), ("workplace", "工作地址"),
    ("home_address", "家庭住址"), ("body", "身体数据"),
    ("healthy_desc", "健康状况"), ("belief", "信仰"), ("family", "家庭状况"),
    ("salary", "年收入"), ("economic_desc", "经济状况"),
    ("lifestyle_desc", "生活方式"), ("favorite_foods", "喜欢食物"),
    ("hobbies", "爱好"), ("aim", "目标"), ("personality", "性格"),
    ("work_desc", "工作描述"),
]


def build_persona_core(persona: dict) -> dict:


    core = {}
    for key, label in PERSONA_CORE_ORDER:
        if key not in persona:
            continue
        value = persona[key]
        if value is None or value == "" or value == [] or value == {}:
            continue
        core[key] = value

    relations = _flatten_relations(persona)
    if relations:
        core["relation_index"] = [
            {
                "name": r.get("name"),
                "relation": r.get("relation"),
                "nickname": r.get("nickname"),
                "social_circle": r.get("social circle"),
            }
            for r in relations
        ]
    return core


def _flatten_relations(persona: dict) -> list:


    relations = persona.get("relation") or []
    flattened = []
    for i, group in enumerate(relations):
        items = group if isinstance(group, list) else [group]
        for j, rel in enumerate(items):
            if isinstance(rel, dict):
                rel = dict(rel)
                rel["_pointer"] = f"{i}/{j}"
                flattened.append(rel)
    return flattened


def persona_section_values(persona: dict, sections: list,
                           relationship_names=None) -> list:


    seen = set()
    evidence = []
    for s in sections:
        fields = PERSONA_SECTION_FIELD_NAMES.get(s, [])
        for f in fields:
            if f in seen or f not in persona:
                continue
            value = persona[f]
            if value is None or value == "" or value == [] or value == {}:
                continue
            seen.add(f)
            evidence.append({"source": "persona.json", "pointer": f"{s}/{f}",
                             "value": value})


        if s == "relationships":
            for rel in _flatten_relations(persona):
                name = rel.get("name")
                rel_ptr = f"{s}/relation/{rel['_pointer']}"
                if rel_ptr in seen:
                    continue
                if relationship_names and name not in relationship_names:
                    continue
                seen.add(rel_ptr)
                entry = {k: v for k, v in rel.items() if k != "_pointer"}
                evidence.append({"source": "persona.json",
                                 "pointer": rel_ptr,
                                 "value": entry})
    return evidence


PERSONA_SECTION_FIELD_NAMES = {
    s: [f for f in fields if f != "relation"]
    for s, fields in PERSONA_SECTION_FIELDS.items()
}


def group_events_by_day(parsed_events: list) -> dict:


    days = defaultdict(list)
    for pe in parsed_events:
        if pe["role"] == ROLE_INVALID_LINEAGE:
            continue
        covered = set()
        for iv in pe["intervals"]:
            d = iv.start.date()
            covered.add(d)
            d2 = iv.end.date()
            if d2 != d:

                cur = d + timedelta(days=1)
                while cur <= d2:
                    covered.add(cur)
                    cur += timedelta(days=1)
        for d in sorted(covered):
            days[d.strftime("%Y-%m-%d")].append(pe)
    return days


def prepare_events(daily_events: list, outline: OutlineIndex) -> list:


    parsed = []
    for event in daily_events:
        check = _check_event(event)
        role, resolved, missing = lineage_role_of(
            check, event.get("atomic_id") if not check.errors else [], outline)
        errors = list(check.errors)
        try:
            intervals = parse_event_intervals(event)
        except ValueError:
            intervals = []
            errors.append(INPUT_ERROR_UNPARSEABLE_DATE)
        parsed.append({
            "event": event,
            "intervals": intervals,
            "role": role,
            "resolved": resolved,
            "missing": missing,
            "errors": errors,
            "owner_date": owner_date_of(intervals) if intervals else None,
        })
    return parsed


def load_daily_draft(draft_file: str | None) -> dict:


    if not draft_file or not os.path.exists(draft_file):
        return {}
    with open(draft_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    by_date = {}
    for month in (data.values() if isinstance(data, dict) else []):
        for day in month if isinstance(month, list) else []:
            if isinstance(day, dict) and isinstance(day.get("date"), str):
                by_date[day["date"]] = day
    return by_date


def estimate_tokens(text: str) -> int:


    cjk = sum(1 for ch in text if '一' <= ch <= '鿿'
              or '　' <= ch <= '〿' or '＀' <= ch <= '￯')
    other = len(text) - cjk
    return int(cjk * 0.7 + other * 0.25) + 4


def estimate_batch_tokens(system_text: str, user_text: str,
                          n_targets: int) -> dict:


    return {
        "input_tokens": estimate_tokens(system_text) + estimate_tokens(user_text),
        "output_tokens": 180 * n_targets + 400,
    }
