"""This module calculates evaluation scores."""

def score(counts: dict[str, int]) -> float | None:
    correct = counts.get("地点验证成功", 0)
    suspected = counts.get("地点疑似错误", 0)
    denominator = correct + suspected
    return correct / denominator if denominator else None
