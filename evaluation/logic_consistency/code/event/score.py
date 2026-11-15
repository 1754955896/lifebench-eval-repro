"""Correct event arrangements divided by correct plus incorrect arrangements."""


def score(counts: dict[str, int]) -> float | None:
    correct = counts.get("事件安排正确", 0)
    incorrect = counts.get("事件安排错误", 0)
    denominator = correct + incorrect
    return correct / denominator if denominator else None
