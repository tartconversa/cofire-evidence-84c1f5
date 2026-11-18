"""查看执行、三种口径与风险监测的测试。"""
import datetime
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from helpers import make_review, make_view, repo_review, repo_views  # noqa: E402

from cofire.analysis import (risk_table, run_look,  # noqa: E402
                             split_protocol, unidentifiable_report)
from cofire.registry import Registry  # noqa: E402
from cofire.stats import (newcombe, obf_boundary, spent_alpha,  # noqa: E402
                          wilson)

NOW = datetime.datetime(2025, 2, 1, 8, 0, tzinfo=datetime.timezone.utc)


class LookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.views = list(repo_views().values())

    def look(self, day):
        return run_look(self.views, Registry(repo_review()), day, NOW)

    def test_registered_looks_see_growing_data(self):
        """查看日 8/16/24 分别见到 32/64/96 班，信息比例随之增长。"""
        r8, r16, r24 = self.look(8), self.look(16), self.look(24)
        self.assertEqual((r8["n_shifts"], r16["n_shifts"], r24["n_shifts"]),
                         (32, 64, 96))
        self.assertAlmostEqual(r8["info_fraction"], 1 / 3, places=3)
        self.assertEqual(r24["info_fraction"], 1.0)
        self.assertTrue(all(r["registered"] for r in (r8, r16, r24)))

    def test_look_respects_available_at(self):
        """可知时刻晚于查看截止的班次不得进入该次查看。"""
        views = [make_view("A1", "F1", 0, 0.0, available_lag=1),
                 make_view("A2", "F2", 0, 0.23, available_lag=30)]
        result = run_look(views, Registry(make_review()), 10, NOW)
        self.assertEqual(result["n_shifts"], 1)

    def test_obf_boundaries_tighten_late(self):
        """O'Brien-Fleming：早期界值更严，末次等于单侧 z。"""
        r8, r24 = self.look(8), self.look(24)
        self.assertGreater(r8["alpha_bound"], r24["alpha_bound"])
        self.assertAlmostEqual(r24["alpha_bound"], 1.96, places=2)

    def test_effect_estimates(self):
        """末次查看：0.15 与 0.23 达到登记目标，0.08 证据未齐。"""
        r24 = self.look(24)
        self.assertEqual(r24["effect_status"]["0.23"], "met")
        self.assertEqual(r24["effect_status"]["0.15"], "met")
        self.assertEqual(r24["effect_status"]["0.08"], "uncertain")
        est = r24["contrasts_itt"]["0.23"]["estimate"]
        self.assertTrue(-3.5 < est < -1.2, est)


class ProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.views = list(repo_views().values())
        cls.registry = Registry(repo_review())

    def test_itt_keeps_stopped_pp_excludes(self):
        """停试班次在原定方案口径保留、在实际可运行口径剔除，但不消失。"""
        itt, pp, excluded = split_protocol(self.views, self.registry)
        self.assertIn("S002", {v.shift_id for v in itt})
        self.assertNotIn("S002", {v.shift_id for v in pp})
        noted = {v.shift_id for v, _reasons in excluded}
        self.assertIn("S002", noted)

    def test_unidentifiable_listed(self):
        report = unidentifiable_report(self.views, [])
        self.assertEqual(sum(len(v) for v in report["stopped"].values()), 3)
        self.assertIn("S002", report["stopped"]["0.15"])
        self.assertTrue(any("无法" in n for n in report["notes"]))

    def test_risk_counts_shifts_not_points(self):
        """0.15 比例越限 1 班/24 班，而不是按测点 1/96 稀释。"""
        table = risk_table(self.views, 0.08, 0.06)
        row = table["0.15"]["emission"]
        self.assertEqual(row["k"], 1)
        self.assertEqual(table["0.15"]["n_shifts"], 24)
        self.assertAlmostEqual(row["rate"], 1 / 24)

    def test_risk_status_against_limits(self):
        table = risk_table(self.views, 0.08, 0.06)
        self.assertEqual(table["0.23"]["emission"]["status"], "uncertain")
        self.assertEqual(table["0.23"]["steam"]["status"], "exceeds")


class StatsTests(unittest.TestCase):
    def test_wilson_zero_events(self):
        """0 事件时 24 班不足以证明越限率≤0.08，48 班可以。"""
        self.assertGreater(wilson(0, 24)[1], 0.08)
        self.assertLess(wilson(0, 48)[1], 0.08)

    def test_obf_spending(self):
        self.assertAlmostEqual(spent_alpha(0.025, 1.0), 0.025, places=6)
        self.assertLess(spent_alpha(0.025, 0.5), 0.025)
        self.assertGreater(obf_boundary(0.025, 0.33), obf_boundary(0.025, 1.0))

    def test_newcombe_equal_rates_covers_zero(self):
        lo, hi = newcombe(2, 24, 2, 24)
        self.assertLess(lo, 0)
        self.assertGreater(hi, 0)


if __name__ == "__main__":
    unittest.main()
