"""Select independent scores and take their unweighted arithmetic mean."""

from __future__ import annotations

from collections.abc import Sequence
from statistics import fmean

from .event.score import score as event_score
from .location.score import score as location_score
from .path.score import score as path_score
from .person.score import score as person_score
from .relation_update.score import score as relation_update_score

SCORERS = {
    "event": ("event_consistency_rate", event_score),
    "person": ("person_consistency_rate", person_score),
    "location": ("location_truth_rate", location_score),
    "path": ("path_truth_rate_legacy", path_score),
    "relation_update": ("relation_rate", relation_update_score),
}
DEFAULT_SCORES = tuple(name for name in SCORERS if name != "relation_update")


def select_scores(methods: Sequence[str] | None) -> tuple[str, ...]:
    selected = tuple(methods) if methods is not None else DEFAULT_SCORES
    if not selected:
        raise ValueError("Select at least one scoring method")
    unknown = set(selected) - SCORERS.keys()
    if unknown:
        raise ValueError(f"Unknown scoring methods: {', '.join(sorted(unknown))}")
    if len(set(selected)) != len(selected):
        raise ValueError("A scoring method can be selected only once")
    return selected


def all_rates(counts: dict[str, int]) -> dict[str, float | None]:
    return {field: scorer(counts) for name, (field, scorer) in SCORERS.items()
            if name != "relation_update"}


def combine(counts: dict[str, int], methods: Sequence[str] | None = None,
            extra_scores: dict[str, float | None] | None = None) -> dict:
    selected = select_scores(methods)
    rates = all_rates(counts)
    extra_scores = extra_scores or {}
    scores = {name: (extra_scores.get(name) if name == "relation_update"
                     else rates[SCORERS[name][0]]) for name in selected}
    missing = [name for name, value in scores.items() if value is None]
    return {
        "selected_methods": list(selected),
        "method_scores": scores,
        "missing_methods": missing,
        "final_score": fmean(scores.values()) if not missing else None,
    }
