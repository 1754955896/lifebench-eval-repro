"""This module adapts language model responses."""

from __future__ import annotations

import json
import logging
import re
import time

from tools.llm import chat_completion

logger = logging.getLogger("evaluation_model_adapter")


class ModelError(Exception):
    pass


class BatchTruncated(Exception):
    pass


class BudgetExceeded(Exception):
    pass


class BudgetClient:


    def __init__(self, client, cap: int, initial: int = 0):
        self._client = client
        self.cap = cap
        self.sent = initial
        self.exhausted = False

    def chat(self, messages: list, max_tokens: int) -> dict:
        if self.sent >= self.cap:
            self.exhausted = True
            raise BudgetExceeded(
                f"模型请求预算耗尽: 已发出 {self.sent} 次请求"
                f"(硬上限 {self.cap}),本次请求在发出前被阻断")
        self.sent += 1
        return self._client.chat(messages, max_tokens)


class ModelClient:


    def __init__(self, model: str, temperature: float = 0.0,
                 max_retries: int = 3, timeout: float = 600.0,
                 completion_fn=None):
        self.model = model
        self.temperature = temperature
        self.max_retries = max_retries
        self.timeout = timeout

        self._completion = completion_fn or chat_completion

    def chat(self, messages: list, max_tokens: int) -> dict:
        errors = []
        empty_retries = 0
        for attempt in range(self.max_retries + 1):
            try:
                result = self._completion(
                    messages,
                    model=self.model,
                    temperature=self.temperature,
                    max_tokens=max_tokens,
                    response_format={"type": "json_object"},
                    extra_body={"thinking": {"type": "disabled"}},
                    timeout=self.timeout,
                    sdk_max_retries=0,
                )
            except Exception as exc:
                err_name, err_text = type(exc).__name__, str(exc)
                errors.append(f"{err_name}: {err_text[:300]}")
                if "thinking" in err_text and "BadRequest" in err_name:
                    raise ModelError(
                        f"thinking 参数被服务端拒绝，无法显式禁用思考模式: "
                        f"{err_text[:200]}") from exc
                if attempt >= self.max_retries:
                    raise ModelError(
                        f"API 调用失败: {' | '.join(errors)}") from exc
                time.sleep(min(2 ** attempt, 8))
                continue

            if not result.content.strip():
                if empty_retries < 1:
                    empty_retries += 1
                    logger.warning("空 content，重试 (%d)", empty_retries)
                    continue
                raise ModelError("EMPTY_CONTENT: 模型返回空内容")

            return self._pack_result(result, result.content, attempt)

        raise ModelError("EMPTY_CONTENT: 模型返回空内容")

    @staticmethod
    def _pack_result(result, content: str, retries: int) -> dict:

        usage = result.usage or {}
        out = {
            "content": content,
            "finish_reason": result.finish_reason,
            "model_returned": result.model_returned,
            "retries": retries,
            "usage": {
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "cache_hit_tokens": usage.get("cache_hit_tokens"),
                "cache_miss_tokens": usage.get("cache_miss_tokens"),
                "cache_hit_ratio": None,
            },
            "cache_warning": None,
        }
        hit = usage.get("cache_hit_tokens")
        miss = usage.get("cache_miss_tokens")
        prompt = usage.get("prompt_tokens")
        out["usage"]["retries"] = retries
        if hit is not None and miss is not None:
            if prompt is not None and hit + miss != prompt:
                out["cache_warning"] = (
                    f"cache_hit({hit}) + cache_miss({miss}) != "
                    f"prompt_tokens({prompt})")
            out["usage"]["cache_hit_ratio"] = (
                hit / (hit + miss) if hit + miss > 0 else None)
        return out


def parse_model_json(content: str):

    text = content.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


__all__ = [
    "ModelError", "BatchTruncated", "BudgetExceeded", "BudgetClient",
    "ModelClient", "parse_model_json",
]
