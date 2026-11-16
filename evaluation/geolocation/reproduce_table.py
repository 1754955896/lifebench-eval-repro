"""This module generates a geolocation comparison table."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parent
METRICS = (
    "jump_distance_km",
    "daily_distance_km",
    "radius_gyration_km",
    "dwell_minutes",
    "daily_locations",
)
BINS = {
    "jump_distance_km": np.r_[-np.inf, np.arange(0.0, 101.0, 1.0), np.inf],
    "daily_distance_km": np.r_[-np.inf, np.arange(0.0, 505.0, 5.0), np.inf],
    "radius_gyration_km": np.r_[-np.inf, np.arange(0.0, 101.0, 1.0), np.inf],
    "dwell_minutes": np.r_[-np.inf, np.arange(0.0, 1470.0, 30.0), np.inf],
    "daily_locations": np.r_[-np.inf, np.arange(0.5, 49.5, 1.0), np.inf],
}
REFERENCES = {
    "chinamobile": ROOT / "data/WorldMove/reference_chinamobile_beijing.npz",
    "tencent_poi": ROOT / "data/WorldMove/reference_tencent_poi_beijing.npz",
    "geolife": ROOT / "data/WorldMove/reference_geolife_beijing.npz",
}


def load_standardized(path: Path, coords: np.ndarray, grid_shape: tuple[int, int]) -> np.ndarray:
    with np.load(path, allow_pickle=False) as data:
        traj = np.asarray(data["traj"], dtype=np.int64)
        actual_coords = np.asarray(data["coords"], dtype=np.float64)
        actual_shape = tuple(np.asarray(data["grid_shape"], dtype=np.int64).tolist())
    if traj.ndim != 2 or traj.shape[1] != 48:
        raise ValueError(f"Invalid trajectory shape in {path}: {traj.shape}")
    if actual_shape != grid_shape or actual_coords.shape != coords.shape or not np.allclose(actual_coords, coords):
        raise ValueError(f"Grid mismatch in {path}")
    if np.any(traj[traj >= 0] >= len(coords)):
        raise ValueError(f"Invalid location ID in {path}")
    return traj


def sample_rows(traj: np.ndarray, count: int, rng: np.random.Generator, min_slots: int) -> np.ndarray:
    eligible = np.flatnonzero(np.count_nonzero(traj >= 0, axis=1) >= min_slots)
    if len(eligible) < count:
        raise ValueError(f"Only {len(eligible)} eligible rows; need {count}")
    return traj[np.sort(rng.choice(eligible, size=count, replace=False))]


def haversine_km(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    lon1, lat1 = np.radians(left[..., 0]), np.radians(left[..., 1])
    lon2, lat2 = np.radians(right[..., 0]), np.radians(right[..., 1])
    delta_lon, delta_lat = lon2 - lon1, lat2 - lat1
    value = np.sin(delta_lat / 2.0) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(delta_lon / 2.0) ** 2
    return 2.0 * 6371.0088 * np.arcsin(np.sqrt(np.clip(value, 0.0, 1.0)))


def extract_features(traj: np.ndarray, coords: np.ndarray) -> dict[str, np.ndarray]:
    values: dict[str, list[float]] = {name: [] for name in METRICS}
    for row in traj:
        valid = row >= 0
        ids = row[valid]
        if not len(ids):
            continue
        values["daily_locations"].append(float(np.unique(ids).size))
        points = coords[ids]
        lon_center, lat_center = points.mean(axis=0)
        x = (points[:, 0] - lon_center) * 111.32 * math.cos(math.radians(lat_center))
        y = (points[:, 1] - lat_center) * 110.57
        values["radius_gyration_km"].append(float(np.sqrt(np.mean(x * x + y * y))))
        changed = valid[:-1] & valid[1:] & (row[:-1] != row[1:])
        distances = haversine_km(coords[row[:-1][changed]], coords[row[1:][changed]])
        values["jump_distance_km"].extend(distances.tolist())
        values["daily_distance_km"].append(float(distances.sum()))
        start = 0
        while start < len(row):
            if row[start] < 0:
                start += 1
                continue
            end = start + 1
            while end < len(row) and row[end] == row[start]:
                end += 1
            values["dwell_minutes"].append(float((end - start) * 30))
            start = end
    return {name: np.asarray(items, dtype=np.float64) for name, items in values.items()}


def jsd_base2(left: np.ndarray, right: np.ndarray, bins: np.ndarray) -> float:
    left = left[np.isfinite(left)]
    right = right[np.isfinite(right)]
    left_counts = np.histogram(left, bins=bins)[0].astype(np.float64)
    right_counts = np.histogram(right, bins=bins)[0].astype(np.float64)
    left_probability = left_counts + 1e-12
    right_probability = right_counts + 1e-12
    left_probability /= left_probability.sum()
    right_probability /= right_probability.sum()
    midpoint = 0.5 * (left_probability + right_probability)
    divergence = 0.5 * np.sum(left_probability * np.log(left_probability / midpoint))
    divergence += 0.5 * np.sum(right_probability * np.log(right_probability / midpoint))
    return float(divergence / math.log(2.0))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "table.json")
    args = parser.parse_args()

    with np.load(ROOT / "data/WorldMove/342_CN_Beijing.npz", allow_pickle=True) as data:
        grid = data["grid"].item()
        coords = np.asarray([grid[str(index)] for index in range(len(grid))], dtype=np.float64)
        grid_shape = tuple(np.asarray(data["pop"]).shape)
        worldmove = np.asarray(data["traj"], dtype=np.int64)
    lifebench = load_standardized(ROOT / "data/lifebench_5p_300d_new.npz", coords, grid_shape)
    copb = load_standardized(ROOT / "data/CoPB/copb_beijing_half_hour_missing.npz", coords, grid_shape)
    life_eligible = np.flatnonzero(np.count_nonzero(lifebench >= 0, axis=1) >= 48)
    sample_size = min(300, len(life_eligible))
    life_rows = lifebench[life_eligible[:sample_size]]
    results = {}

    for reference_name, path in REFERENCES.items():
        reference = load_standardized(path, coords, grid_shape)
        samples = {method: {metric: [] for metric in METRICS} for method in ("lifebench", "worldmove", "copb")}
        for repeat in range(50):
            rng = np.random.default_rng(100 + repeat)
            reference_rows = sample_rows(reference, sample_size, rng, 4)
            world_rows = sample_rows(worldmove, sample_size, rng, 48)
            copb_rows = sample_rows(copb, sample_size, rng, 4)
            for method, rows in (("lifebench", life_rows), ("worldmove", world_rows), ("copb", copb_rows)):
                matched_rows = rows.copy()
                matched_rows[reference_rows < 0] = -1
                matched_reference = reference_rows.copy()
                matched_reference[rows < 0] = -1
                features = extract_features(matched_rows, coords)
                reference_features = extract_features(matched_reference, coords)
                for metric in METRICS:
                    samples[method][metric].append(jsd_base2(features[metric], reference_features[metric], BINS[metric]))
        results[reference_name] = {
            method: {metric: {"mean": float(np.mean(values)), "std": float(np.std(values))}
                     for metric, values in metrics.items()}
            for method, metrics in samples.items()
        }

    output = {"metric": "coverage_matched_jsd_base2", "sample_size": sample_size,
              "repeats": 50, "seeds": [100, 149], "results": results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    for metric in METRICS:
        print(metric)
        for reference_name, methods in results.items():
            values = "  ".join(f"{methods[method][metric]['mean']:.4f}" for method in ("lifebench", "worldmove", "copb"))
            print(f"  {reference_name}: {values}")


if __name__ == "__main__":
    main()
