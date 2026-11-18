"""登记冻结、修订与显著性守卫的测试。"""
import datetime
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from helpers import BASE, make_review, make_view  # noqa: E402

from cofire.analysis import run_look  # noqa: E402
from cofire.registry import Registry  # noqa: E402

NOW = datetime.datetime(2025, 2, 1, 8, 0, tzinfo=datetime.timezone.utc)


class RegistryTests(unittest.TestCase):
    def test_freeze_on_first_look(self):
        registry = Registry(make_review())
        self.assertIsNone(registry.frozen_at)
        views = [make_view("A1", "F1", 0, 0.0), make_view("A2", "F2", 0, 0.23)]
        run_look(views, registry, 10, NOW)
        self.assertIsNotNone(registry.frozen_at)

    def test_amend_before_freeze_keeps_confirmatory(self):
        registry = Registry(make_review())
        late = registry.amend({"effect_target": -1.5}, NOW)
        self.assertFalse(late)
        self.assertTrue(registry.confirmatory)
        self.assertEqual(registry.effect_target, -1.5)

    def test_amend_after_freeze_voids(self):
        registry = Registry(make_review())
        views = [make_view("A1", "F1", 0, 0.0), make_view("A2", "F2", 0, 0.23)]
        run_look(views, registry, 10, NOW)
        late = registry.amend({"effect_target": -1.5}, NOW, reason="中途改主要指标")
        self.assertTrue(late)
        self.assertFalse(registry.confirmatory)
        self.assertIn("effect_target", registry.void_reasons[-1]["reason"])

    def test_unregistered_look_voids(self):
        registry = Registry(make_review())
        views = [make_view("A1", "F1", 0, 0.0), make_view("A2", "F2", 0, 0.23)]
        result = run_look(views, registry, 11, NOW)  # 11 不在 (10, 20)
        self.assertFalse(result["registered"])
        self.assertFalse(registry.confirmatory)
        self.assertFalse(result["confirmatory"])

    def test_subgroup_look_voids(self):
        registry = Registry(make_review())
        views = [make_view("A1", "F1", 0, 0.0), make_view("A2", "F2", 0, 0.23),
                 make_view("A3", "F2", 1, 0.0), make_view("A4", "F1", 1, 0.23)]
        result = run_look(views, registry, 10, NOW, subset=["F1"])
        self.assertTrue(result["subgroup"])
        self.assertFalse(result["confirmatory"])
        self.assertFalse(registry.confirmatory)

    def test_hash_tracks_spec(self):
        registry = Registry(make_review())
        before = registry.hash()
        registry.amend({"effect_target": -1.5}, NOW)
        self.assertNotEqual(before, registry.hash())

    def test_roundtrip(self):
        registry = Registry(make_review())
        registry.freeze(NOW)
        again = Registry.from_dict(registry.to_dict())
        self.assertEqual(again.hash(), registry.hash())
        self.assertEqual(again.frozen_at, registry.frozen_at)


if __name__ == "__main__":
    unittest.main()
