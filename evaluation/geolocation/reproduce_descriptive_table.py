"""This module generates a geolocation summary table."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from reproduce_table import ROOT, extract_features, load_standardized


DATASETS = (
    ("LIFEBENCH-BEIJING", ROOT / "data/lifebench_5p_300d_new.npz"),
    ("WorldMove", None),
    ("CoPB", ROOT / "data/CoPB/copb_beijing_half_hour_missing.npz"),
    ("ChinaMobile", ROOT / "data/WorldMove/reference_chinamobile_beijing.npz"),
    ("Tencent POI", ROOT / "data/WorldMove/reference_tencent_poi_beijing.npz"),
    ("GeoLife", ROOT / "data/WorldMove/reference_geolife_beijing.npz"),
)
FEATURES = (
    "jump_distance_km",
    "daily_distance_km",
    "radius_gyration_km",
    "dwell_minutes",
    "daily_locations",
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "descriptive_table.json")
    args = parser.parse_args()

    with np.load(ROOT / "data/WorldMove/342_CN_Beijing.npz", allow_pickle=True) as data:
        grid = data["grid"].item()
        coords = np.asarray([grid[str(index)] for index in range(len(grid))], dtype=np.float64)
        grid_shape = tuple(np.asarray(data["pop"]).shape)
        worldmove = np.asarray(data["traj"], dtype=np.int64)

    results = {}
    for name, path in DATASETS:
        traj = worldmove if path is None else load_standardized(path, coords, grid_shape)
        features = extract_features(traj, coords)
        observed = np.count_nonzero(traj >= 0, axis=1)
        observed = observed[observed > 0]
        if not len(observed):
            raise ValueError(f"No observed days in {name}")
        results[name] = {
            feature: {"mean": float(np.mean(features[feature])), "median": float(np.median(features[feature]))}
            for feature in FEATURES
        }
        results[name]["mean_valid_slots_per_day"] = float(np.mean(observed))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print("Dataset | Jump length (km) | Daily distance (km) | Radius of gyration (km) | Dwell time (min) | Daily visited locations | Mean valid slots/day")
    print("--- | --- | --- | --- | --- | --- | ---")
    for name, summary in results.items():
        fields = [f"{summary[feature]['mean']:.2f} / {summary[feature]['median']:.2f}" for feature in FEATURES]
        print(f"{name} | {' | '.join(fields)} | {summary['mean_valid_slots_per_day']:.2f}")


if __name__ == "__main__":
    main()
