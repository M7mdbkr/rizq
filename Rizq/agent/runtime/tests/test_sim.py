import unittest

from rizq_agent.observe import Observer
from rizq_agent.sim import Shop, vat_of


class TestSimulator(unittest.TestCase):
    def test_deterministic_by_seed(self):
        a, b = Shop(42), Shop(42)
        for _ in range(30):
            a.tick(); b.tick()
        self.assertEqual(a.orders, b.orders)
        self.assertEqual(a.logs, b.logs)

    def test_healthy_system_never_violates_invariants(self):
        for seed in range(40):
            shop = Shop(seed, log_noise=0.5, reword=seed % 2 == 0)
            shop.spikes.append((10, 14, 4))
            shop.sync_blips = {5, 6, 20}
            obs = Observer(shop)
            for _ in range(60):
                shop.tick()
                self.assertEqual(obs.failing(), [], f"seed {seed} t={shop.t}")

    def test_vat_is_15_percent_in_halalas(self):
        self.assertEqual(vat_of(10000), 1500)
        self.assertEqual(vat_of(333), 50)

    def test_every_order_adds_up(self):
        shop = Shop(3)
        for _ in range(20):
            shop.tick()
        for o in shop.orders.values():
            self.assertEqual(o["final_total"], o["total"] - o["discount_amount"] + o["vat_amount"])


if __name__ == "__main__":
    unittest.main()
