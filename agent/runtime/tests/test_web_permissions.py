"""End-to-end HTTP checks for CH-05: worker requests to privileged endpoints must fail.

These run a real ThreadingHTTPServer on an ephemeral loopback port and talk to it over
HTTP, so they cover the actual request path (token parsing, status codes, ordering of
the auth check) rather than the policy table alone.
"""
from __future__ import annotations

import base64
import json
import os
import pathlib
import shutil
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from rizq_agent import permissions, web

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEV = str(ROOT / "fixtures" / "rizq_dev.sqlite")
OWNER_PIN = "90901234"
STAFF_PIN = "11112222"


def make_db(path: str) -> None:
    shutil.copyfile(DEV, path)
    con = sqlite3.connect(path)
    con.execute("DELETE FROM users")
    con.executemany("INSERT INTO users (id,name,role,pin) VALUES (?,?,?,?)",
                    [("user_owner", "صاحب المطعم", "owner", OWNER_PIN),
                     ("user_staff1", "أحمد", "staff", STAFF_PIN)])
    con.commit()
    con.close()


class HttpRoles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = tempfile.mkstemp(suffix=".sqlite")[1]
        make_db(cls.db)
        cls.snapshot = base64.b64encode(pathlib.Path(cls.db).read_bytes()).decode()
        cls._old_db = web.ACTIVE_DB
        web.ACTIVE_DB = pathlib.Path(cls.db)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        web.ACTIVE_DB = cls._old_db
        pathlib.Path(cls.db).unlink(missing_ok=True)

    def setUp(self):
        permissions.reset_for_tests()
        self.audit = tempfile.mkstemp(suffix=".jsonl")[1]
        self._old_audit = permissions.AUDIT_PATH
        permissions.AUDIT_PATH = pathlib.Path(self.audit)

    def tearDown(self):
        permissions.AUDIT_PATH = self._old_audit
        permissions.reset_for_tests()
        pathlib.Path(self.audit).unlink(missing_ok=True)

    # ---------------------------------------------------------------- helpers
    def call(self, path, body=None, token=None, method=None):
        url = f"http://127.0.0.1:{self.port}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method or ("POST" if data else "GET"))
        req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Authorization", "Bearer " + token)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def login(self, pin):
        status, out = self.call("/api/auth/login", {"pin": pin, "sqlite_base64": self.snapshot})
        self.assertEqual(status, 200, out)
        return out["token"], out

    # ---------------------------------------------------------------- login
    def test_login_with_the_right_pin_returns_the_server_resolved_role(self):
        _, owner = self.login(OWNER_PIN)
        self.assertEqual(owner["role"], "owner")
        _, staff = self.login(STAFF_PIN)
        self.assertEqual(staff["role"], "staff")
        self.assertNotIn(STAFF_PIN, json.dumps(staff))

    def test_login_with_a_wrong_pin_is_403(self):
        status, out = self.call("/api/auth/login",
                                {"pin": "00000000", "sqlite_base64": self.snapshot})
        self.assertEqual(status, 403)
        self.assertEqual(out["code"], "forbidden")

    def test_whoami_reports_only_the_tools_that_role_may_use(self):
        token, _ = self.login(STAFF_PIN)
        status, out = self.call("/api/auth/whoami", {"token": token})
        self.assertEqual(status, 200)
        self.assertEqual(out["role"], "staff")
        self.assertNotIn("apply_fixes", out["allowed_tools"])
        self.assertIn("get_order", out["allowed_tools"])

    # ---------------------------------------------- privileged read endpoints
    def test_scan_requires_a_token(self):
        self.assertEqual(self.call("/api/scan")[0], 403)

    def test_scan_refuses_a_worker_and_serves_the_owner(self):
        staff, _ = self.login(STAFF_PIN)
        self.assertEqual(self.call("/api/scan", token=staff)[0], 403)
        owner, _ = self.login(OWNER_PIN)
        status, out = self.call("/api/scan", token=owner)
        self.assertEqual(status, 200)
        self.assertIn("findings", out)

    def test_vat_summary_refuses_a_worker(self):
        staff, _ = self.login(STAFF_PIN)
        self.assertEqual(self.call("/api/zatca/vat-summary?month=2026-10", token=staff)[0], 403)
        self.assertEqual(self.call("/api/zatca/vat-summary?month=2026-10")[0], 403)

    def test_a_token_in_the_query_string_also_works(self):
        owner, _ = self.login(OWNER_PIN)
        self.assertEqual(self.call(f"/api/zatca/vat-summary?month=2026-10&token={owner}")[0], 200)

    # ---------------------------------------------- privileged write endpoints
    def test_database_swap_endpoints_are_owner_only(self):
        staff, _ = self.login(STAFF_PIN)
        self.assertEqual(self.call("/api/database/reset", {}, token=staff)[0], 403)
        self.assertEqual(self.call("/api/database/import",
                                   {"sqlite_base64": self.snapshot}, token=staff)[0], 403)
        self.assertEqual(self.call("/api/database/reset", {})[0], 403)

    def test_audit_log_is_owner_only(self):
        staff, _ = self.login(STAFF_PIN)
        self.assertEqual(self.call("/api/audit", {"token": staff})[0], 403)
        owner, _ = self.login(OWNER_PIN)
        status, out = self.call("/api/audit", {"token": owner})
        self.assertEqual(status, 200)
        self.assertTrue(any(e["tool"] == "auth.login" for e in out["entries"]))

    # ---------------------------------------------------------------- chat
    def test_chat_endpoints_reject_a_missing_or_stale_token(self):
        for path in ("/api/chat", "/api/chat/local"):
            status, out = self.call(path, {"message": "كم مبيعاتي؟",
                                           "sqlite_base64": self.snapshot})
            self.assertEqual(status, 403, (path, out))
        token, _ = self.login(OWNER_PIN)
        permissions.logout(token)
        self.assertEqual(self.call("/api/chat", {"message": "x"}, token=token)[0], 403)

    def test_chat_cannot_be_reached_with_a_self_declared_user_id(self):
        """The old hole: claiming an identity in the body must no longer work."""
        status, _ = self.call("/api/chat/local", {"message": "كم مبيعاتي؟",
                                                 "user_id": "user_owner",
                                                 "sqlite_base64": self.snapshot})
        self.assertEqual(status, 403)

    # ---------------------------------------------------------------- memory
    def test_history_needs_a_token_and_returns_only_your_own(self):
        from rizq_agent import memory
        fd, mem = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        old = memory.PATH
        memory.PATH = pathlib.Path(mem)
        try:
            memory.append_exchange("user_owner", "سؤال المالك", "جواب المالك")
            memory.append_exchange("user_staff1", "سؤال العامل", "جواب العامل")
            self.assertEqual(self.call("/api/memory/history", {})[0], 403)
            staff, _ = self.login(STAFF_PIN)
            status, out = self.call("/api/memory/history", {"token": staff})
            self.assertEqual(status, 200)
            texts = [m["text"] for m in out["messages"]]
            self.assertIn("سؤال العامل", texts)
            self.assertNotIn("سؤال المالك", texts)
        finally:
            memory.PATH = old
            pathlib.Path(mem).unlink(missing_ok=True)

    def test_history_cannot_be_read_by_naming_another_user(self):
        from rizq_agent import memory
        fd, mem = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        old = memory.PATH
        memory.PATH = pathlib.Path(mem)
        try:
            memory.append_exchange("user_owner", "سري", "سري")
            staff, _ = self.login(STAFF_PIN)
            status, out = self.call("/api/memory/history",
                                    {"token": staff, "user_id": "user_owner"})
            self.assertEqual(status, 200)
            self.assertEqual([m["text"] for m in out["messages"]], [])
        finally:
            memory.PATH = old
            pathlib.Path(mem).unlink(missing_ok=True)

    # ---------------------------------------------------------------- open endpoints
    def test_non_sensitive_endpoints_stay_open(self):
        for path in ("/api/health", "/api/skills", "/api/features",
                     "/api/problems", "/api/zatca/status"):
            self.assertEqual(self.call(path)[0], 200, path)


if __name__ == "__main__":
    unittest.main()
