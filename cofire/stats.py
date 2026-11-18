"""统计原语：区间估计、线性模型与序贯消耗。

显著性口径：单侧 alpha（默认 0.025），O'Brien-Fleming 形 Lan-DeMets 消耗，
信息比例按可见班次数占登记末次查看应见班次数计算。
"""
from __future__ import annotations

import math

import numpy as np
from scipy.stats import norm, t as t_dist

Z_95 = 1.959963984540054  # 双侧 95% 对应的标准正态分位


def wilson(k, n, z=Z_95):
    """二项比例的 Wilson 区间；n=0 时返回全宽并视为无信息。"""
    if n <= 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1.0 + z * z / n
    centre = p + z * z / (2 * n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((centre - half) / denom, (centre + half) / denom)


def newcombe(k1, n1, k2, n2, z=Z_95):
    """两组比例差（p1-p2）的 Newcombe 区间，用于与对照比较的诊断。"""
    if n1 <= 0 or n2 <= 0:
        return (math.nan, math.nan)
    lo1, hi1 = wilson(k1, n1, z)
    lo2, hi2 = wilson(k2, n2, z)
    p1, p2 = k1 / n1, k2 / n2
    return (p1 - p2 - math.sqrt((p1 - lo1) ** 2 + (hi2 - p2) ** 2),
            p1 - p2 + math.sqrt((hi1 - p1) ** 2 + (p2 - lo2) ** 2))


def obf_boundary(alpha, info_fraction):
    """O'Brien-Fleming 形单侧界值：z / sqrt(信息比例)。"""
    info_fraction = min(max(info_fraction, 1e-9), 1.0)
    return norm.ppf(1.0 - alpha) / math.sqrt(info_fraction)


def spent_alpha(alpha, info_fraction):
    """到信息比例 t 为止累计消耗的单侧 alpha。"""
    info_fraction = min(max(info_fraction, 1e-9), 1.0)
    return 1.0 - norm.cdf(norm.ppf(1.0 - alpha) / math.sqrt(info_fraction))


def ols(y, x):
    """最小二乘；返回 (系数, 标准误, 残差自由度)。秩不足时标准误为 nan。"""
    y = np.asarray(y, dtype=float)
    x = np.asarray(x, dtype=float)
    n, p = x.shape
    if n <= p:
        return None, None, n - p
    beta, *_ = np.linalg.lstsq(x, y, rcond=None)
    resid = y - x @ beta
    dof = n - p
    s2 = float(resid @ resid) / dof
    cov = np.linalg.pinv(x.T @ x) * s2
    return beta, np.sqrt(np.diag(cov)), dof


def mean_ci(values, alpha=0.05):
    """均值的双侧 t 区间。"""
    values = list(values)
    n = len(values)
    if n < 2:
        return (math.nan, math.nan)
    mean = sum(values) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1))
    half = t_dist.ppf(1 - alpha / 2, n - 1) * sd / math.sqrt(n)
    return (mean - half, mean + half)


def shifts_needed_for_rate(k, n, limit, z=Z_95, cap=100000):
    """按当前事件率外推，Wilson 上界降到 limit 以下大约需要的班次数。

    率已高于 limit 时返回 None（靠加班次无法达成，需改变条件本身）。
    """
    if n > 0 and k / n > limit:
        return None
    for target in range(n, cap):
        if wilson(k, target, z)[1] <= limit:
            return target
    return None
