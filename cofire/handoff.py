"""运行交接报告：可用工况范围、不确定性、未执行计划与结论限制。"""
from __future__ import annotations

from .analysis import MIN_CELL_SHIFTS, MIN_RATIO_SHIFTS, evidence_inventory
from .model import cell_text
from .recommend import VERDICT_TEXT


def _fmt_ci(ci, digits=3):
    if not ci or any(v != v for v in ci):  # nan
        return "无信息"
    return f"[{ci[0]:.{digits}f}, {ci[1]:.{digits}f}]"


def render_handoff(registry, views, requests, look, decision_round):
    lines = []
    lines.append("# 掺烧试验运行交接")
    lines.append("")
    status = "确认性有效" if registry.confirmatory else "确认性已失效（结论为探索性）"
    lines.append(f"- 登记：{registry.review_id}（哈希 {registry.hash()}），{status}")
    if registry.void_reasons:
        for item in registry.void_reasons:
            lines.append(f"  - 失效原因：{item['reason']}（{item['at']}）")
    lines.append(f"- 主要指标：{registry.primary_metric}，"
                 f"登记目标：差值上界 ≤ {registry.effect_target:g} kg/t")
    lines.append(f"- 风险上限：越限率 ≤ {registry.emission_risk_limit:g}，"
                 f"不足率 ≤ {registry.steam_risk_limit:g}（按班次有无评估）")
    lines.append("")

    # 一、效果结论
    lines.append("## 一、效果结论（原定方案口径，经负荷/含水率/煤种/炉组/前序替代比调整）")
    if look is None:
        lines.append("尚未执行登记查看，无效果结论。")
    else:
        lines.append(f"最近查看：第{look['look_day']}天，数据截止{look['cutoff']}，"
                     f"班次 {look['n_shifts']}，信息比例 {look['info_fraction']}，"
                     f"本轮界值 {look['alpha_bound']}，累计消耗 alpha "
                     f"{look['spent_alpha']}")
        if not look["confirmatory"]:
            lines.append("该查看不具备确认性（未登记查看/重复查看/部分炉组/登记已修订）。")
        lines.append("")
        lines.append("| 替代比 | 调整后差值 | 序贯区间上界 | 状态 |")
        lines.append("| --- | --- | --- | --- |")
        status_text = {"met": "达到登记目标", "uncertain": "证据未齐",
                       "not_met": "确认不达标", "insufficient": "班次不足"}
        for lv, c in sorted(look["contrasts_itt"].items(), key=lambda x: float(x[0])):
            st = look["effect_status"].get(lv, "insufficient")
            if "estimate" in c:
                lines.append(f"| {lv} | {c['estimate']:.3f} | {c['upper']:.3f} "
                             f"| {status_text[st]} |")
            else:
                lines.append(f"| {lv} | — | — | {status_text[st]} |")
    lines.append("")

    # 二、风险监测（全部可见资料，安全监测不占显著性额度）
    lines.append("## 二、风险监测（全部可见资料；放行按登记上限的绝对口径）")
    from .analysis import risk_table
    risks = risk_table(views, registry.emission_risk_limit,
                       registry.steam_risk_limit)
    lines.append("| 替代比 | 班次 | 越限率[95%区间] | 判定 | 不足率[95%区间] | 判定 |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    status_cn = {"within": "上限内", "uncertain": "未决", "exceeds": "超上限"}
    for lv, row in sorted(risks.items(), key=lambda x: float(x[0])):
        em, st = row["emission"], row["steam"]
        lines.append(
            f"| {lv} | {row['n_shifts']} | {em['rate']:.3f}{_fmt_ci(em['ci'])} "
            f"| {status_cn[em['status']]} | {st['rate']:.3f}{_fmt_ci(st['ci'])} "
            f"| {status_cn[st['status']]} |")
    lines.append("")

    # 三、可用工况范围
    lines.append("## 三、可用工况范围")
    inventory = evidence_inventory(views)
    stopped_cells = {v.cell for v in views if v.stopped}
    # 与判断引擎一致：正比例一旦出现安全停试，该比例各工况格都不可继续
    stopped_ratios = {v.treatment for v in views if v.stopped and v.treatment != 0}
    released, continuable = [], []
    for key, n in sorted(inventory["cells"].items()):
        rkey = f"{key[0]:g}"
        row = risks.get(rkey, {})
        em_ok = row.get("emission", {}).get("status") == "within"
        st_ok = row.get("steam", {}).get("status") == "within"
        eff_ok = look and look["effect_status"].get(rkey) == "met"
        supported = n >= MIN_CELL_SHIFTS and inventory["ratios"].get(rkey, 0) >= MIN_RATIO_SHIFTS
        blocked = key in stopped_cells or key[0] in stopped_ratios
        if supported and not blocked and em_ok and st_ok and eff_ok:
            released.append((key, n))
        elif not blocked and supported:
            rel = row.get("steam", {}).get("vs_control")
            worse = rel and rel == rel and rel[0] > 0
            if not worse:
                continuable.append((key, n))
    if released:
        lines.append("可放行工况：")
        for key, n in released:
            lines.append(f"- {cell_text(key)}（{n}班）")
    else:
        lines.append("可放行工况：无。任何工况格都未同时满足效果目标与两类风险上限；"
                     "不得把全厂或对照的平均安全推广到未支持的工况。")
    if continuable:
        lines.append("可继续试验（不可放行）工况：")
        for key, n in continuable:
            lines.append(f"- {cell_text(key)}（{n}班）")
    else:
        lines.append("可继续试验工况：无。")
    lines.append("")

    # 四、未执行计划与偏离
    lines.append("## 四、未执行计划与偏离")
    unread = [v.shift_id for v in views if v.n_points == 0]
    stopped = [v for v in views if v.stopped]
    other_dev = [v for v in views if v.deviations and not v.stopped]
    lines.append(f"- 已登记未产生测点的班次：{','.join(unread) if unread else '无'}")
    if stopped:
        lines.append("- 安全停试班次（保留在原定方案口径内，未从分析中删除）：")
        for v in stopped:
            reasons = ";".join(f"{k}({r})" for k, r in v.deviations)
            lines.append(f"  - {v.shift_id}（炉组{v.boiler_id}，计划替代比"
                         f"{v.treatment:g}）：{reasons}")
    else:
        lines.append("- 安全停试班次：无")
    if other_dev:
        lines.append("- 其他偏离班次：" + ",".join(v.shift_id for v in other_dev))
    pending = [r for r in requests if r.status in ("draft", "approved")]
    if pending:
        lines.append("- 未开始的下一轮请求："
                     + ",".join(f"{r.request_id}({r.status})" for r in pending))
    lines.append("")

    # 五、结论限制
    lines.append("## 五、结论限制")
    limits = []
    if look:
        limits.extend(look["unidentifiable"]["notes"])
    control = risks.get("0")
    if control:
        if control["steam"]["rate"] > registry.steam_risk_limit:
            limits.append(
                f"不足率登记上限{registry.steam_risk_limit:g}低于对照班次实测"
                f"{control['steam']['rate']:.3f}：按现口径任何工况都无法放行，"
                "属运行基线与登记口径的冲突，需复核口径或改善供汽基线。")
        if control["emission"]["rate"] > registry.emission_risk_limit:
            limits.append("越限率登记上限低于对照实测，同样需复核口径。")
    limits.append("工况连续变量（负荷、含水率）上的结论只覆盖已有班次支持的区间，"
                  "区间外工况无证据。")
    if not registry.confirmatory:
        limits.append("登记确认性已失效，全部结论仅为探索性参考。")
    for item in limits:
        lines.append(f"- {item}")
    lines.append("")

    # 六、下一轮建议
    lines.append("## 六、下一轮建议")
    if decision_round is None:
        lines.append("尚未产生判断。")
    else:
        lines.append(f"判断时间 {decision_round['decided_at']}，"
                     f"数据截止 {decision_round['data_cutoff']}，"
                     f"登记哈希 {decision_round['registry_hash']}")
        lines.append("")
        for d in decision_round["decisions"]:
            req = d["request"]
            lines.append(
                f"### {d['request_id']}：{VERDICT_TEXT[d['verdict']]}"
                f"（炉组{req['boiler_id']}，替代比{req['ratio']:g}，"
                f"负荷{req['load']:g}，{req['coal_grade']}，含水{req['moisture_pct']:g}%）")
            for reason in d["rationale"]:
                lines.append(f"- {reason}")
            lines.append(f"- 交接动作：{d['action']['detail']}")
        lines.append("")
        lines.append("逐条回查：`python3 -m cofire why <工作区> <请求号>`。")
    return "\n".join(lines) + "\n"
