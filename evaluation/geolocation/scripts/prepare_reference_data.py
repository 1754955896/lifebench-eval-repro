"""Map GeoLife Beijing or Telecom Shanghai observations to a WorldMove grid.

The output is a standardized NPZ consumed by the separate evaluation scripts.
Each row is a local calendar day with 48 half-hour slots.  Missing slots are -1;
they are not silently filled unless ``--fill-limit-slots`` is requested.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator
from zipfile import ZipFile

import numpy as np

from worldmove_eval_common import EARTH_RADIUS_KM, load_worldmove


XML_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
EXCEL_EPOCH = datetime(1899, 12, 30)


@dataclass
class DayAccumulator:
    ids: np.ndarray
    coordinates: np.ndarray
    temporal_error_seconds: np.ndarray

    @classmethod
    def empty(cls, slots: int) -> "DayAccumulator":
        return cls(
            ids=np.full(slots, -1, dtype=np.int32),
            coordinates=np.full((slots, 2), np.nan, dtype=np.float64),
            temporal_error_seconds=np.full(slots, np.inf, dtype=np.float64),
        )

    def offer(self, slot: int, lon: float, lat: float, error_seconds: float) -> None:
        if error_seconds < self.temporal_error_seconds[slot]:
            self.coordinates[slot] = (lon, lat)
            self.temporal_error_seconds[slot] = error_seconds


class RegularGridMapper:
    """Fast mapping to the nearest released WorldMove representative point."""

    def __init__(self, coords: np.ndarray, shape: tuple[int, int], max_distance_km: float):
        self.coords = np.asarray(coords, dtype=np.float64)
        self.shape = shape
        self.matrix = self.coords.reshape(shape + (2,))
        self.max_distance_km = max_distance_km
        self.lon_min = float(self.coords[:, 0].min())
        self.lon_max = float(self.coords[:, 0].max())
        self.lat_min = float(self.coords[:, 1].min())
        self.lat_max = float(self.coords[:, 1].max())

    @staticmethod
    def _distance_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
        lon1, lat1, lon2, lat2 = map(math.radians, (lon1, lat1, lon2, lat2))
        dlon, dlat = lon2 - lon1, lat2 - lat1
        value = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(min(1.0, max(0.0, value))))

    def map(self, lon: float, lat: float) -> tuple[int, float] | None:
        if not (self.lon_min <= lon <= self.lon_max and self.lat_min <= lat <= self.lat_max):
            return None
        rows, cols = self.shape
        row_guess = int(round((self.lat_max - lat) / (self.lat_max - self.lat_min) * (rows - 1)))
        col_guess = int(round((lon - self.lon_min) / (self.lon_max - self.lon_min) * (cols - 1)))
        best_id, best_distance = -1, math.inf
        for row in range(max(0, row_guess - 2), min(rows, row_guess + 3)):
            for col in range(max(0, col_guess - 2), min(cols, col_guess + 3)):
                candidate_lon, candidate_lat = self.matrix[row, col]
                distance = self._distance_km(lon, lat, float(candidate_lon), float(candidate_lat))
                if distance < best_distance:
                    best_id = row * cols + col
                    best_distance = distance
        if best_distance > self.max_distance_km:
            return None
        return best_id, best_distance


def slot_and_error(timestamp: datetime, slot_minutes: int) -> tuple[int, float]:
    minute = timestamp.hour * 60 + timestamp.minute
    slot = min((24 * 60 // slot_minutes) - 1, minute // slot_minutes)
    slot_center_seconds = (slot * slot_minutes + slot_minutes / 2) * 60
    actual_seconds = minute * 60 + timestamp.second
    return slot, abs(actual_seconds - slot_center_seconds)


def get_day(days: dict[tuple[str, str], DayAccumulator], key: tuple[str, str], slots: int, max_days: int | None) -> DayAccumulator | None:
    day = days.get(key)
    if day is not None:
        return day
    if max_days and len(days) >= max_days:
        return None
    day = DayAccumulator.empty(slots)
    days[key] = day
    return day


def read_geolife(
    root: Path,
    slot_minutes: int,
    utc_offset_hours: float,
    max_files: int | None,
    max_records: int | None,
    max_days: int | None,
) -> tuple[dict[tuple[str, str], DayAccumulator], dict[str, int]]:
    days: dict[tuple[str, str], DayAccumulator] = {}
    slots = 24 * 60 // slot_minutes
    counters = {"files": 0, "records": 0, "parse_errors": 0, "invalid_dates": 0}
    paths = sorted(root.glob("*/Trajectory/*.plt"))
    for path in paths:
        if max_files and counters["files"] >= max_files:
            break
        counters["files"] += 1
        user_id = path.parent.parent.name
        with path.open("r", encoding="ascii", errors="replace") as handle:
            for _ in range(6):
                next(handle, None)
            for line in handle:
                if max_records and counters["records"] >= max_records:
                    return days, counters
                counters["records"] += 1
                fields = line.rstrip("\n").split(",")
                if len(fields) != 7:
                    counters["parse_errors"] += 1
                    continue
                try:
                    lat, lon = float(fields[0]), float(fields[1])
                    # GeoLife uses fixed-width ISO date/time fields; fromisoformat
                    # is substantially faster than strptime across ~25M points.
                    timestamp = datetime.fromisoformat(f"{fields[5]}T{fields[6]}")
                except (ValueError, OverflowError):
                    counters["parse_errors"] += 1
                    continue
                if not datetime(2007, 4, 1) <= timestamp < datetime(2012, 9, 1):
                    counters["invalid_dates"] += 1
                    continue
                timestamp += timedelta(hours=utc_offset_hours)
                key = (user_id, timestamp.date().isoformat())
                day = get_day(days, key, slots, max_days)
                if day is None:
                    continue
                slot, error = slot_and_error(timestamp, slot_minutes)
                day.offer(slot, lon, lat, error)
    return days, counters


def _shared_strings(archive: ZipFile) -> list[str]:
    path = "xl/sharedStrings.xml"
    if path not in archive.namelist():
        return []
    values: list[str] = []
    with archive.open(path) as handle:
        for event, element in ET.iterparse(handle, events=("end",)):
            if element.tag == f"{XML_NS}si":
                values.append("".join(node.text or "" for node in element.iter(f"{XML_NS}t")))
                element.clear()
    return values


def _column_index(cell_reference: str) -> int:
    letters = re.match(r"[A-Z]+", cell_reference)
    if not letters:
        return -1
    value = 0
    for char in letters.group(0):
        value = value * 26 + ord(char) - ord("A") + 1
    return value - 1


def iter_xlsx_rows(path: Path) -> Iterator[list[str | None]]:
    with ZipFile(path) as archive:
        shared = _shared_strings(archive)
        with archive.open("xl/worksheets/sheet1.xml") as handle:
            for event, row in ET.iterparse(handle, events=("end",)):
                if row.tag != f"{XML_NS}row":
                    continue
                values: list[str | None] = [None] * 7
                for cell in row.findall(f"{XML_NS}c"):
                    index = _column_index(cell.attrib.get("r", ""))
                    if not 0 <= index < len(values):
                        continue
                    node = cell.find(f"{XML_NS}v")
                    if node is None or node.text is None:
                        continue
                    text = node.text
                    if cell.attrib.get("t") == "s":
                        try:
                            text = shared[int(text)]
                        except (ValueError, IndexError):
                            text = None
                    values[index] = text
                yield values
                row.clear()


def read_telecom(
    root: Path,
    slot_minutes: int,
    max_files: int | None,
    max_records: int | None,
    max_days: int | None,
) -> tuple[dict[tuple[str, str], DayAccumulator], dict[str, int]]:
    days: dict[tuple[str, str], DayAccumulator] = {}
    slots = 24 * 60 // slot_minutes
    counters = {"files": 0, "records": 0, "parse_errors": 0, "missing_coordinates": 0}
    for path in sorted(root.glob("*.xlsx")):
        if max_files and counters["files"] >= max_files:
            break
        counters["files"] += 1
        for row_number, values in enumerate(iter_xlsx_rows(path), 1):
            if row_number == 1:
                continue
            if max_records and counters["records"] >= max_records:
                return days, counters
            counters["records"] += 1
            start_raw, lat_raw, lon_raw, user_id = values[2], values[4], values[5], values[6]
            if lat_raw is None or lon_raw is None:
                counters["missing_coordinates"] += 1
                continue
            try:
                timestamp = EXCEL_EPOCH + timedelta(days=float(start_raw))
                lat, lon = float(lat_raw), float(lon_raw)
            except (TypeError, ValueError, OverflowError):
                counters["parse_errors"] += 1
                continue
            if not user_id:
                counters["parse_errors"] += 1
                continue
            key = (str(user_id), timestamp.date().isoformat())
            day = get_day(days, key, slots, max_days)
            if day is None:
                continue
            slot, error = slot_and_error(timestamp, slot_minutes)
            day.offer(slot, lon, lat, error)
    return days, counters


def fill_short_internal_gaps(row: np.ndarray, limit: int) -> None:
    if limit <= 0:
        return
    index = 0
    while index < len(row):
        if row[index] >= 0:
            index += 1
            continue
        start = index
        while index < len(row) and row[index] < 0:
            index += 1
        end = index
        if start == 0 or end == len(row) or end - start > limit:
            continue
        left, right = row[start - 1], row[end]
        midpoint = (start + end) / 2
        for slot in range(start, end):
            row[slot] = left if slot < midpoint else right


def finalize_days(
    days: dict[tuple[str, str], DayAccumulator],
    mapper: RegularGridMapper,
    min_observed_slots: int,
    fill_limit_slots: int,
) -> tuple[np.ndarray, np.ndarray, dict[str, int | float]]:
    rows: list[np.ndarray] = []
    keys: list[str] = []
    distances: list[float] = []
    rejected_points = 0
    for (user_id, date), day in sorted(days.items()):
        row = np.full(len(day.ids), -1, dtype=np.int32)
        offered = np.isfinite(day.temporal_error_seconds)
        for slot in np.flatnonzero(offered):
            lon, lat = day.coordinates[slot]
            mapped = mapper.map(float(lon), float(lat))
            if mapped is None:
                rejected_points += 1
                continue
            row[slot], distance = mapped
            distances.append(distance)
        fill_short_internal_gaps(row, fill_limit_slots)
        if np.count_nonzero(row >= 0) < min_observed_slots:
            continue
        rows.append(row)
        keys.append(f"{user_id}|{date}")
    traj = np.vstack(rows) if rows else np.empty((0, 24 * 60 // 30), dtype=np.int32)
    return traj, np.asarray(keys, dtype=str), {
        "candidate_user_days": len(days),
        "kept_user_days": len(rows),
        "rejected_outside_grid_points": rejected_points,
        "mapped_points": len(distances),
        "mapping_distance_km_mean": float(np.mean(distances)) if distances else math.nan,
        "mapping_distance_km_max": float(np.max(distances)) if distances else math.nan,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("geolife", "telecom"), required=True)
    parser.add_argument("--worldmove", type=Path, required=True, help="对应城市的 WorldMove NPZ")
    parser.add_argument("--input", type=Path, required=True, help="GeoLife Data 目录或 Telecom Excel 目录")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--slot-minutes", type=int, default=30)
    parser.add_argument("--min-observed-slots", type=int, default=4)
    parser.add_argument("--fill-limit-slots", type=int, default=0)
    parser.add_argument("--max-mapping-distance-km", type=float, default=3.0)
    parser.add_argument("--geolife-utc-offset-hours", type=float, default=8.0)
    parser.add_argument("--max-files", type=int, default=None, help="仅用于抽样或冒烟测试")
    parser.add_argument("--max-records", type=int, default=None, help="仅用于抽样或冒烟测试")
    parser.add_argument("--max-days", type=int, default=None, help="仅用于抽样或冒烟测试")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.slot_minutes != 30:
        raise SystemExit("当前 WorldMove 文件固定为 48×30 分钟；--slot-minutes 必须为 30")
    if not 1 <= args.min_observed_slots <= 48:
        raise SystemExit("--min-observed-slots 必须在 1..48")

    worldmove = load_worldmove(args.worldmove)
    mapper = RegularGridMapper(worldmove.coords, worldmove.grid_shape, args.max_mapping_distance_km)
    if args.source == "geolife":
        days, read_stats = read_geolife(
            args.input,
            args.slot_minutes,
            args.geolife_utc_offset_hours,
            args.max_files,
            args.max_records,
            args.max_days,
        )
        source_name, city = "GeoLife", "Beijing"
    else:
        days, read_stats = read_telecom(
            args.input,
            args.slot_minutes,
            args.max_files,
            args.max_records,
            args.max_days,
        )
        source_name, city = "Telecom Shanghai Dataset", "Shanghai"

    traj, row_keys, mapping_stats = finalize_days(
        days,
        mapper,
        args.min_observed_slots,
        args.fill_limit_slots,
    )
    metadata = {
        "format_version": 1,
        "source": source_name,
        "city": city,
        "slot_minutes": args.slot_minutes,
        "missing_location": -1,
        "selection_per_slot": "observation nearest to half-hour slot center",
        "fill_limit_slots": args.fill_limit_slots,
        "min_observed_slots": args.min_observed_slots,
        "worldmove_path": str(worldmove.path),
        "worldmove_sha256": worldmove.metadata["worldmove_sha256"],
        "input_path": str(args.input.resolve()),
        "input_mode": "sampled" if any((args.max_files, args.max_records, args.max_days)) else "full",
        "read_stats": read_stats,
        "mapping_stats": mapping_stats,
        "notes": (
            "GeoLife timestamps were shifted from UTC to UTC+8 before daily slotting."
            if args.source == "geolife"
            else "Telecom timestamps were treated as local Shanghai time; only event start positions were used."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        args.output,
        traj=traj,
        observed=traj >= 0,
        coords=worldmove.coords,
        grid_shape=np.asarray(worldmove.grid_shape, dtype=np.int32),
        row_keys=row_keys,
        metadata_json=np.asarray(json.dumps(metadata, ensure_ascii=False)),
    )
    print(json.dumps({"output": str(args.output.resolve()), "shape": list(traj.shape), "metadata": metadata}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
