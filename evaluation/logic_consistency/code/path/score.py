"""This module calculates evaluation scores."""

def score(counts: dict[str, int]) -> float | None:
    success = counts.get("路径查询成功", 0)
    failure = counts.get("路径查询失败", 0)
    time_error = counts.get("路径时间错误", 0)
    denominator = success + failure
    return (failure + success - time_error) / denominator if denominator else None
