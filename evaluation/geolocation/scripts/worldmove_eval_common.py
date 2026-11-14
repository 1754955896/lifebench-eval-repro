"""Shared I/O and metrics for the WorldMove evaluation scripts.

Only NumPy and the Python standard library are required.  Reference files are
created by ``prepare_reference_data.py`` and use -1 for an unobserved time slot.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np


EARTH_RADIUS_KM = 6371.0088
MISSING_LOCATION = -1


@dataclass(frozen=True)
class TrajectoryDataset:
    path: Path
    traj: np.ndarray
    coords: np.ndarray
    grid_shape: tuple[int, int]
    slot_minutes: int
    source: str
    city: str
    metadata: dict[str, Any]

    @property
    def n_locations(self) -> int:
        return int(self.coords.shape[0])


def sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _read_text_scalar(value: np.ndarray | str) -> str:
    if isinstance(value, str):
        return value
    return str(np.asarray(value).item())


def load_worldmove(path: str | Path) -> TrajectoryDataset:
    path = Path(path).resolve()
    with np.load(path, allow_pickle=True) as data:
        required = {"grid", "pop", "poi", "traj"}
        missing = required.difference(data.files)
        if missing:
            raise ValueError(f"{path} 缺少 WorldMove 键: {sorted(missing)}")
        grid = data["grid"].item()
        pop = np.asarray(data["pop"])
        traj = np.asarray(data["traj"], dtype=np.int64)

    if not isinstance(grid, dict):
        raise TypeError("grid 必须是由 object 标量取出的字典")
    coords = np.asarray([grid[str(i)] for i in range(len(grid))], dtype=np.float64)
    if traj.ndim != 2:
        raise ValueError(f"traj 必须为二维数组，实际为 {traj.shape}")
    if len(grid) != pop.size:
        raise ValueError("grid 大小与 pop 网格大小不一致")
    if traj.size and (traj.min() < 0 or traj.max() >= len(grid)):
        raise ValueError("traj 含越界栅格 ID")
    city = "Beijing" if "Beijing" in path.name else "Shanghai" if "Shanghai" in path.name else path.stem
    return TrajectoryDataset(
        path=path,
        traj=traj,
        coords=coords,
        grid_shape=(int(pop.shape[0]), int(pop.shape[1])),
        slot_minutes=30,
        source="WorldMove",
        city=city,
        metadata={"worldmove_sha256": sha256_file(path)},
    )


def load_reference(path: str | Path, worldmove: TrajectoryDataset) -> TrajectoryDataset:
    path = Path(path).resolve()
    with np.load(path, allow_pickle=False) as data:
        required = {"traj", "coords", "grid_shape", "metadata_json"}
        missing = required.difference(data.files)
        if missing:
            raise ValueError(f"{path} 缺少参照数据键: {sorted(missing)}")
        traj = np.asarray(data["traj"], dtype=np.int64)
        coords = np.asarray(data["coords"], dtype=np.float64)
        shape_raw = np.asarray(data["grid_shape"], dtype=np.int64).reshape(-1)
        metadata = json.loads(_read_text_scalar(data["metadata_json"]))

    if traj.ndim != 2 or traj.shape[1] != worldmove.traj.shape[1]:
        raise ValueError(
            f"参照轨迹应为 (*, {worldmove.traj.shape[1]})，实际为 {traj.shape}"
        )
    if coords.shape != worldmove.coords.shape or not np.allclose(coords, worldmove.coords):
        raise ValueError("参照文件的坐标表与待评 WorldMove 文件不一致")
    if tuple(shape_raw.tolist()) != worldmove.grid_shape:
        raise ValueError("参照文件的网格形状与 WorldMove 不一致")
    valid = traj >= 0
    if np.any(traj[valid] >= worldmove.n_locations):
        raise ValueError("参照轨迹含越界栅格 ID")
    expected_hash = metadata.get("worldmove_sha256")
    actual_hash = worldmove.metadata["worldmove_sha256"]
    if expected_hash and expected_hash.upper() != actual_hash.upper():
        raise ValueError("参照文件基于另一份 WorldMove 网格生成，SHA-256 不匹配")

    return TrajectoryDataset(
        path=path,
        traj=traj,
        coords=coords,
        grid_shape=worldmove.grid_shape,
        slot_minutes=int(metadata.get("slot_minutes", worldmove.slot_minutes)),
        source=str(metadata.get("source", "reference")),
        city=str(metadata.get("city", worldmove.city)),
        metadata=metadata,
    )


def choose_rows(
    traj: np.ndarray,
    max_rows: int | None,
    seed: int,
    min_observed_slots: int = 1,
) -> np.ndarray:
    observed = np.count_nonzero(traj >= 0, axis=1)
    eligible = np.flatnonzero(observed >= min_observed_slots)
    if max_rows is None or max_rows <= 0 or len(eligible) <= max_rows:
        return traj[eligible]
    rng = np.random.default_rng(seed)
    selected = np.sort(rng.choice(eligible, size=max_rows, replace=False))
    return traj[selected]


def matched_row_samples(
    synthetic: np.ndarray,
    reference: np.ndarray,
    max_rows: int | None,
    seed: int,
    min_observed_slots: int,
) -> tuple[np.ndarray, np.ndarray]:
    reference = choose_rows(reference, max_rows, seed, min_observed_slots)
    target = len(reference)
    if target == 0:
        raise ValueError("没有满足最少观测时隙要求的参照日轨迹")
    synthetic = choose_rows(synthetic, target, seed + 1, synthetic.shape[1])
    target = min(len(synthetic), len(reference))
    return synthetic[:target], reference[:target]


def haversine_km(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Vectorized Haversine distance for arrays ending in [lon, lat]."""
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    lon1, lat1 = np.radians(a[..., 0]), np.radians(a[..., 1])
    lon2, lat2 = np.radians(b[..., 0]), np.radians(b[..., 1])
    dlon, dlat = lon2 - lon1, lat2 - lat1
    value = np.sin(dlat / 2.0) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2.0) ** 2
    return 2.0 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(value, 0.0, 1.0)))


def jsd_probabilities(p: np.ndarray, q: np.ndarray, epsilon: float = 1e-12) -> float:
    """Jensen-Shannon divergence using natural logarithms (0..ln(2))."""
    p = np.asarray(p, dtype=np.float64).reshape(-1)
    q = np.asarray(q, dtype=np.float64).reshape(-1)
    if p.shape != q.shape:
        raise ValueError("JSD 两个概率向量形状必须一致")
    p = p + epsilon
    q = q + epsilon
    p /= p.sum()
    q /= q.sum()
    midpoint = 0.5 * (p + q)
    return float(0.5 * np.sum(p * np.log(p / midpoint)) + 0.5 * np.sum(q * np.log(q / midpoint)))


def jsd_base2(p: np.ndarray, q: np.ndarray, epsilon: float = 1e-12) -> float:
    """Jensen-Shannon divergence in bits, normalized to the paper's [0, 1] range."""
    return float(jsd_probabilities(p, q, epsilon=epsilon) / math.log(2.0))


def histogram_probabilities(values: np.ndarray, bins: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=np.float64)
    values = values[np.isfinite(values)]
    counts, _ = np.histogram(values, bins=np.asarray(bins, dtype=np.float64))
    return counts.astype(np.float64)


def ks_statistic(a: np.ndarray, b: np.ndarray) -> float:
    a = np.sort(np.asarray(a, dtype=np.float64))
    b = np.sort(np.asarray(b, dtype=np.float64))
    a = a[np.isfinite(a)]
    b = b[np.isfinite(b)]
    if not len(a) or not len(b):
        return math.nan
    support = np.sort(np.concatenate((a, b)))
    cdf_a = np.searchsorted(a, support, side="right") / len(a)
    cdf_b = np.searchsorted(b, support, side="right") / len(b)
    return float(np.max(np.abs(cdf_a - cdf_b)))


def wasserstein_1d(a: np.ndarray, b: np.ndarray) -> float:
    """Exact one-dimensional Wasserstein-1 distance without SciPy."""
    a = np.sort(np.asarray(a, dtype=np.float64))
    b = np.sort(np.asarray(b, dtype=np.float64))
    a = a[np.isfinite(a)]
    b = b[np.isfinite(b)]
    if not len(a) or not len(b):
        return math.nan
    support = np.unique(np.concatenate((a, b)))
    if len(support) < 2:
        return 0.0
    cdf_a = np.searchsorted(a, support[:-1], side="right") / len(a)
    cdf_b = np.searchsorted(b, support[:-1], side="right") / len(b)
    return float(np.sum(np.abs(cdf_a - cdf_b) * np.diff(support)))


def numeric_distribution_metrics(
    synthetic: np.ndarray,
    reference: np.ndarray,
    bins: np.ndarray,
) -> dict[str, Any]:
    synthetic = np.asarray(synthetic, dtype=np.float64)
    reference = np.asarray(reference, dtype=np.float64)
    synthetic = synthetic[np.isfinite(synthetic)]
    reference = reference[np.isfinite(reference)]
    syn_hist = histogram_probabilities(synthetic, bins)
    ref_hist = histogram_probabilities(reference, bins)
    return {
        "n_synthetic": int(len(synthetic)),
        "n_reference": int(len(reference)),
        "synthetic_summary": summarize_numeric(synthetic),
        "reference_summary": summarize_numeric(reference),
        "jsd_natural_log": jsd_probabilities(syn_hist, ref_hist),
        "wasserstein_1": wasserstein_1d(synthetic, reference),
        "ks_statistic": ks_statistic(synthetic, reference),
        "bins": [finite_or_string(x) for x in np.asarray(bins)],
    }


def paper_distribution_metrics(
    synthetic: np.ndarray,
    reference: np.ndarray,
    bins: np.ndarray,
) -> dict[str, Any]:
    """Distribution metrics with the WorldMove paper's base-2 JSD convention."""
    result = numeric_distribution_metrics(synthetic, reference, bins)
    result["jsd_base2"] = float(result.pop("jsd_natural_log") / math.log(2.0))
    return result


def summarize_numeric(values: np.ndarray) -> dict[str, Any]:
    values = np.asarray(values, dtype=np.float64)
    values = values[np.isfinite(values)]
    if not len(values):
        return {"count": 0}
    q = np.quantile(values, [0.0, 0.25, 0.5, 0.75, 1.0])
    return {
        "count": int(len(values)),
        "mean": float(values.mean()),
        "std": float(values.std()),
        "min": float(q[0]),
        "q25": float(q[1]),
        "median": float(q[2]),
        "q75": float(q[3]),
        "max": float(q[4]),
    }


def finite_or_string(value: float) -> float | str:
    value = float(value)
    if math.isinf(value):
        return "+inf" if value > 0 else "-inf"
    if math.isnan(value):
        return "nan"
    return value


def extract_individual_features(dataset: TrajectoryDataset, traj: np.ndarray) -> dict[str, np.ndarray]:
    daily_distance: list[float] = []
    radius: list[float] = []
    daily_locations: list[int] = []
    jumps: list[float] = []
    dwell_minutes: list[int] = []
    observed_slots: list[int] = []

    for row in traj:
        valid = row >= 0
        ids = row[valid]
        if not len(ids):
            continue
        observed_slots.append(int(len(ids)))
        daily_locations.append(int(np.unique(ids).size))

        points = dataset.coords[ids]
        lon0, lat0 = points.mean(axis=0)
        x = (points[:, 0] - lon0) * 111.32 * math.cos(math.radians(lat0))
        y = (points[:, 1] - lat0) * 110.57
        radius.append(float(np.sqrt(np.mean(x * x + y * y))))

        adjacent = valid[:-1] & valid[1:]
        changed = adjacent & (row[:-1] != row[1:])
        if np.any(changed):
            distances = haversine_km(dataset.coords[row[:-1][changed]], dataset.coords[row[1:][changed]])
            jumps.extend(distances.tolist())
            daily_distance.append(float(distances.sum()))
        else:
            daily_distance.append(0.0)

        start = 0
        while start < len(row):
            if row[start] < 0:
                start += 1
                continue
            end = start + 1
            while end < len(row) and row[end] == row[start]:
                end += 1
            dwell_minutes.append((end - start) * dataset.slot_minutes)
            start = end

    return {
        "jump_distance_km": np.asarray(jumps, dtype=np.float64),
        "daily_distance_km": np.asarray(daily_distance, dtype=np.float64),
        "radius_gyration_km": np.asarray(radius, dtype=np.float64),
        "dwell_minutes": np.asarray(dwell_minutes, dtype=np.float64),
        "daily_locations": np.asarray(daily_locations, dtype=np.float64),
        "observed_slots": np.asarray(observed_slots, dtype=np.float64),
    }


def aggregate_location_ids(ids: np.ndarray, grid_shape: tuple[int, int], scale: int) -> tuple[np.ndarray, int, tuple[int, int]]:
    if scale < 1:
        raise ValueError("scale 必须 >= 1")
    ids = np.asarray(ids, dtype=np.int64)
    rows, cols = grid_shape
    parent_rows = (rows + scale - 1) // scale
    parent_cols = (cols + scale - 1) // scale
    out = np.full(ids.shape, MISSING_LOCATION, dtype=np.int64)
    valid = ids >= 0
    source_rows = ids[valid] // cols
    source_cols = ids[valid] % cols
    out[valid] = (source_rows // scale) * parent_cols + source_cols // scale
    return out, parent_rows * parent_cols, (parent_rows, parent_cols)


def count_categories(codes: np.ndarray, support_size: int) -> np.ndarray:
    codes = np.asarray(codes, dtype=np.int64)
    codes = codes[codes >= 0]
    return np.bincount(codes, minlength=support_size).astype(np.float64)


def transition_codes(traj: np.ndarray, n_locations: int, exclude_self: bool = True) -> np.ndarray:
    left, right = traj[:, :-1], traj[:, 1:]
    valid = (left >= 0) & (right >= 0)
    if exclude_self:
        valid &= left != right
    return left[valid].astype(np.int64) * n_locations + right[valid].astype(np.int64)


def aligned_sparse_metrics(synthetic_codes: np.ndarray, reference_codes: np.ndarray) -> dict[str, Any]:
    syn_keys, syn_counts = np.unique(np.asarray(synthetic_codes, dtype=np.int64), return_counts=True)
    ref_keys, ref_counts = np.unique(np.asarray(reference_codes, dtype=np.int64), return_counts=True)
    support = np.union1d(syn_keys, ref_keys)
    syn = np.zeros(len(support), dtype=np.float64)
    ref = np.zeros(len(support), dtype=np.float64)
    syn[np.searchsorted(support, syn_keys)] = syn_counts
    ref[np.searchsorted(support, ref_keys)] = ref_counts
    syn_total, ref_total = syn.sum(), ref.sum()
    if syn_total:
        syn /= syn_total
    if ref_total:
        ref /= ref_total
    denominator = syn.sum() + ref.sum()
    cpc = 2.0 * np.minimum(syn, ref).sum() / denominator if denominator else 1.0
    delta = syn - ref
    return {
        "synthetic_events": int(syn_counts.sum()),
        "reference_events": int(ref_counts.sum()),
        "support_size": int(len(support)),
        "jsd_natural_log": jsd_probabilities(syn, ref) if len(support) else 0.0,
        "cpc": float(cpc),
        "mae_probability": float(np.mean(np.abs(delta))) if len(delta) else 0.0,
        "rmse_probability": float(np.sqrt(np.mean(delta * delta))) if len(delta) else 0.0,
        "total_variation": float(0.5 * np.abs(delta).sum()),
    }


def aligned_flow_metrics(
    synthetic_codes: np.ndarray,
    reference_codes: np.ndarray,
    total_pair_count: int | None = None,
) -> dict[str, Any]:
    """Paper-style OD comparison, reporting normalized and raw-count variants.

    Normalized metrics compare the shape of the OD distributions despite GeoLife's
    incomplete coverage. Raw metrics expose the coverage/sample-volume mismatch.
    """
    synthetic_codes = np.asarray(synthetic_codes, dtype=np.int64)
    reference_codes = np.asarray(reference_codes, dtype=np.int64)
    syn_keys, syn_counts = np.unique(synthetic_codes, return_counts=True)
    ref_keys, ref_counts = np.unique(reference_codes, return_counts=True)
    support = np.union1d(syn_keys, ref_keys)
    syn_raw = np.zeros(len(support), dtype=np.float64)
    ref_raw = np.zeros(len(support), dtype=np.float64)
    if len(syn_keys):
        syn_raw[np.searchsorted(support, syn_keys)] = syn_counts
    if len(ref_keys):
        ref_raw[np.searchsorted(support, ref_keys)] = ref_counts

    syn_probability = syn_raw / syn_raw.sum() if syn_raw.sum() else syn_raw.copy()
    ref_probability = ref_raw / ref_raw.sum() if ref_raw.sum() else ref_raw.copy()

    def summarize(left: np.ndarray, right: np.ndarray) -> dict[str, float]:
        denominator = left.sum() + right.sum()
        delta = left - right
        summary = {
            "cpc": float(2.0 * np.minimum(left, right).sum() / denominator) if denominator else 1.0,
            "rmse_nonzero_union": float(np.sqrt(np.mean(delta * delta))) if len(delta) else 0.0,
            "mae_nonzero_union": float(np.mean(np.abs(delta))) if len(delta) else 0.0,
        }
        if total_pair_count:
            summary["rmse_all_od_pairs"] = float(
                np.sqrt(np.sum(delta * delta) / total_pair_count)
            )
            summary["mae_all_od_pairs"] = float(
                np.sum(np.abs(delta)) / total_pair_count
            )
        return summary

    return {
        "synthetic_flows": int(syn_raw.sum()),
        "reference_flows": int(ref_raw.sum()),
        "support_size": int(len(support)),
        "total_possible_od_pairs": int(total_pair_count) if total_pair_count else None,
        "normalized_probability": summarize(syn_probability, ref_probability),
        "raw_counts": summarize(syn_raw, ref_raw),
    }


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): json_ready(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_ready(v) for v in value]
    if isinstance(value, np.ndarray):
        return json_ready(value.tolist())
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return finite_or_string(float(value))
    if isinstance(value, float) and not math.isfinite(value):
        return finite_or_string(value)
    return value


def write_json(result: dict[str, Any], output: str | Path | None) -> None:
    text = json.dumps(json_ready(result), ensure_ascii=False, indent=2)
    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
        print(f"结果已写入: {path.resolve()}")
    else:
        print(text)


def base_result(method: str, synthetic: TrajectoryDataset, reference: TrajectoryDataset | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "method": method,
        "synthetic": {
            "path": str(synthetic.path),
            "source": synthetic.source,
            "city": synthetic.city,
            "shape": list(synthetic.traj.shape),
            "grid_shape": list(synthetic.grid_shape),
            "slot_minutes": synthetic.slot_minutes,
            "sha256": synthetic.metadata.get("worldmove_sha256"),
        },
    }
    if reference is not None:
        result["reference"] = {
            "path": str(reference.path),
            "source": reference.source,
            "city": reference.city,
            "shape": list(reference.traj.shape),
            "grid_shape": list(reference.grid_shape),
            "slot_minutes": reference.slot_minutes,
            "metadata": reference.metadata,
        }
    return result


def parse_scales(text: str) -> list[int]:
    scales = sorted({int(item.strip()) for item in text.split(",") if item.strip()})
    if not scales or scales[0] < 1:
        raise ValueError("scales 应为逗号分隔的正整数，例如 1,2,4")
    return scales


def entropy_from_ids(ids: np.ndarray, normalized: bool = False) -> float:
    ids = np.asarray(ids, dtype=np.int64)
    ids = ids[ids >= 0]
    if not len(ids):
        return math.nan
    _, counts = np.unique(ids, return_counts=True)
    probabilities = counts / counts.sum()
    entropy = float(-np.sum(probabilities * np.log(probabilities)))
    if normalized and len(counts) > 1:
        entropy /= math.log(len(counts))
    elif normalized:
        entropy = 0.0
    return entropy


def categorical_jsd(synthetic: Iterable[Any], reference: Iterable[Any]) -> dict[str, Any]:
    from collections import Counter

    syn_counter = Counter(synthetic)
    ref_counter = Counter(reference)
    support = sorted(set(syn_counter) | set(ref_counter), key=str)
    syn = np.asarray([syn_counter[key] for key in support], dtype=np.float64)
    ref = np.asarray([ref_counter[key] for key in support], dtype=np.float64)
    return {
        "synthetic_count": int(syn.sum()),
        "reference_count": int(ref.sum()),
        "support_size": int(len(support)),
        "jsd_natural_log": jsd_probabilities(syn, ref) if support else 0.0,
    }
