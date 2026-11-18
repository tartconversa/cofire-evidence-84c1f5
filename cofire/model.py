"""类型化记录与班次级视图。

班次是独立安排单位：readings 的行只是班次内测点，聚合后每班只剩一条
ShiftView，测点数量不进入任何计数。前序影响按 boilers.carry_window_shifts
界定检查范围，以滞后班次计划替代比进入模型，不固定更复杂的算法。
"""
from __future__ import annotations

import datetime
import math
from dataclasses import asdict, dataclass, field

TZ = datetime.timezone(datetime.timedelta(hours=8))


def _ts(text):
    return datetime.datetime.fromisoformat(text)


def _f(text):
    return float(text)


@dataclass(frozen=True)
class Boiler:
    boiler_id: str
    capacity_tph: float
    carry_window_shifts: int

    @classmethod
    def from_row(cls, row):
        return cls(row["boiler_id"], _f(row["capacity_tph"]), int(row["carry_window_shifts"]))


@dataclass(frozen=True)
class Plan:
    shift_id: str
    boiler_id: str
    start: datetime.datetime
    treatment: float
    planned_load: float
    coal_grade: str
    registered_at: datetime.datetime
    available_at: datetime.datetime

    @classmethod
    def from_row(cls, row):
        return cls(
            row["shift_id"], row["boiler_id"], _ts(row["start"]), _f(row["treatment"]),
            _f(row["planned_load"]), row["coal_grade"], _ts(row["registered_at"]),
            _ts(row["available_at"]),
        )


@dataclass(frozen=True)
class Reading:
    reading_id: str
    shift_id: str
    within_shift: int
    actual_ratio: float
    moisture_pct: float
    coal_per_steam: float
    steam_tph: float
    emission_mg: float
    emission_breach: bool
    steam_shortfall: bool
    available_at: datetime.datetime
    actual_load: float

    @classmethod
    def from_row(cls, row):
        return cls(
            row["reading_id"], row["shift_id"], int(row["within_shift"]),
            _f(row["actual_ratio"]), _f(row["moisture_pct"]), _f(row["coal_per_steam"]),
            _f(row["steam_tph"]), _f(row["emission_mg"]), row["emission_breach"] == "true",
            row["steam_shortfall"] == "true", _ts(row["available_at"]), _f(row["actual_load"]),
        )


@dataclass(frozen=True)
class Deviation:
    event_id: str
    shift_id: str
    kind: str
    reason: str
    occurred_at: datetime.datetime
    available_at: datetime.datetime

    @classmethod
    def from_row(cls, row):
        return cls(
            row["event_id"], row["shift_id"], row["kind"], row["reason"],
            _ts(row["occurred_at"]), _ts(row["available_at"]),
        )


@dataclass(frozen=True)
class Review:
    """事先登记的声明范围与查看计划；是判断口径的来源。"""

    review_id: str
    registered_at: datetime.datetime
    look_days: tuple
    effect_target: float
    emission_risk_limit: float
    steam_risk_limit: float
    load_min: float
    load_max: float

    @classmethod
    def from_row(cls, row):
        days = tuple(int(x) for x in row["look_days"].split(";") if x.strip())
        return cls(
            row["review_id"], _ts(row["registered_at"]), days,
            _f(row["effect_target"]), _f(row["emission_risk_limit"]),
            _f(row["steam_risk_limit"]), _f(row["load_min"]), _f(row["load_max"]),
        )


@dataclass(frozen=True)
class NextRequest:
    request_id: str
    boiler_id: str
    ratio: float
    load: float
    coal_grade: str
    moisture_pct: float
    status: str
    start: datetime.datetime

    @classmethod
    def from_row(cls, row):
        return cls(
            row["request_id"], row["boiler_id"], _f(row["ratio"]), _f(row["load"]),
            row["coal_grade"], _f(row["moisture_pct"]), row["status"], _ts(row["start"]),
        )


def load_band(load):
    """负荷工况带，带宽 0.1，左闭右开。"""
    return math.floor(float(load) * 10 + 1e-9) / 10


def moisture_band(pct):
    """含水率工况带，带宽 5 个百分点，左闭右开。"""
    return int(math.floor(float(pct) / 5.0) * 5)


def cell_key(ratio, load, coal_grade, moisture_pct):
    """工况格键：计划替代比 × 负荷带 × 煤种 × 含水率带。"""
    return (round(float(ratio), 4), load_band(load), str(coal_grade), moisture_band(moisture_pct))


def cell_text(key):
    ratio, load, coal, moist = key
    return f"替代比{ratio:g}/负荷带{load:.1f}-{load + 0.1:.1f}/煤种{coal}/含水{moist}-{moist + 5}%"


@dataclass
class ShiftView:
    """一个班次的聚合视图；分析的唯一计数单位。"""

    shift_id: str
    boiler_id: str
    start: datetime.datetime
    treatment: float
    planned_load: float
    coal_grade: str
    n_points: int
    actual_ratio: float
    moisture_pct: float
    coal_per_steam: float
    steam_tph: float
    emission_mg: float
    actual_load: float
    emission_breach: bool
    steam_shortfall: bool
    deviations: tuple = ()
    stopped: bool = False
    prev_treatments: tuple = ()
    available_at: datetime.datetime = None

    @property
    def cell(self):
        return cell_key(self.treatment, self.planned_load, self.coal_grade, self.moisture_pct)

    def to_dict(self):
        data = asdict(self)
        for name in ("start", "available_at"):
            data[name] = data[name].isoformat()
        return data

    @classmethod
    def from_dict(cls, data):
        data = dict(data)
        data["start"] = _ts(data["start"])
        data["available_at"] = _ts(data["available_at"])
        data["deviations"] = tuple(tuple(d) for d in data["deviations"])
        data["prev_treatments"] = tuple(data["prev_treatments"])
        return cls(**data)


def build_shift_views(plans, readings, deviations, boilers):
    """把测点聚合成班次视图，并接上同一炉组前序班次的计划替代比。

    不利事件按班次有无评估（任一侧点为真即真），不按测点数稀释。
    """
    by_shift = {}
    for reading in readings:
        by_shift.setdefault(reading.shift_id, []).append(reading)
    dev_by_shift = {}
    for event in deviations:
        dev_by_shift.setdefault(event.shift_id, []).append(event)
    windows = {b.boiler_id: b.carry_window_shifts for b in boilers}
    views = {}
    for plan in plans:
        points = by_shift.get(plan.shift_id, [])
        events = dev_by_shift.get(plan.shift_id, [])
        if points:
            n = len(points)
            mean = lambda pick: sum(pick(p) for p in points) / n
            view = ShiftView(
                shift_id=plan.shift_id, boiler_id=plan.boiler_id, start=plan.start,
                treatment=plan.treatment, planned_load=plan.planned_load,
                coal_grade=plan.coal_grade, n_points=n,
                actual_ratio=mean(lambda p: p.actual_ratio),
                moisture_pct=mean(lambda p: p.moisture_pct),
                coal_per_steam=mean(lambda p: p.coal_per_steam),
                steam_tph=mean(lambda p: p.steam_tph),
                emission_mg=mean(lambda p: p.emission_mg),
                actual_load=mean(lambda p: p.actual_load),
                emission_breach=any(p.emission_breach for p in points),
                steam_shortfall=any(p.steam_shortfall for p in points),
                deviations=tuple((e.kind, e.reason) for e in events),
                stopped=any(e.kind == "safety_stop" for e in events),
                available_at=max(
                    [plan.available_at] + [p.available_at for p in points]
                    + [e.available_at for e in events]
                ),
            )
        else:  # 计划已知但尚无测点：保留为未执行计划，不编造结局
            view = ShiftView(
                shift_id=plan.shift_id, boiler_id=plan.boiler_id, start=plan.start,
                treatment=plan.treatment, planned_load=plan.planned_load,
                coal_grade=plan.coal_grade, n_points=0,
                actual_ratio=math.nan, moisture_pct=math.nan, coal_per_steam=math.nan,
                steam_tph=math.nan, emission_mg=math.nan, actual_load=math.nan,
                emission_breach=False, steam_shortfall=False,
                deviations=tuple((e.kind, e.reason) for e in events),
                stopped=any(e.kind == "safety_stop" for e in events),
                available_at=plan.available_at,
            )
        views[plan.shift_id] = view
    # 前序影响：同一炉组按开始时刻排序，取 carry_window_shifts 内的前序计划替代比
    by_boiler = {}
    for view in views.values():
        by_boiler.setdefault(view.boiler_id, []).append(view)
    for boiler_id, group in by_boiler.items():
        group.sort(key=lambda v: v.start)
        window = windows.get(boiler_id, 0)
        for index, view in enumerate(group):
            lags = []
            for lag in range(1, window + 1):
                lags.append(group[index - lag].treatment if index - lag >= 0 else 0.0)
            view.prev_treatments = tuple(lags)
    return views


def parse_pack(pack):
    """把资料包原始行解析为类型化记录。"""
    return {
        "boilers": [Boiler.from_row(r) for r in pack.boilers],
        "plans": [Plan.from_row(r) for r in pack.plans],
        "readings": [Reading.from_row(r) for r in pack.readings],
        "deviations": [Deviation.from_row(r) for r in pack.deviations],
        "reviews": [Review.from_row(r) for r in pack.reviews],
        "next_trials": [NextRequest.from_row(r) for r in pack.next_trials],
    }
