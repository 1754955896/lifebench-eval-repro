"""This module calculates evaluation metrics."""

import math
import random
from collections import Counter

import numpy as np
from scipy.spatial.distance import jensenshannon
from scipy.stats import entropy, wasserstein_distance


def js_divergence(p, q):


    v = jensenshannon(p, q)
    return float(v ** 2)


def cosine_similarity(p, q):


    return float(np.dot(p, q) / (np.linalg.norm(p) * np.linalg.norm(q)))


def normalized_entropy(probs):


    K = len(probs)
    if K <= 1:
        return 0.0
    return float(entropy(probs) / math.log(K))


def counts_to_probs(counts_dict, keys):


    arr = np.array([counts_dict.get(k, 0) for k in keys], dtype=float)
    total = arr.sum()
    return arr / total if total > 0 else arr


def wasserstein_log10(vals1, vals2):


    v1 = np.log10(np.array(vals1, dtype=float) + 1)
    v2 = np.log10(np.array(vals2, dtype=float) + 1)
    all_vals = np.sort(np.concatenate([v1, v2]))
    cdf1 = np.searchsorted(np.sort(v1), all_vals, side='right') / len(v1)
    cdf2 = np.searchsorted(np.sort(v2), all_vals, side='right') / len(v2)
    dx = np.diff(all_vals)
    w = np.sum(np.abs(cdf1[:-1] - cdf2[:-1]) * dx)
    return float(w)


def circular_wasserstein(p, q, n_bins):


    best = float("inf")
    x = np.arange(n_bins)
    for shift in range(n_bins):
        q_shifted = np.roll(q, shift)
        w = wasserstein_distance(x, x, p, q_shifted)
        if w < best:
            best = w
    return float(best)


def gini(values):


    arr = np.sort(np.asarray(values, dtype=float))
    n = len(arr)
    index = np.arange(1, n + 1)
    return float((2 * np.sum(index * arr) - (n + 1) * np.sum(arr)) / (n * np.sum(arr)))


def haversine(lat1, lon1, lat2, lon2):


    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def bounding_box_area(coords_list):


    if len(coords_list) < 2:
        return 0.0
    lats = [c[0] for c in coords_list]
    lngs = [c[1] for c in coords_list]
    lat_range = max(lats) - min(lats)
    lng_range = max(lngs) - min(lngs)
    mid_lat = np.mean(lats)
    lat_km = lat_range * 111.32
    lng_km = lng_range * 111.32 * math.cos(math.radians(mid_lat))
    return lat_km * lng_km


def bootstrap_baseline(full_pop, n_sample, n_iter, metric_fn, extract_fn=None, keys=None, percentiles=(95,)):


    if extract_fn is None:

        full_val = metric_fn(full_pop)
        values = []
        for _ in range(n_iter):
            subset = random.sample(full_pop, n_sample)
            values.append(metric_fn(subset))
        result = {
            "mean": float(np.mean(values)),
            "std": float(np.std(values, ddof=1)),
            "full_value": float(full_val),
        }
    else:

        full_counter = Counter()
        for item in full_pop:
            full_counter[extract_fn(item)] += 1
        if keys is None:
            keys = sorted(full_counter.keys())
        K = len(keys)
        values = []
        for _ in range(n_iter):
            subset = random.sample(full_pop, n_sample)
            sub_counter = Counter()
            for item in subset:
                sub_counter[extract_fn(item)] += 1
            p = counts_to_probs(sub_counter, keys)
            q = counts_to_probs(full_counter, keys)
            p_s = (p * n_sample + 1) / (n_sample + K)
            q_s = (q * len(full_pop) + 1) / (len(full_pop) + K)
            values.append(metric_fn(p_s, q_s))
        result = {
            "mean": float(np.mean(values)),
            "std": float(np.std(values, ddof=1)),
        }
    for pct in percentiles:
        result[f"p{pct}"] = float(np.percentile(values, pct))
    return result
