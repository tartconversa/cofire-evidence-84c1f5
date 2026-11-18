"""命令行入口：登记、逐轮查看、建议、回查与交接。

用法概览：
    python3 -m cofire check <资料包>
    python3 -m cofire init <工作区> --pack <资料包>
    python3 -m cofire ingest <工作区> --pack <资料包>
    python3 -m cofire look <工作区> --day 8
    python3 -m cofire decide <工作区>
    python3 -m cofire why <工作区> <请求号>
    python3 -m cofire handoff <工作区> [-o 文件]
    python3 -m cofire status <工作区>
    python3 -m cofire amend <工作区> --set 字段=值 ...
    python3 -m cofire synth-round2 <输出目录>
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

from . import analysis, handoff, recommend, synth
from .model import build_shift_views, parse_pack
from .pack import load_pack
from .registry import Registry
from .store import Workspace

NOW = lambda: datetime.datetime.now(datetime.timezone.utc)


def _parse_value(name, text):
    if name == "look_days":
        return [int(x) for x in text.split(";") if x.strip()]
    if name == "primary_metric":
        return text
    return float(text)


def cmd_check(args):
    from sourcepack.catalog import inspect
    report = inspect(Path(args.pack))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def _load_views(pack):
    parsed = parse_pack(pack)
    views = build_shift_views(parsed["plans"], parsed["readings"],
                              parsed["deviations"], parsed["boilers"])
    return parsed, views


def cmd_init(args):
    pack = load_pack(args.pack)
    parsed, views = _load_views(pack)
    if not parsed["reviews"]:
        raise ValueError("资料包缺少 reviews 登记")
    registry = Registry(parsed["reviews"][0])
    trial_start = min(p.start for p in parsed["plans"])
    ws = Workspace(args.workspace)
    ws.init(registry, trial_start)
    added, skipped = ws.ingest_shifts(sorted(views.values(), key=lambda v: v.start))
    ws.upsert_requests(parsed["next_trials"], NOW())
    ws.note_round(pack.root, added, skipped, NOW())
    print(f"工作区已建立：{ws.root}，登记 {registry.review_id}"
          f"（哈希 {registry.hash()}），班次 {added} 条。")
    return 0


def cmd_ingest(args):
    ws = Workspace(args.workspace)
    registry = ws.load_registry()
    pack = load_pack(args.pack)
    parsed, views = _load_views(pack)
    # 登记一致性：资料包内的 reviews 与冻结登记不符即视为修订
    if parsed["reviews"]:
        review = parsed["reviews"][0]
        changes = {}
        if list(review.look_days) != registry.look_days:
            changes["look_days"] = list(review.look_days)
        for field in ("effect_target", "emission_risk_limit", "steam_risk_limit",
                      "load_min", "load_max"):
            if getattr(review, field) != getattr(registry, field):
                changes[field] = getattr(review, field)
        if changes:
            registry.amend(changes, NOW(), reason="资料包登记与冻结口径不一致")
    added, skipped = ws.ingest_shifts(sorted(views.values(), key=lambda v: v.start))
    ws.upsert_requests(parsed["next_trials"], NOW())
    ws.note_round(pack.root, added, skipped, NOW())
    ws.save_registry(registry)
    print(f"已并入 {added} 个新班次（跳过 {skipped} 个重复）；"
          f"登记确认性：{'有效' if registry.confirmatory else '已失效'}。")
    return 0


def cmd_look(args):
    ws = Workspace(args.workspace)
    registry = ws.load_registry()
    views = ws.shifts()
    done = {look["look_day"] for look in ws.looks()}
    if args.day in done and not args.force:
        print(f"第{args.day}天查看已执行过；如需重复请用 --force"
              "（将使确认性失效）。", file=sys.stderr)
        return 2
    if args.day in done:
        registry.note_repeat_look(args.day, NOW())
    subset = args.boilers.split(",") if args.boilers else None
    result = analysis.run_look(views, registry, args.day, NOW(), subset=subset)
    ws.save_registry(registry)
    ws.add_look(result)
    print(json.dumps({"look_day": result["look_day"],
                      "registered": result["registered"],
                      "n_shifts": result["n_shifts"],
                      "info_fraction": result["info_fraction"],
                      "confirmatory": result["confirmatory"],
                      "effect_status": result["effect_status"]},
                     ensure_ascii=False, indent=2))
    return 0


def _latest_look(ws):
    looks = ws.looks()
    return looks[-1] if looks else None


def cmd_decide(args):
    ws = Workspace(args.workspace)
    registry = ws.load_registry()
    views = ws.shifts()
    requests = ws.requests()
    if not requests:
        print("工作区没有下一轮请求。", file=sys.stderr)
        return 2
    cutoff = max(v.available_at for v in views)
    record = recommend.decide_requests(requests, views, registry,
                                       _latest_look(ws), NOW(), cutoff)
    ws.add_decisions(record)
    for d in record["decisions"]:
        print(f"{d['request_id']}: {d['verdict_text']} —— {d['action']['detail']}")
    return 0


def cmd_why(args):
    ws = Workspace(args.workspace)
    history = ws.decision_history(args.request_id)
    if not history:
        print(f"请求 {args.request_id} 没有判断记录。", file=sys.stderr)
        return 2
    current = history[-1]
    previous = history[-2] if len(history) > 1 else None
    print(f"请求 {args.request_id} 当前判断："
          f"{recommend.VERDICT_TEXT[current['verdict']]}")
    print(f"判断时间 {current['decided_at']}，数据截止 {current['data_cutoff']}，"
          f"登记哈希 {current['registry_hash']}")
    print("\n变化说明：")
    for line in recommend.explain_change(previous, current):
        print(f"- {line}")
    print("\n当前依据：")
    for reason in current["decision"]["rationale"]:
        print(f"- {reason}")
    print(f"- 交接动作：{current['decision']['action']['detail']}")
    return 0


def cmd_handoff(args):
    ws = Workspace(args.workspace)
    registry = ws.load_registry()
    views = ws.shifts()
    rounds = ws.decision_rounds()
    text = handoff.render_handoff(registry, views, ws.requests(),
                                  _latest_look(ws), rounds[-1] if rounds else None)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"交接报告已写入 {args.output}")
    else:
        print(text)
    return 0


def cmd_status(args):
    ws = Workspace(args.workspace)
    registry = ws.load_registry()
    meta = ws.meta()
    looks = ws.looks()
    print(f"工作区：{ws.root}")
    print(f"登记 {registry.review_id}（哈希 {registry.hash()}），"
          f"确认性：{'有效' if registry.confirmatory else '已失效'}，"
          f"查看计划：{registry.look_days}")
    print(f"已并入轮次：{len(meta['rounds'])}，班次：{len(ws.shifts())}，"
          f"已执行查看：{[l['look_day'] for l in looks]}")
    for record in ws.decision_rounds()[-1:]:
        for d in record["decisions"]:
            print(f"  {d['request_id']}: {d['verdict_text']}")
    return 0


def cmd_amend(args):
    ws = Workspace(args.workspace)
    registry = ws.load_registry()
    changes = {}
    for item in args.sets:
        name, _, text = item.partition("=")
        changes[name] = _parse_value(name, text)
    late = registry.amend(changes, NOW(), reason=args.reason or "命令行修订")
    ws.save_registry(registry)
    ws.audit("amend", changes=changes, late=late)
    print("修订已记录；确认性：" + ("有效" if registry.confirmatory else "已失效"))
    return 0


def cmd_synth(args):
    out = synth.generate_round2(Path(args.output))
    print(f"合成第二轮资料包已生成：{out}（仅用于演示与测试，非真实装置数据）")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="cofire", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("check", help="核对资料包完整性")
    p.add_argument("pack")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("init", help="建立工作区并并入首轮资料")
    p.add_argument("workspace")
    p.add_argument("--pack", required=True)
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("ingest", help="并入新一轮资料")
    p.add_argument("workspace")
    p.add_argument("--pack", required=True)
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("look", help="执行一次查看")
    p.add_argument("workspace")
    p.add_argument("--day", type=int, required=True)
    p.add_argument("--boilers", help="只取部分炉组（将使确认性失效）")
    p.add_argument("--force", action="store_true", help="重复查看（将使确认性失效）")
    p.set_defaults(func=cmd_look)

    p = sub.add_parser("decide", help="对下一轮请求给出判断")
    p.add_argument("workspace")
    p.set_defaults(func=cmd_decide)

    p = sub.add_parser("why", help="回查某请求判断的依据与变化")
    p.add_argument("workspace")
    p.add_argument("request_id")
    p.set_defaults(func=cmd_why)

    p = sub.add_parser("handoff", help="生成运行交接报告")
    p.add_argument("workspace")
    p.add_argument("-o", "--output")
    p.set_defaults(func=cmd_handoff)

    p = sub.add_parser("status", help="工作区概览")
    p.add_argument("workspace")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("amend", help="修订登记字段（冻结后修订使确认性失效）")
    p.add_argument("workspace")
    p.add_argument("--set", dest="sets", action="append", required=True,
                   help="字段=值，可多次")
    p.add_argument("--reason", default="")
    p.set_defaults(func=cmd_amend)

    p = sub.add_parser("synth-round2", help="生成合成第二轮资料包（演示/测试用）")
    p.add_argument("output")
    p.set_defaults(func=cmd_synth)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
