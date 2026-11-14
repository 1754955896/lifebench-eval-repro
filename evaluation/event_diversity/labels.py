"""事件类型标签归一化（官方九类）。

只作用于计算过程中的字符串：先去除首尾空白、折叠连续空白，再统一
``&`` 两侧空格，最后按明确别名表映射到官方规范名称。不修改
``daily_event.json``，不调用模型推断类别。
"""
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

# 报告中确认的已确认同义词/变体。规范化空白与 & 之后仍匹配不到的
# 字符串才查此表。
ALIASES = {
    "Family": "Family&Living Situation",
    "Home&Living Situation": "Family&Living Situation",
}

_WHITESPACE_RE = re.compile(r"\s+")
_AMPERSAND_RE = re.compile(r"\s*&\s*")

# 官方名称的大小写不敏感索引（仅用于把 career/CAREER 等写法规整到
# 官方拼写；不做任何语义推断）。
_OFFICIAL_CASEFOLD = {name.casefold(): name for name in OFFICIAL_EVENT_TYPES}
_ALIAS_CASEFOLD = {key.casefold(): value for key, value in ALIASES.items()}


class EventTypeError(ValueError):
    """事件类型字符串无法归一到官方九类。"""


def normalize_event_type(value: str) -> str:
    """把单个 type 字符串归一到官方九类之一。

    处理顺序：
    1. 非字符串 → TypeError（提供明确输入诊断）；
    2. 去除首尾空白、折叠连续空白；
    3. 统一 ``&`` 两侧空格为无空格；
    4. 官方名称精确匹配（大小写不敏感）→ 规范拼写；
    5. 别名表精确匹配（大小写不敏感）→ 规范名称；
    6. 空字符串或未知标签 → EventTypeError。

    官方九类输入保持不变，函数幂等。
    """
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
    """批量归一化并返回 (normalized, diagnostics)。

    diagnostics 逐条记录 index / raw / reason，绝不静默删除无法识别的
    事件；调用方可据此在 metrics 中给出输入诊断。
    """
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
