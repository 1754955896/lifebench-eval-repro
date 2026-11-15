"""
人际关系合理性评估:协议 schema 与校验

方案版本 relation-persona-update-v2。三个独立协议:

- relation-library-v1      动态关系库(程序构造与维护,模型只读)
- relation-selector-v1     选择器路由输出(匹配对象 + 分支 + 证据 + 理由)
- relation-writer-v1       受限增量补丁(add/update)

与噪声评估(noise_v2_schema)完全独立,不得混用协议与目录。

异常安全约定: 校验器对任意畸形模型输出(错误类型、嵌套结构)只返回
校验错误,绝不抛出 TypeError/AttributeError;非字符串集合成员一律先
判类型再进入集合运算。
"""
from __future__ import annotations

from dataclasses import dataclass, field

# ── 版本与常量 ────────────────────────────────────────────────────────

# 编排逻辑语义版本: 任何改变路由语义、更新白名单或标签映射的修改
# 都必须人工提升该版本;仅修复 bug 或重构时不提升。
# v2: 新增同日身份冲突一致性校验(day_conflicts)——同一人物 + 同一
# 关系称谓当日早前已判 UNEXPLAINED 时,后续不得降级 EXISTING_KEEP。
PIPELINE_VERSION = "relation-persona-update-v2"

LIB_SCHEMA = "relation-library-v1"
SELECTOR_SCHEMA = "relation-selector-v1"
WRITER_SCHEMA = "relation-writer-v1"
EVENT_RESULT_SCHEMA = "relation-event-result-v1"

MAX_REASON_CHARS = 200
MAX_NOTE_CHARS = 200
MAX_TAG_CHARS = 24

# ── 路由与判定枚举 ─────────────────────────────────────────────────────

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

# 选择器路由 → 案例级判定 / 违规类型 / 是否进入 Writer(仅来源事件)
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

# 事件级判定(第 9 节): nonconform > unknown > conform > not_applicable
EVENT_LABEL_PRIORITY = (FINAL_NONCONFORM, FINAL_UNKNOWN,
                        FINAL_CONFORM, FINAL_NOT_APPLICABLE)

# ── Writer 白名单 ──────────────────────────────────────────────────────

# update/add 补丁允许的顶层字段
WRITER_ALLOWED_KEYS = frozenset({
    "source_case_ids", "operation", "target_person_id", "name",
    "aliases_to_add", "relation_tags_to_add", "social_circle",
    "relation_note", "evidence_event_ids", "unknown_fields",
})
WRITER_UNKNOWN_FIELDS_ALLOWED = frozenset({
    "social_circle", "aliases_to_add", "relation_tags_to_add",
})
WRITER_OPERATIONS = frozenset({"add", "update"})

# 关系标签归一化(程序只做已冻结的同义词归一化,不做模糊合并)
TAG_SYNONYMS = {
    "妈妈": "母亲", "爸爸": "父亲", "好友": "朋友", "闺蜜": "朋友",
    "老师": "导师", "瑜伽教练": "瑜伽老师", "合作伙伴": "合作伙伴",
    "发小": "朋友", "同事": "同事", "同学": "同学", "邻居": "邻居",
    "姐姐": "姐姐", "妹妹": "妹妹", "哥哥": "哥哥", "弟弟": "弟弟",
}


def normalize_tag(tag: str) -> str:
    """已冻结的同义词归一化(仅映射表内项,其余原样保留)"""
    return TAG_SYNONYMS.get(tag, tag)


# ── 通用校验工具 ───────────────────────────────────────────────────────

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
    """集合运算前的类型闸门(防 list/dict 等不可哈希值崩溃)"""
    return isinstance(value, str)


# ── 选择器输出校验 ─────────────────────────────────────────────────────

def validate_selector_response(obj, expected_owner, expected_date,
                               expected_cases: list,
                               library_person_ids: set,
                               allowed_context: dict,
                               day_conflicts: set | None = None) \
        -> RelationValidationResult:
    """
    选择器输出校验(relation-selector-v1)

    - envelope: schema_version / owner / date;
    - items 与输入 case_id 恰好一一对应(不得遗漏/重复/新增);
    - matched_person_id / conflicting_person_id 只能引用当前关系库真实 ID;
    - EXISTING_KEEP/EXISTING_UPDATE 必须给出 matched_person_id;
    - UNEXPLAINED_RELATION_CHANGE 需给出 matched_person_id 或
      conflicting_person_id 之一;
    - 同日一致性: day_conflicts 为 {(person_id, 归一化关系称谓)}——
      同一人物同一称谓当天早前已判为身份冲突时,后续相同事实不得
      降级为 EXISTING_KEEP(校验失败触发修复轮);
    - evidence_event_ids 必须在该案例允许使用的证据范围内;
    - UNKNOWN 必须列出 unknown_fields;ignore 路由必须给出合法
      ignore_reason;其他路由 ignore_reason 必须为 null。

    任何畸形字段(错误类型/嵌套结构)只产生校验错误,不抛出异常。
    """
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
        # 同日一致性: 同一人物 + 归一化关系称谓当天早前已判为身份
        # 冲突(UNEXPLAINED_RELATION_CHANGE)的,后续相同事实不得降级
        # 为 EXISTING_KEEP;校验失败触发一次协议修复轮。
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


# ── Writer 输出校验 ─────────────────────────────────────────────────────

def validate_writer_response(obj, expected_owner, expected_date,
                             expected_cases: list,
                             library_person_ids: set,
                             record_names: dict,
                             existing_names: set,
                             existing_aliases: set) -> RelationValidationResult:
    """
    Writer 输出校验(relation-writer-v1)

    - patches 仅允许白名单字段,operation ∈ {add, update};
    - 每个本批写入案例必须恰好被一个补丁覆盖;
    - 补丁操作必须与 Selector 已确认的路由一致(职责绑定):
      * EXISTING_UPDATE → operation=update 且 target_person_id 等于
        该案例的 matched_person_id;
      * NEW_RELATION → operation=add 且 name 等于该案例人物姓名;
      * update 的 name 必须与库中正式姓名一致(不得改写姓名);
    - 证据绑定: evidence_event_ids 必须是对应案例
      selector_evidence_event_ids 的非空子集(多案例补丁取各案例并集),
      不得引用 Selector 未采用、Writer 未见过正文的事件;
    - 未知单值(social_circle)为 null 时必须出现在 unknown_fields;
    - 标签必须是单一、简短角色(禁止整句/长标签与连接词);
    - add 的姓名/别名与现有对象冲突时拒绝。

    任何畸形字段(错误类型/嵌套结构)只产生校验错误,不抛出异常。
    """
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
        # ── 职责绑定: 补丁操作必须与 Selector 路由一致 ──
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
                    continue  # 非字符串已在上一步记录错误
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
            # 证据绑定: 必须是各来源案例 Selector 证据的并集子集
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
            uf = []  # 类型非法时不再参与后续集合运算
        else:
            illegal_uf = sorted(set(uf) - WRITER_UNKNOWN_FIELDS_ALLOWED)
            if illegal_uf:
                result.add(f"{base}.unknown_fields",
                           f"非法未知字段: {illegal_uf}")
        # 未知单值一致性: social_circle 为 null 时必须出现在 unknown_fields
        if sc is None and "social_circle" not in uf:
            result.add(f"{base}.unknown_fields",
                       "social_circle 为 null(未知)时必须写入 unknown_fields")
        # 防编造: 新增姓名/别名与现有对象姓名/别名冲突时拒绝(add 时)
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
