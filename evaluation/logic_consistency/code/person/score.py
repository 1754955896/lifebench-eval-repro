"""This module calculates evaluation scores."""

def score(counts: dict[str, int]) -> float | None:
    correct = counts.get("人物关系正确", 0)
    incorrect = counts.get("人物关系错误", 0)
    denominator = correct + incorrect
    return correct / denominator if denominator else None
