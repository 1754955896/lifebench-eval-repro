"""
共享统计指标函数集

供 analyze/ 下各分析引擎复用：
- 分布距离：js_divergence / circular_wasserstein / wasserstein_log10 / cosine_similarity
- 分布统计：normalized_entropy / counts_to_probs / gini
- 地理计算：haversine / bounding_box_area
- 自举基线：bootstrap_baseline（scalar 与 histogram 两种模式统一）
"""
import math
import random
from collections import Counter

import numpy as np
from scipy.spatial.distance import jensenshannon
from scipy.stats import entropy, wasserstein_distance


def js_divergence(p, q):
    """
    Jensen-Shannon 散度

    输入:
    - p: np.ndarray - 概率分布
    - q: np.ndarray - 概率分布

    输出:
    - float: JS 散度（scipy 返回开方值，此处平方还原）
    """
    v = jensenshannon(p, q)
    return float(v ** 2)


def cosine_similarity(p, q):
    """
    余弦相似度

    输入:
    - p: np.ndarray - 向量
    - q: np.ndarray - 向量

    输出:
    - float: 余弦相似度
    """
    return float(np.dot(p, q) / (np.linalg.norm(p) * np.linalg.norm(q)))


def normalized_entropy(probs):
    """
    归一化 Shannon 熵

    输入:
    - probs: np.ndarray - 概率分布

    输出:
    - float: entropy / log(K)，K<=1 时返回 0.0
    """
    K = len(probs)
    if K <= 1:
        return 0.0
    return float(entropy(probs) / math.log(K))


def counts_to_probs(counts_dict, keys):
    """
    按指定键序将计数转为概率数组

    输入:
    - counts_dict: dict - 键到计数的映射
    - keys: list - 键序（缺失键补 0）

    输出:
    - np.ndarray: 概率数组
    """
    arr = np.array([counts_dict.get(k, 0) for k in keys], dtype=float)
    total = arr.sum()
    return arr / total if total > 0 else arr


def wasserstein_log10(vals1, vals2):
    """
    对 log10(x+1) 变换后序列计算 Wasserstein 距离

    输入:
    - vals1: list - 数值序列
    - vals2: list - 数值序列

    输出:
    - float: 经验 CDF 积分实现的 Wasserstein 距离
    """
    v1 = np.log10(np.array(vals1, dtype=float) + 1)
    v2 = np.log10(np.array(vals2, dtype=float) + 1)
    all_vals = np.sort(np.concatenate([v1, v2]))
    cdf1 = np.searchsorted(np.sort(v1), all_vals, side='right') / len(v1)
    cdf2 = np.searchsorted(np.sort(v2), all_vals, side='right') / len(v2)
    dx = np.diff(all_vals)
    w = np.sum(np.abs(cdf1[:-1] - cdf2[:-1]) * dx)
    return float(w)


def circular_wasserstein(p, q, n_bins):
    """
    环形分布上的 Wasserstein 距离

    输入:
    - p: np.ndarray - 分布
    - q: np.ndarray - 分布
    - n_bins: int - 环上离散位置数

    输出:
    - float: 遍历所有循环移位取最小的 Wasserstein 距离
    """
    best = float("inf")
    x = np.arange(n_bins)
    for shift in range(n_bins):
        q_shifted = np.roll(q, shift)
        w = wasserstein_distance(x, x, p, q_shifted)
        if w < best:
            best = w
    return float(best)


def gini(values):
    """
    基尼系数

    输入:
    - values: list - 数值序列

    输出:
    - float: 基尼系数
    """
    arr = np.sort(np.asarray(values, dtype=float))
    n = len(arr)
    index = np.arange(1, n + 1)
    return float((2 * np.sum(index * arr) - (n + 1) * np.sum(arr)) / (n * np.sum(arr)))


def haversine(lat1, lon1, lat2, lon2):
    """
    两点间球面距离

    输入:
    - lat1, lon1: float - 第一个点的纬度/经度
    - lat2, lon2: float - 第二个点的纬度/经度

    输出:
    - float: 距离（km）
    """
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def bounding_box_area(coords_list):
    """
    坐标列表包围盒面积

    输入:
    - coords_list: list - (lat, lng) 元组列表

    输出:
    - float: 包围盒面积（km²），少于 2 个点时返回 0.0
    """
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
    """
    自举基线估计（scalar 与 histogram 两种模式统一）

    输入:
    - full_pop: list - 基线总体（如 ATM 全部样本）
    - n_sample: int - 每次抽样的样本量（如 len(yw)）
    - n_iter: int - 自举迭代次数
    - metric_fn: callable - 距离度量函数
    - extract_fn: callable|None - histogram 模式的键提取函数；为 None 时走 scalar 模式
    - keys: list|None - 显式分箱键（缺失分箱需传入，如 range(24)）
    - percentiles: tuple - 输出分位数（如 (5, 50, 95)）

    输出:
    - dict: {"mean","std",各分位数键}；scalar 模式额外含 "full_value"
    """
    if extract_fn is None:
        # scalar 模式：metric_fn 作用于总体/子集，返回标量
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
        # histogram 模式：metric_fn 作用于 (样本分布, 总体分布)
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
