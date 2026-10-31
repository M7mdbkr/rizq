"""Agent on the REAL Rizq database (fixtures exported from the real app). Held-out fixture is never used here."""
import hashlib
import pathlib
import unittest

from rizq_agent import rizq, rizq_eval

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEV = str(ROOT / "fixtures" / "rizq_dev.sqlite")


class TestRizq(unittest.TestCase):
    def test_untouched_db_only_shows_known_real_issue(self):
        findings = rizq_eval.pristine_check("dev", str(ROOT))
        self.assertIn("future_timestamp:ord_zjkx2gs", findings)
        # Rizq never writes daily_ledger (P-6), so every reconcilable day is "missing",
        # not "drift"; one day is blocked by an order under investigation.
        self.assertEqual(sum(x.startswith("daily_ledger_missing:") for x in findings), 29)
        self.assertEqual(sum(x.startswith("daily_ledger_drift:") for x in findings), 0)
        self.assertEqual(sum(x.startswith("daily_ledger_blocked:") for x in findings), 1)

    def test_every_planted_mistake_kind(self):
        for idx, kind in enumerate(rizq_eval.MUTATIONS):
            for k in range(3):
                r = rizq_eval.run_scenario("dev", idx * 1000 + k, [kind], str(ROOT))
                self.assertTrue(r["success"], r)

    def test_mixed_mistakes(self):
        res = rizq_eval.evaluate("dev", 15, str(ROOT))
        self.assertEqual(res["success_%"], 100.0)
        self.assertEqual(res["spurious_findings"], 0)

    def test_original_file_is_never_modified(self):
        h = hashlib.sha256(pathlib.Path(DEV).read_bytes()).hexdigest()
        rizq.run_agent(DEV)
        self.assertEqual(h, hashlib.sha256(pathlib.Path(DEV).read_bytes()).hexdigest())

    def test_ledger_with_suspect_order_is_not_rebuilt(self):
        db = rizq.open_copy(DEV)
        try:
            findings = rizq.scan(db, rizq_eval.NOW)
            blocked = [f for f in findings if f.kind == "daily_ledger_blocked"]
            self.assertTrue(blocked)
            rizq.repair(db, findings)
            for finding in blocked:
                self.assertIsNone(db.execute("SELECT id FROM daily_ledger WHERE date=?", (finding.entity,)).fetchone())
        finally:
            db.close()

    def test_money_is_never_auto_edited(self):
        for kind, (_, _, handling, _) in rizq_eval.MUTATIONS.items():
            if kind in ("RZ04_vat_wrong", "RZ05_final_wrong"):
                self.assertEqual(handling, "escalate")
        self.assertEqual({k for k, v in rizq.KINDS.items() if v[1] == "auto"},
                         {"line_total_wrong", "stock_movement_missing", "customer_drift",
                          "daily_ledger_drift", "daily_ledger_missing"})


if __name__ == "__main__":
    unittest.main()
