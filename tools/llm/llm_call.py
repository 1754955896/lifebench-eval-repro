"""This module calls language models."""

from __future__ import annotations

import atexit
import json
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openai import OpenAI

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "config" / "config.json"
EXAMPLE_CONFIG_PATH = REPO_ROOT / "config" / "config.example.json"


def _load_llm_config() -> dict:
    path = CONFIG_PATH if CONFIG_PATH.exists() else EXAMPLE_CONFIG_PATH
    with path.open("r", encoding="utf-8") as f:
        return json.load(f).get("llm", {})


_LLM_CONFIG = _load_llm_config()

API_KEY = os.environ.get("LLM_API_KEY") or _LLM_CONFIG.get("api_key", "")
BASE_URL = os.environ.get("LLM_BASE_URL") or _LLM_CONFIG.get("base_url", "https://api.deepseek.com")
DEFAULT_MODEL = os.environ.get("LLM_DEFAULT_MODEL") or _LLM_CONFIG.get("default_model", "deepseek-v4-flash")
REASON_MODEL = os.environ.get("LLM_REASON_MODEL") or _LLM_CONFIG.get("reason_model", "deepseek-v4-pro")
STRIP_THINK = bool(_LLM_CONFIG.get("strip_think", False))

DEFAULT_CONTEXT = "你是一名严谨的数据评测专家，擅长依据事实与规则核对并评估模型输出。"

_client: OpenAI | None = None
_client_lock = threading.Lock()


def get_client() -> OpenAI:


    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                if not API_KEY:
                    raise RuntimeError(
                        "未配置 LLM API key：请在 config/config.json 的 llm.api_key "
                        "中填写，或设置环境变量 LLM_API_KEY")
                try:
                    from openai import OpenAI
                except ImportError as exc:
                    raise RuntimeError(
                        "缺少 openai 依赖，请先安装 requirements.txt") from exc
                _client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
    return _client


def reset_client() -> None:

    global _client
    with _client_lock:
        _client = None


def _strip_think_text(text: str) -> str:

    return re.sub(r"<think>[\s\S]*?</think>", "", text).strip()


def strip_think_content(text: str) -> str:

    if not STRIP_THINK:
        return text
    return _strip_think_text(text)


_token_usage_lock = threading.Lock()
_token_usage = {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0,
    "cache_hit_tokens": 0,
    "cache_miss_tokens": 0,
    "call_count": 0,
    "models": {},
}
_token_dump_path: str | None = None


def _get_token_dump_path() -> str | None:
    global _token_dump_path
    if _token_dump_path is None:
        token_dir = os.environ.get("LIFEBENCH_EVAL_TOKEN_DIR")
        if token_dir:
            os.makedirs(token_dir, exist_ok=True)
            _token_dump_path = os.path.join(token_dir, f"token_{os.getpid()}.json")
        else:
            _token_dump_path = ""
    return _token_dump_path or None


def _flush_token_usage_locked() -> None:

    path = _get_token_dump_path()
    if not path:
        return
    snapshot = json.loads(json.dumps(_token_usage))
    snapshot["pid"] = os.getpid()
    snapshot["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    except OSError:
        pass


def _record_usage(response) -> None:

    usage = getattr(response, "usage", None)
    if usage is None:
        return

    prompt = getattr(usage, "prompt_tokens", 0) or 0
    completion = getattr(usage, "completion_tokens", 0) or 0
    total = getattr(usage, "total_tokens", 0) or 0
    cache_hit = getattr(usage, "prompt_cache_hit_tokens", None)
    cache_miss = getattr(usage, "prompt_cache_miss_tokens", None)
    model = getattr(response, "model", None) or "unknown"

    with _token_usage_lock:
        _token_usage["prompt_tokens"] += prompt
        _token_usage["completion_tokens"] += completion
        _token_usage["total_tokens"] += total
        if cache_hit:
            _token_usage["cache_hit_tokens"] += cache_hit
        if cache_miss:
            _token_usage["cache_miss_tokens"] += cache_miss
        _token_usage["call_count"] += 1

        model_stats = _token_usage["models"].setdefault(model, {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cache_hit_tokens": 0,
            "cache_miss_tokens": 0,
            "call_count": 0,
        })
        model_stats["prompt_tokens"] += prompt
        model_stats["completion_tokens"] += completion
        model_stats["total_tokens"] += total
        if cache_hit:
            model_stats["cache_hit_tokens"] += cache_hit
        if cache_miss:
            model_stats["cache_miss_tokens"] += cache_miss
        model_stats["call_count"] += 1

        _flush_token_usage_locked()


def get_token_usage() -> dict:

    with _token_usage_lock:
        return json.loads(json.dumps(_token_usage))


def reset_token_usage() -> None:

    global _token_usage
    with _token_usage_lock:
        _token_usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cache_hit_tokens": 0,
            "cache_miss_tokens": 0,
            "call_count": 0,
            "models": {},
        }


def _flush_on_exit() -> None:
    if not os.environ.get("LIFEBENCH_EVAL_TOKEN_DIR"):
        return
    with _token_usage_lock:
        _flush_token_usage_locked()


atexit.register(_flush_on_exit)


@dataclass
class ChatCompletionResult:


    content: str
    model_returned: str | None
    finish_reason: str | None
    usage: dict[str, Any] | None
    raw_response: Any = None

    def to_dict(self) -> dict:
        return {
            "content": self.content,
            "model_returned": self.model_returned,
            "finish_reason": self.finish_reason,
            "usage": self.usage,
        }


def _usage_to_dict(usage) -> dict | None:
    if usage is None:
        return None
    if hasattr(usage, "model_dump"):
        try:
            raw = usage.model_dump()
        except Exception:
            raw = None
    elif isinstance(usage, dict):
        raw = dict(usage)
    else:
        raw = None
    return {
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
        "cache_hit_tokens": getattr(usage, "prompt_cache_hit_tokens", None),
        "cache_miss_tokens": getattr(usage, "prompt_cache_miss_tokens", None),
        "raw": raw,
    }


def chat_completion(
    messages,
    *,
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    response_format=None,
    extra_body=None,
    timeout: float | None = None,
    sdk_max_retries: int | None = None,
    strip_think: bool = False,
) -> ChatCompletionResult:


    messages = list(messages)
    model = model or DEFAULT_MODEL
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
    }
    if temperature is not None:
        kwargs["temperature"] = temperature
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    if response_format is not None:
        kwargs["response_format"] = response_format
    if extra_body is not None:
        kwargs["extra_body"] = extra_body

    client = get_client()
    if timeout is not None or sdk_max_retries is not None:
        overrides = {}
        if timeout is not None:
            overrides["timeout"] = timeout
        if sdk_max_retries is not None:
            overrides["max_retries"] = sdk_max_retries
        client = client.with_options(**overrides)

    response = client.chat.completions.create(**kwargs)
    _record_usage(response)
    choice = response.choices[0]
    content = choice.message.content or ""
    if strip_think:
        content = _strip_think_text(content)
    return ChatCompletionResult(
        content=content,
        model_returned=getattr(response, "model", None),
        finish_reason=getattr(choice, "finish_reason", None),
        usage=_usage_to_dict(getattr(response, "usage", None)),
        raw_response=response,
    )


def _chat(prompt: str, context: str, model: str, json_mode: bool = False) -> str:
    messages = [
        {"role": "system", "content": context},
        {"role": "user", "content": prompt},
    ]
    result = chat_completion(
        messages,
        model=model,
        response_format={"type": "json_object"} if json_mode else None,
    )

    return strip_think_content(result.content)


def llm_call(prompt: str, context: str = DEFAULT_CONTEXT) -> str:

    return _chat(prompt, context, DEFAULT_MODEL)


def llm_call_reason(prompt: str, context: str = DEFAULT_CONTEXT) -> str:

    return _chat(prompt, context, REASON_MODEL)


def llm_call_j(prompt: str, context: str = DEFAULT_CONTEXT) -> str:

    return _chat(prompt, context, DEFAULT_MODEL, json_mode=True)


def llm_call_reason_j(prompt: str, context: str = DEFAULT_CONTEXT) -> str:

    return _chat(prompt, context, REASON_MODEL, json_mode=True)


def llm_agent(messages, tools, dispatch, model: str | None = None, max_turns: int = 12):


    client = get_client()
    model = model or DEFAULT_MODEL
    transcript = list(messages)

    for _ in range(max(int(max_turns), 1)):
        response = client.chat.completions.create(
            model=model,
            messages=transcript,
            tools=tools,
            stream=False,
        )
        _record_usage(response)
        message = response.choices[0].message
        tool_calls = getattr(message, "tool_calls", None) or []
        if not tool_calls:
            return strip_think_content(getattr(message, "content", None) or "")

        transcript.append({
            "role": "assistant",
            "content": getattr(message, "content", None) or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments or "{}",
                    },
                }
                for tc in tool_calls
            ],
        })
        for tc in tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except (json.JSONDecodeError, TypeError):
                args = {}
            result = dispatch(tc.function.name, args)
            if not isinstance(result, str):
                result = json.dumps(result, ensure_ascii=False)
            transcript.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result,
            })
    return None


__all__ = [
    "DEFAULT_CONTEXT",
    "DEFAULT_MODEL",
    "REASON_MODEL",
    "ChatCompletionResult",
    "chat_completion",
    "get_client",
    "reset_client",
    "strip_think_content",
    "get_token_usage",
    "reset_token_usage",
    "llm_call",
    "llm_call_reason",
    "llm_call_j",
    "llm_call_reason_j",
    "llm_agent",
]
