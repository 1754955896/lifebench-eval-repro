"""This module validates evaluation data."""

from __future__ import annotations

from dataclasses import dataclass, field


PIPELINE_VERSION = "relation-persona-update-v2"

LIB_SCHEMA = "relation-library-v1"
SELECTOR_SCHEMA = "relation-selector-v1"
WRITER_SCHEMA = "relation-writer-v1"
EVENT_RESULT_SCHEMA = "relation-event-result-v1"

MAX_REASON_CHARS = 200
MAX_NOTE_CHARS = 200
MAX_TAG_CHARS = 24


ROUTE_EXISTING_KEEP = "EXISTING_KEEP"
ROUTE_EXISTING_UPDATE = "EXISTING_UPDATE"
ROUTE_NEW_RELATION = "NEW_RELATION"
ROUTE_TEMPORARY_CONTACT = "TEMPORARY_CONTACT"
ROUTE_IGNORE = "IGNORE"
ROUTE_SUDDEN = "SUDDEN_FAMILIAR_CONFLICT"
ROUTE_UNEXPLAINED = "UNEXPLAINED_RELATION_CHANGE"
ROUTE_UNKNOWN = "UNKNOWN"

ROUTES = frozenset({
    ROUTE_EXISTING_KEEP, ROUTE_EXISTING_UPDATE, ROUTE_NEW_RELATION,
    ROUTE_TEMPORARY_CONTACT, ROUTE_IGNORE, ROUTE_SUDDEN,
    ROUTE_UNEXPLAINED, ROUTE_UNKNOWN,
})

IGNORE_REASONS = frozenset({
    "self", "group", "mentioned_only", "no_direct_interaction",
    "not_a_person", "other",
})

FINAL_CONFORM = "conform"
FINAL_NONCONFORM = "nonconform"
FINAL_UNKNOWN = "unknown"
FINAL_NOT_APPLICABLE = "not_applicable"
FINAL_LABELS = frozenset({FINAL_CONFORM, FINAL_NONCONFORM,
                          FINAL_UNKNOWN, FINAL_NOT_APPLICABLE})

VIOLATION_SUDDEN = "SUDDEN_FAMILIAR_PERSON"
VIOLATION_UNEXPLAINED = "UNEXPLAINED_RELATION_CHANGE"


ROUTE_SEMANTICS = {
    ROUTE_EXISTING_KEEP: (FINAL_CONFORM, None, False),
    ROUTE_EXISTING_UPDATE: (FINAL_CONFORM, None, True),
    ROUTE_NEW_RELATION: (FINAL_CONFORM, None, True),
    ROUTE_TEMPORARY_CONTACT: (FINAL_CONFORM, None, False),
    ROUTE_IGNORE: (FINAL_NOT_APPLICABLE, None, False),
    ROUTE_SUDDEN: (FINAL_NONCONFORM, VIOLATION_SUDDEN, False),
    ROUTE_UNEXPLAINED: (FINAL_NONCONFORM, VIOLATION_UNEXPLAINED, False),
    ROUTE_UNKNOWN: (FINAL_UNKNOWN, None, False),
}


EVENT_LABEL_PRIORITY = (FINAL_NONCONFORM, FINAL_UNKNOWN,
                        FINAL_CONFORM, FINAL_NOT_APPLICABLE)


WRITER_ALLOWED_KEYS = frozenset({
    "source_case_ids", "operation", "target_person_id", "name",
    "aliases_to_add", "relation_tags_to_add", "social_circle",
    "relation_note", "evidence_event_ids", "unknown_fields",
})
WRITER_UNKNOWN_FIELDS_ALLOWED = frozenset({
    "social_circle", "aliases_to_add", "relation_tags_to_add",
})
WRITER_OPERATIONS = frozenset({"add", "update"})


TAG_SYNONYMS = {
    "妈妈": "母亲", "爸爸": "父亲", "好友": "朋友", "闺蜜": "朋友",
    "老师": "导师", "瑜伽教练": "瑜伽老师", "合作伙伴": "合作伙伴",
    "发小": "朋友", "同事": "同事", "同学": "同学", "邻居": "邻居",
    "姐姐": "姐姐", "妹妹": "妹妹", "哥哥": "哥哥", "弟弟": "弟弟",
}


def normalize_tag(tag: str) -> str:

    return TAG_SYNONYMS.get(tag, tag)


@dataclass
class RelationValidationResult:
    ok: bool
    errors: list = field(default_factory=list)

    def add(self, path: str, msg: str):
        self.ok = False
        self.errors.append({"pointer": path, "message": msg})
        return self


def _check_nonempty_str(result, base, value, field, max_chars=None) -> bool:
    if not isinstance(value, str) or not value.strip():
        result.add(f"{base}.{field}", "必须是非空字符串")
        return False
    if max_chars and len(value) > max_chars:
        result.add(f"{base}.{field}", f"长度 {len(value)} 超过上限 {max_chars}")
    return True


def _is_str(value) -> bool:

    return isinstance(value, str)


def validate_selector_response(obj, expected_owner, expected_date,
                               expected_cases: list,
                               library_person_ids: set,
                               allowed_context: dict,
                               day_conflicts: set | None = None) \
        -> RelationValidationResult:


    result = RelationValidationResult(ok=True)
    if not isinstance(obj, dict):
        result.add("$", "输出必须是 JSON 对象")
        return result
    if obj.get("schema_version") != SELECTOR_SCHEMA:
        result.add("$.schema_version", f"必须为 {SELECTOR_SCHEMA!r}")
    if obj.get("owner") != expected_owner:
        result.add("$.owner", f"必须为 {expected_owner!r}")
    if obj.get("date") != expected_date:
        result.add("$.date", f"必须为 {expected_date!r}")
    for key in ("patches", "final_label", "label"):
        if key in obj:
            result.add(f"$.{key}", f"选择器禁止输出 {key}")

    expected_ids = {c["case_id"] for c in expected_cases}
    expected_by_id = {c["case_id"]: c for c in expected_cases}
    items = obj.get("items")
    if not isinstance(items, list):
        result.add("$.items", "必须是数组")
        return result
    seen = set()
    for i, item in enumerate(items):
        base = f"$.items[{i}]"
        if not isinstance(item, dict):
            result.add(base, "必须是对象")
            continue
        cid = item.get("case_id")
        if not isinstance(cid, str) or not cid:
            result.add(f"{base}.case_id", "必须是非空字符串")
            continue
        if cid in seen:
            result.add(f"{base}.case_id", f"重复的 case_id {cid!r}")
        seen.add(cid)
        if cid not in expected_ids:
            result.add(f"{base}.case_id", f"{cid!r} 不是输入的案例")
            continue
        case = expected_by_id[cid]
        allowed = set(allowed_context.get(cid, []))
        evidence = item.get("evidence_event_ids")
        if not isinstance(evidence, list) or not evidence or not all(
                _is_str(x) and x for x in evidence):
            result.add(f"{base}.evidence_event_ids", "必须是非空字符串数组")
        else:
            illegal = sorted(set(evidence) - allowed)
            if illegal:
                result.add(f"{base}.evidence_event_ids",
                           f"引用不允许使用的证据事件: {illegal}")
        route = item.get("route")
        if not _is_str(route) or route not in ROUTES:
            result.add(f"{base}.route", f"必须为 {'/'.join(sorted(ROUTES))}")
            route = None
        for id_field in ("matched_person_id", "conflicting_person_id"):
            val = item.get(id_field)
            if val is not None and (not _is_str(val)
                                    or val not in library_person_ids):
                result.add(f"{base}.{id_field}",
                           f"{val!r} 不是当前关系库中的真实 person_id")
        if route in (ROUTE_EXISTING_KEEP, ROUTE_EXISTING_UPDATE) \
                and item.get("matched_person_id") is None:
            result.add(f"{base}.matched_person_id",
                       f"{route} 必须给出 matched_person_id")


        if route == ROUTE_EXISTING_KEEP and day_conflicts \
                and item.get("matched_person_id") is not None:
            rel = normalize_tag(case.get("observed_relation") or "")
            key = (item["matched_person_id"], rel)
            if key in day_conflicts:
                result.add(
                    f"{base}.route",
                    f"当日早前已判定 {item['matched_person_id']} 的"
                    f"{case.get('observed_relation')!r} 为身份冲突"
                    "(UNEXPLAINED_RELATION_CHANGE),相同事实不得降级为 "
                    "EXISTING_KEEP;若无新的明确解释请改判为 "
                    "UNEXPLAINED_RELATION_CHANGE")
        if route == ROUTE_UNEXPLAINED and (
                item.get("matched_person_id") is None
                and item.get("conflicting_person_id") is None):
            result.add(base, f"{route} 必须给出 matched_person_id 或 "
                       "conflicting_person_id")
        if route in (ROUTE_NEW_RELATION, ROUTE_TEMPORARY_CONTACT,
                     ROUTE_SUDDEN, ROUTE_IGNORE, ROUTE_UNKNOWN):
            if item.get("matched_person_id") is not None:
                result.add(f"{base}.matched_person_id",
                           f"{route} 通常不得给出 matched_person_id")
        if route == ROUTE_IGNORE:
            ig = item.get("ignore_reason")
            if not _is_str(ig) or ig not in IGNORE_REASONS:
                result.add(f"{base}.ignore_reason",
                           f"必须为 {'/'.join(sorted(IGNORE_REASONS))}")
        elif item.get("ignore_reason") is not None:
            result.add(f"{base}.ignore_reason",
                       f"{route} 的 ignore_reason 必须为 null")
        if route == ROUTE_UNKNOWN:
            uf = item.get("unknown_fields")
            if not isinstance(uf, list) or not uf \
                    or not all(_is_str(x) and x for x in uf):
                result.add(f"{base}.unknown_fields",
                           "UNKNOWN 必须列出非空 unknown_fields")
        _check_nonempty_str(result, base, item.get("reason"), "reason",
                            MAX_REASON_CHARS)
    missing = expected_ids - seen
    if missing:
        result.add("$.items", f"遗漏案例: {sorted(missing)}")
    return result


def validate_writer_response(obj, expected_owner, expected_date,
                             expected_cases: list,
                             library_person_ids: set,
                             record_names: dict,
                             existing_names: set,
                             existing_aliases: set) -> RelationValidationResult:


    result = RelationValidationResult(ok=True)
    if not isinstance(obj, dict):
        result.add("$", "输出必须是 JSON 对象")
        return result
    if obj.get("schema_version") != WRITER_SCHEMA:
        result.add("$.schema_version", f"必须为 {WRITER_SCHEMA!r}")
    if obj.get("owner") != expected_owner:
        result.add("$.owner", f"必须为 {expected_owner!r}")
    if obj.get("date") != expected_date:
        result.add("$.date", f"必须为 {expected_date!r}")
    for key in ("items", "route", "final_label"):
        if key in obj:
            result.add(f"$.{key}", f"Writer 禁止输出 {key}")

    expected_ids = {c["case_id"] for c in expected_cases}
    expected_by_id = {c["case_id"]: c for c in expected_cases}
    patches = obj.get("patches")
    if not isinstance(patches, list) or not patches:
        result.add("$.patches", "必须是非空 patches 数组")
        return result
    seen_sources = set()
    for i, p in enumerate(patches):
        base = f"$.patches[{i}]"
        if not isinstance(p, dict):
            result.add(base, "必须是对象")
            continue
        for key in p:
            if key not in WRITER_ALLOWED_KEYS:
                result.add(f"{base}.{key}", f"白名单之外字段 {key}")
        op = p.get("operation")
        if not _is_str(op) or op not in WRITER_OPERATIONS:
            result.add(f"{base}.operation",
                       f"必须为 {'/'.join(sorted(WRITER_OPERATIONS))}")
            continue
        sources = p.get("source_case_ids")
        if not isinstance(sources, list) or not sources:
            result.add(f"{base}.source_case_ids", "必须是非空数组")
            sources = []
        else:
            for cid in sources:
                if not _is_str(cid):
                    result.add(f"{base}.source_case_ids",
                               f"数组元素必须是字符串,收到 {type(cid).__name__}")
                    continue
                if cid not in expected_ids:
                    result.add(f"{base}.source_case_ids",
                               f"{cid!r} 不是本批写入案例")
                if cid in seen_sources:
                    result.add(f"{base}.source_case_ids",
                               f"案例 {cid!r} 已被其他补丁使用")
                seen_sources.add(cid)
        name = p.get("name")
        name_ok = _check_nonempty_str(result, base, name, "name")
        tid = p.get("target_person_id")
        if op == "add" and tid is not None:
            result.add(f"{base}.target_person_id", "add 必须为 null")
        if op == "update":
            if not _is_str(tid) or tid not in library_person_ids:
                result.add(f"{base}.target_person_id",
                           f"{tid!r} 不是当前关系库中的真实 person_id")
            elif name_ok and tid in record_names \
                    and name.strip() != record_names[tid]:
                result.add(f"{base}.name",
                           f"update 不得改写正式姓名: "
                           f"{name!r} != {record_names[tid]!r}")

        for cid in sources:
            if not _is_str(cid) or cid not in expected_by_id:
                continue
            case = expected_by_id[cid]
            route = case.get("selector_route")
            if route == ROUTE_EXISTING_UPDATE:
                if op != "update":
                    result.add(f"{base}.operation",
                               f"案例 {cid} 的 Selector 路由为 "
                               f"{route},必须生成 update 补丁")
                elif _is_str(tid) and _is_str(case.get("matched_person_id")) \
                        and tid != case.get("matched_person_id"):
                    result.add(f"{base}.target_person_id",
                               f"必须等于案例 {cid} 的 matched_person_id "
                               f"{case.get('matched_person_id')!r}")
            elif route == ROUTE_NEW_RELATION:
                if op != "add":
                    result.add(f"{base}.operation",
                               f"案例 {cid} 的 Selector 路由为 "
                               f"{route},必须生成 add 补丁")
                elif name_ok and name.strip() != (case.get("candidate_key")
                                                  or "").strip():
                    result.add(f"{base}.name",
                               f"add 姓名必须与案例 {cid} 人物一致: "
                               f"{name!r} != {case.get('candidate_key')!r}")
            else:
                result.add(f"{base}.source_case_ids",
                           f"案例 {cid} 缺少合法的 Selector 路由,"
                           "Writer 只能处理 EXISTING_UPDATE/NEW_RELATION")
        for field in ("aliases_to_add", "relation_tags_to_add"):
            vals = p.get(field)
            if vals is None:
                result.add(f"{base}.{field}", "必须是数组(未知时为空数组)")
                continue
            if not isinstance(vals, list):
                result.add(f"{base}.{field}", "必须是数组")
                continue
            for k, v in enumerate(vals):
                if not _is_str(v) or not v.strip():
                    result.add(f"{base}.{field}[{k}]", "必须是非空字符串")
        tags = p.get("relation_tags_to_add")
        if isinstance(tags, list):
            for k, tag in enumerate(tags):
                if not _is_str(tag):
                    continue
                if len(tag) > MAX_TAG_CHARS:
                    result.add(f"{base}.relation_tags_to_add[{k}]",
                               f"标签过长({len(tag)}>{MAX_TAG_CHARS}),"
                               "必须是单一简短角色")
                for conj in ("兼", "并", "且", "、", "，", "与", "和"):
                    if conj in tag:
                        result.add(
                            f"{base}.relation_tags_to_add[{k}]",
                            f"标签含连接词 {conj!r},不得把整句写成一个标签")
        sc = p.get("social_circle")
        if sc is not None and not (isinstance(sc, str) and sc.strip()):
            result.add(f"{base}.social_circle", "必须是字符串或 null")
        _check_nonempty_str(result, base, p.get("relation_note"),
                            "relation_note", MAX_NOTE_CHARS)
        evidence = p.get("evidence_event_ids")
        if not isinstance(evidence, list) or not evidence or not all(
                _is_str(x) and x for x in evidence):
            result.add(f"{base}.evidence_event_ids", "必须是非空字符串数组")
        else:

            allowed = set()
            for cid in sources:
                if not _is_str(cid) or cid not in expected_by_id:
                    continue
                sev = expected_by_id[cid].get("selector_evidence_event_ids")
                if not isinstance(sev, list) or not sev \
                        or not all(_is_str(x) for x in sev):
                    result.add(f"{base}.source_case_ids",
                               f"案例 {cid} 缺少 selector_evidence_event_ids")
                    continue
                allowed |= set(sev)
            illegal = sorted(set(evidence) - allowed)
            if illegal:
                result.add(f"{base}.evidence_event_ids",
                           f"引用非 Selector 证据事件(其正文未提供给 "
                           f"Writer): {illegal}")
        uf = p.get("unknown_fields")
        if not isinstance(uf, list) or not all(_is_str(x) for x in uf):
            result.add(f"{base}.unknown_fields", "必须是字符串数组")
            uf = []
        else:
            illegal_uf = sorted(set(uf) - WRITER_UNKNOWN_FIELDS_ALLOWED)
            if illegal_uf:
                result.add(f"{base}.unknown_fields",
                           f"非法未知字段: {illegal_uf}")

        if sc is None and "social_circle" not in uf:
            result.add(f"{base}.unknown_fields",
                       "social_circle 为 null(未知)时必须写入 unknown_fields")

        if op == "add" and name_ok:
            if name in existing_names or name in existing_aliases:
                result.add(f"{base}.name",
                           f"{name!r} 与现有对象姓名/别名冲突")
            aliases = p.get("aliases_to_add")
            if isinstance(aliases, list):
                for alias in aliases:
                    if not _is_str(alias):
                        continue
                    if alias.strip() in existing_names \
                            or alias.strip() in existing_aliases:
                        result.add(f"{base}.aliases_to_add",
                                   f"新增别名 {alias!r} 与现有对象冲突")
    missing = expected_ids - seen_sources
    if missing:
        result.add("$.patches", f"遗漏写入案例: {sorted(missing)}")
    return result
