"""
噪声事件合理性评估：输入校验、日期分组、大纲索引与上下文构造

纯函数模块，不发起网络请求、不调用模型。职责：

1. 时间字符串解析与规范化（支持 YYYY-MM-DD HH:MM:SS至YYYY-MM-DD HH:MM:SS，
   以及民用时间口径下显式的次日时刻标记 24:00:00 / 26:15:00）；
2. 递归建立 event_tree 的 ID 索引（数字/字符串 ID 统一规范化为字符串键）；
3. lineage 审计：把 daily_event 的每条事件分为
   noise_target / source_resolved / source_partial / source_unresolved / invalid_lineage；
4. 确定性构造 persona_core（稳定、可缓存前缀）与 persona section 详情映射；
5. 按自然日分组，构造第 5.4 节的动态日上下文 JSON；
6. token 估算（仅用于拆分决策，非精确计费）。

口径冻结点：
- 跨日事件按最早开始时间所在日期作为归属日（owner_date）；
- 同日结束时间早于开始时间不自动修正为次日，记录 INVALID_DURATION；
- 小时 >= 24 的结束时刻是数据中显式的次日标记（如 24:00、26:15），
  按民用时间口径规范化为次日时刻并在区间上打 end_hour_marker 标记，
  不视为"擅自修正"；原始字符串原样保留供审计。
"""
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

# ── 时间解析 ───────────────────────────────────────────────────────────

TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}):(\d{2}):(\d{2})$")
INTERVAL_RE = re.compile(r"^(.+?)至(.+)$")

# 民用时间: 周几（英文，与设计文档 5.4 示例一致）
WEEKDAYS_EN = ["Monday", "Tuesday", "Wednesday", "Thursday",
               "Friday", "Saturday", "Sunday"]
WEEKDAYS_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

DESCRIPTION_EXCERPT_CHARS = 300  # 来源事件描述的确定性截断长度


def parse_timestamp(ts: str):
    """
    解析 YYYY-MM-DD HH:MM:SS 时间字符串为 naive datetime（中国本地民用时间）

    输入:
    - ts: str - 时间字符串

    输出:
    - (datetime, bool): 解析结果与是否发生"小时>=24 次日标记"规范化

    异常:
    - ValueError: 无法解析时抛出（调用方记录为输入错误）

    说明: 不做 UTC 转换，不依赖宿主机时区；小时 >= 24 表示显式的次日时刻
    （如 24:00:00 = 次日 00:00:00，26:15:00 = 次日 02:15:00），
    与"结束日期明确为次日"等价，因此按合法跨日处理并打标记。
    """
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
    """
    解析 "YYYY-MM-DD HH:MM:SS至YYYY-MM-DD HH:MM:SS" 时间段

    输入:
    - date_str: str - 原始时间段字符串

    输出:
    - Interval

    跨午夜口径(v2.6):
    - 起止日期相同且结束钟点早于开始钟点(如 22:30 至 00:10)按"隐式
      跨午夜"将结束时间解释为次日,并打 implicit_cross_midnight 标记;
      原始字符串保留供审计,不再直接判 INVALID_DURATION;
    - 结束时刻等于开始时刻仍保持零时长,不自动增加一天;
    - 已明确写成次日日期或使用 24 点以上写法(end_hour_marker)保持原逻辑。

    异常:
    - ValueError: 格式无法解析
    """
    if not isinstance(date_str, str):
        # 非字符串 date 元素属于输入错误，必须转成 ValueError 而非 TypeError
        raise ValueError(f"时间段必须是字符串: {date_str!r}")
    m = INTERVAL_RE.match(date_str)
    if not m:
        raise ValueError(f"时间段缺少 '至' 分隔符: {date_str!r}")
    start_raw, end_raw = m.group(1), m.group(2)
    start, _ = parse_timestamp(start_raw)
    end, end_marker = parse_timestamp(end_raw)
    implicit_cross = False
    if end.date() == start.date() and end < start and not end_marker:
        # 隐式跨午夜: 同日结束钟点更早 → 结束时间解释为次日
        end = end + timedelta(days=1)
        implicit_cross = True
    return Interval(start=start, end=end, start_raw=start_raw,
                    end_raw=end_raw, end_hour_marker=end_marker,
                    implicit_cross_midnight=implicit_cross)


def parse_event_intervals(event) -> list:
    """
    把一条 daily_event 的 date 数组解析为按开始时间排序的 Interval 列表

    输入:
    - event: dict - daily_event 事件

    输出:
    - list[Interval]

    异常:
    - ValueError: 任一 date 字符串无法解析（调用方记录 INPUT_ERROR_UNPARSEABLE_DATE）
    """
    intervals = [parse_interval(d) for d in event.get("date") or []]
    intervals.sort(key=lambda iv: iv.start)
    return intervals


def owner_date_of(intervals: list) -> str:
    """
    事件归属日 = 最早开始时间所在日期（YYYY-MM-DD 字符串）

    输入:
    - intervals: list[Interval] - 解析后的时间段（非空）

    输出:
    - str: 归属日
    """
    return intervals[0].start.strftime("%Y-%m-%d")


def weekday_en(date_str: str) -> str:
    """
    由日期字符串直接计算星期（不依赖宿主机时区）

    输入:
    - date_str: str - YYYY-MM-DD

    输出:
    - str: 英文星期名，如 "Sunday"
    """
    return WEEKDAYS_EN[datetime.strptime(date_str, "%Y-%m-%d").weekday()]


def weekday_cn(date_str: str) -> str:
    """由日期字符串直接计算中文星期（用于与 daily_draft.week 对照）"""
    return WEEKDAYS_CN[datetime.strptime(date_str, "%Y-%m-%d").weekday()]


# ── event_tree 大纲索引 ────────────────────────────────────────────────

class OutlineIndex:
    """
    event_tree 的递归 ID 索引

    - id -> node 摘要（原始 event_id 值保留在 node["event_id"]，类型保留在
      node["event_id_type"]，键统一规范化为字符串）；
    - id -> 祖先链摘要（自下而上），用于证据检索；
    - 父 ID 推断: 形如 "13-4-1" 的 ID 剥离最后一段 "-N" 得到父键，
      仅当剥离结果真实存在于索引中才认定父子关系。
    """

    def __init__(self):
        self.nodes = {}        # str -> dict 节点摘要
        self.ancestors = {}    # str -> list[dict] 祖先链（自下而上）
        self.parent = {}       # str -> str 或 None

    def add(self, key: str, node: dict, summary: dict, parent_key: str | None):
        self.nodes[key] = node
        self.nodes[key]["_summary"] = summary
        self.parent[key] = parent_key

    def resolve(self, atomic_id: str) -> dict | None:
        """按字符串键查询节点摘要；不存在返回 None"""
        return self.nodes.get(str(atomic_id))

    def summarize(self, key: str, excerpt_chars: int = DESCRIPTION_EXCERPT_CHARS):
        """返回节点摘要（不含内部 _summary 元数据）"""
        node = self.nodes.get(key)
        if node is None:
            return None
        summary = dict(node["_summary"])
        return summary

    def build_ancestors(self):
        """索引建立后填充祖先链摘要"""
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
    """确定性提取大纲节点摘要"""
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
    """
    递归建立 event_tree 大纲 ID 索引

    输入:
    - tree: list - event_tree.json 顶层数组

    输出:
    - OutlineIndex
    """
    index = OutlineIndex()

    def walk(node, parent_key):
        nid = node.get("event_id")
        if nid is None or isinstance(nid, (list, dict)):
            # 节点无合法 ID：仍然递归子节点，但不注册自身
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

    # 补一层按命名规则推断的父关系（仅当真实存在时）：
    # "13-4-1" 的父键 "13-4" 若在索引中则建立父子链接；
    # 顶层节点由 walk 注册时 parent 为 None。
    for key in list(index.parent):
        if index.parent[key] is None and "-" in key:
            candidate = key.rsplit("-", 1)[0]
            if candidate in index.nodes:
                index.parent[key] = candidate
    index.build_ancestors()
    return index


# ── lineage 审计 ───────────────────────────────────────────────────────

def lineage_role_of(event_check, atomic_ids, outline: OutlineIndex):
    """
    判定一条事件的 lineage 角色

    输入:
    - event_check: InputEventCheck - 结构校验结论
    - atomic_ids: list[str] - atomic_id 内容（结构合法时）
    - outline: OutlineIndex - 大纲索引

    输出:
    - (role, resolved: list[str], missing: list[str])

    规则: 只有 atomic_id == [] 才是噪声；非空 ID 无论能否解析都保持来源事件身份。
    """
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
    """
    生成 lineage 审计报告

    输入:
    - daily_events: list - daily_event.json 内容
    - outline: OutlineIndex - 大纲索引

    输出:
    - dict: {
        "counts": {...按角色计数...},
        "atomic_id_stats": {"distinct": n, "missing_in_outline": n,
                            "events_with_missing_ids": n},
        "events": [{event_id, role, resolved, missing, errors} ...],
        "input_errors": [{event_id, codes} ...],
      }
    """
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
    """内部: 引用 schema 模块的结构校验（延迟导入避免循环）"""
    from tools.evaluation_common.input_schema import check_daily_event
    return check_daily_event(event)


# ── persona 分层画像 ───────────────────────────────────────────────────

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
    """
    确定性构造 persona_core（稳定核心画像）

    输入:
    - persona: dict - persona.json 内容

    输出:
    - dict: 字段顺序固定、内容为原始字段或确定性抽取，序列化字节稳定

    说明: 不用 LLM 摘要；完整原始字段全部可通过 persona_detail 查询取得。
    关系只保留索引（姓名、关系、昵称、社交圈），详细描述留在详情库。
    """
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
    """
    按原顺序确定性展平 persona.relation（嵌套数组）

    输入:
    - persona: dict - persona.json

    输出:
    - list[dict]: 展平后的人际关系项（附带内部 _pointer 定位）
    """
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
    """
    按固定映射取出 persona 详情 section 的原始字段内容

    输入:
    - persona: dict - persona.json
    - sections: list[str] - 合法 section 枚举
    - relationship_names: list[str] | None - 可选关系人姓名过滤；
      缺省或为空时返回全部关系详情（与协议"姓名可选"一致）

    输出:
    - list[dict]: evidence 条目 [{source, pointer, value}]，
      同一原始字段出现在多个 section 时按字段名去重（只返回一次，
      pointer 指向最先请求的 section），顺序确定
    """
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
        # 关系人明细: 请求了 relationships section 时返回；
        # 指定姓名则过滤，未指定则返回全部
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


# ── 日分组与时间线 ─────────────────────────────────────────────────────

def group_events_by_day(parsed_events: list) -> dict:
    """
    按自然日把事件放入时间线：跨日事件出现在所有覆盖到的自然日

    输入:
    - parsed_events: list[dict] - 每条包含 {event, intervals, role, resolved,
      missing, errors}（由 prepare_events 产出）

    输出:
    - dict[date_str] -> list[timeline 条目]
    """
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
                # 覆盖所有自然日（含末尾当天）
                cur = d + timedelta(days=1)
                while cur <= d2:
                    covered.add(cur)
                    cur += timedelta(days=1)
        for d in sorted(covered):
            days[d.strftime("%Y-%m-%d")].append(pe)
    return days


def prepare_events(daily_events: list, outline: OutlineIndex) -> list:
    """
    解析每条事件的时间段并判定 lineage 角色

    输入:
    - daily_events: list - daily_event.json
    - outline: OutlineIndex - 大纲索引

    输出:
    - list[dict]: {
        event, intervals, role, resolved, missing,
        owner_date (仅结构合法的事件), errors
      }
      时间段解析失败的事件 role 保持其 lineage 角色但 errors 含
      INPUT_ERROR_UNPARSEABLE_DATE；噪声目标的时间段解析失败属于输入错误，
      不生成语义标签。
    """
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
    """
    加载 daily_draft（可选，只用于天气/节假日/星期等元数据）

    输入:
    - draft_file: str | None

    输出:
    - dict: {date_str: day_record}
    """
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


# ── token 估算 ─────────────────────────────────────────────────────────

def estimate_tokens(text: str) -> int:
    """
    粗略 token 估算（仅用于拆分决策，非精确计费）

    输入:
    - text: str - 文本

    输出:
    - int: 估算 token 数

    口径: 中日韩字符按 ~0.7 token/字符，其余按 ~0.25 token/字符，
    JSON 标点按 ascii 计。DeepSeek 分词器下为保守上界。
    """
    cjk = sum(1 for ch in text if '一' <= ch <= '鿿'
              or '　' <= ch <= '〿' or '＀' <= ch <= '￯')
    other = len(text) - cjk
    return int(cjk * 0.7 + other * 0.25) + 4


def estimate_batch_tokens(system_text: str, user_text: str,
                          n_targets: int) -> dict:
    """
    估算单批次输入/输出 token

    输入:
    - system_text / user_text: str - 组装后的系统与用户文本
    - n_targets: int - 该批次 target 数量

    输出:
    - dict: {"input_tokens": int, "output_tokens": int}
    """
    return {
        "input_tokens": estimate_tokens(system_text) + estimate_tokens(user_text),
        "output_tokens": 180 * n_targets + 400,
    }
