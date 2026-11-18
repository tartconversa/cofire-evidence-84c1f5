"""资料包读取：先用 sourcepack 核对完整性，再给出原始行。

资料行中的拒绝、停试、缺测是业务记录，读取阶段不剔除、不解释。
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from sourcepack.catalog import inspect

TABLES = ("boilers", "plans", "readings", "deviations", "reviews", "next_trials")


@dataclass
class Pack:
    """一个通过完整性核对的资料包快照。"""

    root: Path
    tables: dict

    def __getattr__(self, name):
        if name in TABLES:
            return self.tables[name]
        raise AttributeError(name)


def load_pack(root) -> Pack:
    """读取资料包；完整性不符时抛出 ValueError。"""
    root = Path(root)
    inspect(root)
    tables = {}
    for name in TABLES:
        with (root / "data" / f"{name}.csv").open(encoding="utf-8", newline="") as fh:
            tables[name] = list(csv.DictReader(fh))
    return Pack(root=root, tables=tables)
