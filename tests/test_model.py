"""班次聚合、测点不虚增与前序影响的测试。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from helpers import repo_views  # noqa: E402

from cofire.analysis import evidence_inventory  # noqa: E402
from cofire.model import (build_shift_views, load_band,  # noqa: E402
                          moisture_band)
from cofire.pack import load_pack  # noqa: E402
from cofire.model import parse_pack  # noqa: E402


class AggregationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.views = repo_views()

    def test_shift_is_the_unit(self):
        """96 个班次各聚成一条视图；测点数只是属性，不是计数。"""
        self.assertEqual(len(self.views), 96)
        view = self.views["S001"]
        self.assertEqual(view.n_points, 4)
        self.assertAlmostEqual(view.coal_per_steam,
                               (38.199 + 39.035 + 38.914 + 37.319) / 4, places=3)

    def test_adverse_event_per_shift(self):
        """越限按班次有无评估：S002 有 1 个越限测点，班次记一次。"""
        self.assertTrue(self.views["S002"].emission_breach)
        self.assertFalse(self.views["S001"].emission_breach)

    def test_points_do_not_inflate_evidence(self):
        """把每班测点复制一份再聚合：班次统计与证据计数都不变。"""
        pack = load_pack(Path(__file__).resolve().parents[1])
        parsed = parse_pack(pack)
        doubled = parsed["readings"] + parsed["readings"]
        views = build_shift_views(parsed["plans"], doubled,
                                  parsed["deviations"], parsed["boilers"])
        self.assertEqual(len(views), 96)
        self.assertAlmostEqual(views["S001"].coal_per_steam,
                               self.views["S001"].coal_per_steam, places=9)
        self.assertEqual(views["S002"].emission_breach,
                         self.views["S002"].emission_breach)
        before = evidence_inventory(list(self.views.values()))["ratios"]
        after = evidence_inventory(list(views.values()))["ratios"]
        self.assertEqual(before, after)

    def test_carryover_lags(self):
        """F4 检查范围为 2 个前序班次，其余炉组为 1；首班前序为零。"""
        f4 = sorted((v for v in self.views.values() if v.boiler_id == "F4"),
                    key=lambda v: v.start)
        self.assertEqual(len(f4[0].prev_treatments), 2)
        self.assertEqual(f4[0].prev_treatments, (0.0, 0.0))
        self.assertEqual(f4[2].prev_treatments,
                         (f4[1].treatment, f4[0].treatment))
        f1 = sorted((v for v in self.views.values() if v.boiler_id == "F1"),
                    key=lambda v: v.start)
        self.assertEqual(len(f1[0].prev_treatments), 1)
        self.assertEqual(f1[1].prev_treatments, (f1[0].treatment,))

    def test_bands(self):
        self.assertEqual(load_band(0.88), 0.8)
        self.assertEqual(load_band(0.55), 0.5)
        self.assertEqual(moisture_band(24), 20)
        self.assertEqual(moisture_band(31), 30)


if __name__ == "__main__":
    unittest.main()
