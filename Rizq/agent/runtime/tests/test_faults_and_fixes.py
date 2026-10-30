import unittest

from rizq_agent import act
from rizq_agent.faults import ALL_FAULTS, inject
from rizq_agent.observe import Observer
from rizq_agent.sim import Shop

BREAKS = {
    "silent_drop": "payments_match_orders", "queue_loss": "queue_conservation",
    "sync_corruption": "replica_consistent", "hung_workflow": "receipts_timely",
    "runaway_repair": "retry_budget", "unknown": "prices_match_catalog",
}


def broken(label, seed=1):
    shop = Shop(seed)
    for _ in range(5):
        shop.tick()
    inject(shop, label, severity=1.0)
    obs = Observer(shop)
    for _ in range(15):
        shop.tick()
    return shop, obs


class TestFaults(unittest.TestCase):
    def test_each_fault_breaks_its_invariant(self):
        for label in ALL_FAULTS:
            for seed in range(5):
                _, obs = broken(label, seed)
                self.assertIn(BREAKS[label], obs.failing(), f"{label} seed {seed}")

    def test_faults_are_silent_in_logs(self):
        """The core claim: 4 of the 5 challenge faults produce no ERROR line."""
        for label in ("silent_drop", "sync_corruption", "hung_workflow", "unknown"):
            shop, _ = broken(label)
            self.assertFalse([l for l in shop.logs if l[1] == "ERROR"], label)


class TestPlaybooks(unittest.TestCase):
    def test_playbook_fixes_each_fixable_fault(self):
        for label, steps in act.PLAYBOOKS.items():
            for seed in range(5):
                shop, obs = broken(label, seed)
                act.execute(obs, steps, [])
                for _ in range(6):
                    shop.tick()
                self.assertNotIn(label, shop.faults)
                self.assertEqual(obs.failing(), [], f"{label} seed {seed}")

    def test_restart_does_not_fix_sticky_fault(self):
        shop = Shop(1)
        inject(shop, "silent_drop", sticky=True)
        shop.admin("restart_service", name="order_writer")
        self.assertIn("silent_drop", shop.faults)


if __name__ == "__main__":
    unittest.main()
