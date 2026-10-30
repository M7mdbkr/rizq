import unittest

from rizq_agent.evaluate import evaluate, run_one, make_scenario

N = 15  # train split only: the held-out split is never touched by tests


class TestAgent(unittest.TestCase):
    def assertSuite(self, suite, floor=100.0):
        rows = evaluate("train", N, "rizq_agent", suite)
        bad = [r for r in rows if not r["success"]]
        self.assertGreaterEqual(100 * (N - len(bad)) / N, floor, f"{suite}: {bad[:2]}")
        self.assertEqual(sum(r["false_recovery"] for r in rows), 0, suite)

    def test_each_single_fault(self):
        for s in ("silent_drop", "queue_loss", "sync_corruption", "hung_workflow", "runaway_repair", "unknown"):
            self.assertSuite(s)

    def test_double_faults(self):
        self.assertSuite("double")

    def test_healthy_means_zero_actions(self):
        for r in evaluate("train", N, "rizq_agent", "healthy"):
            self.assertEqual(r["n_actions"], 0)

    def test_unknown_fault_takes_no_fix_action(self):
        from rizq_agent.evaluate import _FaultSchedule, build_shop
        from rizq_agent.agent import run_agent
        for i in range(N):
            sc = make_scenario(i, "train", "unknown")
            res = run_agent(_FaultSchedule(build_shop(sc), sc))
            self.assertEqual(res["actions"], ["escalate"])

    def test_sticky_fix_stops_within_budget(self):
        for r in evaluate("train", N, "rizq_agent", "sticky_fix"):
            self.assertLessEqual(r["rounds"], 3)
            self.assertEqual(r["outcome"], "escalated")

    def test_blind_check_escalates_instead_of_guessing(self):
        self.assertSuite("blind_replica")


if __name__ == "__main__":
    unittest.main()
