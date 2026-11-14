"""Export LifeBench final itineraries to the same half-hour grid as GeoLife."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.lifebench.event.simulation.geolocation import (  # noqa: E402
    build_half_hour_trajectory, gcj02_to_wgs84,
)
from worldmove_eval_common import load_worldmove  # noqa: E402


def _nearest_grid(points: np.ndarray, grid: np.ndarray) -> np.ndarray:
    result = np.full(points.shape[0], -1, dtype=np.int64)
    lower, upper = np.nanmin(grid, axis=0) - 0.02, np.nanmax(grid, axis=0) + 0.02
    for index, point in enumerate(points):
        if np.all(np.isfinite(point)) and np.all(point >= lower) and np.all(point <= upper):
            # 纬度方向缩放经度，足以做固定北京网格的最近中心映射。
            delta = grid - point
            delta[:, 0] *= np.cos(np.deg2rad(point[1]))
            result[index] = int(np.argmin(np.sum(delta * delta, axis=1)))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--worldmove", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--instance-id", default="")
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--observed-output", type=Path)
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="递归查找 input-dir 下所有 intermediate_outputs_*.json，适合多人物汇总导出。",
    )
    args = parser.parse_args()
    world = load_worldmove(args.worldmove)
    rows, keys, raw_rows = [], [], []
    paths = (
        args.input_dir.rglob("intermediate_outputs_*.json")
        if args.recursive
        else args.input_dir.glob("intermediate_outputs_*.json")
    )
    for path in sorted(paths):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for date, day in sorted(payload.items()):
            if not isinstance(day, dict) or not isinstance(day.get("location_records"), dict):
                continue
            trajectory = day.get("half_hour_trajectory") or build_half_hour_trajectory(
                day["location_records"], date=date, instance_id=args.instance_id,
            )
            points_list = []
            for slot in trajectory["slots"]:
                longitude = slot.get("canonical_longitude")
                latitude = slot.get("canonical_latitude")
                if longitude is None or latitude is None:
                    try:
                        longitude, latitude = gcj02_to_wgs84(
                            float(slot.get("longitude")), float(slot.get("latitude")),
                        )
                    except (TypeError, ValueError):
                        longitude, latitude = np.nan, np.nan
                points_list.append([longitude, latitude])
            points = np.asarray(points_list, dtype=float)
            if points.shape != (48, 2):
                continue
            rows.append(_nearest_grid(points, world.coords))
            source_key = args.instance_id or str(path.relative_to(args.input_dir).parent)
            keys.append("%s|%s" % (source_key, date))
            raw_rows.append(trajectory)
    traj = np.asarray(rows, dtype=np.int64).reshape((-1, 48))
    metadata = {"source": "LifeBench final reconciliation", "native_coordinate_system": "GCJ-02", "coordinate_system": "WGS-84", "transform_version": "gcj02_to_wgs84_v1", "worldmove_path": str(args.worldmove.resolve()), "row_count": len(rows)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(args.output, traj=traj, observed=traj >= 0, coords=world.coords, grid_shape=np.asarray(world.grid_shape), row_keys=np.asarray(keys), metadata_json=np.asarray(json.dumps(metadata, ensure_ascii=False)))
    args.output.with_suffix(".jsonl").write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in raw_rows), encoding="utf-8")
    if args.reference and args.observed_output:
        with np.load(args.reference, allow_pickle=False) as ref:
            ref_traj = np.asarray(ref["traj"], dtype=np.int64)
        observed = traj.copy()
        for index in range(len(observed)):
            mask = ref_traj[index % max(1, len(ref_traj))] < 0
            observed[index, mask] = -1
        np.savez_compressed(args.observed_output, traj=observed, observed=observed >= 0, latent_traj=traj, coords=world.coords, grid_shape=np.asarray(world.grid_shape), row_keys=np.asarray(keys), metadata_json=np.asarray(json.dumps({**metadata, "observation_adapter": "reference_row_mask"}, ensure_ascii=False)))
    print(json.dumps({"output": str(args.output.resolve()), "shape": list(traj.shape)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
