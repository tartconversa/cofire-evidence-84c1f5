"""合成第二轮资料包生成器（仅用于演示与测试，非真实装置数据）。

延续首轮拉丁方交叉安排 6 天（2025-01-30 至 2025-02-04），并在 0.23 比例
的一个班次植入一次越限与安全停试，用于演示"新结果使曾经满足条件的工况
需要暂停"以及建议的可回查变化。
"""
from __future__ import annotations

import csv
import datetime
import hashlib
import json
from pathlib import Path

import numpy as np

from .pack import load_pack

TZ = datetime.timezone(datetime.timedelta(hours=8))
START = datetime.datetime(2025, 1, 30, 8, 0, tzinfo=TZ)
DAYS = 6
TREATMENTS = [0.08, 0.15, 0.23, 0.0]
POINTS_PER_SHIFT = 4

CATALOG_TEMPLATE = {
    "title": "掺烧试验中的证据放行与运行边界（合成第二轮）",
    "encoding": "UTF-8", "delimiter": ",", "missing": "", "synthetic": True,
}


def _columns(root):
    catalog = json.loads((root / "data/catalog.json").read_text(encoding="utf-8"))
    return {t["name"]: t for t in catalog["tables"]}


def generate_round2(out_dir, template_root=None, seed=20250204):
    """生成第二轮资料包；返回输出目录。"""
    template_root = Path(template_root or Path(__file__).resolve().parents[1])
    pack = load_pack(template_root)
    out_dir = Path(out_dir)
    (out_dir / "data").mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)

    plans = pack.tables["plans"]
    readings = pack.tables["readings"]
    template_by_key = {}
    for row in plans:
        day = (datetime.datetime.fromisoformat(row["start"]).date()
               - datetime.date(2025, 1, 6)).days
        template_by_key[(day % 24, row["boiler_id"])] = row
    moisture_by_shift = {}
    for row in readings:
        moisture_by_shift.setdefault(row["shift_id"], float(row["moisture_pct"]))

    boilers = pack.tables["boilers"]
    boiler_ids = [b["boiler_id"] for b in boilers]
    new_plans, new_readings, new_deviations = [], [], []
    planted = False
    for day in range(DAYS):
        start = START + datetime.timedelta(days=day)
        avail = start + datetime.timedelta(days=1)
        day_index = 24 + day  # 首轮第 0 天为 2025-01-06
        for j, boiler_id in enumerate(boiler_ids):
            treatment = TREATMENTS[(day_index + j) % 4]
            template = template_by_key[(day_index % 24, boiler_id)]
            shift_id = f"S3{day:02d}{j}"
            new_plans.append({
                "shift_id": shift_id, "boiler_id": boiler_id,
                "start": start.isoformat(), "treatment": f"{treatment:g}",
                "planned_load": template["planned_load"],
                "coal_grade": template["coal_grade"],
                "registered_at": (start - datetime.timedelta(days=2)).isoformat(),
                "available_at": avail.isoformat(),
            })
            moisture = moisture_by_shift.get(template["shift_id"], 24.0)
            # 在 0.23 比例的第一个 F1 班次植入越限与安全停试
            plant = (not planted and treatment == 0.23 and boiler_id == "F1")
            for point in range(POINTS_PER_SHIFT):
                base = 39.5 - 9.2 * treatment
                breach = plant and point == 1
                new_readings.append({
                    "reading_id": f"{shift_id}-{point}",
                    "shift_id": shift_id, "within_shift": str(point),
                    "actual_ratio": f"{treatment:g}",
                    "moisture_pct": f"{moisture:g}",
                    "coal_per_steam": f"{base + rng.normal(0, 0.9):.3f}",
                    "steam_tph": f"{float(template['planned_load']) * 120 + rng.normal(0, 1.5):.3f}",
                    "emission_mg": f"{95 + rng.normal(0, 8) + (60 if breach else 0):.3f}",
                    "emission_breach": "true" if breach else "false",
                    "steam_shortfall": "true" if rng.random() < 0.08 else "false",
                    "available_at": avail.isoformat(),
                    "actual_load": f"{float(template['planned_load']) - 0.005:.3f}",
                })
            if plant:
                new_deviations.append({
                    "event_id": f"E3{day:02d}{j}", "shift_id": shift_id,
                    "kind": "safety_stop", "reason": "emission_limit",
                    "occurred_at": (start + datetime.timedelta(hours=4)).isoformat(),
                    "available_at": avail.isoformat(),
                })
                planted = True

    reviews = pack.tables["reviews"]  # 登记不变：与冻结口径一致
    next_trials = [
        {"request_id": "N1", "boiler_id": "F1", "ratio": "0.23", "load": "0.88",
         "coal_grade": "C1", "moisture_pct": "24", "status": "approved",
         "start": "2025-02-08T08:00:00+08:00"},
        {"request_id": "N2", "boiler_id": "F2", "ratio": "0.3", "load": "0.9",
         "coal_grade": "C2", "moisture_pct": "31", "status": "draft",
         "start": "2025-02-09T08:00:00+08:00"},
        {"request_id": "N3", "boiler_id": "F3", "ratio": "0.15", "load": "0.72",
         "coal_grade": "C1", "moisture_pct": "20", "status": "running",
         "start": "2025-01-29T08:00:00+08:00"},
        {"request_id": "N4", "boiler_id": "F4", "ratio": "0.23", "load": "0.72",
         "coal_grade": "C2", "moisture_pct": "26", "status": "draft",
         "start": "2025-02-10T08:00:00+08:00"},
    ]

    tables = _columns(template_root)
    rows = {"boilers": boilers, "plans": new_plans, "readings": new_readings,
            "deviations": new_deviations, "reviews": reviews,
            "next_trials": next_trials}
    catalog = dict(CATALOG_TEMPLATE)
    catalog["tables"] = []
    for name, table in tables.items():
        path = out_dir / "data" / f"{name}.csv"
        fieldnames = [c["name"] for c in table["columns"]]
        with path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows[name])
        entry = {k: v for k, v in table.items()
                 if k in ("name", "description", "columns", "key", "references")}
        entry.update({"path": f"data/{name}.csv", "row_count": len(rows[name]),
                      "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        catalog["tables"].append(entry)
    (out_dir / "data" / "catalog.json").write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_dir
