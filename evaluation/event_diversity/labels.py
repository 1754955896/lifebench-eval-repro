"""This module normalizes event labels."""

from __future__ import annotations

import re

OFFICIAL_EVENT_TYPES = (
    "Career",
    "Education",
    "Relationships",
    "Family&Living Situation",
    "Personal Life",
    "Finance",
    "Health",
    "Unexpected Events",
    "Other",
)
OFFICIAL_EVENT_TYPE_SET = frozenset(OFFICIAL_EVENT_TYPES)


ALIASES = {
    "Family": "Family&Living Situation",
    "Home&Living Situation": "Family&Living Situation",
}

_WHITESPACE_RE = re.compile(r"\s+")
_AMPERSAND_RE = re.compile(r"\s*&\s*")


_OFFICIAL_CASEFOLD = {name.casefold(): name for name in OFFICIAL_EVENT_TYPES}
_ALIAS_CASEFOLD = {key.casefold(): value for key, value in ALIASES.items()}


class EventTypeError(ValueError):
    pass


def normalize_event_type(value: str) -> str:


    if not isinstance(value, str):
        raise TypeError(f"事件 type 必须是非空字符串，收到 {type(value).__name__}")
    text = _WHITESPACE_RE.sub(" ", value.strip())
    text = _AMPERSAND_RE.sub("&", text)
    if not text:
        raise EventTypeError("事件 type 为空字符串")
    if text in OFFICIAL_EVENT_TYPE_SET:
        return text
    folded = text.casefold()
    if folded in _OFFICIAL_CASEFOLD:
        return _OFFICIAL_CASEFOLD[folded]
    if text in ALIASES:
        return ALIASES[text]
    if folded in _ALIAS_CASEFOLD:
        return _ALIAS_CASEFOLD[folded]
    raise EventTypeError(
        f"无法识别的事件 type {value!r}（规范化后为 {text!r}），"
        f"官方类别为: {', '.join(OFFICIAL_EVENT_TYPES)}")


def normalize_event_types(values):


    normalized = []
    diagnostics = []
    for index, value in enumerate(values):
        try:
            normalized.append(normalize_event_type(value))
        except (TypeError, EventTypeError) as exc:
            diagnostics.append({
                "index": index,
                "raw": value if isinstance(value, str) else repr(value),
                "reason": str(exc),
            })
    return normalized, diagnostics


__all__ = [
    "OFFICIAL_EVENT_TYPES", "OFFICIAL_EVENT_TYPE_SET", "ALIASES",
    "EventTypeError", "normalize_event_type", "normalize_event_types",
]
