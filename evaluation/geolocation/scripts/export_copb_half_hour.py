"""Convert the released CoPB Beijing JSONL into the common 48-slot grid.

Each CoPB record is one synthetic day made of activity intervals.  A half-hour
slot is first assigned when its centre falls inside an interval.  With the
default ``previous`` gap policy, unassigned slots use zero-order hold so that
the displacement between consecutive activity endpoints remains measurable.
The conservative ``missing`` policy leaves those slots as -1.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np

from prepare_reference_data import RegularGridMapper
from worldmove_eval_common import load_worldmove, sha256_file


SLOTS_PER_DAY = 48
SLOT_MINUTES = 30


def minute_of_day(value: str) -> int:
    hour, minute = (int(part) for part in value.split(":"))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(value)
    return hour * 60 + minute


def convert_record(
    activities: Any,
    mapper: RegularGridMapper,
    coordinate_cache: dict[tuple[float, float], int],
    counters: dict[str, int],
    gap_policy: str,
) -> np.ndarray:
    row = np.full(SLOTS_PER_DAY, -1, dtype=np.int32)
    if not isinstance(activities, list):
        counters["invalid_records"] += 1
        return row

    for activity in activities:
        try:
            start = minute_of_day(str(activity["start_time"]))
            end = minute_of_day(str(activity["end_time"]))
            lat = float(activity["latitude"])
            lon = float(activity["longitude"])
        except (KeyError, TypeError, ValueError, OverflowError):
            counters["invalid_activities"] += 1
            continue
        if end <= start:
            counters["nonpositive_intervals"] += 1
            continue

        key = (lon, lat)
        grid_id = coordinate_cache.get(key)
        if grid_id is None:
            mapped = mapper.map(lon, lat)
            grid_id = -1 if mapped is None else int(mapped[0])
            coordinate_cache[key] = grid_id
        if grid_id < 0:
            counters["unmapped_activities"] += 1
            continue

        first = max(0, (start - SLOT_MINUTES // 2 + SLOT_MINUTES - 1) // SLOT_MINUTES)
        last = min(SLOTS_PER_DAY, (end - SLOT_MINUTES // 2 + SLOT_MINUTES - 1) // SLOT_MINUTES)
        for slot in range(first, last):
            centre = slot * SLOT_MINUTES + SLOT_MINUTES // 2
            if start <= centre < end:
                if row[slot] >= 0 and row[slot] != grid_id:
                    counters["overlapping_slot_assignments"] += 1
                row[slot] = grid_id
    if gap_policy == "previous" and np.any(row >= 0):
        counters["gap_slots_filled"] += int(np.count_nonzero(row < 0))
        valid = np.flatnonzero(row >= 0)
        row[:valid[0]] = row[valid[0]]
        previous = int(row[valid[0]])
        for slot in range(int(valid[0]) + 1, SLOTS_PER_DAY):
            if row[slot] < 0:
                row[slot] = previous
            else:
                previous = int(row[slot])
    return row


def count_lines(path: Path) -> int:
    with path.open("rb") as handle:
        return sum(1 for _ in handle)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--worldmove", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-distance-km", type=float, default=3.0)
    parser.add_argument("--max-records", type=int)
    parser.add_argument("--gap-policy", choices=("previous", "missing"), default="previous")
    args = parser.parse_args()

    world = load_worldmove(args.worldmove)
    available = count_lines(args.input)
    total = min(available, args.max_records) if args.max_records else available
    traj = np.full((total, SLOTS_PER_DAY), -1, dtype=np.int32)
    keys = np.asarray([f"copb_released|{index:09d}" for index in range(total)])
    mapper = RegularGridMapper(world.coords, world.grid_shape, args.max_distance_km)
    cache: dict[tuple[float, float], int] = {}
    counters = {
        "input_lines": 0,
        "json_errors": 0,
        "invalid_records": 0,
        "invalid_activities": 0,
        "nonpositive_intervals": 0,
        "unmapped_activities": 0,
        "overlapping_slot_assignments": 0,
        "gap_slots_filled": 0,
    }

    with args.input.open("r", encoding="utf-8") as handle:
        for index, line in enumerate(handle):
            if index >= total:
                break
            counters["input_lines"] += 1
            try:
                activities = json.loads(line)
            except json.JSONDecodeError:
                counters["json_errors"] += 1
                continue
            traj[index] = convert_record(activities, mapper, cache, counters, args.gap_policy)

    observed = np.count_nonzero(traj >= 0, axis=1).astype(np.int16)
    metadata = {
        "source": "CoPB released trajectory_CoPB.jsonl",
        "city": "Beijing",
        "slot_minutes": SLOT_MINUTES,
        "slot_assignment": "slot centre t is assigned iff start_time <= t < end_time",
        "gap_policy": (
            "previous activity location (zero-order hold)" if args.gap_policy == "previous"
            else "missing (-1); no interpolation across activity intervals"
        ),
        "coordinate_order": "WorldMove coords=[longitude, latitude]; CoPB fields latitude/longitude",
        "max_mapping_distance_km": args.max_distance_km,
        "worldmove_sha256": world.metadata["worldmove_sha256"],
        "copb_input_sha256": sha256_file(args.input),
        "records_available": available,
        "records_exported": total,
        "unique_coordinates": len(cache),
        "observed_slots": {
            "min": int(observed.min()) if len(observed) else 0,
            "mean": float(observed.mean()) if len(observed) else 0.0,
            "median": float(np.median(observed)) if len(observed) else 0.0,
            "max": int(observed.max()) if len(observed) else 0,
        },
        "counters": counters,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        args.output,
        traj=traj,
        observed=observed,
        coords=world.coords,
        grid_shape=np.asarray(world.grid_shape, dtype=np.int32),
        row_keys=keys,
        metadata_json=np.asarray(json.dumps(metadata, ensure_ascii=False)),
    )
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    print(f"结果已写入: {args.output.resolve()}")


if __name__ == "__main__":
    main()
