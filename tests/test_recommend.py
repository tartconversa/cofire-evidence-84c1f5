"""下一轮判断口径与复核动作的测试。"""
import datetime
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from helpers import (BASE, TZ, crossover_views, make_review, make_view,  # noqa: E402
                     repo_review, repo_views)

from cofire.analysis import run_look  # noqa: E402
from cofire.model import NextRequest  # noqa: E402
from cofire.recommend import decide_requests, explain_change  # noqa: E402
from cofire.registry import Registry  # noqa: E402

NOW = datetime.datetime(2025, 2, 1, 8, 0, tzinfo=datetime.timezone.utc)
CUTOFF = datetime.datetime(2025, 1, 30, 8, 0, tzinfo=TZ)


def req(request_id, ratio, status="draft", load=0.8, grade="C1", moisture=24.0):
    return NextRequest(request_id, "F1", ratio, load, grade, moisture,
                       status, BASE)


def decide(views, requests, look_days=(10, 20), look_day=20, **review_kw):
    registry = Registry(make_review(look_days=look_days, **review_kw))
    look = run_look(views, registry, look_day, NOW)
    cutoff = max(v.available_at for v in views)
    return decide_requests(requests, views, registry, look, NOW, cutoff)


class RepoDecideTests(unittest.TestCase):
    """以仓库资料包驱动的一轮判断。"""

    @classmethod
    def setUpClass(cls):
        views = list(repo_views().values())
        registry = Registry(repo_review())
        look = run_look(views, registry, 24, NOW)
        requests = [
            NextRequest("N1", "F1", 0.23, 0.88, "C1", 24.0, "approved", BASE),
            NextRequest("N2", "F2", 0.30, 0.9, "C2", 31.0, "draft", BASE),
            NextRequest("N3", "F3", 0.15, 0.72, "C1", 20.0, "running", BASE),
        ]
        cutoff = max(v.available_at for v in views)
        cls.record = decide_requests(requests, views, registry, look, NOW, cutoff)
        cls.by_id = {d["request_id"]: d for d in cls.record["decisions"]}

    def test_unsupported_ratio_rejected(self):
        """0.30 从未试验：拒绝，且说明不能外推。"""
        d = self.by_id["N2"]
        self.assertEqual(d["verdict"], "reject_unsupported")
        self.assertTrue(any("不能外推" in r for r in d["rationale"]))

    def test_steam_limit_blocks_release(self):
        """不足率上限低于对照基线：0.23 不能放行，并说明属基线冲突。"""
        d = self.by_id["N1"]
        self.assertEqual(d["verdict"], "reject_risk")
        self.assertTrue(any("基线" in r for r in d["rationale"]))
        self.assertEqual(d["action"]["kind"], "revoke_approval")

    def test_stop_in_ratio_holds_running(self):
        """0.15 有安全停试：暂停复核；执行中的请求保留依据并提示复核。"""
        d = self.by_id["N3"]
        self.assertEqual(d["verdict"], "hold")
        self.assertIn("S002", d["components"]["stops"]["in_ratio"])
        self.assertEqual(d["action"]["kind"], "keep_basis_review")

    def test_decision_is_auditable(self):
        """每条判断带数据截止点、登记哈希与分量证据。"""
        self.assertEqual(self.record["data_cutoff"], CUTOFF.isoformat())
        for d in self.record["decisions"]:
            self.assertIn("support", d["components"])
            self.assertIn("effect", d["components"])
            self.assertIn("risk", d["components"])
        self.assertEqual(len(self.record["registry_hash"]), 16)


class FixtureDecideTests(unittest.TestCase):
    """合成班次驱动的判断规则。"""

    def test_accept_when_all_gates_pass(self):
        """证据齐全、效果达标、风险在上限内：可放行。"""
        views = crossover_views(70)
        record = decide(views, [req("Q1", 0.23)], look_days=(80,), look_day=80)
        d = record["decisions"][0]
        self.assertEqual(d["verdict"], "accept")
        self.assertEqual(d["action"]["kind"], "advice")

    def test_incomplete_trial_is_not_ineffective(self):
        """试验未完成（班次数不足）只能判证据不足，不能判燃料无效。"""
        views = crossover_views(6)  # 每臂仅 6 班
        record = decide(views, [req("Q2", 0.23)], look_days=(80,), look_day=80)
        d = record["decisions"][0]
        self.assertIn(d["verdict"], ("hold", "continue"))
        self.assertNotEqual(d["verdict"], "reject_effect")

    def test_confirmed_harm_rejected(self):
        """煤耗确认不降：拒绝，而不是继续。"""
        views = crossover_views(70, treated_coal=40.5, control_coal=39.5)
        record = decide(views, [req("Q3", 0.23)], look_days=(80,), look_day=80)
        self.assertEqual(record["decisions"][0]["verdict"], "reject_effect")

    def test_confirmed_risk_excess_rejected(self):
        """越限率确认超上限：拒绝。"""
        views = crossover_views(70, breach_days=set(range(0, 70, 3)))
        record = decide(views, [req("Q4", 0.23)], look_days=(80,), look_day=80)
        self.assertEqual(record["decisions"][0]["verdict"], "reject_risk")

    def test_uncertain_effect_continues(self):
        """效果区间跨过目标：可继续试验但不可放行。"""
        views = crossover_views(70, treated_coal=38.6, control_coal=39.5)
        record = decide(views, [req("Q5", 0.23)], look_days=(80,), look_day=80)
        self.assertEqual(record["decisions"][0]["verdict"], "continue")

    def test_stop_in_cell_holds(self):
        views = crossover_views(70)
        views.append(make_view("S9", "F1", 71, 0.23, stopped=True, breach=True))
        record = decide(views, [req("Q6", 0.23)], look_days=(80,), look_day=80)
        d = record["decisions"][0]
        self.assertEqual(d["verdict"], "hold")
        self.assertIn("S9", d["components"]["stops"]["in_cell"])

    def test_no_look_no_accept(self):
        """尚未执行登记查看时不得放行。"""
        views = crossover_views(70)
        registry = Registry(make_review(look_days=(80,)))
        cutoff = max(v.available_at for v in views)
        record = decide_requests([req("Q7", 0.23)], views, registry, None,
                                 NOW, cutoff)
        self.assertNotEqual(record["decisions"][0]["verdict"], "accept")


class ExplainChangeTests(unittest.TestCase):
    def test_explains_verdict_change_and_new_stops(self):
        previous = {
            "verdict": "continue", "data_cutoff": "2025-01-30T08:00:00+08:00",
            "registry_hash": "a" * 16,
            "decision": {"components": {
                "effect": {"status": "uncertain"},
                "risk": {"emission": {"status": "uncertain", "k": 0, "n_shifts": 24},
                         "steam": {"status": "exceeds", "k": 8, "n_shifts": 24}},
                "stops": {"in_ratio": [], "in_cell": []},
                "support": {"status": "full"}}},
        }
        current = {
            "verdict": "hold", "data_cutoff": "2025-02-05T08:00:00+08:00",
            "registry_hash": "a" * 16,
            "decision": {"components": {
                "effect": {"status": "uncertain"},
                "risk": {"emission": {"status": "uncertain", "k": 1, "n_shifts": 30},
                         "steam": {"status": "exceeds", "k": 9, "n_shifts": 30}},
                "stops": {"in_ratio": ["S3020"], "in_cell": []},
                "support": {"status": "full"}}},
        }
        lines = explain_change(previous, current)
        text = "\n".join(lines)
        self.assertIn("暂停复核", text)
        self.assertIn("S3020", text)
        self.assertIn("数据截止点", text)

    def test_first_round_noted(self):
        current = {"verdict": "hold", "data_cutoff": "x", "registry_hash": "h",
                   "decision": {"components": {}}}
        self.assertIn("首轮", explain_change(None, current)[0])


if __name__ == "__main__":
    unittest.main()
