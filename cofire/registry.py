"""事先登记的生命周期：登记、冻结、修订与确认性失效。

登记内容（主要指标、查看日、效果目标、风险上限、负荷范围、显著性水平）
构成判断口径。首次执行查看后冻结；此后修改这些字段、在未登记日查看、
或只取部分炉组，都使确认性解释失效——结果仍可使用，但只能标注为探索性。
"""
from __future__ import annotations

import datetime
import hashlib
import json

PRIMARY_METRIC = "coal_per_steam"
REGISTERED_FIELDS = (
    "primary_metric", "look_days", "effect_target", "emission_risk_limit",
    "steam_risk_limit", "load_min", "load_max", "alpha",
)


class Registry:
    def __init__(self, review, alpha=0.025):
        self.review_id = review.review_id
        self.registered_at = review.registered_at.isoformat()
        self.primary_metric = PRIMARY_METRIC
        self.look_days = list(review.look_days)
        self.effect_target = review.effect_target
        self.emission_risk_limit = review.emission_risk_limit
        self.steam_risk_limit = review.steam_risk_limit
        self.load_min = review.load_min
        self.load_max = review.load_max
        self.alpha = alpha
        self.frozen_at = None
        self.confirmatory = True
        self.void_reasons = []
        self.amendments = []

    # ---- 口径指纹 -------------------------------------------------
    def spec(self):
        return {name: getattr(self, name) for name in REGISTERED_FIELDS}

    def hash(self):
        blob = json.dumps(self.spec(), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]

    # ---- 生命周期 -------------------------------------------------
    def freeze(self, at):
        if self.frozen_at is None:
            self.frozen_at = at.isoformat()

    def _void(self, reason, at):
        self.confirmatory = False
        self.void_reasons.append({"at": at.isoformat(), "reason": reason})

    def amend(self, changes, at, reason=""):
        """修订登记字段。冻结前允许；冻结后记录修订且确认性失效。"""
        unknown = set(changes) - set(REGISTERED_FIELDS)
        if unknown:
            raise ValueError("不可修订的字段：" + ",".join(sorted(unknown)))
        if self.frozen_at is None:
            for name, value in changes.items():
                setattr(self, name, value)
            self.amendments.append({
                "at": at.isoformat(), "changes": dict(changes),
                "reason": reason, "phase": "冻结前，确认性不受影响",
            })
            return False
        touched = [k for k, v in changes.items() if getattr(self, k) != v]
        if touched:
            self.amendments.append({
                "at": at.isoformat(), "changes": dict(changes),
                "reason": reason, "phase": "冻结后",
            })
            self._void("冻结后修订登记字段：" + ",".join(touched), at)
        return True

    def note_unregistered_look(self, day, at):
        self._void(f"在未登记日第{day}天执行查看", at)

    def note_repeat_look(self, day, at):
        self._void(f"重复执行第{day}天查看", at)

    def note_subgroup(self, kept, total, at):
        self._void(f"只取部分炉组（{kept}/{total}）查看", at)

    def check_look(self, day):
        return day in self.look_days

    # ---- 序列化 ---------------------------------------------------
    def to_dict(self):
        return {
            "review_id": self.review_id, "registered_at": self.registered_at,
            **self.spec(), "frozen_at": self.frozen_at,
            "confirmatory": self.confirmatory, "void_reasons": self.void_reasons,
            "amendments": self.amendments,
        }

    @classmethod
    def from_dict(cls, data):
        obj = cls.__new__(cls)
        obj.review_id = data["review_id"]
        obj.registered_at = data["registered_at"]
        for name in REGISTERED_FIELDS:
            setattr(obj, name, data[name])
        obj.frozen_at = data["frozen_at"]
        obj.confirmatory = data["confirmatory"]
        obj.void_reasons = data["void_reasons"]
        obj.amendments = data["amendments"]
        return obj
