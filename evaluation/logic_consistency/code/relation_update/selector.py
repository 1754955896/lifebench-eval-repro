"""This module selects relation updates."""

from __future__ import annotations

import json
import logging
import uuid

from tools.evaluation_common.context import estimate_batch_tokens
from tools.evaluation_common.signatures import stable_dumps
from tools.evaluation_common.model_adapter import (
    BatchTruncated, ModelClient, ModelError,
)
from evaluation.logic_consistency.code.relation_update.schema import RelationValidationResult

logger = logging.getLogger("relation_eval")


REPAIR_MESSAGE = (
    "你的上一条输出未通过协议校验,错误如下:\n{errors}\n"
    "请只返回修正后的完整合法 JSON 对象,不要输出任何其他文本、"
    "不要遗漏或增加任何案例。")


class SelectorPromptManager:


    def __init__(self, selector_path: str, writer_path: str):
        import hashlib
        self.paths = {"selector": selector_path, "writer": writer_path}
        self.templates = {}
        for stage, path in self.paths.items():
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
            if "@@SYSTEM@@" not in text or "@@USER@@" not in text:
                raise ValueError(f"{path} 缺少 @@SYSTEM@@ / @@USER@@ 标记")
            _, rest = text.split("@@SYSTEM@@", 1)
            system, user = rest.split("@@USER@@", 1)
            self.templates[stage] = {"system": system.strip("\n"),
                                     "user": user.strip("\n")}
        digest = hashlib.sha256(
            json.dumps([self.templates[s]["system"]
                        + self.templates[s]["user"]
                        for s in ("selector", "writer")],
                       ensure_ascii=False).encode("utf-8")).hexdigest()[:8]
        self.version = f"relation-prompt-v1-{digest}"

    def selector_messages(self, owner: str, date: str,
                          library: dict, event: dict, cases: list,
                          earlier_events: list) -> list:

        return [{"role": "system",
                 "content": self.templates["selector"]["system"]},
                {"role": "user",
                 "content": (self.templates["selector"]["user"]
                             .replace("{{OWNER}}", owner)
                             .replace("{{DATE}}", date)
                             .replace("{{RELATION_LIBRARY}}",
                                      stable_dumps(library))
                             .replace("{{CURRENT_EVENT}}",
                                      stable_dumps(event))
                             .replace("{{EARLIER_CONTEXT}}",
                                      stable_dumps(earlier_events))
                             .replace("{{CASES}}",
                                      stable_dumps(cases)))}]

    def writer_messages(self, owner: str, date: str,
                        library: dict, write_cases: list) -> list:
        return [{"role": "system",
                 "content": self.templates["writer"]["system"]},
                {"role": "user",
                 "content": (self.templates["writer"]["user"]
                             .replace("{{OWNER}}", owner)
                             .replace("{{DATE}}", date)
                             .replace("{{RELATION_LIBRARY}}",
                                      stable_dumps(library))
                             .replace("{{WRITE_CASES}}",
                                      stable_dumps(write_cases)))}]


def plan_case_chunks(prompts: SelectorPromptManager, owner: str, date: str,
                     library: dict, cases: list, stage: str,
                     max_ctx: int, max_out: int, ctx: dict | None = None) -> list:


    ctx = ctx or {}
    system = prompts.templates[stage]["system"]

    def build(chunk):
        if stage == "selector":
            messages = prompts.selector_messages(
                owner, date, library, ctx["event"], chunk,
                ctx.get("earlier") or [])
        else:
            messages = prompts.writer_messages(owner, date, library, chunk)
        est = estimate_batch_tokens(system, messages[-1]["content"],
                                    len(chunk))
        return messages, est

    chunks = [list(cases)]
    while True:
        splits = []
        for chunk in chunks:
            _, est = build(chunk)
            if (est["input_tokens"] > max_ctx
                    or est["output_tokens"] > max_out) and len(chunk) > 1:
                half = len(chunk) // 2
                splits.extend([chunk[:half], chunk[half:]])
            else:
                splits.append(chunk)
        if all(s == chunks[i] for i, s in enumerate(splits)):
            break
        chunks = splits
    planned = []
    for chunk in chunks:
        messages, est = build(chunk)
        planned.append((chunk, messages, est["input_tokens"],
                        est["output_tokens"]))
    return planned


def _safe_validate(validate_fn, obj, chunk) -> RelationValidationResult:

    try:
        return validate_fn(obj, chunk)
    except Exception as exc:
        logger.warning("校验器异常: %s: %s", type(exc).__name__, exc)
        result = RelationValidationResult(ok=False)
        result.add("$", f"校验器异常: {type(exc).__name__}: {exc}"[:300])
        return result


def run_model_round(client, store, prompts: SelectorPromptManager,
                    stage: str, owner: str, date: str, library: dict,
                    chunk: list, validate_fn, meta: dict,
                    opts: dict, ctx: dict | None = None) -> tuple | None:


    planned = plan_case_chunks(prompts, owner, date, library, chunk, stage,
                               opts.get("max_context_tokens", 24000),
                               opts.get("max_output_tokens", 8192),
                               ctx=ctx)
    collected = []
    pending = list(planned)

    def call_and_trace(messages, phase):

        call_id = uuid.uuid4().hex[:12]
        try:
            resp = client.chat(messages, opts["max_output_tokens"])
        except BatchTruncated:
            store.append_jsonl("selector_traces.jsonl", {
                **meta, "stage": stage, "call_id": call_id, "phase": phase,
                "attempt": 1, "status": "error",
                "error_type": "truncated",
            })
            raise
        except ModelError:
            store.append_jsonl("selector_traces.jsonl", {
                **meta, "stage": stage, "call_id": call_id, "phase": phase,
                "attempt": 1, "status": "error",
                "error_type": "api_error",
            })
            raise
        store.append_jsonl("selector_traces.jsonl", {
            **meta, "stage": stage, "call_id": call_id, "phase": phase,
            "attempt": 1, "status": "ok",
            "finish_reason": resp.get("finish_reason"),
            "usage": resp.get("usage"),
            "request_hash": json.dumps(messages, ensure_ascii=False)[:16],
            "response_excerpt": (resp.get("content") or "")[:2000],
        })
        return resp

    while pending:
        sub_chunk, messages, _in, _out = pending.pop(0)
        try:
            resp = call_and_trace(messages, "initial")
        except BatchTruncated:
            if len(sub_chunk) > 1:
                half = len(sub_chunk) // 2
                pending.insert(0, plan_case_chunks(
                    prompts, owner, date, library, sub_chunk[half:], stage,
                    opts.get("max_context_tokens", 24000),
                    opts.get("max_output_tokens", 8192), ctx=ctx)[0])
                pending.insert(0, plan_case_chunks(
                    prompts, owner, date, library, sub_chunk[:half], stage,
                    opts.get("max_context_tokens", 24000),
                    opts.get("max_output_tokens", 8192), ctx=ctx)[0])
                continue
            store.append_jsonl("errors.jsonl", {
                **meta,
                "error_type": "output_truncated",
                "case_ids": [c["case_id"] for c in sub_chunk],
                "message": "单案例输出仍被截断",
            })
            return None
        except ModelError as exc:
            store.append_jsonl("errors.jsonl", {
                **meta, "error_type": "api_error",
                "case_ids": [c["case_id"] for c in sub_chunk],
                "message": f"{stage} 阶段: {exc}"[:500],
            })
            return None
        if resp.get("finish_reason") == "length":
            if len(sub_chunk) > 1:
                half = len(sub_chunk) // 2
                pending.insert(0, plan_case_chunks(
                    prompts, owner, date, library, sub_chunk[half:], stage,
                    opts.get("max_context_tokens", 24000),
                    opts.get("max_output_tokens", 8192), ctx=ctx)[0])
                pending.insert(0, plan_case_chunks(
                    prompts, owner, date, library, sub_chunk[:half], stage,
                    opts.get("max_context_tokens", 24000),
                    opts.get("max_output_tokens", 8192), ctx=ctx)[0])
                continue
            store.append_jsonl("errors.jsonl", {
                **meta, "error_type": "output_truncated",
                "case_ids": [c["case_id"] for c in sub_chunk],
                "message": "单案例输出仍被截断",
            })
            return None
        obj = None
        try:
            obj = json.loads(resp["content"])
        except ValueError:
            obj = None
        validation = _safe_validate(validate_fn, obj, sub_chunk) \
            if obj is not None else RelationValidationResult(ok=False)
        if obj is None:
            validation.add("$", "输出不是合法 JSON")
        store.append_jsonl("selector_traces.jsonl", {
            **meta, "stage": f"{stage}:validation",
            "validation_ok": validation.ok,
            "validation_errors": validation.errors,
        })
        if validation.ok:
            collected.extend(obj.get("items") or obj.get("patches") or [])
            continue

        messages.append({"role": "assistant", "content": resp["content"]})
        messages.append({"role": "user",
                         "content": REPAIR_MESSAGE.format(
                             errors=json.dumps(validation.errors,
                                               ensure_ascii=False,
                                               indent=1))})
        try:
            resp2 = call_and_trace(messages, "repair")
        except BatchTruncated:
            if len(sub_chunk) > 1:
                half = len(sub_chunk) // 2
                pending.insert(0, plan_case_chunks(
                    prompts, owner, date, library, sub_chunk[half:], stage,
                    opts.get("max_context_tokens", 24000),
                    opts.get("max_output_tokens", 8192), ctx=ctx)[0])
                pending.insert(0, plan_case_chunks(
                    prompts, owner, date, library, sub_chunk[:half], stage,
                    opts.get("max_context_tokens", 24000),
                    opts.get("max_output_tokens", 8192), ctx=ctx)[0])
                continue
            store.append_jsonl("errors.jsonl", {
                **meta, "error_type": "output_truncated",
                "case_ids": [c["case_id"] for c in sub_chunk],
                "message": "修复后单案例输出仍被截断",
            })
            return None
        except ModelError as exc:
            store.append_jsonl("errors.jsonl", {
                **meta, "error_type": "api_error",
                "case_ids": [c["case_id"] for c in sub_chunk],
                "message": f"{stage} 修复: {exc}"[:500],
            })
            return None
        if resp2.get("finish_reason") == "length":
            if len(sub_chunk) > 1:
                half = len(sub_chunk) // 2
                pending.insert(0, plan_case_chunks(
                    prompts, owner, date, library, sub_chunk[half:], stage,
                    opts.get("max_context_tokens", 24000),
                    opts.get("max_output_tokens", 8192), ctx=ctx)[0])
                pending.insert(0, plan_case_chunks(
                    prompts, owner, date, library, sub_chunk[:half], stage,
                    opts.get("max_context_tokens", 24000),
                    opts.get("max_output_tokens", 8192), ctx=ctx)[0])
                continue
            store.append_jsonl("errors.jsonl", {
                **meta, "error_type": "output_truncated",
                "case_ids": [c["case_id"] for c in sub_chunk],
                "message": "修复后单案例输出仍被截断",
            })
            return None
        obj2 = None
        try:
            obj2 = json.loads(resp2["content"])
        except ValueError:
            obj2 = None
        validation2 = _safe_validate(validate_fn, obj2, sub_chunk) \
            if obj2 is not None else RelationValidationResult(ok=False)
        if obj2 is None:
            validation2.add("$", "输出不是合法 JSON")
        store.append_jsonl("selector_traces.jsonl", {
            **meta, "stage": f"{stage}:validation",
            "validation_ok": validation2.ok,
            "validation_errors": validation2.errors,
        })
        if validation2.ok:
            collected.extend(obj2.get("items") or obj2.get("patches") or [])
            continue
        store.append_jsonl("errors.jsonl", {
            **meta, "error_type": "model_output_error",
            "case_ids": [c["case_id"] for c in sub_chunk],
            "message": f"{stage} 输出无法解析或校验失败",
            "detail": validation2.errors,
        })
        return None
    return collected


class RelationSelector:


    def __init__(self, client: ModelClient, store, prompts,
                 opts: dict):
        self.client = client
        self.store = store
        self.prompts = prompts
        self.opts = opts

    def select_event(self, owner: str, date: str, library: dict,
                     event: dict, cases: list, earlier_events: list,
                     meta: dict, day_conflicts: set | None = None):
        from evaluation.logic_consistency.code.relation_update.schema import validate_selector_response
        library_ids = {r["person_id"] for r in library["records"]}
        allowed = {c["case_id"]: c["available_context_event_ids"]
                   for c in cases}

        def validate_fn(obj, chunk):
            return validate_selector_response(
                obj, owner, date, chunk, library_ids, allowed,
                day_conflicts=day_conflicts)

        return run_model_round(self.client, self.store, self.prompts,
                               "selector", owner, date, library, cases,
                               validate_fn, meta, self.opts,
                               ctx={"event": event,
                                    "earlier": earlier_events})
