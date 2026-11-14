"""Reproduce WorldMove fidelity metrics with Beijing GeoLife as reference.

The paper's private six-city reference data are unavailable. This evaluator uses
the released Beijing trajectories and a GeoLife reference prepared by
``prepare_reference_data.py``. It reproduces the metric families and makes the
coverage-induced deviations explicit instead of presenting them as paper values.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from worldmove_eval_common import (
    aligned_flow_metrics,
    base_result,
    extract_individual_features,
    jsd_base2,
    load_reference,
    load_worldmove,
    matched_row_samples,
    paper_distribution_metrics,
    write_json,
)


BINS = {
    "jump_distance_km": np.r_[-np.inf, np.arange(0.0, 101.0, 1.0), np.inf],
    "daily_distance_km": np.r_[-np.inf, np.arange(0.0, 505.0, 5.0), np.inf],
    "radius_gyration_km": np.r_[-np.inf, np.arange(0.0, 101.0, 1.0), np.inf],
    "dwell_minutes": np.r_[-np.inf, np.arange(0.0, 1470.0, 30.0), np.inf],
    "daily_locations": np.r_[-np.inf, np.arange(0.5, 49.5, 1.0), np.inf],
}


def individual_rank_mass(traj: np.ndarray, top_k: int = 48) -> np.ndarray:
    mass = np.zeros(top_k + 1, dtype=np.float64)
    for row in traj:
        ids = row[row >= 0]
        if not len(ids):
            continue
        _, counts = np.unique(ids, return_counts=True)
        ranked = np.sort(counts.astype(np.float64))[::-1] / counts.sum()
        size = min(top_k, len(ranked))
        mass[:size] += ranked[:size]
        if len(ranked) > top_k:
            mass[-1] += ranked[top_k:].sum()
    return mass


def transition_codes_for_slots(
    traj: np.ndarray,
    n_locations: int,
    left_slots: np.ndarray,
    right_slots: np.ndarray,
) -> np.ndarray:
    left = traj[:, left_slots]
    right = traj[:, right_slots]
    valid = (left >= 0) & (right >= 0) & (left != right)
    return left[valid] * n_locations + right[valid]


def od_protocols(traj: np.ndarray, n_locations: int) -> dict[str, np.ndarray]:
    # The released repository's complete_transition_matrix samples [0, 24, 47]
    # for a 48-slot day. The final paper additionally describes 07:00-09:00 and
    # 17:00-19:00 commuting flows, so both reproducible interpretations are kept.
    official = np.asarray([0, 24, 47], dtype=np.int64)
    peak_left = np.r_[np.arange(14, 18), np.arange(34, 38)]
    return {
        "official_repository_12h": transition_codes_for_slots(
            traj, n_locations, official[:-1], official[1:]
        ),
        "paper_commute_peaks": transition_codes_for_slots(
            traj, n_locations, peak_left, peak_left + 1
        ),
        "all_adjacent_slots": transition_codes_for_slots(
            traj, n_locations, np.arange(47), np.arange(1, 48)
        ),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worldmove", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-trajectories", type=int, default=20000)
    parser.add_argument("--min-reference-slots", type=int, default=4)
    parser.add_argument("--seed", type=int, default=42)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    synthetic = load_worldmove(args.worldmove)
    reference = load_reference(args.reference, synthetic)
    syn_rows, ref_rows = matched_row_samples(
        synthetic.traj,
        reference.traj,
        args.max_trajectories,
        args.seed,
        args.min_reference_slots,
    )

    syn_features = extract_individual_features(synthetic, syn_rows)
    ref_features = extract_individual_features(reference, ref_rows)
    individual = {
        name: paper_distribution_metrics(syn_features[name], ref_features[name], bins)
        for name, bins in BINS.items()
    }
    syn_rank = individual_rank_mass(syn_rows)
    ref_rank = individual_rank_mass(ref_rows)
    individual["zipf_individual_rank"] = {
        "jsd_base2": jsd_base2(syn_rank, ref_rank),
        "rank_support": "ranks 1..48 plus tail",
    }

    syn_od = od_protocols(syn_rows, synthetic.n_locations)
    ref_od = od_protocols(ref_rows, synthetic.n_locations)
    population = {
        name: aligned_flow_metrics(
            syn_od[name],
            ref_od[name],
            total_pair_count=synthetic.n_locations**2,
        )
        for name in syn_od
    }

    reference_observed = np.count_nonzero(ref_rows >= 0, axis=1)
    reference_adjacent = np.count_nonzero(
        (ref_rows[:, :-1] >= 0) & (ref_rows[:, 1:] >= 0), axis=1
    )
    result = base_result("worldmove_paper_fidelity_geolife_beijing", synthetic, reference)
    result.update(
        {
            "reproduction_status": "metric reproduction with a public proxy reference; not a numerical reproduction of paper Tables 3-4",
            "sampling": {
                "seed": args.seed,
                "matched_trajectory_count": int(len(syn_rows)),
                "min_reference_slots": args.min_reference_slots,
            },
            "reference_coverage": {
                "mean_observed_slots_per_day": float(reference_observed.mean()),
                "median_observed_slots_per_day": float(np.median(reference_observed)),
                "mean_adjacent_observed_pairs_per_day": float(reference_adjacent.mean()),
                "fully_observed_days": int(np.count_nonzero(reference_observed == 48)),
            },
            "individual_fidelity": individual,
            "population_flow": population,
            "definitions": {
                "jsd_base2": "base-2 Jensen-Shannon divergence in [0,1], lower is better",
                "ks": "two-sample empirical-CDF maximum distance, lower is better",
                "cpc": "2*sum(min(flow_real, flow_syn))/sum(flow_real+flow_syn), higher is better",
                "rmse": "reported both on the nonzero OD union and on all grid-pair cells, lower is better",
                "official_repository_12h": "WorldMove public code samples slots 0, 24, 47 and counts the two non-self transitions",
                "paper_commute_peaks": "adjacent half-hour transitions within 07:00-09:00 and 17:00-19:00",
                "all_adjacent_slots": "diagnostic: all adjacent observed half-hour transitions",
            },
            "limitations": [
                "The paper evaluates six proprietary GPS/CDR/transaction datasets; Beijing GeoLife is not one of its reported cities.",
                "GeoLife is session-based and has missing half-hour slots, while WorldMove rows are complete synthetic days.",
                "No membership-inference evaluation is possible because the released NPZ has no training membership labels or latent representations.",
                "The released Beijing NPZ has no dates or stable cross-day user IDs, so weekday/weekend and longitudinal-user analyses cannot be reproduced.",
            ],
        }
    )
    write_json(result, args.output)


if __name__ == "__main__":
    main()
