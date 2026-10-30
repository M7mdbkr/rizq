"""Regression gate: run after every change. Fails if any tracked metric drops below the recorded baseline."""
import json
import pathlib
import unittest

from rizq_agent.evaluate import evaluate, summarize
from rizq_agent.sheet import run_sheet

ROOT = pathlib.Path(__file__).resolve().parents[1]
BASE = json.loads((ROOT / "tests" / "baseline.json").read_text())


class TestRegression(unittest.TestCase):
    def test_dev_metrics_not_worse_than_baseline(self):
        s = summarize(evaluate("dev", BASE["n"], "rizq_agent"))
        self.assertGreaterEqual(s["success_%"], BASE["success_%"])
        self.assertGreaterEqual(s["diagnosis_%"], BASE["diagnosis_%"])
        self.assertLessEqual(s["false_recovery_claims"], 0)
        self.assertLessEqual(s["false_alarm_%"], BASE["false_alarm_%"])

    def test_components_earn_their_place(self):
        """Ablation ordering must hold: removing verify or invariants makes things worse."""
        full = summarize(evaluate("dev", 60, "rizq_agent", "double"))["success_%"]
        self.assertGreater(full, summarize(evaluate("dev", 60, "no_verify", "double"))["success_%"])
        self.assertGreater(full, summarize(evaluate("dev", 60, "logs_only", "double"))["success_%"])
        sticky = summarize(evaluate("dev", 30, "rizq_agent", "sticky_fix"))["success_%"]
        self.assertGreater(sticky, summarize(evaluate("dev", 30, "no_retry_budget", "sticky_fix"))["success_%"])

    def test_mistakes_sheet_all_pass(self):
        rows = run_sheet(str(ROOT / "mistakes.csv"), runs=5)
        self.assertEqual([r["id"] for r in rows if r["status"] == "FAIL"], [])


if __name__ == "__main__":
    unittest.main()
