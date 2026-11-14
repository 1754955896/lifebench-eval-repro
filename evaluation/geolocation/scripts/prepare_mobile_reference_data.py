"""Build Beijing half-hour reference trajectories from ChinaMobile or Tencent POI stays.

The output follows the same NPZ contract as ``prepare_reference_data.py``:
one local calendar day per row, 48 half-hour slots, and ``-1`` for missing
observations.  Coordinates are mapped to the released WorldMove Beijing grid.
"""
from __future__ import annotations

import argparse
import ast
import json
import math
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from prepare_reference_data import RegularGridMapper, fill_short_internal_gaps
from worldmove_eval_common import load_worldmove


SLOT_SECONDS = 30 * 60
SLOTS_PER_DAY = 48
BEIJING_UTC_OFFSET_SECONDS = 8 * 60 * 60


def iter_json_array(path: Path, chunk_size: int = 1 << 20) -> Iterator[dict[str, Any]]:
    """Stream objects from a top-level JSON array using only the standard library."""
    decoder = json.JSONDecoder()
    with path.open("r", encoding="utf-8") as handle:
        buffer = ""
        eof = False
        started = False
        while True:
            if not eof and len(buffer) < chunk_size:
                chunk = handle.read(chunk_size)
                if chunk:
                    buffer += chunk
                else:
                    eof = True
            buffer = buffer.lstrip()
            if not started:
                if not buffer:
                    if eof:
                        raise ValueError(f"{path} 是空文件")
                    continue
                if buffer[0] != "[":
                    raise ValueError(f"{path} 顶层必须是 JSON 数组")
                buffer = buffer[1:]
                started = True
                continue
            buffer = buffer.lstrip()
            if buffer.startswith(","):
                buffer = buffer[1:]
                continue
            if buffer.startswith("]"):
                return
            if not buffer:
                if eof:
                    raise ValueError(f"{path} 的 JSON 数组未闭合")
                continue
            try:
                value, end = decoder.raw_decode(buffer)
            except json.JSONDecodeError:
                if eof:
                    raise
                chunk = handle.read(chunk_size)
                if chunk:
                    buffer += chunk
                else:
                    eof = True
                continue
            if not isinstance(value, dict):
                raise ValueError("ChinaMobile 顶层数组元素必须是对象")
            yield value
            buffer = buffer[end:]


class CachedMapper:
    def __init__(self, mapper: RegularGridMapper):
        self.mapper = mapper
        self.cache: dict[tuple[float, float], tuple[int, float] | None] = {}

    def map(self, lon: float, lat: float) -> tuple[int, float] | None:
        key = (lon, lat)
        if key not in self.cache:
            self.cache[key] = self.mapper.map(lon, lat)
        return self.cache[key]


def _keep_day(
    rows: list[np.ndarray],
    keys: list[str],
    user_id: str,
    date_text: str,
    row: np.ndarray,
    min_observed_slots: int,
    fill_limit_slots: int,
) -> bool:
    fill_short_internal_gaps(row, fill_limit_slots)
    if np.count_nonzero(row >= 0) < min_observed_slots:
        return False
    rows.append(row)
    keys.append(f"{user_id}|{date_text}")
    return True


def read_chinamobile(
    path: Path,
    mapper: CachedMapper,
    min_observed_slots: int,
    fill_limit_slots: int,
    max_users: int | None = None,
    max_records: int | None = None,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    rows: list[np.ndarray] = []
    keys: list[str] = []
    counters: dict[str, int] = {
        "users": 0, "records": 0, "parse_errors": 0,
        "outside_grid_points": 0, "mapped_points": 0, "candidate_user_days": 0,
    }
    distances: list[float] = []
    stop = False
    for user in iter_json_array(path):
        if max_users and counters["users"] >= max_users:
            break
        counters["users"] += 1
        user_id = str(user.get("user_id", counters["users"] - 1))
        # Keep the observation closest to the centre of each half-hour slot.
        days: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        for record in user.get("user_traces", []):
            if max_records and counters["records"] >= max_records:
                stop = True
                break
            counters["records"] += 1
            try:
                stamp = str(record[0])
                timestamp = datetime.strptime("2017" + stamp, "%Y%m%d%H%M")
                lon, lat = float(record[2]), float(record[3])
            except (IndexError, TypeError, ValueError, OverflowError):
                counters["parse_errors"] += 1
                continue
            date_text = timestamp.date().isoformat()
            if date_text not in days:
                days[date_text] = (
                    np.full(SLOTS_PER_DAY, -1, dtype=np.int32),
                    np.full(SLOTS_PER_DAY, np.inf, dtype=np.float64),
                )
            row, errors = days[date_text]
            minute = timestamp.hour * 60 + timestamp.minute
            slot = min(SLOTS_PER_DAY - 1, minute // 30)
            error = abs((minute * 60 + timestamp.second) - (slot * SLOT_SECONDS + SLOT_SECONDS / 2))
            if error >= errors[slot]:
                continue
            mapped = mapper.map(lon, lat)
            if mapped is None:
                counters["outside_grid_points"] += 1
                continue
            row[slot], distance = mapped
            errors[slot] = error
            counters["mapped_points"] += 1
            distances.append(distance)
        counters["candidate_user_days"] += len(days)
        for date_text, (row, _) in sorted(days.items()):
            _keep_day(rows, keys, user_id, date_text, row, min_observed_slots, fill_limit_slots)
        if stop:
            break
    traj = np.vstack(rows) if rows else np.empty((0, SLOTS_PER_DAY), dtype=np.int32)
    stats: dict[str, Any] = {
        **counters,
        "kept_user_days": len(rows),
        "unique_coordinates": len(mapper.cache),
        "mapping_distance_km_mean": float(np.mean(distances)) if distances else math.nan,
        "mapping_distance_km_max": float(np.max(distances)) if distances else math.nan,
    }
    return traj, np.asarray(keys, dtype=str), stats


def _local_date_from_day_number(day_number: int) -> str:
    return (datetime(1970, 1, 1) + timedelta(days=day_number)).date().isoformat()


def iter_tencent_lines(path: Path) -> Iterator[tuple[str, list[Any]]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            fields = line.rstrip("\n").split("\t", 1)
            if len(fields) != 2:
                raise ValueError(f"Tencent 第 {line_number} 行没有 UID 和停留列表")
            value = ast.literal_eval(fields[1])
            if not isinstance(value, list):
                raise ValueError(f"Tencent 第 {line_number} 行停留字段不是列表")
            yield fields[0], value


def read_tencent_poi(
    path: Path,
    mapper: CachedMapper,
    min_observed_slots: int,
    fill_limit_slots: int,
    max_users: int | None = None,
    max_records: int | None = None,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    rows: list[np.ndarray] = []
    keys: list[str] = []
    counters: dict[str, int] = {
        "users": 0, "records": 0, "parse_errors": 0, "invalid_intervals": 0,
        "outside_grid_stays": 0, "mapped_stays": 0, "overlapping_slot_assignments": 0,
        "candidate_user_days": 0,
    }
    distances: list[float] = []
    stop = False
    for user_id, stays in iter_tencent_lines(path):
        if max_users and counters["users"] >= max_users:
            break
        counters["users"] += 1
        days: dict[int, tuple[np.ndarray, np.ndarray]] = {}
        for stay in stays:
            if max_records and counters["records"] >= max_records:
                stop = True
                break
            counters["records"] += 1
            try:
                lon, lat = float(stay[0]), float(stay[1])
                start, end = int(stay[2]), int(stay[3])
            except (IndexError, TypeError, ValueError, OverflowError):
                counters["parse_errors"] += 1
                continue
            if end <= start:
                counters["invalid_intervals"] += 1
                continue
            mapped = mapper.map(lon, lat)
            if mapped is None:
                counters["outside_grid_stays"] += 1
                continue
            location_id, distance = mapped
            counters["mapped_stays"] += 1
            distances.append(distance)
            start_local = start + BEIJING_UTC_OFFSET_SECONDS
            end_local = end + BEIJING_UTC_OFFSET_SECONDS
            # Slot k has centre 00:15 + 30*k. Include it iff start <= centre < end.
            first_k = math.ceil((start_local - SLOT_SECONDS / 2) / SLOT_SECONDS)
            last_k = math.ceil((end_local - SLOT_SECONDS / 2) / SLOT_SECONDS) - 1
            duration = end - start
            for global_slot in range(first_k, last_k + 1):
                day_number, slot = divmod(global_slot, SLOTS_PER_DAY)
                if day_number not in days:
                    days[day_number] = (
                        np.full(SLOTS_PER_DAY, -1, dtype=np.int32),
                        np.full(SLOTS_PER_DAY, -1, dtype=np.int64),
                    )
                row, durations = days[day_number]
                if row[slot] >= 0:
                    counters["overlapping_slot_assignments"] += 1
                if duration > durations[slot]:
                    row[slot] = location_id
                    durations[slot] = duration
        counters["candidate_user_days"] += len(days)
        for day_number, (row, _) in sorted(days.items()):
            _keep_day(
                rows, keys, str(user_id), _local_date_from_day_number(day_number), row,
                min_observed_slots, fill_limit_slots,
            )
        if stop:
            break
    traj = np.vstack(rows) if rows else np.empty((0, SLOTS_PER_DAY), dtype=np.int32)
    stats: dict[str, Any] = {
        **counters,
        "kept_user_days": len(rows),
        "unique_coordinates": len(mapper.cache),
        "mapping_distance_km_mean": float(np.mean(distances)) if distances else math.nan,
        "mapping_distance_km_max": float(np.max(distances)) if distances else math.nan,
    }
    return traj, np.asarray(keys, dtype=str), stats


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("chinamobile", "tencent_poi"), required=True)
    parser.add_argument("--worldmove", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--min-observed-slots", type=int, default=4)
    parser.add_argument("--fill-limit-slots", type=int, default=0)
    parser.add_argument("--max-mapping-distance-km", type=float, default=3.0)
    parser.add_argument("--max-users", type=int, default=None, help="抽样/测试用")
    parser.add_argument("--max-records", type=int, default=None, help="抽样/测试用")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if not 1 <= args.min_observed_slots <= SLOTS_PER_DAY:
        raise SystemExit("--min-observed-slots 必须在 1..48")
    world = load_worldmove(args.worldmove)
    mapper = CachedMapper(RegularGridMapper(world.coords, world.grid_shape, args.max_mapping_distance_km))
    if args.source == "chinamobile":
        traj, row_keys, stats = read_chinamobile(
            args.input, mapper, args.min_observed_slots, args.fill_limit_slots,
            args.max_users, args.max_records,
        )
        source_name = "ChinaMobile Beijing"
        selection = "observation nearest to each half-hour slot centre"
        notes = "MMDDHHMM timestamps are interpreted as Beijing local time in 2017."
    else:
        traj, row_keys, stats = read_tencent_poi(
            args.input, mapper, args.min_observed_slots, args.fill_limit_slots,
            args.max_users, args.max_records,
        )
        source_name = "Tencent Mobility Beijing POI stays"
        selection = "POI stay containing each half-hour slot centre; longer stay resolves overlap"
        notes = "Unix timestamps are converted to UTC+8; gaps between released POI stays remain missing."
    metadata = {
        "format_version": 1,
        "source": source_name,
        "city": "Beijing",
        "slot_minutes": 30,
        "missing_location": -1,
        "selection_per_slot": selection,
        "fill_limit_slots": args.fill_limit_slots,
        "min_observed_slots": args.min_observed_slots,
        "worldmove_path": str(world.path),
        "worldmove_sha256": world.metadata["worldmove_sha256"],
        "input_path": str(args.input.resolve()),
        "input_mode": "sampled" if args.max_users or args.max_records else "full",
        "read_and_mapping_stats": stats,
        "notes": notes,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        args.output,
        traj=traj,
        observed=traj >= 0,
        coords=world.coords,
        grid_shape=np.asarray(world.grid_shape, dtype=np.int32),
        row_keys=row_keys,
        metadata_json=np.asarray(json.dumps(metadata, ensure_ascii=False)),
    )
    summary = {"output": str(args.output.resolve()), "shape": list(traj.shape), "metadata": metadata}
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
