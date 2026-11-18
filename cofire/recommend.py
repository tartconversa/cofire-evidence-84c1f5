"""下一轮建议：把查看结论、风险监测与证据范围合成可回查的判断。

判断口径（版本化，随登记哈希记录）：
- 放行（accept）：工况格有支持、效果达到登记目标、两类风险上界都在登记
  上限内、所在比例无未复核的安全停试；
- 可继续试验（continue）：不劣于对照且证据未齐，可在监测下继续积累，
  但不得放行；
- 暂停复核（hold）：所在比例或工况格出现安全停试，或关键证据缺口需要
  先补，暂停新安排；
- 拒绝（reject_*）：比例从未试验、风险已确认越上限、或效果已确认不达标。

风险放行用登记上限的绝对口径；全厂或对照的平均安全不能推广到没有支持
的工况。继续试验的门槛是相对对照不劣，与放行门槛分开表述。
"""
from __future__ import annotations

import math

from .analysis import (MIN_CELL_SHIFTS, MIN_RATIO_SHIFTS, effect_status,
                       evidence_inventory, risk_table)
from .model import cell_key, cell_text

VERDICT_TEXT = {
    "accept": "可放行",
    "continue": "可继续试验（不可放行）",
    "hold": "暂停复核",
    "reject_unsupported": "拒绝（无支持证据）",
    "reject_risk": "拒绝（不满足登记风险上限）",
    "reject_effect": "拒绝（效果确认不达标）",
}


def _support(req, inventory):
    """请求工况的证据支持度：full / partial / none，附缺口说明。"""
    rkey = f"{req.ratio:g}"
    ratios = inventory["ratios"]
    if rkey not in ratios:
        return "none", [f"替代比{req.ratio:g}从未进入已登记试验，不能外推任何平均结论"]
    key = cell_key(req.ratio, req.load, req.coal_grade, req.moisture_pct)
    cell_n = inventory["cells"].get(key, 0)
    ratio_n = ratios[rkey]
    gaps = []
    if ratio_n < MIN_RATIO_SHIFTS:
        gaps.append(f"替代比{req.ratio:g}累计{ratio_n}班（需≥{MIN_RATIO_SHIFTS}）")
    if cell_n < MIN_CELL_SHIFTS:
        gaps.append(
            f"工况格{cell_text(key)}仅{cell_n}班（需≥{MIN_CELL_SHIFTS}）"
        )
        for dim, label in (("load", "负荷带"), ("coal", "煤种"), ("moisture", "含水带")):
            want = {"load": (rkey, key[1]), "coal": (rkey, key[2]),
                    "moisture": (rkey, key[3])}[dim]
            got = inventory["marginals"].get(dim, {}).get(want, 0)
            if got < MIN_CELL_SHIFTS:
                gaps.append(f"{label}维度仅{got}班")
    if not gaps:
        return "full", []
    return ("none" if ratio_n < MIN_RATIO_SHIFTS else "partial"), gaps


def _stops(req, views):
    """请求比例与工况格内的安全停试。"""
    in_ratio, in_cell = [], []
    key = cell_key(req.ratio, req.load, req.coal_grade, req.moisture_pct)
    for v in views:
        if not v.stopped:
            continue
        if v.treatment == req.ratio:
            in_ratio.append(v.shift_id)
            if v.cell == key:
                in_cell.append(v.shift_id)
    return in_ratio, in_cell


def _effect_component(req, look):
    """效果分量取自最近一次已执行查看；未执行查看时不可用。"""
    rkey = f"{req.ratio:g}"
    if look is None:
        return {"status": "unavailable", "note": "尚未执行登记查看，无效果结论"}
    contrast = look["contrasts_itt"].get(rkey)
    if contrast is None:
        return {"status": "unavailable",
                "note": f"查看中替代比{req.ratio:g}无对照估计"}
    status = look["effect_status"].get(rkey, "insufficient")
    return {"status": status, "look_day": look["look_day"],
            "confirmatory": look["confirmatory"], **contrast}


def _risk_component(req, risks):
    rkey = f"{req.ratio:g}"
    row = risks.get(rkey)
    if row is None:
        return {"status": "unavailable"}
    control = risks.get("0", {})
    out = {"n_shifts": row["n_shifts"],
           "emission": row["emission"], "steam": row["steam"]}
    if control:
        out["control_rates"] = {
            "emission": control["emission"]["rate"],
            "steam": control["steam"]["rate"],
        }
    return out


def decide_requests(requests, views, registry, look, decided_at, data_cutoff):
    """对每条下一轮请求给出判断；每个分量都可回查。"""
    inventory = evidence_inventory(views)
    risks = risk_table(views, registry.emission_risk_limit,
                       registry.steam_risk_limit)
    decisions = []
    for req in requests:
        rationale = []
        support, gaps = _support(req, inventory)
        stop_ratio, stop_cell = _stops(req, views)
        effect = _effect_component(req, look)
        risk = _risk_component(req, risks)
        components = {"support": {"status": support, "gaps": gaps},
                      "stops": {"in_ratio": stop_ratio, "in_cell": stop_cell},
                      "effect": effect, "risk": risk}

        verdict = None
        if support == "none" and f"{req.ratio:g}" not in inventory["ratios"]:
            verdict = "reject_unsupported"
            rationale += gaps
        elif stop_cell:
            verdict = "hold"
            rationale.append(
                f"请求工况格内发生安全停试（{','.join(stop_cell)}），需复核后再安排")
        elif stop_ratio and req.ratio != 0:
            verdict = "hold"
            rationale.append(
                f"替代比{req.ratio:g}出现安全停试（{','.join(stop_ratio)}），"
                "该比例各工况暂停新安排，待复核")
        if verdict is None and risk.get("emission"):
            em, st = risk["emission"], risk["steam"]
            if em["status"] == "exceeds" or st["status"] == "exceeds":
                verdict = "reject_risk"
                which = []
                if em["status"] == "exceeds":
                    which.append(
                        f"越限率{em['rate']:.3f}下界超上限{registry.emission_risk_limit:g}")
                if st["status"] == "exceeds":
                    which.append(
                        f"不足率{st['rate']:.3f}下界超上限{registry.steam_risk_limit:g}")
                rationale.append("；".join(which))
                ctrl = risk.get("control_rates")
                if ctrl and any(
                        ctrl[k] > lim for k, lim in
                        (("emission", registry.emission_risk_limit),
                         ("steam", registry.steam_risk_limit))):
                    rationale.append(
                        "对照班次同样超出登记上限：属运行基线与登记口径的冲突，"
                        "不能据此认定燃料归因，需复核登记口径或改善基线")
        if verdict is None and support != "full":
            verdict = "hold"
            rationale += gaps or ["证据范围不足"]
        if verdict is None and effect["status"] == "not_met":
            verdict = "reject_effect"
            rationale.append(
                f"调整后煤耗差{effect.get('estimate', float('nan')):.3f}，"
                f"区间下界仍高于登记目标{registry.effect_target:g}")
        if verdict is None and effect["status"] in ("uncertain", "insufficient",
                                                    "unavailable"):
            verdict = "continue"
            rationale.append("效果证据未齐：" + effect.get("note", "区间仍跨过登记目标"))
        if verdict is None:
            em, st = risk["emission"], risk["steam"]
            if em["status"] == "within" and st["status"] == "within":
                verdict = "accept"
                rationale.append("效果达标且两类风险上界均在登记上限内")
            else:
                verdict = "continue"
                for label, part, lim in (("越限率", em, registry.emission_risk_limit),
                                         ("不足率", st, registry.steam_risk_limit)):
                    if part["status"] == "uncertain":
                        need = part.get("need_n")
                        extra = f"，约需累计{need}班" if need else "，靠加班次无法达成"
                        rationale.append(
                            f"{label}区间仍跨过上限{lim:g}"
                            f"（当前{part['k']}/{risk['n_shifts']}班{extra}）")
        if not registry.confirmatory or (look and not look["confirmatory"]):
            rationale.append("登记确认性已失效，本判断仅为探索性参考")
        decisions.append({
            "request_id": req.request_id,
            "request": {"boiler_id": req.boiler_id, "ratio": req.ratio,
                        "load": req.load, "coal_grade": req.coal_grade,
                        "moisture_pct": req.moisture_pct, "status": req.status},
            "verdict": verdict,
            "verdict_text": VERDICT_TEXT[verdict],
            "components": components,
            "rationale": rationale,
            "action": _action_for(req, verdict),
        })
    return {
        "decided_at": decided_at.isoformat(),
        "data_cutoff": data_cutoff.isoformat(),
        "registry_hash": registry.hash(),
        "confirmatory": registry.confirmatory and bool(look and look["confirmatory"]),
        "look_day": look["look_day"] if look else None,
        "decisions": decisions,
    }


def _action_for(req, verdict):
    """按执行状态给出交接动作：未开始的重评依据，执行中的保留依据并提示复核。"""
    bad = verdict in ("hold", "reject_risk", "reject_unsupported", "reject_effect")
    if req.status == "approved":
        if bad:
            return {"kind": "revoke_approval",
                    "detail": "已批准未开始：原批准依据不再成立，需重新评估后方可执行"}
        return {"kind": "affirm_approval", "detail": "已批准未开始：依据仍然成立"}
    if req.status == "running":
        if bad:
            return {"kind": "keep_basis_review",
                    "detail": "正在执行：保留原批准依据，但发出复核提示，由运行方决定"}
        return {"kind": "keep_basis", "detail": "正在执行：依据不变"}
    if req.status == "draft":
        return {"kind": "advice", "detail": "草案：本判断供登记参考"}
    return {"kind": "none", "detail": f"状态{req.status}：无需动作"}


def explain_change(previous, current):
    """比较同一请求相邻两轮判断，说明建议为什么变化。"""
    lines = []
    if previous is None:
        lines.append("首轮判断，无前一可比较轮次。")
        return lines
    if previous["verdict"] != current["verdict"]:
        lines.append(
            f"判断由「{VERDICT_TEXT[previous['verdict']]}」变为"
            f"「{VERDICT_TEXT[current['verdict']]}」。")
    else:
        lines.append(f"判断维持「{VERDICT_TEXT[current['verdict']]}」。")
    if previous["data_cutoff"] != current["data_cutoff"]:
        lines.append(
            f"数据截止点由{previous['data_cutoff']}推进到{current['data_cutoff']}。")
    for name, label in (("effect", "效果"), ):
        old = previous["decision"]["components"].get(name, {}).get("status")
        new = current["decision"]["components"].get(name, {}).get("status")
        if old != new:
            lines.append(f"{label}分量：{old} → {new}。")
    for name, label in (("emission", "越限率"), ("steam", "不足率")):
        old = previous["decision"]["components"].get("risk", {}).get(name, {})
        new = current["decision"]["components"].get("risk", {}).get(name, {})
        if old.get("status") != new.get("status"):
            lines.append(
                f"{label}：{old.get('status')} → {new.get('status')}"
                f"（{old.get('k')}/{old.get('n_shifts')} → "
                f"{new.get('k')}/{new.get('n_shifts')}班）。")
    old_stops = set(previous["decision"]["components"]["stops"]["in_ratio"])
    new_stops = set(current["decision"]["components"]["stops"]["in_ratio"])
    added = new_stops - old_stops
    if added:
        lines.append("新增安全停试班次：" + ",".join(sorted(added)) + "。")
    old_sup = previous["decision"]["components"]["support"]["status"]
    new_sup = current["decision"]["components"]["support"]["status"]
    if old_sup != new_sup:
        lines.append(f"证据支持度：{old_sup} → {new_sup}。")
    if previous["registry_hash"] != current["registry_hash"]:
        lines.append("判断口径（登记哈希）已变化，显著性解释不可直接沿用。")
    return lines
