"""测试共享辅助：载入仓库资料包、构造合成班次与登记。"""
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cofire.model import (Boiler, Plan, Reading, Review, ShiftView,  # noqa: E402
                          build_shift_views)
from cofire.pack import load_pack  # noqa: E402
from cofire.model import parse_pack  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
TZ = datetime.timezone(datetime.timedelta(hours=8))
BASE = datetime.datetime(2025, 1, 6, 8, 0, tzinfo=TZ)


def repo_views():
    """仓库资料包的班次视图（按 shift_id 索引）。"""
    pack = load_pack(REPO)
    parsed = parse_pack(pack)
    return build_shift_views(parsed["plans"], parsed["readings"],
                             parsed["deviations"], parsed["boilers"])


def repo_review():
    pack = load_pack(REPO)
    return parse_pack(pack)["reviews"][0]


def make_review(**kw):
    return Review(
        review_id=kw.get("review_id", "T"),
        registered_at=kw.get("registered_at", BASE),
        look_days=kw.get("look_days", (10, 20)),
        effect_target=kw.get("effect_target", -1.0),
        emission_risk_limit=kw.get("emission_risk_limit", 0.08),
        steam_risk_limit=kw.get("steam_risk_limit", 0.06),
        load_min=kw.get("load_min", 0.5),
        load_max=kw.get("load_max", 0.9),
    )


def make_view(shift_id, boiler, day, treatment, coal=38.0, breach=False,
              short=False, load=0.8, moisture=24.0, grade="C1", stopped=False,
              available_lag=1):
    start = BASE + datetime.timedelta(days=day)
    return ShiftView(
        shift_id=shift_id, boiler_id=boiler, start=start, treatment=treatment,
        planned_load=load, coal_grade=grade, n_points=4,
        actual_ratio=treatment, moisture_pct=moisture, coal_per_steam=coal,
        steam_tph=100.0, emission_mg=90.0, actual_load=load,
        emission_breach=breach, steam_shortfall=short,
        deviations=(("safety_stop", "emission_limit"),) if stopped else (),
        stopped=stopped, prev_treatments=(0.0,),
        available_at=start + datetime.timedelta(days=available_lag),
    )


def crossover_views(n_days, boilers=("F1", "F2"), treated_coal=37.0,
                    control_coal=39.5, breach_days=(), short_days=()):
    """构造两炉组逐日交叉 0 / 0.23 的合成班次（含确定性扰动）。"""
    views = []
    for day in range(n_days):
        for j, boiler in enumerate(boilers):
            treatment = 0.23 if (day + j) % 2 else 0.0
            jitter = ((day * 37 + j * 11) % 21 - 10) * 0.08
            base = treated_coal if treatment else control_coal
            views.append(make_view(
                f"X{day:03d}{j}", boiler, day, treatment,
                coal=base + jitter,
                load=0.8 + ((day % 3) - 1) * 0.04,
                moisture=24 + ((day + j) % 5) - 2,
                breach=day in breach_days and j == 0,
                short=day in short_days and j == 0,
            ))
    return views
