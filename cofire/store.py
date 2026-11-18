"""工作区持久化：登记、班次、查看、判断与审计轨迹。

所有结论都可回查：每条判断记录数据截止点、登记哈希（判断口径）与分量
证据；录入新轮次后，可比较同一请求相邻两轮判断的差异。
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path

from .model import ShiftView

FILES = {
    "meta": "workspace.json",
    "registry": "registry.json",
    "shifts": "shifts.jsonl",
    "requests": "requests.json",
    "looks": "looks.jsonl",
    "decisions": "decisions.jsonl",
    "audit": "audit.jsonl",
}


class Workspace:
    def __init__(self, root):
        self.root = Path(root)

    # ---- 基础读写 -------------------------------------------------
    def _path(self, name):
        return self.root / FILES[name]

    def _read_json(self, name, default=None):
        path = self._path(name)
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_json(self, name, data):
        self._path(name).write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _read_jsonl(self, name):
        path = self._path(name)
        if not path.exists():
            return []
        return [json.loads(line) for line in
                path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def _append_jsonl(self, name, record):
        with self._path(name).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    def audit(self, action, **detail):
        record = {"at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  "action": action, **detail}
        self._append_jsonl("audit", record)

    # ---- 初始化 ---------------------------------------------------
    def init(self, registry, trial_start):
        self.root.mkdir(parents=True, exist_ok=True)
        if self._path("meta").exists():
            raise ValueError("工作区已存在：" + str(self.root))
        self._write_json("meta", {
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "trial_start": trial_start.isoformat(), "rounds": [],
        })
        self.save_registry(registry)
        self.audit("init", review_id=registry.review_id, registry_hash=registry.hash())

    def exists(self):
        return self._path("meta").exists()

    # ---- 登记 -----------------------------------------------------
    def save_registry(self, registry):
        self._write_json("registry", registry.to_dict())

    def load_registry(self):
        from .registry import Registry
        data = self._read_json("registry")
        if data is None:
            raise ValueError("工作区缺少登记，请先 init")
        return Registry.from_dict(data)

    # ---- 班次 -----------------------------------------------------
    def shifts(self):
        return [ShiftView.from_dict(d) for d in self._read_jsonl("shifts")]

    def ingest_shifts(self, views):
        """追加新班次；同一 shift_id 内容冲突即拒绝，一致则跳过。"""
        known = {v.shift_id: v for v in self.shifts()}
        added, skipped = 0, 0
        with self._path("shifts").open("a", encoding="utf-8") as fh:
            for view in views:
                old = known.get(view.shift_id)
                if old is not None:
                    if old.to_dict() != view.to_dict():
                        raise ValueError("班次记录冲突：" + view.shift_id)
                    skipped += 1
                    continue
                fh.write(json.dumps(view.to_dict(), ensure_ascii=False) + "\n")
                added += 1
        return added, skipped

    # ---- 下一轮请求 ----------------------------------------------
    def upsert_requests(self, requests, seen_at):
        state = self._read_json("requests", default={})
        for req in requests:
            entry = state.setdefault(req.request_id, {"history": []})
            latest = entry.get("latest")
            snapshot = {"boiler_id": req.boiler_id, "ratio": req.ratio,
                        "load": req.load, "coal_grade": req.coal_grade,
                        "moisture_pct": req.moisture_pct, "status": req.status,
                        "start": req.start.isoformat()}
            if latest != snapshot:
                entry["history"].append({"at": seen_at.isoformat(), **snapshot})
                entry["latest"] = snapshot
        self._write_json("requests", state)

    def requests(self):
        from .model import NextRequest
        import datetime as _dt
        state = self._read_json("requests", default={})
        out = []
        for request_id, entry in state.items():
            latest = entry["latest"]
            out.append(NextRequest(
                request_id, latest["boiler_id"], latest["ratio"], latest["load"],
                latest["coal_grade"], latest["moisture_pct"], latest["status"],
                _dt.datetime.fromisoformat(latest["start"])))
        return out

    # ---- 查看与判断 ----------------------------------------------
    def looks(self):
        return self._read_jsonl("looks")

    def add_look(self, result):
        self._append_jsonl("looks", result)
        self.audit("look", day=result["look_day"],
                   registered=result["registered"],
                   confirmatory=result["confirmatory"])

    def decision_rounds(self):
        return self._read_jsonl("decisions")

    def add_decisions(self, round_record):
        self._append_jsonl("decisions", round_record)
        self.audit("decide", n=len(round_record["decisions"]),
                   registry_hash=round_record["registry_hash"])

    def decision_history(self, request_id):
        """同一请求的历史判断，按时间排列，供 why 回查。"""
        out = []
        for record in self.decision_rounds():
            for decision in record["decisions"]:
                if decision["request_id"] == request_id:
                    out.append({
                        "verdict": decision["verdict"],
                        "data_cutoff": record["data_cutoff"],
                        "registry_hash": record["registry_hash"],
                        "decided_at": record["decided_at"],
                        "decision": decision,
                    })
        return out

    # ---- 轮次 -----------------------------------------------------
    def note_round(self, pack_root, added, skipped, as_of):
        meta = self._read_json("meta")
        meta["rounds"].append({
            "pack": str(pack_root), "ingested_at": as_of.isoformat(),
            "added": added, "skipped": skipped,
        })
        self._write_json("meta", meta)
        self.audit("ingest", pack=str(pack_root), added=added, skipped=skipped)

    def meta(self):
        return self._read_json("meta")
