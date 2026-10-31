"""Filling an owner's document, and exporting everything for an accountant.

The properties that matter: a figure is written only when it was independently
verified, and no employee PIN ever leaves the building.
"""
from __future__ import annotations

import io
import os
import pathlib
import re
import shutil
import sqlite3
import tempfile
import unittest
import zipfile

from rizq_agent import copilot, export, fill, permissions, rizq

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEV = str(ROOT / "fixtures" / "rizq_dev.sqlite")


class FillTemplate(unittest.TestCase):
    def setUp(self):
        self.db = rizq.open_copy(DEV)
        self.addCleanup(self.db.close)

    def test_known_fields_are_filled_with_numbers(self):
        out = fill.fill(self.db, "sales={{sales_total}} orders={{sales_orders}}")
        self.assertEqual(len(out["unresolved"]), 0)
        self.assertNotIn("{{", out["text"])
        self.assertRegex(out["text"], r"sales=[\d,]+\.\d\d orders=[\d,]+")

    def test_a_parameter_changes_the_period(self):
        week = fill.fill(self.db, "{{sales_total:days=7}}")["text"]
        month = fill.fill(self.db, "{{sales_total:days=30}}")["text"]
        self.assertNotEqual(week, month)

    def test_an_unknown_field_is_left_visible_and_explained(self):
        out = fill.fill(self.db, "x={{not_a_field}}")
        self.assertIn("{{not_a_field}}", out["text"])
        self.assertEqual(out["unresolved"][0]["reason"], "no such field")

    def test_a_value_needing_owner_input_is_never_guessed(self):
        """Zakat needs cash balances Rizq does not store: leave the blank."""
        out = fill.fill(self.db, "zakat={{zakat_due}}")
        self.assertIn("{{zakat_due}}", out["text"])
        self.assertIn("needs information", out["unresolved"][0]["reason"])

    def test_nothing_is_written_when_the_checker_disagrees(self):
        from unittest import mock
        with mock.patch("rizq_agent.skills.run",
                        return_value={"status": "checker_disagrees", "skill": "sales_period"}):
            out = fill.fill(self.db, "s={{sales_total}}")
        self.assertIn("{{sales_total}}", out["text"])
        self.assertIn("checker disagreed", out["unresolved"][0]["reason"])

    def test_meta_fields_resolve(self):
        import datetime
        out = fill.fill(self.db, "{{today}}", now=datetime.datetime(2026, 10, 31, 9, 0))
        self.assertEqual(out["text"], "2026-10-31")

    def test_a_document_with_no_placeholders_is_unchanged(self):
        text = "تقرير بدون أي حقول"
        self.assertEqual(fill.fill(self.db, text)["text"], text)

    def test_every_advertised_field_actually_resolves(self):
        """A field offered to the owner must work, or be honestly unresolvable."""
        for name in fill.available_fields():
            out = fill.fill(self.db, "{{%s}}" % name)
            if out["unresolved"]:
                self.assertIn(out["unresolved"][0]["reason"],
                              ("needs information Rizq does not store",), name)
            else:
                self.assertNotIn("{{", out["text"], name)


class ExportBundle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.name, cls.blob = export.build(DEV, who="tester")
        cls.zip = zipfile.ZipFile(io.BytesIO(cls.blob))

    def test_it_is_a_zip_with_a_dated_name(self):
        self.assertRegex(self.name, r"^rizq-export-\d{8}-\d{6}\.zip$")
        self.assertIsNone(self.zip.testzip())

    def test_every_table_is_exported(self):
        con = sqlite3.connect(f"file:{DEV}?mode=ro", uri=True)
        tables = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        con.close()
        names = set(self.zip.namelist())
        for t in tables:
            self.assertIn(f"data/{t}.csv", names, t)

    def test_logs_and_reports_are_present(self):
        names = set(self.zip.namelist())
        for expected in ("README.txt", "data/rizq.sqlite",
                         "logs/chat_history.csv", "reports/integrity.csv",
                         "reports/summary.json"):
            self.assertIn(expected, names)

    def test_pins_are_redacted_in_the_csv(self):
        users = self.zip.read("data/users.csv").decode()
        self.assertIn(export.REDACTED, users)
        self.assertIsNone(re.search(r"\b\d{8}\b", users), "a PIN-shaped number survived")

    def test_pins_are_redacted_in_the_database_copy(self):
        fd, tmp = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        try:
            pathlib.Path(tmp).write_bytes(self.zip.read("data/rizq.sqlite"))
            con = sqlite3.connect(tmp)
            self.assertEqual({r[0] for r in con.execute("SELECT pin FROM users")}, {export.REDACTED})
            self.assertEqual(con.execute("SELECT COUNT(*) FROM orders").fetchone()[0],
                             477, "order data must survive the redaction")
            con.close()
        finally:
            pathlib.Path(tmp).unlink(missing_ok=True)

    def test_the_source_database_is_not_modified(self):
        import hashlib
        before = hashlib.sha256(pathlib.Path(DEV).read_bytes()).hexdigest()
        export.build(DEV, who="tester")
        self.assertEqual(before, hashlib.sha256(pathlib.Path(DEV).read_bytes()).hexdigest())

    def test_the_summary_states_it_is_not_a_tax_filing(self):
        import json
        summary = json.loads(self.zip.read("reports/summary.json"))
        self.assertFalse(summary["submitted_to_zatca"])
        self.assertTrue(summary["pins_redacted"])
        self.assertEqual(summary["tables"]["orders"], 477)


class RolesCoverTheNewTools(unittest.TestCase):
    def setUp(self):
        permissions.reset_for_tests()
        self.out = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.out, True)
        self.audit = tempfile.mkstemp(suffix=".jsonl")[1]
        self._old = permissions.AUDIT_PATH
        permissions.AUDIT_PATH = pathlib.Path(self.audit)
        self.addCleanup(lambda: setattr(permissions, "AUDIT_PATH", self._old))
        self.addCleanup(lambda: pathlib.Path(self.audit).unlink(missing_ok=True))

    def tools(self, role):
        return copilot.PosTools(DEV, self.out, confirm=lambda _: True,
                                actor={"user_id": "u", "name": "n", "role": role})

    def test_a_worker_cannot_fill_or_export(self):
        t = self.tools("staff")
        try:
            for tool, args in (("fill_template", {"text": "{{sales_total}}"}),
                               ("export_data", {}), ("list_fill_fields", {})):
                self.assertIn("error", t.call(tool, args), tool)
            self.assertEqual(os.listdir(self.out), [], "nothing may be written for a worker")
        finally:
            t.db.close()

    def test_the_owner_can_fill_and_export(self):
        t = self.tools("owner")
        try:
            filled = t.call("fill_template", {"text": "s={{sales_total}}"})
            self.assertNotIn("error", filled)
            self.assertNotIn("{{", filled["text"])
            out = t.call("export_data", {})
            self.assertNotIn("error", out)
            self.assertTrue(pathlib.Path(out["saved"]).is_file())
            self.assertTrue(out["pins_redacted"])
        finally:
            t.db.close()

    def test_an_empty_template_is_rejected(self):
        t = self.tools("owner")
        try:
            self.assertIn("error", t.call("fill_template", {"text": "   "}))
        finally:
            t.db.close()


if __name__ == "__main__":
    unittest.main()
