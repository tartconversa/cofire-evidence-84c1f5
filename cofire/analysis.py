"""逐轮查看与证据汇总。

三种口径分开报告：
- 原定方案（ITT）：按计划替代比分组，停试班次保留在内；
- 实际可运行（PP）：无偏离、执行与计划一致、负荷在登记范围内的班次；
- 无法识别部分：安全停试等使反事实不可得的班次，单列而不悄悄剔除。

显著性只在登记查看日按 O'Brien-Fleming 消耗解释；风险监测使用全部可见
资料，不占显著性额度，但也无权给出"效果确认"。
"""
from __future__ import annotations

import datetime
import math

from .model import cell_key, cell_text
from .stats import (newcombe, obf_boundary, ols, shifts_needed_for_rate,
                    spent_alpha, wilson)

PP_RATIO_TOL = 0.02   # 执行替代比与计划一致的判断容差
PP_LOAD_TOL = 0.05    # 实际负荷与计划负荷一致的判断容差
MIN_RATIO_SHIFTS = 8  # 比例层可评估的最少班次数
MIN_CELL_SHIFTS = 2   # 工况格获得支持的最少班次数


def _design(views, levels, boilers):
    """ITT 调整模型：处理哑变量 + 负荷 + 含水率 + 煤种 + 炉组 + 前序替代比。"""
    boiler_ids = sorted({v.boiler_id for v in views} | set(boilers))
    coal_grades = sorted({v.coal_grade for v in views})
    header = ["const"] + [f"T{lv:g}" for lv in levels] + ["load", "moisture"]
    header += [f"coal:{g}" for g in coal_grades[1:]]
    header += [f"boiler:{b}" for b in boiler_ids[1:]]
    header += ["lag1", "lag2"]
    rows = []
    for v in views:
        row = [1.0] + [1.0 if v.treatment == lv else 0.0 for lv in levels]
        row += [v.actual_load, v.moisture_pct]
        row += [1.0 if v.coal_grade == g else 0.0 for g in coal_grades[1:]]
        row += [1.0 if v.boiler_id == b else 0.0 for b in boiler_ids[1:]]
        row += [v.prev_treatments[0] if len(v.prev_treatments) > 0 else 0.0,
                v.prev_treatments[1] if len(v.prev_treatments) > 1 else 0.0]
        rows.append(row)
    return header, rows


def _contrasts(views, levels, boilers, bound):
    """各替代比例相对对照的调整后差值；bound 为该查看的序贯界值。"""
    header, rows = _design(views, levels, boilers)
    y = [v.coal_per_steam for v in views]
    beta, se, dof = _safe_ols(y, rows)
    out = {}
    if beta is None:
        for lv in levels:
            out[f"{lv:g}"] = {"status": "insufficient", "n_shifts": len(views)}
        return out
    for i, lv in enumerate(levels):
        est, err = float(beta[1 + i]), float(se[1 + i])
        upper = est + bound * err
        lower = est - bound * err
        # 状态判定在 effect_status 按登记目标完成；此处给出原始量
        out[f"{lv:g}"] = {
            "estimate": est, "se": err, "upper": upper, "lower": lower,
            "dof": dof, "n_shifts": len(views),
        }
    return out


def _safe_ols(y, rows):
    try:
        return ols(y, rows)
    except Exception:
        return None, None, 0


def effect_status(contrast, target):
    """按登记效果目标判定：met / uncertain / not_met。"""
    if "estimate" not in contrast:
        return "insufficient"
    if contrast["upper"] <= target:
        return "met"
    if contrast["lower"] > target:
        return "not_met"
    return "uncertain"


def _risk_row(views, limit):
    n = len(views)
    k_breach = sum(1 for v in views if v.emission_breach)
    k_short = sum(1 for v in views if v.steam_shortfall)
    return {
        "n_shifts": n,
        "breach": {"k": k_breach, "rate": k_breach / n if n else math.nan,
                   "ci": wilson(k_breach, n)},
        "shortfall": {"k": k_short, "rate": k_short / n if n else math.nan,
                      "ci": wilson(k_short, n)},
    }


def rate_status(k, n, limit):
    """越限/不足率相对登记上限：within / uncertain / exceeds。"""
    lo, hi = wilson(k, n)
    if lo > limit:
        return "exceeds"
    if hi <= limit:
        return "within"
    return "uncertain"


def risk_table(views, emission_limit, steam_limit):
    """分比例的班次级风险表：绝对口径（放行用）与相对对照（继续试验用）。"""
    levels = sorted({v.treatment for v in views})
    table = {}
    control = [v for v in views if v.treatment == 0.0]
    c_breach = sum(1 for v in control if v.emission_breach)
    c_short = sum(1 for v in control if v.steam_shortfall)
    for lv in levels:
        group = [v for v in views if v.treatment == lv]
        n = len(group)
        k_b = sum(1 for v in group if v.emission_breach)
        k_s = sum(1 for v in group if v.steam_shortfall)
        table[f"{lv:g}"] = {
            "n_shifts": n,
            "emission": {"k": k_b, "rate": k_b / n if n else math.nan,
                         "ci": wilson(k_b, n),
                         "status": rate_status(k_b, n, emission_limit),
                         "need_n": shifts_needed_for_rate(k_b, n, emission_limit),
                         "vs_control": newcombe(k_b, n, c_breach, len(control))},
            "steam": {"k": k_s, "rate": k_s / n if n else math.nan,
                      "ci": wilson(k_s, n),
                      "status": rate_status(k_s, n, steam_limit),
                      "need_n": shifts_needed_for_rate(k_s, n, steam_limit),
                      "vs_control": newcombe(k_s, n, c_short, len(control))},
        }
    return table


def evidence_inventory(views):
    """证据范围：比例层与工况格的班次计数。无支持的工况不得外推。"""
    ratios = {}
    cells = {}
    marginals = {}
    for v in views:
        if v.n_points == 0:
            continue
        rkey = f"{v.treatment:g}"
        ratios[rkey] = ratios.get(rkey, 0) + 1
        cells[v.cell] = cells.get(v.cell, 0) + 1
        ratio, load, coal, moist = v.cell
        for dim, key in (("load", (rkey, load)), ("coal", (rkey, coal)),
                         ("moisture", (rkey, moist))):
            marginals.setdefault(dim, {}).setdefault(key, 0)
            marginals[dim][key] += 1
    return {"ratios": ratios, "cells": cells, "marginals": marginals}


def split_protocol(views, registry):
    """拆分原定方案集与实际可运行集；返回 (itt, pp, excluded)。"""
    itt = [v for v in views if v.n_points > 0]
    pp, excluded = [], []
    for v in itt:
        reasons = []
        if v.deviations:
            reasons.append("有偏离记录：" + ";".join(f"{k}({r})" for k, r in v.deviations))
        if abs(v.actual_ratio - v.treatment) > PP_RATIO_TOL:
            reasons.append(f"执行替代比{v.actual_ratio:.3f}偏离计划{v.treatment:g}")
        if abs(v.actual_load - v.planned_load) > PP_LOAD_TOL:
            reasons.append(f"实际负荷{v.actual_load:.3f}偏离计划{v.planned_load:g}")
        if not (registry.load_min <= v.actual_load <= registry.load_max):
            reasons.append("实际负荷超出登记范围")
        if reasons:
            excluded.append((v, reasons))
        else:
            pp.append(v)
    return itt, pp, excluded


def unidentifiable_report(views, planned_unread):
    """无法识别的部分：停试班次的反事实与未执行计划，不悄悄消失。"""
    stopped = [v for v in views if v.stopped]
    by_ratio = {}
    for v in stopped:
        by_ratio.setdefault(f"{v.treatment:g}", []).append(v.shift_id)
    notes = []
    if stopped:
        notes.append(
            "安全停试班次的后续反事实无法从资料识别；原定方案口径保留其观测值，"
            "相关结论依赖'停试班次结局可代表'这一无法验证的假设。"
        )
    if planned_unread:
        notes.append("已登记但未产生测点的班次：" + ",".join(planned_unread))
    if not notes:
        notes.append("本轮无停试或未执行班次。")
    return {
        "stopped": {k: sorted(v) for k, v in sorted(by_ratio.items())},
        "planned_unread": sorted(planned_unread),
        "notes": notes,
    }


def run_look(views, registry, day, now, subset=None):
    """执行一次查看。可见性由 available_at 与登记查看日共同决定。"""
    trial_start = min(v.start for v in views)
    cutoff = trial_start + datetime.timedelta(days=day)
    visible = [v for v in views if v.available_at <= cutoff and v.n_points > 0]
    planned_unread = [v.shift_id for v in views
                      if v.available_at <= cutoff and v.n_points == 0]
    registered = registry.check_look(day)
    subgroup = False
    if subset is not None:
        keep = set(subset)
        total = len({v.boiler_id for v in views})
        if len(keep) < total:
            subgroup = True
            registry.note_subgroup(len(keep), total, now)
        visible = [v for v in visible if v.boiler_id in keep]
    if not registered:
        registry.note_unregistered_look(day, now)
    registry.freeze(now)

    # 信息比例：可见班次 / 登记末次查看应见班次
    final_cut = trial_start + datetime.timedelta(days=max(registry.look_days))
    planned_total = len([v for v in views if v.start < final_cut])
    info = len(visible) / planned_total if planned_total else 0.0
    bound = obf_boundary(registry.alpha, info)

    levels = sorted({v.treatment for v in visible if v.treatment != 0.0})
    itt, pp, excluded = split_protocol(visible, registry)
    has_control = any(v.treatment == 0.0 for v in visible)
    if has_control and levels:
        contrasts_itt = _contrasts(itt, levels, {v.boiler_id for v in visible}, bound)
        contrasts_pp = _contrasts(pp, levels, {v.boiler_id for v in visible}, bound)
    else:  # 无对照班次时对照估计不可识别
        contrasts_itt = contrasts_pp = {
            f"{lv:g}": {"status": "insufficient", "n_shifts": len(visible)}
            for lv in levels
        }
    result = {
        "look_day": day,
        "executed_at": now.isoformat(),
        "registered": registered,
        "subgroup": subgroup,
        "cutoff": cutoff.isoformat(),
        "n_shifts": len(visible),
        "info_fraction": round(info, 4),
        "alpha_bound": round(bound, 4),
        "spent_alpha": round(spent_alpha(registry.alpha, info), 5),
        "registry_hash": registry.hash(),
        "confirmatory": registry.confirmatory and registered and not subgroup,
        "contrasts_itt": contrasts_itt,
        "contrasts_pp": contrasts_pp,
        "effect_status": {lv: effect_status(c, registry.effect_target)
                          for lv, c in contrasts_itt.items()},
        "risk": risk_table(visible, registry.emission_risk_limit,
                           registry.steam_risk_limit),
        "pp_excluded": [{"shift_id": v.shift_id, "reasons": r} for v, r in excluded],
        "unidentifiable": unidentifiable_report(visible, planned_unread),
    }
    return result
