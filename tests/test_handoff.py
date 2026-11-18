"""交接报告内容的测试。"""
import datetime
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from helpers import BASE, repo_review, repo_views  # noqa: E402

from cofire.analysis import run_look  # noqa: E402
from cofire.handoff import render_handoff  # noqa: E402
from cofire.model import NextRequest  # noqa: E402
from cofire.recommend import decide_requests  # noqa: E402
from cofire.registry import Registry  # noqa: E402

NOW = datetime.datetime(2025, 2, 1, 8, 0, tzinfo=datetime.timezone.utc)


class HandoffTests(unittest.TestCase):
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
        record = decide_requests(requests, views, registry, look, NOW, cutoff)
        cls.text = render_handoff(registry, views, requests, look, record)

    def test_required_sections(self):
        for section in ("效果结论", "风险监测", "可用工况范围",
                        "未执行计划与偏离", "结论限制", "下一轮建议"):
            self.assertIn(section, self.text)

    def test_no_release_without_support(self):
        """没有工况同时满足效果与风险：明示不可放行、不得外推。"""
        self.assertIn("可放行工况：无", self.text)
        self.assertIn("不得把全厂或对照的平均安全推广到未支持的工况", self.text)

    def test_limitations_are_explicit(self):
        self.assertIn("无法从资料识别", self.text)  # 停试反事实不可识别
        self.assertIn("低于对照班次实测", self.text)  # 不足率上限低于基线

    def test_stopped_shifts_stay_visible(self):
        self.assertIn("S002", self.text)
        self.assertIn("未从分析中删除", self.text)

    def test_verdicts_listed(self):
        self.assertIn("拒绝（无支持证据）", self.text)
        self.assertIn("暂停复核", self.text)


if __name__ == "__main__":
    unittest.main()
