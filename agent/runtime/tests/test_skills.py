"""Skills: maker/checker answers, zakat maths, corruption is caught, read-only guarantee, learned skills, routing,
and the problems catalog stays in sync with the code."""
from __future__ import annotations

import hashlib
import os
import pathlib
import sqlite3
import tempfile
import unittest

from rizq_agent import copilot, rizq, skills

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEV = str(ROOT / "fixtures" / "rizq_dev.sqlite")
HELD = str(ROOT / "fixtures" / "rizq_heldout.sqlite")
CATALOG = (ROOT.parents[1] / "Rizq.md").read_text(encoding="utf-8")      # the single project file


def fingerprint(db) -> str:
    h = hashlib.sha256()
    for line in db.iterdump():
        h.update(line.encode())
    return h.hexdigest()


def tiny_db() -> sqlite3.Connection:
    """Hand-made shop with numbers small enough to check by hand."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("""
    CREATE TABLE products(id TEXT, name_ar TEXT, price REAL, cost REAL, stock INTEGER);
    CREATE TABLE orders(id TEXT, total REAL, discount_amount REAL, vat_amount REAL, final_total REAL, status TEXT, created_at TEXT);
    CREATE TABLE order_items(id TEXT, order_id TEXT, product_id TEXT, quantity INTEGER, unit_price REAL, total REAL, name TEXT);
    INSERT INTO products VALUES ('p1','برغر',20,10,60),('p2','بطاطس',10,4,100);   -- inventory at cost = 600 + 400 = 1000
    INSERT INTO orders VALUES ('o1',100,0,15,115,'completed','2026-10-30 12:00:00'),
                              ('o2',200,20,27,207,'completed','2026-10-29 12:00:00'),
                              ('o3',50,0,7.5,57.5,'cancelled','2026-10-30 13:00:00');
    INSERT INTO order_items VALUES ('i1','o1','p1',5,20,100,NULL),('i2','o2','p2',20,10,200,NULL),('i3','o3','p1',2,20,50,NULL);
    """)
    return db


class TestBuiltinSkills(unittest.TestCase):
    def test_every_skill_answers_on_both_real_databases(self):
        for path in (DEV, HELD):
            db = rizq.open_copy(path)
            for name, sk in skills.SKILLS.items():
                params = {"cash": 1000} if name == "zakat_estimate" else {}
                r = skills.run(db, name, params)
                self.assertEqual(r["status"], "ok", f"{name} on {os.path.basename(path)}: {r}")

    def test_sales_matches_a_direct_query(self):
        db = rizq.open_copy(DEV)
        r = skills.run(db, "sales_period", {"days": 7})["value"]
        latest = db.execute("SELECT date(MAX(created_at)) FROM orders").fetchone()[0]
        want = db.execute("SELECT ROUND(SUM(final_total),2) FROM orders WHERE status='completed' "
                          "AND date(created_at) > date(?, '-7 days')", (latest,)).fetchone()[0]
        self.assertAlmostEqual(r["sales"], want, places=2)

    def test_hand_checked_numbers(self):
        db = tiny_db()
        self.assertEqual(skills.run(db, "inventory_value")["value"]["inventory_at_cost"], 1000.0)
        # last 7 days, completed only: o1 + o2
        s = skills.run(db, "sales_period", {"days": 7})["value"]
        self.assertEqual((s["orders"], s["sales"], s["vat"]), (2, 322.0, 42.0))
        # profit: revenue ex VAT = 100 + 180 = 280; cost = 5*10 + 20*4 = 130
        p = skills.run(db, "profit_estimate", {"days": 7})["value"]
        self.assertEqual((p["revenue_ex_vat"], p["cost_of_goods"], p["gross_profit"]), (280.0, 130.0, 150.0))
        t = skills.run(db, "top_products", {"days": 7, "n": 1})["value"]["top"]
        self.assertEqual(t, [["بطاطس", 20]])

    def test_cancelled_orders_are_never_counted(self):
        db = tiny_db()
        self.assertEqual(skills.run(db, "vat_collected", {"month": "2026-10"})["value"]["vat_collected"], 42.0)


class TestZakat(unittest.TestCase):
    def test_hijri_year(self):
        # base = inventory 1000 + cash 9000 - liabilities 2000 = 8000; 2.5% = 200
        r = skills.run(tiny_db(), "zakat_estimate", {"cash": 9000, "liabilities": 2000})
        self.assertEqual(r["status"], "ok")
        self.assertEqual((r["value"]["zakat_base"], r["value"]["zakat_due"], r["value"]["rate_percent"]), (8000.0, 200.0, 2.5))

    def test_gregorian_year_uses_prorated_rate(self):
        r = skills.run(tiny_db(), "zakat_estimate", {"cash": 9000, "liabilities": 2000, "calendar": "gregorian"})
        self.assertAlmostEqual(r["value"]["rate_percent"], 2.5777, places=3)
        self.assertAlmostEqual(r["value"]["zakat_due"], 206.21, places=2)

    def test_missing_cash_asks_the_owner_instead_of_guessing(self):
        r = skills.run(tiny_db(), "zakat_estimate", {})
        self.assertEqual(r["status"], "needs_input")
        self.assertEqual([q["param"] for q in r["questions"]], ["cash"])

    def test_below_nisab_nothing_due(self):
        r = skills.run(tiny_db(), "zakat_estimate", {"cash": 100, "nisab_sar": 20000})
        self.assertEqual(r["value"]["zakat_due"], 0.0)

    def test_negative_base_nothing_due(self):
        r = skills.run(tiny_db(), "zakat_estimate", {"cash": 0, "liabilities": 50000})
        self.assertEqual(r["value"]["zakat_due"], 0.0)

    def test_assumptions_always_returned(self):
        r = skills.run(tiny_db(), "zakat_estimate", {"cash": 1})
        self.assertTrue(any("هيئة الزكاة" in a for a in r["assumptions"]))

    def test_bad_calendar_is_rejected(self):
        self.assertEqual(skills.run(tiny_db(), "zakat_estimate", {"cash": 1, "calendar": "lunar"})["status"], "needs_input")


class TestCheckerCatchesProblems(unittest.TestCase):
    def test_wrong_vat_on_one_order_blocks_the_answer(self):
        db = tiny_db()
        db.execute("UPDATE orders SET vat_amount = 40 WHERE id='o1'")      # should be 15
        r = skills.run(db, "vat_collected", {"month": "2026-10"})
        self.assertEqual(r["status"], "checker_disagrees")
        self.assertIn("escalate", r["action"])

    def test_wrong_charged_total_blocks_sales(self):
        db = tiny_db()
        db.execute("UPDATE orders SET final_total = 999 WHERE id='o2'")
        self.assertEqual(skills.run(db, "sales_period", {"days": 7})["status"], "checker_disagrees")

    def test_one_halala_rounding_is_not_a_false_alarm(self):
        db = tiny_db()
        db.execute("UPDATE orders SET vat_amount = 15.01, final_total = 115.01 WHERE id='o1'")
        self.assertEqual(skills.run(db, "vat_collected", {"month": "2026-10"})["status"], "ok")

    def test_a_broken_maker_is_caught(self):
        sk = skills.SKILLS["inventory_value"]
        good = sk.compute
        sk.compute = lambda db, p: {"inventory_at_cost": 1.0, "inventory_at_retail": 1.0, "products": 2}
        try:
            self.assertEqual(skills.run(tiny_db(), "inventory_value")["status"], "checker_disagrees")
        finally:
            sk.compute = good

    def test_unknown_skill_is_an_error_not_a_crash(self):
        r = skills.run(tiny_db(), "delete_everything")
        self.assertEqual(r["status"], "error")


class TestReadOnly(unittest.TestCase):
    def test_running_every_skill_changes_nothing(self):
        db = rizq.open_copy(DEV)
        before = fingerprint(db)
        for name in skills.SKILLS:
            skills.run(db, name, {"cash": 5})
        self.assertEqual(before, fingerprint(db))
        db.execute("UPDATE products SET stock = stock")       # writes work again: query_only was switched back off

    def test_original_file_is_never_opened_for_writing(self):
        before = hashlib.sha256(open(DEV, "rb").read()).hexdigest()
        t = copilot.PosTools(DEV, tempfile.mkdtemp(), lambda q: True)
        t.call("run_skill", {"name": "sales_period", "params": {}})
        self.assertEqual(before, hashlib.sha256(open(DEV, "rb").read()).hexdigest())


class TestLearnedSkills(unittest.TestCase):
    GOOD = dict(name="cash_share", description="share of cash orders",
                sql="SELECT COUNT(*) FROM orders WHERE status='completed'",
                check_sql="SELECT COUNT(id) FROM orders WHERE status IN ('completed')")

    def setUp(self):
        self.db = tiny_db()
        self.path = os.path.join(tempfile.mkdtemp(), "learned.json")

    def test_installed_only_after_human_approval(self):
        r = skills.build_learned_skill(self.db, self.path, confirm=lambda q: False, **self.GOOD)
        self.assertEqual(r["status"], "not_installed")
        self.assertFalse(os.path.exists(self.path))
        r = skills.build_learned_skill(self.db, self.path, confirm=lambda q: True, **self.GOOD)
        self.assertEqual(r["status"], "installed")

    def test_learned_skill_runs_later_without_any_model(self):
        skills.build_learned_skill(self.db, self.path, confirm=lambda q: True, **self.GOOD)
        r = skills.run_learned(self.db, self.path, "cash_share")
        self.assertEqual((r["status"], r["value"]), ("ok", [[2]]))

    def test_maker_and_checker_must_agree(self):
        bad = dict(self.GOOD, check_sql="SELECT COUNT(*) FROM orders")      # counts cancelled too
        r = skills.build_learned_skill(self.db, self.path, confirm=lambda q: True, **bad)
        self.assertEqual(r["status"], "checker_disagrees")
        self.assertFalse(os.path.exists(self.path))

    def test_same_query_twice_is_not_a_checker(self):
        bad = dict(self.GOOD, check_sql=self.GOOD["sql"].lower())
        self.assertEqual(skills.build_learned_skill(self.db, self.path, confirm=lambda q: True, **bad)["status"], "rejected")

    def test_write_statements_are_rejected(self):
        for evil in ("DELETE FROM orders", "SELECT 1; DROP TABLE orders", "UPDATE orders SET total=0",
                     "WITH x AS (SELECT 1) DELETE FROM orders", "PRAGMA writable_schema=1", "ATTACH DATABASE 'x' AS y"):
            r = skills.build_learned_skill(self.db, self.path, "evil_skill", "x", evil, "SELECT 1", lambda q: True)
            self.assertIn(r["status"], ("rejected", "error"), evil)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 3)

    def test_bad_sql_from_a_model_does_not_crash(self):
        r = skills.build_learned_skill(self.db, self.path, "broken_one", "x", "SELECT nope FROM nowhere", "SELECT 2", lambda q: True)
        self.assertEqual(r["status"], "error")

    def test_bad_names_rejected(self):
        for n in ("../etc", "A", "ab", "zakat_estimate"):
            self.assertEqual(skills.build_learned_skill(self.db, self.path, n, "x", "SELECT 1", "SELECT 2-1", lambda q: True)["status"], "error")

    def test_through_the_agent_tools(self):
        out = tempfile.mkdtemp()
        t = copilot.PosTools(DEV, out, lambda q: True)
        r = t.call("build_skill", dict(name="orders_total", description="orders count",
                                       sql="SELECT COUNT(*) FROM orders", check_sql="SELECT COUNT(id) FROM orders"))
        self.assertEqual(r["status"], "installed")
        self.assertIn("orders_total", t.call("list_skills", {})["learned"])
        # a brand-new session (new tools object) still has it
        t2 = copilot.PosTools(DEV, out, lambda q: True)
        self.assertEqual(t2.call("run_skill", {"name": "orders_total"})["status"], "ok")


# phrase -> expected skill. Mixed Gulf / Egyptian Arabic, English, typos. Written before tuning the patterns.
ROUTING = [
    ("كم زكاتي؟", "zakat_estimate"), ("ابغى احسب زكاة المطعم", "zakat_estimate"), ("how much zakat do I owe", "zakat_estimate"),
    ("عايز أعرف الزكاه بتاعتي", "zakat_estimate"),
    ("كم مبيعاتي الاسبوع هذا", "sales_period"), ("ايه الدخل بتاع اخر 3 ايام", "sales_period"), ("what was my revenue last week", "sales_period"),
    ("كم الضريبة اللي جمعتها", "vat_collected"), ("how much VAT did I collect", "vat_collected"),
    ("كم قيمة المخزون عندي", "inventory_value"), ("what is my inventory worth", "inventory_value"),
    ("كم ربحي هالشهر", "profit_estimate"), ("وش هامش الربح", "profit_estimate"), ("what's my profit", "profit_estimate"),
    ("ايش اكثر صنف يتباع", "top_products"), ("ايه اكتر صنف اتباع", "top_products"), ("top selling items", "top_products"),
]


class TestRouting(unittest.TestCase):
    def test_phrases_reach_the_right_skill(self):
        wrong = [(q, want, skills.match(q)) for q, want in ROUTING if skills.match(q) != want]
        acc = 1 - len(wrong) / len(ROUTING)
        print(f"\n  routing accuracy: {acc:.0%} ({len(ROUTING) - len(wrong)}/{len(ROUTING)})  misses: {wrong}")
        self.assertGreaterEqual(acc, 0.9, wrong)

    def test_a_repair_request_never_becomes_a_skill(self):
        t = copilot.PosTools(DEV, tempfile.mkdtemp(), lambda q: False)
        copilot.ask(copilot.RulesBrain(), t, "اصلح مشكلة المبيعات", on_event=lambda *a: None)
        self.assertNotIn("run_skill", [x["tool"] for x in t.log])

    def test_extract_numbers_from_text(self):
        p = skills.extract_params("زكاتي: نقد 50,000 وديون علي 12000 سنة ميلادية")
        self.assertEqual((p["cash"], p["liabilities"], p["calendar"]), (50000.0, 12000.0, "gregorian"))

    def test_end_to_end_zakat_conversation(self):
        t = copilot.PosTools(DEV, tempfile.mkdtemp(), lambda q: True)
        a = copilot.ask(copilot.RulesBrain(), t, "كم زكاتي؟", on_event=lambda *x: None)
        self.assertIn("أحتاج منك", a)
        b = copilot.ask(copilot.RulesBrain(), t, "زكاتي: نقد 50000 وديون علي 12000", on_event=lambda *x: None)
        self.assertIn("1101.6", b)


class TestCatalogStaysInSync(unittest.TestCase):
    def test_every_detectable_problem_is_documented(self):
        missing = [k for k in rizq.KINDS if k not in CATALOG]
        self.assertEqual(missing, [], "add these to Projects/Rizq/Rizq.md")

    def test_every_skill_is_documented(self):
        self.assertEqual([k for k in skills.SKILLS if k not in CATALOG], [])

    def test_known_bugs_listed(self):
        for bug in ("BUG-1", "BUG-2"):
            self.assertIn(bug, CATALOG)


if __name__ == "__main__":
    unittest.main()
