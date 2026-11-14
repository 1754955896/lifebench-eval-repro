"""Equal-sample comparison of LifeBench, WorldMove, and CoPB against a Beijing reference."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from evaluate_worldmove_paper import BINS, individual_rank_mass
from worldmove_eval_common import (
    TrajectoryDataset,
    aggregate_location_ids,
    aligned_sparse_metrics,
    extract_individual_features,
    jsd_base2,
    ks_statistic,
    load_reference,
    load_worldmove,
    paper_distribution_metrics,
    summarize_numeric,
    transition_codes,
    write_json,
)


def load_standardized(path: Path, world: TrajectoryDataset, source: str) -> tuple[TrajectoryDataset, np.ndarray]:
    with np.load(path, allow_pickle=False) as data:
        traj = np.asarray(data["traj"], dtype=np.int64)
        coords = np.asarray(data["coords"], dtype=np.float64)
        shape = tuple(np.asarray(data["grid_shape"], dtype=np.int64).tolist())
        keys = np.asarray(data["row_keys"]).astype(str)
        metadata = json.loads(str(np.asarray(data["metadata_json"]).item())) if "metadata_json" in data else {}
    if traj.ndim != 2 or traj.shape[1] != 48:
        raise ValueError(f"{source} traj 应为 (*, 48)，实际为 {traj.shape}")
    if coords.shape != world.coords.shape or not np.allclose(coords, world.coords):
        raise ValueError(f"{source} 与 WorldMove/真实参照未使用同一坐标表")
    if shape != world.grid_shape:
        raise ValueError(f"{source} 与 WorldMove/真实参照网格形状不一致")
    valid = traj >= 0
    if np.any(traj[valid] >= world.n_locations):
        raise ValueError(f"{source} 含越界网格 ID")
    expected_hash = metadata.get("worldmove_sha256")
    if expected_hash and expected_hash.upper() != world.metadata["worldmove_sha256"].upper():
        raise ValueError(f"{source} 基于另一份 WorldMove 网格生成，SHA-256 不匹配")
    dataset = TrajectoryDataset(
        path=path.resolve(), traj=traj, coords=coords, grid_shape=shape,
        slot_minutes=30, source=source, city="Beijing", metadata=metadata,
    )
    return dataset, keys


def mean_std(values: list[float]) -> dict[str, float]:
    array = np.asarray(values, dtype=np.float64)
    return {"mean": float(array.mean()), "std": float(array.std())}


def movement_share(traj: np.ndarray) -> float:
    valid = (traj[:, :-1] >= 0) & (traj[:, 1:] >= 0)
    changed = valid & (traj[:, :-1] != traj[:, 1:])
    return float(changed.sum() / valid.sum()) if valid.any() else math.nan


def temporal_move_mass(traj: np.ndarray) -> np.ndarray:
    valid = (traj[:, :-1] >= 0) & (traj[:, 1:] >= 0)
    moved = valid & (traj[:, :-1] != traj[:, 1:])
    return moved.sum(axis=0).astype(np.float64)


def day_structure(traj: np.ndarray) -> dict[str, np.ndarray]:
    entropy, exploration, stay_locations, stay_runs = [], [], [], []
    for row in traj:
        ids = row[row >= 0]
        if not len(ids):
            continue
        _, counts = np.unique(ids, return_counts=True)
        probability = counts / counts.sum()
        value = float(-np.sum(probability * np.log(probability)))
        entropy.append(value / math.log(len(counts)) if len(counts) > 1 else 0.0)

        visits = [int(ids[0])]
        visits.extend(int(value) for value in ids[1:][ids[1:] != ids[:-1]])
        exploration.append(len(set(visits)) / len(visits))

        stable_ids: set[int] = set()
        runs = 0
        start = 0
        while start < len(row):
            if row[start] < 0:
                start += 1
                continue
            end = start + 1
            while end < len(row) and row[end] == row[start]:
                end += 1
            if end - start >= 2:
                stable_ids.add(int(row[start]))
                runs += 1
            start = end
        stay_locations.append(len(stable_ids))
        stay_runs.append(runs)
    return {
        "normalized_visit_entropy": np.asarray(entropy, dtype=float),
        "within_day_exploration_rate": np.asarray(exploration, dtype=float),
        "stay_locations_60m": np.asarray(stay_locations, dtype=float),
        "stay_runs_60m": np.asarray(stay_runs, dtype=float),
    }


def distance_bands(values: np.ndarray) -> dict[str, float]:
    values = np.asarray(values, dtype=float)
    return {
        "low_0_10_km": float(np.mean(values <= 10)),
        "routine_10_30_km": float(np.mean((values > 10) & (values < 30))),
        "urban_30_80_km": float(np.mean((values >= 30) & (values < 80))),
        "long_80_plus_km": float(np.mean(values >= 80)),
    }


def sample_rows(traj: np.ndarray, count: int, rng: np.random.Generator, min_slots: int) -> np.ndarray:
    eligible = np.flatnonzero(np.count_nonzero(traj >= 0, axis=1) >= min_slots)
    if len(eligible) < count:
        raise ValueError(f"满足覆盖要求的轨迹只有 {len(eligible)}，不足 {count}")
    return traj[np.sort(rng.choice(eligible, size=count, replace=False))]


def spatial_metrics(syn: np.ndarray, ref: np.ndarray, shape: tuple[int, int], scale: int) -> dict[str, float]:
    syn_grid, _, _ = aggregate_location_ids(syn, shape, scale)
    ref_grid, _, _ = aggregate_location_ids(ref, shape, scale)
    visit = aligned_sparse_metrics(syn_grid[syn_grid >= 0], ref_grid[ref_grid >= 0])
    od = aligned_sparse_metrics(transition_codes(syn_grid, 1_000_000), transition_codes(ref_grid, 1_000_000))
    return {
        "visit_jsd_base2": float(visit["jsd_natural_log"] / math.log(2)),
        "visit_cpc": float(visit["cpc"]),
        "od_jsd_base2": float(od["jsd_natural_log"] / math.log(2)),
        "od_cpc": float(od["cpc"]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lifebench", type=Path, required=True)
    parser.add_argument("--worldmove", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument(
        "--reference-name", default="geolife",
        help="JSON 中使用的真实参照短名称，例如 geolife、chinamobile、tencent_poi",
    )
    parser.add_argument("--copb", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sample-size", type=int, default=300)
    parser.add_argument("--lifebench-min-observed-slots", type=int, default=48)
    parser.add_argument("--copb-min-observed-slots", type=int, default=4)
    parser.add_argument("--repeats", type=int, default=50)
    parser.add_argument("--seed-start", type=int, default=100)
    args = parser.parse_args()

    reference_name = args.reference_name.strip().lower()
    if not reference_name or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789_" for char in reference_name):
        raise SystemExit("--reference-name 只能包含小写字母、数字和下划线")
    if reference_name in {"lifebench", "worldmove", "copb"}:
        raise SystemExit("--reference-name 不能与合成方法名称重复")

    world = load_worldmove(args.worldmove)
    reference = load_reference(args.reference, world)
    lifebench, row_keys = load_standardized(args.lifebench, world, "LifeBench")
    copb = load_standardized(args.copb, world, "CoPB")[0] if args.copb else None
    life_observed = np.count_nonzero(lifebench.traj >= 0, axis=1)
    life_eligible = np.flatnonzero(life_observed >= args.lifebench_min_observed_slots)
    target_size = min(args.sample_size, len(life_eligible))
    if target_size == 0:
        raise ValueError("LifeBench 没有满足覆盖门槛的日轨迹")
    life_eval_rows = lifebench.traj[life_eligible[:target_size]]

    method_names = ["lifebench", "worldmove"] + (["copb"] if copb is not None else [])
    all_results: dict[str, dict[str, list[float]]] = {name: {} for name in method_names}
    masked_results: dict[str, dict[str, list[float]]] = {name: {} for name in method_names}
    spatial: dict[str, dict[str, dict[str, list[float]]]] = {
        "lifebench": {str(s): {} for s in (1, 2, 4)},
        "worldmove": {str(s): {} for s in (1, 2, 4)},
        **({"copb": {str(s): {} for s in (1, 2, 4)}} if copb is not None else {}),
    }
    masked_spatial: dict[str, dict[str, dict[str, list[float]]]] = {
        "lifebench": {str(s): {} for s in (1, 2, 4)},
        "worldmove": {str(s): {} for s in (1, 2, 4)},
        **({"copb": {str(s): {} for s in (1, 2, 4)}} if copb is not None else {}),
    }
    movement: dict[str, list[float]] = {name: [] for name in method_names + [reference_name]}
    masked_movement: dict[str, list[float]] = {name: [] for name in method_names}
    temporal: dict[str, list[float]] = {name: [] for name in method_names}
    masked_temporal: dict[str, list[float]] = {name: [] for name in method_names}
    joint_slots: dict[str, list[float]] = {name: [] for name in method_names}

    for repeat in range(args.repeats):
        rng = np.random.default_rng(args.seed_start + repeat)
        geo_rows = sample_rows(reference.traj, target_size, rng, 4)
        world_rows = sample_rows(world.traj, target_size, rng, 48)
        life_rows = life_eval_rows
        geo_features = extract_individual_features(reference, geo_rows)
        selected = [("lifebench", lifebench, life_rows), ("worldmove", world, world_rows)]
        if copb is not None:
            selected.append(("copb", copb, sample_rows(copb.traj, target_size, rng, args.copb_min_observed_slots)))

        for name, dataset, rows in selected:
            features = extract_individual_features(dataset, rows)
            matched_rows = rows.copy()
            matched_rows[geo_rows < 0] = -1
            matched_geo_rows = geo_rows.copy()
            matched_geo_rows[rows < 0] = -1
            matched_features = extract_individual_features(dataset, matched_rows)
            matched_geo_features = extract_individual_features(reference, matched_geo_rows)
            joint_slots[name].append(float(np.count_nonzero(matched_rows >= 0, axis=1).mean()))
            for metric, bins in BINS.items():
                result = paper_distribution_metrics(features[metric], geo_features[metric], bins)
                masked_result = paper_distribution_metrics(matched_features[metric], matched_geo_features[metric], bins)
                for field in ("jsd_base2", "ks_statistic"):
                    all_results[name].setdefault(f"{metric}.{field}", []).append(float(result[field]))
                    masked_results[name].setdefault(f"{metric}.{field}", []).append(float(masked_result[field]))
            rank_jsd = jsd_base2(individual_rank_mass(rows), individual_rank_mass(geo_rows))
            all_results[name].setdefault("zipf_individual_rank.jsd_base2", []).append(rank_jsd)
            movement[name].append(movement_share(rows))
            masked_movement[name].append(movement_share(matched_rows))
            temporal[name].append(jsd_base2(temporal_move_mass(rows), temporal_move_mass(geo_rows)))
            masked_temporal[name].append(jsd_base2(temporal_move_mass(matched_rows), temporal_move_mass(matched_geo_rows)))
            for scale in (1, 2, 4):
                values = spatial_metrics(rows, geo_rows, world.grid_shape, scale)
                masked_values = spatial_metrics(matched_rows, matched_geo_rows, world.grid_shape, scale)
                for key, value in values.items():
                    spatial[name][str(scale)].setdefault(key, []).append(value)
                for key, value in masked_values.items():
                    masked_spatial[name][str(scale)].setdefault(key, []).append(value)
        movement[reference_name].append(movement_share(geo_rows))

    fixed_features = {
        "lifebench": extract_individual_features(lifebench, lifebench.traj),
        "worldmove": extract_individual_features(world, world.traj),
        reference_name: extract_individual_features(reference, reference.traj),
    }
    if copb is not None:
        fixed_features["copb"] = extract_individual_features(copb, copb.traj)
    descriptive_datasets = [
        ("lifebench", lifebench), ("worldmove", world), (reference_name, reference),
    ]
    if copb is not None:
        descriptive_datasets.append(("copb", copb))
    entity_names = np.asarray([key.split("|", 1)[0] for key in row_keys])
    per_entity = {}
    for entity in sorted(set(entity_names.tolist())):
        rows = lifebench.traj[entity_names == entity]
        features = extract_individual_features(lifebench, rows)
        observed = np.count_nonzero(rows >= 0, axis=1)
        per_entity[entity] = {
            "days": int(len(rows)),
            "complete_beijing_days": int(np.count_nonzero(observed == 48)),
            "partial_beijing_days": int(np.count_nonzero((observed > 0) & (observed < 48))),
            "outside_beijing_days": int(np.count_nonzero(observed == 0)),
            "daily_distance_km": summarize_numeric(features["daily_distance_km"]),
            "radius_gyration_km": summarize_numeric(features["radius_gyration_km"]),
            "daily_locations": summarize_numeric(features["daily_locations"]),
            "movement_transition_share": movement_share(rows),
        }
    result: dict[str, Any] = {
        "protocol": {
            "lifebench_days": len(lifebench.traj),
            "lifebench_entities": int(len(set(key.split("|", 1)[0] for key in row_keys))),
            "worldmove_days_available": len(world.traj),
            "reference_name": reference_name,
            "reference_source": reference.source,
            "reference_days_available": len(reference.traj),
            "copb_days_available": len(copb.traj) if copb is not None else None,
            "requested_sample_size": args.sample_size,
            "equal_sample_size": target_size,
            "lifebench_min_observed_slots": args.lifebench_min_observed_slots,
            "repeats": args.repeats,
            "seeds": [args.seed_start, args.seed_start + args.repeats - 1],
            "reference_min_observed_slots": 4,
            "copb_min_observed_slots": args.copb_min_observed_slots if copb is not None else None,
        },
        "equal_sample_comparison": {
            name: {key: mean_std(values) for key, values in metrics.items()}
            for name, metrics in all_results.items()
        },
        "coverage_matched_comparison": {
            name: {key: mean_std(values) for key, values in metrics.items()}
            for name, metrics in masked_results.items()
        },
        "full_dataset_descriptive": {
            name: {
                metric: summarize_numeric(values)
                for metric, values in features.items()
            }
            for name, features in fixed_features.items()
        },
        "lifebench_per_entity": per_entity,
        "daily_distance_band_shares": {
            name: distance_bands(features["daily_distance_km"])
            for name, features in fixed_features.items()
        },
        "movement_transition_share": {
            name: mean_std(values) for name, values in movement.items()
        },
        "coverage_matched_movement_transition_share": {
            name: mean_std(values) for name, values in masked_movement.items()
        },
        "coverage_matched_mean_joint_observed_slots": {
            name: mean_std(values) for name, values in joint_slots.items()
        },
        "movement_time_jsd_base2": {
            name: mean_std(values) for name, values in temporal.items()
        },
        "coverage_matched_movement_time_jsd_base2": {
            name: mean_std(values) for name, values in masked_temporal.items()
        },
        "day_structure_descriptive": {
            name: {key: summarize_numeric(value) for key, value in day_structure(dataset.traj).items()}
            for name, dataset in descriptive_datasets
        },
        "spatial_multiscale": {
            name: {
                scale: {key: mean_std(values) for key, values in metrics.items()}
                for scale, metrics in scales.items()
            }
            for name, scales in spatial.items()
        },
        "coverage_matched_spatial_multiscale": {
            name: {
                scale: {key: mean_std(values) for key, values in metrics.items()}
                for scale, metrics in scales.items()
            }
            for name, scales in masked_spatial.items()
        },
        "limitations": [
            f"{reference.source} is an observational proxy with missing slots; daily distance is adjacent observed-slot displacement, not complete daily mileage.",
            "WorldMove has no stable cross-day user identifiers in the released Beijing file.",
            "LifeBench has 300 person-days but only five independent personas, limiting city-level spatial coverage.",
            "CoPB released trajectories contain no stable user/date identifiers; rows are independent generated daily schedules.",
            f"CoPB activity-interval gaps are retained as missing slots, so raw temporal metrics also reflect its output coverage; coverage-matched metrics use the CoPB/{reference_name} slot intersection.",
            f"The CoPB repository contains training/evaluation pipelines for {reference_name}; results against this reference are not an independent held-out generalization test.",
        ],
    }
    write_json(result, args.output)


if __name__ == "__main__":
    main()
