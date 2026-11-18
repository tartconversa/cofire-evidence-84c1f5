"""端到端流程：建区、查看、判断、录入第二轮、复核变化、交接。"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def run_cli(*argv, expect=0):
    result = subprocess.run(
        [sys.executable, "-m", "cofire", *argv],
        cwd=REPO, capture_output=True, text=True)
    if result.returncode != expect:
        raise AssertionError(
            f"cofire {' '.join(argv)} -> {result.returncode}\n"
            f"{result.stdout}\n{result.stderr}")
    return result


class FlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="cofire-flow-"))
        cls.ws = cls.tmp / "ws"
        run_cli("init", str(cls.ws), "--pack", str(REPO))
        for day in ("8", "16", "24"):
            run_cli("look", str(cls.ws), "--day", day)
        run_cli("decide", str(cls.ws))
        # 第二轮：合成资料包含 0.23 比例的一次新停试
        cls.round2 = cls.tmp / "round2"
        run_cli("synth-round2", str(cls.round2))
        run_cli("check", str(cls.round2))  # 合成包自身通过完整性核对
        run_cli("ingest", str(cls.ws), "--pack", str(cls.round2))
        run_cli("decide", str(cls.ws))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def decisions(self, which=-1):
        lines = (self.ws / "decisions.jsonl").read_text(encoding="utf-8").splitlines()
        return json.loads(lines[which])

    def test_round1_verdicts(self):
        record = self.decisions(0)
        verdicts = {d["request_id"]: d["verdict"] for d in record["decisions"]}
        self.assertEqual(verdicts["N1"], "reject_risk")
        self.assertEqual(verdicts["N2"], "reject_unsupported")
        self.assertEqual(verdicts["N3"], "hold")

    def test_repeat_look_refused(self):
        run_cli("look", str(self.ws), "--day", "8", expect=2)

    def test_round2_changes_recommendation(self):
        """录入第二轮后：0.23 出现新停试，N1 由拒绝风险变为暂停复核。"""
        record = self.decisions(-1)
        verdicts = {d["request_id"]: d["verdict"] for d in record["decisions"]}
        self.assertEqual(verdicts["N1"], "hold")
        self.assertEqual(verdicts["N4"], "hold")  # 新请求同样受 0.23 停试约束
        n1 = next(d for d in record["decisions"] if d["request_id"] == "N1")
        self.assertIn("S3020", n1["components"]["stops"]["in_ratio"])
        self.assertEqual(n1["action"]["kind"], "revoke_approval")

    def test_why_explains_change(self):
        result = run_cli("why", str(self.ws), "N1")
        self.assertIn("S3020", result.stdout)
        self.assertIn("数据截止点", result.stdout)
        self.assertIn("暂停复核", result.stdout)

    def test_handoff_report(self):
        out = self.tmp / "handoff.md"
        run_cli("handoff", str(self.ws), "-o", str(out))
        text = out.read_text(encoding="utf-8")
        for section in ("效果结论", "风险监测", "可用工况范围",
                        "未执行计划与偏离", "结论限制", "下一轮建议"):
            self.assertIn(section, text)
        self.assertIn("不得把全厂或对照的平均安全推广到未支持的工况", text)
        self.assertIn("无法从资料识别", text)
        self.assertIn("S3020", text)

    def test_audit_trail(self):
        actions = [json.loads(line)["action"] for line in
                   (self.ws / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
        for expected in ("init", "ingest", "look", "decide"):
            self.assertIn(expected, actions)

    def test_unregistered_look_marks_exploratory(self):
        ws2 = self.tmp / "ws2"
        run_cli("init", str(ws2), "--pack", str(REPO))
        run_cli("look", str(ws2), "--day", "9")
        result = run_cli("status", str(ws2))
        self.assertIn("已失效", result.stdout)


if __name__ == "__main__":
    unittest.main()
