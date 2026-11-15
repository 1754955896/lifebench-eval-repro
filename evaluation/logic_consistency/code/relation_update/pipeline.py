"""
人际关系合理性评估:逐日状态推进管线

- 同一人物按日期严格串行(日期之间存在状态依赖);
- 选择器按事件时间顺序逐个调用: 每次请求只含日初关系库、当前事件
  正文与当天开始时间不晚于当前事件的来源事件正文(未来事件绝不
  进入同一次请求);
- 每天使用日初关系库作为稳定快照;当天的 KEEP 交集维护与通过校验
  的补丁在日末统一提交;
- 日级事务: 补丁/事件结果/案例日志与 day commit marker 原子提交,
  续跑只重放已提交事务,未提交尾段整体截断;
- 补丁操作与选择器路由强绑定(Writer 不得重新判断身份);
- 补丁失败不伪造结果: 当日中止、--resume 可重试;
- 事件级最终判定: nonconform > unknown > conform > not_applicable;
- --start-date 之前的日期作为 warm-up 完整推进状态(模型调用照常),
  其结果标记 warmup 且不纳入统计。
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from collections import defaultdict
from datetime import datetime

from tools.evaluation_common.context import build_outline_index
from tools.evaluation_common.model_adapter import BudgetClient, BudgetExceeded
from tools.evaluation_common.signatures import sha256_file, stable_dumps
from tools.evaluation_common.storage import PersonOutputStore

from evaluation.logic_consistency.code.relation_update.context import (
    apply_add_patch, apply_keep_maintenance, apply_update_patch,
    build_day_plan, build_initial_relation_library, check_patch_conflicts,
    group_relation_days, prepare_relation_events, rebuild_library_from_updates,
)
from evaluation.logic_consistency.code.relation_update.schema import (
    EVENT_LABEL_PRIORITY, EVENT_RESULT_SCHEMA, FINAL_CONFORM,
    FINAL_NONCONFORM, FINAL_NOT_APPLICABLE, FINAL_UNKNOWN,
    PIPELINE_VERSION, ROUTE_EXISTING_KEEP, ROUTE_SEMANTICS,
    ROUTE_UNEXPLAINED, VIOLATION_SUDDEN, VIOLATION_UNEXPLAINED,
    normalize_tag,
)
from evaluation.logic_consistency.code.relation_update.selector import (
    RelationSelector, SelectorPromptManager, plan_case_chunks,
)
from evaluation.logic_consistency.code.relation_update.writer import RelationWriter

logger = logging.getLogger("relation_eval")

DEFAULT_OUTPUT_ROOT = "output/relation_update"
COMMIT_FILE = "day_commits.jsonl"

# 事务内文件: 续跑时未提交(无 commit marker)的尾段按日期整体截断
TRANSACTIONAL_FILES = ("relation_events.jsonl", "relation_updates.jsonl",
                       "relation_cases.jsonl", "unknown_cases.jsonl",
                       "input_error_events.jsonl")


def _manifest(person, opts, signature, noise_count, dry_run,
              dry_run_estimate=None, completed_from_resume=0,
              warmup_days=None) -> dict:
    return {
        "person": person,
        "pipeline_version": PIPELINE_VERSION,
        "prompt_version": opts["prompts"].version,
        "model": opts.get("model"),
        "input_hashes": opts.get("input_hashes", {}),
        "initial_library_hash": opts.get("initial_library_hash"),
        "evaluation_signature": signature,
        "max_context_tokens": opts.get("max_context_tokens", 24000),
        "max_output_tokens": opts.get("max_output_tokens", 8192),
        "max_model_calls": opts.get("max_model_calls"),
        "max_retries": opts.get("max_retries", 3),
        "window": {"start_date": opts.get("start_date"),
                   "end_date": opts.get("end_date")},
        "dry_run": dry_run,
        "dry_run_estimate": dry_run_estimate,
        "completed_from_resume": completed_from_resume,
        "warmup_days": warmup_days or [],
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }


# ── 事务与续跑状态 ──────────────────────────────────────────────────────

def _load_committed_dates(store, signature) -> set:
    """已提交(有 commit marker)的日期集合,按签名过滤"""
    dates = set()
    path = store._path(COMMIT_FILE)
    if not os.path.exists(path):
        return dates
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("evaluation_signature") == signature \
                    and isinstance(rec.get("date"), str):
                dates.add(rec["date"])
    return dates


def _truncate_uncommitted(store, committed: set):
    """
    事务文件截断: 只保留已提交日期的记录(崩溃尾段整体丢弃)。
    诊断日志 errors.jsonl 中,未提交日期的 input_error 记录是同一
    事务的崩溃残留,一并清除;其余错误类型(API 失败等)是真实运行
    历史,保留。
    """
    for name in TRANSACTIONAL_FILES:
        path = store._path(name)
        if not os.path.exists(path):
            continue
        keep = []
        dirty = False
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                raw = line
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    dirty = True
                    continue
                if rec.get("date") in committed:
                    keep.append(raw)
                else:
                    dirty = True
        if dirty:
            with open(path, "w", encoding="utf-8") as f:
                f.writelines(keep)
    # errors.jsonl 清理: 未提交日期的 input_error 记录
    path = store._path("errors.jsonl")
    if os.path.exists(path):
        keep = []
        dirty = False
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                raw = line
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    dirty = True
                    continue
                if (rec.get("error_type") == "input_error"
                        and isinstance(rec.get("date"), str)
                        and rec["date"] not in committed):
                    dirty = True
                    continue
                keep.append(raw)
        if dirty:
            with open(path, "w", encoding="utf-8") as f:
                f.writelines(keep)


def _load_completed(store, signature) -> set:
    """续跑: 已完成的 (date, event_id) 集合(按签名过滤)"""
    completed = set()
    path = store._path("relation_events.jsonl")
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
            if rec.get("evaluation_signature") != signature:
                continue
            if rec.get("final_label") in (FINAL_CONFORM, FINAL_NONCONFORM,
                                          FINAL_UNKNOWN,
                                          FINAL_NOT_APPLICABLE):
                completed.add((rec.get("date"), rec.get("event_id")))
    return completed


def _load_updates(store, signature) -> list:
    """续跑: 重放用更新日志(按签名过滤,只含已提交日期)"""
    out = []
    path = store._path("relation_updates.jsonl")
    if not os.path.exists(path):
        return out
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("evaluation_signature") == signature:
                    out.append(rec)
    return out


def _load_keep_events(store, signature) -> list:
    """
    续跑: 从已完成事件的 EXISTING_KEEP 案例重放 last_seen /
    interaction_days 维护(与更新日志合并后可确定性重建关系库)。
    """
    out = []
    path = store._path("relation_events.jsonl")
    if not os.path.exists(path):
        return out
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("evaluation_signature") != signature:
                continue
            for cd in rec.get("case_details") or []:
                if (cd.get("route") == ROUTE_EXISTING_KEEP
                        and cd.get("event_origin") == "source"
                        and cd.get("person_id")):
                    out.append((cd["person_id"], rec.get("date")))
    return out


def _has_results(store) -> bool:
    path = store._path("relation_events.jsonl")
    return os.path.exists(path) and os.path.getsize(path) > 0


def _count_call_records(store, signature) -> int:
    """已落盘的真实请求数(按签名过滤,含失败调用): 续跑会话的
    模型调用预算起点,保证授权上限在 resume 之间保持全局口径"""
    count = 0
    path = store._path("selector_traces.jsonl")
    if not os.path.exists(path):
        return 0
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("call_id") is not None \
                    and rec.get("evaluation_signature") == signature:
                count += 1
    return count


def _check_manifest(store, signature) -> bool:
    path = store._path("run_manifest.json")
    if not os.path.exists(path):
        # 已有结果但 manifest 缺失 → 无法验证签名,拒绝续跑(防混签)
        return not _has_results(store)
    try:
        with open(path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, ValueError):
        return False
    return manifest.get("evaluation_signature") == signature


def _append_input_errors(store, parse_errors, person_name, prompts,
                         opts, signature):
    for pe in parse_errors:
        store.append_jsonl("errors.jsonl", {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "person": person_name, "date": None,
            "warmup": False,
            "error_type": "input_error",
            "event_ids": [str(pe["event"]["event_id"])],
            "message": "事件时间段无法解析",
            "pipeline_version": PIPELINE_VERSION,
            "prompt_version": prompts.version,
            "model": opts.get("model"),
            "evaluation_signature": signature,
        })


def _load_input_error_events(store, signature) -> set:
    """已按事务提交的输入错误事件 (date, event_id) 终止状态集合"""
    out = set()
    path = store._path("input_error_events.jsonl")
    if not os.path.exists(path):
        return out
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("evaluation_signature") == signature \
                    and isinstance(rec.get("date"), str) \
                    and isinstance(rec.get("event_id"), str):
                out.add((rec["date"], rec["event_id"]))
    return out


def _read_jsonl(store, name, signature) -> list:
    out = []
    path = store._path(name)
    if not os.path.exists(path):
        return out
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("evaluation_signature") == signature:
                    out.append(rec)
    return out


# ── 主流程 ──────────────────────────────────────────────────────────────

def evaluate_person_relations(person_name: str, event_file: str,
                              outline_file: str, persona_file: str,
                              out_dir: str, opts: dict,
                              client=None, prompts=None) -> dict:
    """
    单人物逐日关系评估(串行)

    opts: model / max_context_tokens / max_output_tokens / start_date /
    end_date / resume / dry_run(不同人物并行由 CLI 处理,本函数内不并行)
    """
    store = PersonOutputStore(out_dir)
    with open(persona_file, "r", encoding="utf-8") as f:
        persona = json.load(f)
    with open(event_file, "r", encoding="utf-8") as f:
        daily_events = json.load(f)
    with open(outline_file, "r", encoding="utf-8") as f:
        tree = json.load(f)
    owner = (persona.get("name") or person_name)
    if not isinstance(owner, str):
        owner = person_name

    outline = build_outline_index(tree)
    parsed, parse_errors = prepare_relation_events(daily_events, outline)

    input_hashes = {"daily_event": sha256_file(event_file),
                    "event_tree": sha256_file(outline_file),
                    "persona": sha256_file(persona_file)}
    if prompts is None:
        prompts = SelectorPromptManager(
            opts["selector_prompt"], opts["writer_prompt"])
    initial_library = build_initial_relation_library(persona, owner)
    initial_library_hash = hashlib_sha256_stable(initial_library)
    signature = {
        "pipeline_version": PIPELINE_VERSION,
        "prompt_version": prompts.version,
        "model": opts.get("model"),
        "input_hashes": input_hashes,
        "initial_library_hash": initial_library_hash,
        "max_context_tokens": opts.get("max_context_tokens", 24000),
        "max_output_tokens": opts.get("max_output_tokens", 8192),
        # 评估窗口进入签名: 同一输出目录禁止改变日期范围
        # (warmup 分类与汇总口径随窗口改变,不允许复用旧结果)
        "window": {
            "start_date": opts.get("start_date"),
            "end_date": opts.get("end_date"),
        },
        "model_params": {
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
            # 授权边界参数进入签名: 同目录禁止改变调用预算/重试口径
            "max_retries": opts.get("max_retries", 3),
            "max_model_calls": opts.get("max_model_calls"),
        },
    }
    opts = dict(opts)
    opts["prompts"] = prompts
    opts["input_hashes"] = input_hashes
    opts["initial_library_hash"] = initial_library_hash
    opts["evaluation_signature"] = signature

    resume_mode = opts.get("resume")
    if not _check_manifest(store, signature):
        raise ValueError(
            f"输出目录 {out_dir} 的 run_manifest.json 与当前签名不一致"
            "(或已有结果但缺失 manifest),请更换 --output-dir 或显式处理"
            "旧结果。")
    if not resume_mode and _has_results(store):
        raise ValueError(
            f"输出目录 {out_dir} 已存在评估结果,非 --resume 运行会重复追加。"
            "请使用 --resume 续跑,或更换 --output-dir。")
    if opts.get("dry_run") and _has_results(store):
        raise ValueError("dry-run 必须使用没有既有结果的输出目录。")

    # 首次模型调用前落盘初始库与 manifest(崩溃后 resume 仍有签名依据)
    store.write_json("initial_relation_library.json", initial_library)

    # 输入错误记录(时间不可解析 / participant 非法)。
    # 续跑时输入签名一致、错误已在首跑落盘,不重复追加。
    if not resume_mode:
        _append_input_errors(store, parse_errors, person_name, prompts,
                             opts, signature)

    by_day = group_relation_days(parsed)
    all_dates = sorted(by_day.keys())
    start_date, end_date = opts.get("start_date"), opts.get("end_date")
    selected = [d for d in all_dates
                if (start_date is None or d >= start_date)
                and (end_date is None or d <= end_date)]
    # P2-7: start_date 之前的日期作为 warm-up 完整推进状态(模型照常
    # 调用、日级事务正常提交),其结果标记 warmup 且不纳入统计。
    warmup = {d for d in all_dates
              if start_date is not None and d < start_date
              and (end_date is None or d <= end_date)}
    process_dates = [d for d in all_dates if d in warmup
                     or d in selected]
    warmup_days = sorted(warmup)

    if resume_mode:
        committed_dates = _load_committed_dates(store, signature)
        _truncate_uncommitted(store, committed_dates)
    completed = _load_completed(store, signature) if resume_mode else set()
    updates = _load_updates(store, signature) if resume_mode else []
    keep_events = _load_keep_events(store, signature) if resume_mode else []
    input_error_events = _load_input_error_events(store, signature) \
        if resume_mode else set()
    library = rebuild_library_from_updates(initial_library, updates,
                                           keep_events) \
        if resume_mode else _rebuild_fresh(initial_library)

    # 空 resume: 全部待处理事件已完成(含已提交的输入错误终止状态)
    # → 不写任何产物、不调用模型
    if resume_mode and not opts.get("dry_run"):
        pending = {(d, str(pe["event"]["event_id"]))
                   for d in process_dates
                   for pe in by_day[d]}
        pending -= input_error_events
        if not (pending - completed):
            evaluated = 0
            summary_path = store._path("summary.json")
            if os.path.exists(summary_path):
                try:
                    with open(summary_path, "r", encoding="utf-8") as f:
                        evaluated = json.load(f).get("evaluated_events", 0)
                except (OSError, ValueError):
                    pass
            return {"person": person_name, "evaluated": evaluated,
                    "dry_run": False,
                    "evaluation_signature": signature,
                    "budget_exhausted": False}

    _write_manifest(store, _manifest(
        person_name, opts, signature, len(parsed), dry_run=False,
        completed_from_resume=len(completed), warmup_days=warmup_days))

    if opts.get("dry_run"):
        est = _dry_run_estimate(prompts, owner, process_dates, warmup_days,
                                by_day, library, persona, opts)
        _write_manifest(store, _manifest(
            person_name, opts, signature, len(parsed), dry_run=True,
            dry_run_estimate=est))
        return {"person": person_name, "dry_run": True, **est,
                "evaluation_signature": signature}

    # 共享模型调用预算(Selector/Writer/协议修复/截断二分共用同一
    # 客户端): 第 N+1 次请求在发出前即被阻断;续跑以已落盘调用记录
    # 为预算起点,授权上限在 resume 会话之间保持全局口径。
    budget_exhausted = False
    if opts.get("max_model_calls"):
        client = BudgetClient(client, opts["max_model_calls"],
                              initial=_count_call_records(store, signature))
    selector = RelationSelector(client, store, prompts, opts)
    writer = RelationWriter(client, store, prompts, opts)

    def meta(date):
        return {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "person": person_name, "date": date,
            "warmup": date in warmup,
            "pipeline_version": PIPELINE_VERSION,
            "prompt_version": prompts.version,
            "model": opts.get("model"),
            "evaluation_signature": signature,
        }

    for date in process_dates:
        is_warmup = date in warmup
        day_events = by_day[date]
        day_library = library   # 日初快照(日末统一提交)
        plan = build_day_plan(day_events, date, day_library, persona)
        day_cases_log = []
        case_results = {}
        day_patches = []
        day_keep_ids = []
        day_input_errors = []
        events_active = []
        day_aborted = False
        # 同日一致性状态: {(person_id, 归一化关系称谓)} —— 当天早前事件
        # 已判为身份冲突(UNEXPLAINED)的事实,约束后续事件的判定
        day_conflicts = set()
        for pe, cases, earlier, case_errors in plan:
            eid = str(pe["event"]["event_id"])
            # 已按事务提交的输入错误事件是终止状态,续跑不再重复处理
            if resume_mode and (date, eid) in input_error_events:
                continue
            # 输入错误随日事务提交(errors.jsonl + input_error_events)
            day_input_errors.extend(case_errors)
            if not cases:
                continue
            active = [c for c in cases
                      if (date, c["event_id"]) not in completed]
            if not active:
                continue
            events_active.append((pe, active))
            day_cases_log.extend(active)
            # 选择器: 单事件请求(当前事件 + 较早来源事件正文)
            try:
                items = selector.select_event(
                    owner, date, day_library, pe["event"], active,
                    [o["event"] for o in earlier], meta(date),
                    day_conflicts=day_conflicts)
            except BudgetExceeded:
                # 预算耗尽: 本次请求未发出,当日中止且不提交,
                # 运行终止(后续走汇总与 manifest 收尾)
                budget_exhausted = True
                day_aborted = True
                break
            if items is None:
                day_aborted = True
                break
            by_case = {it["case_id"]: it for it in items}
            writer_cases = []
            for c in active:
                it = by_case.get(c["case_id"])
                if it is None:
                    # 校验已保证覆盖,理论不可达
                    store.append_jsonl("errors.jsonl", {
                        **meta(date), "error_type": "model_output_error",
                        "case_ids": [c["case_id"]],
                        "message": "选择器响应缺少案例结果",
                    })
                    day_aborted = True
                    break
                route = it["route"]
                label, violation, writer_eligible = ROUTE_SEMANTICS[route]
                case_results[c["case_id"]] = {
                    "case_id": c["case_id"], "event_id": c["event_id"],
                    "route": route, "label": label,
                    "violation": violation,
                    "person_id": it.get("matched_person_id"),
                    "conflicting_person_id":
                        it.get("conflicting_person_id"),
                    "evidence_event_ids":
                        it.get("evidence_event_ids") or [],
                    "reason": it.get("reason"),
                    "event_origin": c["event_origin"],
                    "candidate_key": c["candidate_key"],
                    "observed_relation": c["observed_relation"],
                    "unknown_fields": it.get("unknown_fields") or [],
                }
                # 本事件的身份冲突事实进入同日一致性状态,约束
                # 后续事件: 相同人物 + 相同称谓不得降级为 KEEP
                if route == ROUTE_UNEXPLAINED:
                    for pid in (it.get("matched_person_id"),
                                it.get("conflicting_person_id")):
                        if pid:
                            day_conflicts.add(
                                (pid, normalize_tag(
                                    c["observed_relation"] or "")))
                if route == ROUTE_EXISTING_KEEP \
                        and c["event_origin"] == "source":
                    day_keep_ids.append(it["matched_person_id"])
                if writer_eligible and c["event_origin"] == "source":
                    # 证据绑定: 解析 Selector 证据事件的完整正文
                    # (当前事件 + 较早来源事件),随写入案例交给 Writer
                    evidence_bodies = {
                        str(pe["event"]["event_id"]): pe["event"]}
                    for o in earlier:
                        evidence_bodies[str(o["event"]["event_id"])] = \
                            o["event"]
                    selector_evidence = it.get("evidence_event_ids") or []
                    selector_evidence_events = [
                        {"event_id": str(evid),
                         "event": evidence_bodies[str(evid)]}
                        for evid in selector_evidence
                        if str(evid) in evidence_bodies]
                    writer_cases.append({
                        **c,
                        "selector_route": route,
                        "matched_person_id": it.get("matched_person_id"),
                        "conflicting_person_id":
                            it.get("conflicting_person_id"),
                        "selector_evidence_event_ids":
                            list(selector_evidence),
                        "selector_evidence_events":
                            selector_evidence_events,
                        "selector_reason": it.get("reason"),
                        "event": pe["event"],
                    })
            if day_aborted:
                break
            if writer_cases:
                try:
                    patches = writer.write(owner, date, day_library,
                                           writer_cases, meta(date))
                except BudgetExceeded:
                    budget_exhausted = True
                    day_aborted = True
                    break
                if patches is None:
                    day_aborted = True
                    break
                day_patches.extend(patches)
        if day_aborted:
            # 当日中止: 状态未推进,事务未提交,可 resume
            break
        if not events_active and not day_input_errors:
            # 该日事件全部已完成(续跑)或无案例: 不重复提交
            continue
        # 日末统一提交(与重放顺序一致: 补丁先于当日 KEEP)
        applied, rejected = _apply_patches(store, date, day_library,
                                           day_patches, meta(date))
        if rejected:
            break
        for person_id in day_keep_ids:
            apply_keep_maintenance(day_library, person_id, date)
        for c in day_cases_log:
            store.append_jsonl("relation_cases.jsonl", {
                **meta(date), "case": c,
            })
        _commit_day(store, date, meta(date), events_active, case_results,
                    applied, day_input_errors, is_warmup)

    # 最终关系库 = 初始库 + 已提交更新日志 + 已提交 KEEP 重放(确定性)
    final_updates = _load_updates(store, signature)
    final_keep = _load_keep_events(store, signature)
    final_library = rebuild_library_from_updates(initial_library,
                                                 final_updates, final_keep)
    store.write_json("final_relation_library.json", final_library)
    summary = _build_summary(person_name, store, initial_library,
                             final_library, opts, signature,
                             budget_exhausted=budget_exhausted)
    store.write_json("summary.json", summary)
    _write_manifest(store, _manifest(
        person_name, opts, signature, len(parsed), dry_run=False,
        completed_from_resume=len(completed), warmup_days=warmup_days))
    return {"person": person_name, "evaluated": summary["evaluated_events"],
            "dry_run": False, "evaluation_signature": signature,
            "budget_exhausted": budget_exhausted}


# ── 补丁应用与日级事务 ──────────────────────────────────────────────────

def _apply_patches(store, date, library, patches, meta):
    """应用合法补丁(同日同 candidate_key 新增合并;冲突拒绝并记录)

    全批原子性: 任一补丁被拒 → 返回 rejected 非空,本批已应用的
    补丁不写入更新日志(当日中止,resume 从干净状态重试)。
    """
    applied = []
    rejected = []
    # 合并同日新增: 同 name 的 add 合并为一个补丁
    merged_adds = {}
    for p in patches:
        if p.get("operation") != "add":
            continue
        name = p["name"].strip()
        if name in merged_adds:
            base = merged_adds[name]
            base["aliases_to_add"] = list(dict.fromkeys(
                (base.get("aliases_to_add") or [])
                + (p.get("aliases_to_add") or [])))
            base["relation_tags_to_add"] = list(dict.fromkeys(
                (base.get("relation_tags_to_add") or [])
                + (p.get("relation_tags_to_add") or [])))
            base["evidence_event_ids"] = sorted(set(
                base.get("evidence_event_ids") or [])
                | set(p.get("evidence_event_ids") or []))
            if not base.get("social_circle") and p.get("social_circle"):
                base["social_circle"] = p["social_circle"]
            if (p.get("relation_note") or "").strip():
                base["relation_note"] = (base.get("relation_note", "")
                                         + ";" + p["relation_note"])
            base["source_case_ids"] = list(dict.fromkeys(
                base["source_case_ids"] + p["source_case_ids"]))
        else:
            merged_adds[name] = dict(p)
            merged_adds[name]["source_case_ids"] = list(
                p["source_case_ids"])
    # 合并同日相同目标的重复 update: 归一化后字段相同 → 只提交一次,
    # 合并证据与 source_case_ids(同日前序事件的相同扩展不重复计数)
    def _norm_values(vals, use_tag_normalize=False):
        out = set()
        for v in vals or []:
            if not isinstance(v, str):
                continue
            v = v.strip()
            if not v:
                continue
            if use_tag_normalize:
                v = normalize_tag(v)
            out.add(v)
        return sorted(out)

    def _normalized_fields(p):
        return (_norm_values(p.get("aliases_to_add")),
                _norm_values(p.get("relation_tags_to_add"),
                             use_tag_normalize=True),
                p.get("social_circle"))

    merged_updates = []
    for p in patches:
        if p.get("operation") != "update":
            continue
        match = next((m for m in merged_updates
                      if m.get("target_person_id")
                      == p.get("target_person_id")
                      and _normalized_fields(m) == _normalized_fields(p)),
                     None)
        if match is None:
            merged_updates.append(dict(p))
            continue
        match["evidence_event_ids"] = sorted(set(
            match.get("evidence_event_ids") or [])
            | set(p.get("evidence_event_ids") or []))
        match["source_case_ids"] = list(dict.fromkeys(
            match["source_case_ids"] + p["source_case_ids"]))
        if (p.get("relation_note") or "").strip():
            match["relation_note"] = (match.get("relation_note", "")
                                      + ";" + p["relation_note"])
    ordered = [merged_adds[n] for n in merged_adds] + merged_updates
    pending_log = []
    for p in ordered:
        conflict = check_patch_conflicts(library, p, None)
        if conflict:
            rejected.append({"patch": p, "reason": conflict})
            store.append_jsonl("errors.jsonl", {
                **meta, "error_type": "patch_rejected",
                "case_ids": list(p.get("source_case_ids") or []),
                "message": conflict,
                "detail": p,
            })
            break
        patch = dict(p)
        patch["_date"] = date
        if patch["operation"] == "add":
            rec = apply_add_patch(library, patch)
            patch["target_person_id"] = rec["person_id"]
        else:
            apply_update_patch(library, patch)
        applied.append(patch)
        pending_log.append(patch)
    if not rejected:
        for patch in pending_log:
            store.append_jsonl("relation_updates.jsonl", {
                **meta, "date": date, "patch": {
                    k: v for k, v in patch.items() if k != "_date"}})
    return applied, rejected


def _commit_day(store, date, meta, events_active, case_results,
                applied_patches, input_errors, warmup):
    """
    日级事务提交: 输入错误终止状态 → 事件结果 → 案例日志 → unknown
    清单 → commit marker。更新日志已由 _apply_patches 写入;marker
    最后写入,续跑只重放已提交事务。
    """
    for err in input_errors:
        store.append_jsonl("input_error_events.jsonl", {
            **meta, "event_id": str(err["event_id"]),
            "error_code": "invalid_participant",
            "message": err["message"],
        })
        store.append_jsonl("errors.jsonl", {
            **meta, "error_type": "input_error",
            "event_ids": [str(err["event_id"])],
            "message": err["message"],
        })
    updates_by_event = defaultdict(list)
    for patch in applied_patches:
        summary = {"operation": patch.get("operation"),
                   "target_person_id": patch.get("target_person_id"),
                   "name": patch.get("name"),
                   "relation_tags_to_add":
                       patch.get("relation_tags_to_add") or [],
                   "source_case_ids": patch.get("source_case_ids") or []}
        for cid in patch.get("source_case_ids") or []:
            parts = str(cid).split(":")
            if len(parts) == 3:
                updates_by_event[parts[1]].append(summary)
    event_ids = []
    for pe, active in events_active:
        eid = str(pe["event"]["event_id"])
        event_ids.append(eid)
        labels = [case_results[c["case_id"]]["label"] for c in active]
        final_label = FINAL_NOT_APPLICABLE
        for candidate in EVENT_LABEL_PRIORITY:
            if candidate in labels:
                final_label = candidate
                break
        violations = sorted({
            case_results[c["case_id"]]["violation"]
            for c in active
            if case_results[c["case_id"]]["violation"]})
        if final_label == FINAL_NOT_APPLICABLE:
            violations = []
        origin = "source" if pe["role"] != "noise_target" else "noise"
        store.append_jsonl("relation_events.jsonl", {
            **meta,
            "schema_version": EVENT_RESULT_SCHEMA,
            "pipeline_version": PIPELINE_VERSION,
            "event_id": eid,
            "event_origin": origin,
            "final_label": final_label,
            "case_ids": [c["case_id"] for c in active],
            "violations": violations,
            "updates_applied": updates_by_event.get(eid, []),
            "warmup": warmup,
            "case_details": [case_results[c["case_id"]] for c in active],
            "evaluation_signature": meta["evaluation_signature"],
        })
        for c in active:
            if case_results[c["case_id"]]["label"] == FINAL_UNKNOWN:
                store.append_jsonl("unknown_cases.jsonl", {
                    **meta, "case_id": c["case_id"],
                    "event_id": c["event_id"],
                    "candidate_key": c["candidate_key"],
                    "unknown_fields":
                        case_results[c["case_id"]].get("unknown_fields")
                        or [],
                    "reason": case_results[c["case_id"]].get("reason"),
                })
    store.append_jsonl(COMMIT_FILE, {
        **meta,
        "transaction_id": f"{date}-{uuid.uuid4().hex[:8]}",
        "event_ids": event_ids,
        "warmup": warmup,
    })


def _rebuild_fresh(initial_library):
    import copy as _copy
    return _copy.deepcopy(initial_library)


def _write_manifest(store, manifest: dict):
    """manifest 内容未变时跳过写入(空 resume 不改变任何产物)"""
    path = store._path("run_manifest.json")

    def comparable(m):
        return {k: v for k, v in m.items() if k != "created_at"}

    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                old = json.load(f)
        except (OSError, ValueError):
            old = None
        if isinstance(old, dict) and comparable(old) == comparable(manifest):
            return
    store.write_json("run_manifest.json", manifest)


# ── 汇总(全部指标由已提交产物权威重算)───────────────────────────────────

def _build_summary(person, store, initial_library, final_library,
                   opts, signature, budget_exhausted=False):
    events = _read_jsonl(store, "relation_events.jsonl", signature)
    updates = _read_jsonl(store, "relation_updates.jsonl", signature)
    traces = _read_jsonl(store, "selector_traces.jsonl", signature)
    errors = _read_jsonl(store, "errors.jsonl", signature)
    target_events = [e for e in events if not e.get("warmup")]

    final_labels = {FINAL_CONFORM: 0, FINAL_NONCONFORM: 0,
                    FINAL_UNKNOWN: 0, FINAL_NOT_APPLICABLE: 0}
    source_labels = {FINAL_CONFORM: 0, FINAL_NONCONFORM: 0,
                     FINAL_UNKNOWN: 0, FINAL_NOT_APPLICABLE: 0}
    noise_labels = {FINAL_CONFORM: 0, FINAL_NONCONFORM: 0,
                    FINAL_UNKNOWN: 0, FINAL_NOT_APPLICABLE: 0}
    violations = {}
    routes = {}
    unknown_fields = {}
    for rec in target_events:
        label = rec.get("final_label")
        if label in final_labels:
            final_labels[label] += 1
            bucket = (source_labels if rec.get("event_origin") == "source"
                      else noise_labels)
            bucket[label] += 1
        for v in rec.get("violations") or []:
            violations[v] = violations.get(v, 0) + 1
        for cd in rec.get("case_details") or []:
            r = cd.get("route")
            if r:
                routes[r] = routes.get(r, 0) + 1
            for f in cd.get("unknown_fields") or []:
                unknown_fields[f] = unknown_fields.get(f, 0) + 1
    conform = final_labels[FINAL_CONFORM]
    nonconform = final_labels[FINAL_NONCONFORM]
    rate = (conform / (conform + nonconform)
            if conform + nonconform else None)

    # 过程指标由已提交产物重算(续跑不重置、不重复);
    # 按 warmup 拆分: 目标区间 / warm-up / 总计。真实请求次数含修复、
    # 截断与失败调用,按 call_id 逐条统计:
    # selector_calls / writer_calls = 全部真实请求;
    # *_repair_calls = phase == "repair" 的协议修复轮;
    # transport_retries = usage.retries 之和(网络/服务端内部重试)。
    def _metric_bucket(events_bucket, updates_bucket, traces_bucket,
                       errors_bucket, input_errors_count):
        add_patches = [u for u in updates_bucket
                       if u.get("patch", {}).get("operation") == "add"]
        update_patches = [u for u in updates_bucket
                          if u.get("patch", {}).get("operation") == "update"]
        model_calls = [t for t in traces_bucket
                       if t.get("call_id") is not None
                       and t.get("stage") in ("selector", "writer")]
        prompt_tokens = completion_tokens = 0
        for t in model_calls:
            usage = t.get("usage") or {}
            prompt_tokens += usage.get("prompt_tokens") or 0
            completion_tokens += usage.get("completion_tokens") or 0
        selector_repair_calls = sum(
            1 for t in model_calls
            if t["stage"] == "selector" and t.get("phase") == "repair")
        writer_repair_calls = sum(
            1 for t in model_calls
            if t["stage"] == "writer" and t.get("phase") == "repair")
        transport_retries = sum(
            (t.get("usage") or {}).get("retries") or 0
            for t in model_calls)
        kept = 0
        temporary = 0
        for rec in events_bucket:
            for cd in rec.get("case_details") or []:
                if cd.get("route") == ROUTE_EXISTING_KEEP \
                        and cd.get("event_origin") == "source":
                    kept += 1
                if cd.get("route") == "TEMPORARY_CONTACT":
                    temporary += 1
        error_counts = {}
        for e in errors_bucket:
            kind = e.get("error_type")
            error_counts[kind] = error_counts.get(kind, 0) + 1
        return {
            "new_relations": len(add_patches),
            "maintained_contacts": len(update_patches) + kept,
            "temporary_contacts": temporary,
            "patches_applied": len(updates_bucket),
            "patches_rejected": error_counts.get("patch_rejected", 0),
            "selector_calls": sum(1 for t in model_calls
                                  if t["stage"] == "selector"),
            "writer_calls": sum(1 for t in model_calls
                                if t["stage"] == "writer"),
            "selector_repair_calls": selector_repair_calls,
            "writer_repair_calls": writer_repair_calls,
            "transport_retries": transport_retries,
            "total_prompt_tokens": prompt_tokens,
            "total_completion_tokens": completion_tokens,
            "api_errors": error_counts.get("api_error", 0),
            "model_errors": error_counts.get("model_output_error", 0),
            "input_errors": input_errors_count,
        }

    warmup_events = [e for e in events if e.get("warmup")]
    target_updates = [u for u in updates if not u.get("warmup")]
    warmup_updates = [u for u in updates if u.get("warmup")]
    target_traces = [t for t in traces if not t.get("warmup")]
    warmup_traces = [t for t in traces if t.get("warmup")]
    target_errors = [e for e in errors if not e.get("warmup")]
    warmup_errors = [e for e in errors if e.get("warmup")]
    # 输入错误按事务化的 input_error_events.jsonl 权威统计(事件级、
    # 随日事务回滚,不受 errors.jsonl 诊断残留影响);无日期的时间解析
    # 错误(仅首跑写入 errors.jsonl)并入目标区间。
    ie_records = _read_jsonl(store, "input_error_events.jsonl", signature)
    target_ie = sum(1 for r in ie_records if not r.get("warmup"))
    warmup_ie = sum(1 for r in ie_records if r.get("warmup"))
    target_ie += sum(1 for e in target_errors
                     if e.get("error_type") == "input_error"
                     and e.get("date") is None)
    target_metrics = _metric_bucket(target_events, target_updates,
                                    target_traces, target_errors, target_ie)
    warmup_metrics = _metric_bucket(warmup_events, warmup_updates,
                                    warmup_traces, warmup_errors, warmup_ie)
    total_metrics = {
        k: target_metrics[k] + warmup_metrics[k]
        for k in target_metrics}

    return {
        "person": person,
        "schema_version": EVENT_RESULT_SCHEMA,
        "pipeline_version": PIPELINE_VERSION,
        "prompt_version": opts["prompts"].version,
        "model": opts.get("model"),
        "evaluation_signature": signature,
        "evaluated_events": len(target_events),
        "relation_rate": rate,
        "final_labels": final_labels,
        "violations": {
            "sudden_familiar_conflicts": violations.get(
                VIOLATION_SUDDEN, 0),
            "unexplained_relation_changes": violations.get(
                VIOLATION_UNEXPLAINED, 0),
        },
        "source_labels": source_labels,
        "noise_labels": noise_labels,
        "selector_routes": routes,
        "process_metrics": target_metrics,
        "warmup_process_metrics": warmup_metrics,
        "total_runtime_metrics": total_metrics,
        "model_call_budget": {
            "cap": opts.get("max_model_calls"),
            "consumed": sum(1 for t in traces
                            if t.get("call_id") is not None),
            "exhausted": budget_exhausted,
        },
        "unknown_fields": unknown_fields,
        "initial_relation_count": len(initial_library["records"]),
        "final_relation_count": len(final_library["records"]),
    }


# ── dry-run 估算(与生产共用同一规划函数)─────────────────────────────────

def _dry_run_estimate(prompts, owner, process_dates, warmup_days, by_day,
                      library, persona, opts) -> dict:
    max_ctx = opts.get("max_context_tokens", 24000)
    max_out = opts.get("max_output_tokens", 8192)
    target_cases = warmup_cases = 0
    target_days = warmup_days_count = 0
    selector_chunks = 0
    selector_tokens = 0
    for date in process_dates:
        plan = build_day_plan(by_day[date], date, library, persona)
        day_cases = 0
        for pe, cases, earlier, _errs in plan:
            if not cases:
                continue
            day_cases += len(cases)
            planned = plan_case_chunks(
                prompts, owner, date, library, cases, "selector",
                max_ctx, max_out,
                ctx={"event": pe["event"],
                     "earlier": [o["event"] for o in earlier]})
            selector_chunks += len(planned)
            selector_tokens += sum(est_in for _, _, est_in, _ in planned)
        if not day_cases:
            continue
        if date in warmup_days:
            warmup_days_count += 1
            warmup_cases += day_cases
        else:
            target_days += 1
            target_cases += day_cases
    return {
        "days_with_cases": target_days,
        "total_cases": target_cases,
        "warmup_days": warmup_days_count,
        "warmup_cases": warmup_cases,
        "stage_estimates": {
            "selector": {"calls": selector_chunks,
                         "input_tokens": selector_tokens,
                         "note": "按事件逐个调用的真实规划分片数"
                                 "(生产共用同一规划函数)"},
            "writer": {"calls": 0, "input_tokens": 0,
                       "note": "依赖选择器路由,无法先验估计"},
        },
    }


def hashlib_sha256_stable(obj) -> str:
    import hashlib
    return hashlib.sha256(
        stable_dumps(obj).encode("utf-8")).hexdigest()
