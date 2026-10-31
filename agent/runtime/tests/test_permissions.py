"""Role enforcement for the Rizq Agent (CH-05).

The point of these tests: a worker request to a privileged tool must FAIL, an owner
action must succeed and be audited, and the client must not be able to choose its own
role or read someone else's history.
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import sqlite3
import tempfile
import unittest

from rizq_agent import copilot, permissions

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEV = str(ROOT / "fixtures" / "rizq_dev.sqlite")

OWNER_PIN = "90901234"
STAFF_PIN = "11112222"


def make_db() -> str:
    """A copy of the dev fixture with known PINs."""
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    shutil.copyfile(DEV, path)
    con = sqlite3.connect(path)
    con.execute("DELETE FROM users")
    con.executemany("INSERT INTO users (id,name,role,pin) VALUES (?,?,?,?)",
                    [("user_owner", "صاحب المطعم", "owner", OWNER_PIN),
                     ("user_staff1", "أحمد", "staff", STAFF_PIN)])
    con.commit()
    con.close()
    return path


class PermissionPolicy(unittest.TestCase):
    def test_owner_outranks_staff_everywhere(self):
        for tool in permissions.TOOL_MIN_ROLE:
            self.assertTrue(permissions.role_allows("owner", tool), tool)

    def test_staff_cannot_reach_money_or_write_tools(self):
        for tool in ["scan_pos", "run_sql", "sales_summary", "run_skill",
                     "plan_fixes", "apply_fixes", "build_skill", "save_fixed_copy"]:
            self.assertFalse(permissions.role_allows("staff", tool), tool)

    def test_staff_can_look_up_an_order_and_raise_a_problem(self):
        for tool in ["get_order", "escalate", "list_skills"]:
            self.assertTrue(permissions.role_allows("staff", tool), tool)

    def test_unknown_tool_and_unknown_role_are_denied(self):
        self.assertFalse(permissions.role_allows("staff", "drop_everything"))
        self.assertFalse(permissions.role_allows("", "get_order"))
        self.assertFalse(permissions.role_allows("administrator", "get_order"))

    def test_every_write_tool_is_owner_only(self):
        for tool in permissions.WRITE_TOOLS:
            self.assertEqual(permissions.TOOL_MIN_ROLE[tool], "owner", tool)

    def test_policy_covers_every_declared_agent_tool(self):
        self.assertEqual(set(copilot.TOOL_NAMES) - set(permissions.TOOL_MIN_ROLE), set())


class PinLogin(unittest.TestCase):
    def setUp(self):
        permissions.reset_for_tests()
        self.db = make_db()
        self.audit = tempfile.mkstemp(suffix=".jsonl")[1]
        self._old = permissions.AUDIT_PATH
        permissions.AUDIT_PATH = pathlib.Path(self.audit)

    def tearDown(self):
        permissions.AUDIT_PATH = self._old
        permissions.reset_for_tests()
        for p in (self.db, self.audit):
            pathlib.Path(p).unlink(missing_ok=True)

    def test_correct_pin_resolves_the_role_from_the_database(self):
        self.assertEqual(permissions.login(self.db, OWNER_PIN)["role"], "owner")
        self.assertEqual(permissions.login(self.db, STAFF_PIN)["role"], "staff")

    def test_login_never_returns_the_pin(self):
        blob = json.dumps(permissions.login(self.db, OWNER_PIN), ensure_ascii=False)
        self.assertNotIn(OWNER_PIN, blob)

    def test_wrong_pin_is_refused(self):
        for bad in ["", "0", "99999999", OWNER_PIN + "0", None]:
            with self.assertRaises(permissions.PermissionDenied):
                permissions.login(self.db, bad)

    def test_a_token_resolves_to_its_own_user(self):
        staff = permissions.login(self.db, STAFF_PIN)
        self.assertEqual(permissions.session(staff["token"])["user_id"], "user_staff1")

    def test_an_unknown_or_logged_out_token_is_refused(self):
        with self.assertRaises(permissions.PermissionDenied):
            permissions.session("not-a-real-token")
        owner = permissions.login(self.db, OWNER_PIN)
        permissions.logout(owner["token"])
        with self.assertRaises(permissions.PermissionDenied):
            permissions.session(owner["token"])

    def test_expired_and_idle_tokens_are_refused(self):
        for field, limit in (("created", permissions.TOKEN_TTL_SECONDS),
                             ("last_seen", permissions.TOKEN_IDLE_SECONDS)):
            token = permissions.login(self.db, OWNER_PIN)["token"]
            permissions._TOKENS[token][field] -= limit + 10
            with self.assertRaises(permissions.PermissionDenied):
                permissions.session(token)

    def test_repeated_wrong_pins_are_rate_limited(self):
        for _ in range(permissions.MAX_PIN_FAILURES):
            with self.assertRaises(permissions.PermissionDenied):
                permissions.login(self.db, "00000000", client="1.2.3.4")
        # Even the CORRECT pin is now refused for that client.
        with self.assertRaises(permissions.PermissionDenied) as cm:
            permissions.login(self.db, OWNER_PIN, client="1.2.3.4")
        self.assertIn("محاولات", str(cm.exception))
        # A different client is unaffected.
        self.assertEqual(permissions.login(self.db, OWNER_PIN, client="5.6.7.8")["role"], "owner")

    def test_a_tampered_snapshot_cannot_invent_a_role(self):
        """A crafted import without the role CHECK must still not escalate."""
        con = sqlite3.connect(self.db)
        con.execute("DROP TABLE users")
        con.execute("CREATE TABLE users (id TEXT PRIMARY KEY, name TEXT, role TEXT, pin TEXT)")
        con.execute("INSERT INTO users VALUES ('u1','x','superuser',?)", (OWNER_PIN,))
        con.commit()
        con.close()
        with self.assertRaises(permissions.PermissionDenied):
            permissions.login(self.db, OWNER_PIN)

    def test_a_snapshot_with_no_users_table_cannot_log_in(self):
        con = sqlite3.connect(self.db)
        con.execute("DROP TABLE users")
        con.commit()
        con.close()
        with self.assertRaises(permissions.PermissionDenied):
            permissions.login(self.db, OWNER_PIN)

    def test_audit_records_logins_and_refusals(self):
        permissions.login(self.db, OWNER_PIN)
        with self.assertRaises(permissions.PermissionDenied):
            permissions.login(self.db, "00000000")
        entries = permissions.read_audit()
        self.assertTrue(any(e["tool"] == "auth.login" and e["allowed"] for e in entries))
        self.assertTrue(any(e["tool"] == "auth.login" and not e["allowed"] for e in entries))
        self.assertNotIn(OWNER_PIN, pathlib.Path(self.audit).read_text(encoding="utf-8"))


class ToolsEnforceTheRole(unittest.TestCase):
    """The gate lives in PosTools.call, so no prompt can talk past it."""

    def setUp(self):
        permissions.reset_for_tests()
        self.audit = tempfile.mkstemp(suffix=".jsonl")[1]
        self._old = permissions.AUDIT_PATH
        permissions.AUDIT_PATH = pathlib.Path(self.audit)
        self.out = tempfile.mkdtemp()

    def tearDown(self):
        permissions.AUDIT_PATH = self._old
        permissions.reset_for_tests()
        pathlib.Path(self.audit).unlink(missing_ok=True)
        shutil.rmtree(self.out, ignore_errors=True)

    def tools(self, role):
        actor = {"user_id": "u_" + role, "name": role, "role": role}
        return copilot.PosTools(DEV, self.out, confirm=lambda _: True, actor=actor)

    def test_staff_is_refused_and_the_tool_never_runs(self):
        t = self.tools("staff")
        try:
            for tool in ["scan_pos", "run_sql", "sales_summary", "apply_fixes", "save_fixed_copy"]:
                out = t.call(tool, {"query": "SELECT 1"} if tool == "run_sql" else {})
                self.assertIn("error", out, tool)
                self.assertIn("صلاحية", out["error"], tool)
            # nothing was exported despite confirm=True
            self.assertEqual(os.listdir(self.out), [])
        finally:
            t.db.close()

    def test_owner_may_run_the_same_tools(self):
        t = self.tools("owner")
        try:
            self.assertNotIn("error", t.call("scan_pos", {}))
            self.assertNotIn("error", t.call("sales_summary", {"days": 7}))
        finally:
            t.db.close()

    def test_staff_may_still_look_up_an_order_and_escalate(self):
        t = self.tools("staff")
        try:
            self.assertNotIn("error", t.call("escalate", {"entity": "ord_x", "note": "مشكلة"}))
            self.assertNotIn("error", t.call("list_skills", {}))
        finally:
            t.db.close()

    def test_denials_and_allowed_calls_are_audited(self):
        t = self.tools("staff")
        try:
            t.call("apply_fixes", {})
            t.call("list_skills", {})
        finally:
            t.db.close()
        entries = permissions.read_audit()
        denied = [e for e in entries if e["tool"] == "apply_fixes"]
        allowed = [e for e in entries if e["tool"] == "list_skills"]
        self.assertTrue(denied and denied[-1]["allowed"] is False)
        self.assertEqual(denied[-1]["role"], "staff")
        self.assertTrue(allowed and allowed[-1]["allowed"] is True)

    def test_a_denied_call_is_visible_in_the_action_log(self):
        t = self.tools("staff")
        try:
            t.call("run_sql", {"query": "SELECT 1"})
        finally:
            t.db.close()
        self.assertEqual(t.log[-1], {"tool": "run_sql", "args": {"query": "SELECT 1"}, "ok": False})

    def test_cli_use_without_an_actor_keeps_working(self):
        """actor=None is the local operator who already holds the file; no gate."""
        t = copilot.PosTools(DEV, self.out, confirm=lambda _: False, actor=None)
        try:
            self.assertNotIn("error", t.call("scan_pos", {}))
        finally:
            t.db.close()

    def test_an_unknown_tool_is_refused_for_a_verified_actor(self):
        t = self.tools("owner")
        try:
            self.assertIn("does not exist", t.call("rm_rf", {})["error"])
        finally:
            t.db.close()

    def test_the_original_fixture_is_never_modified(self):
        import hashlib
        before = hashlib.sha256(pathlib.Path(DEV).read_bytes()).hexdigest()
        t = self.tools("owner")
        try:
            t.call("scan_pos", {})
            t.call("apply_fixes", {})
        finally:
            t.db.close()
        self.assertEqual(before, hashlib.sha256(pathlib.Path(DEV).read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
