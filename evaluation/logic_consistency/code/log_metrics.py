"""This module summarizes evaluation logs."""

from __future__ import annotations

import re
from collections import defaultdict

from evaluation.logic_consistency.code.combine import all_rates

DEFAULT_PATTERNS = {
    "时间提取错误": "<提取时间错误>",
    "地点验证成功": "<地点正确>",
    "地点疑似错误": "<地点错误>",
    "人物关系正确": "<人物正确>",
    "人物关系错误": "<人物错误>",
    "路径查询成功": "<路径时间参考>",
    "路径查询失败": "- ERROR - 查看",
    "事件安排正确": "<事件安排正确>",
    "事件安排错误": "<事件安排错误>",
}


def count_log_levels_and_patterns(log_file_path, patterns_dict=None):

    patterns_dict = patterns_dict or DEFAULT_PATTERNS
    pattern_counts = defaultdict(int)
    for key in patterns_dict:
        pattern_counts[key] = 0
    spatiotemporal_error_total = 0
    try:
        with open(log_file_path, "r", encoding="utf-8") as file:
            for line in file:
                line = line.strip()
                for pattern_name, pattern_string in patterns_dict.items():
                    if pattern_string in line:
                        pattern_counts[pattern_name] += 1
                match = re.search(r"时空逻辑错误有(\d+)处", line)
                if match:
                    spatiotemporal_error_total += int(match.group(1))
        pattern_counts["路径时间错误"] = spatiotemporal_error_total
        return dict(pattern_counts)
    except FileNotFoundError:
        return {}


def summarize_log(log_file_path: str) -> dict:

    counts = count_log_levels_and_patterns(log_file_path, DEFAULT_PATTERNS)
    if not counts:
        return {"counts": {}, "rates": {}, "log_file": log_file_path}
    rates = all_rates(counts)
    return {"counts": counts, "rates": rates, "log_file": log_file_path}


__all__ = [
    "DEFAULT_PATTERNS", "count_log_levels_and_patterns", "summarize_log",
]
