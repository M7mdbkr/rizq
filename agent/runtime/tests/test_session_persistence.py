"""Reloading the cashier page must not lose the session or the conversation.

The failure this guards against: the token lived in a JS closure and the sessions lived
only in server memory, so a page reload dropped the user back to the PIN keypad and an
empty chat, and a server restart signed everyone out.
"""
from __future__ import annotations

import base64
import json
import pathlib
import shutil
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from rizq_agent import memory, permissions, web

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEV = str(ROOT / "fixtures" / "rizq_dev.sqlite")
OWNER_PIN = "90901234"


class SessionSurvives(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = tempfile.mkstemp(suffix=".sqlite")[1]
        shutil.copyfile(DEV, cls.db)
        con = sqlite3.connect(cls.db)
        con.execute("DELETE FROM users")
        con.execute("INSERT INTO users (id,name,role,pin) VALUES (?,?,?,?)",
                    ("user_owner", "صاحب المطعم", "owner", OWNER_PIN))
        con.commit(); con.close()
        cls.snapshot = base64.b64encode(pathlib.Path(cls.db).read_bytes()).decode()
        cls._old_db = web.ACTIVE_DB
        web.ACTIVE_DB = pathlib.Path(cls.db)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close()
        web.ACTIVE_DB = cls._old_db
        pathlib.Path(cls.db).unlink(missing_ok=True)

    def setUp(self):
        permissions.reset_for_tests()
        self.sess_db = tempfile.mkstemp(suffix=".sqlite")[1]
        self.mem_db = tempfile.mkstemp(suffix=".sqlite")[1]
        self.audit = tempfile.mkstemp(suffix=".jsonl")[1]
        self._old = (permissions.SESSION_DB, memory.PATH, permissions.AUDIT_PATH)
        permissions.SESSION_DB = pathlib.Path(self.sess_db)
        memory.PATH = pathlib.Path(self.mem_db)
        permissions.AUDIT_PATH = pathlib.Path(self.audit)

    def tearDown(self):
        permissions.SESSION_DB, memory.PATH, permissions.AUDIT_PATH = self._old
        permissions.reset_for_tests()
        for p in (self.sess_db, self.mem_db, self.audit):
            pathlib.Path(p).unlink(missing_ok=True)

    def call(self, path, body=None, token=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",
                                     data=json.dumps(body).encode() if body is not None else None,
                                     method="POST" if body is not None else "GET")
        req.add_header("Content-Type", "application/json")
        if token: req.add_header("Authorization", "Bearer " + token)
        try:
            with urllib.request.urlopen(req, timeout=30) as r: return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e: return e.code, json.loads(e.read())

    def login(self):
        st, out = self.call("/api/auth/login", {"pin": OWNER_PIN, "sqlite_base64": self.snapshot})
        self.assertEqual(st, 200, out)
        return out["token"]

    # --------------------------------------------------------------- reload
    def test_a_reload_keeps_the_session(self):
        """The browser kept only the token; whoami must still identify the user."""
        token = self.login()
        st, who = self.call("/api/auth/whoami", {"token": token})
        self.assertEqual(st, 200)
        self.assertEqual(who["role"], "owner")
        self.assertEqual(who["user_id"], "user_owner")

    def test_a_reload_keeps_the_conversation(self):
        token = self.login()
        memory.append_exchange("user_owner", "كم مبيعاتي؟", "مبيعاتك ١٢٣ ريال")
        st, out = self.call("/api/memory/history", {"token": token})
        self.assertEqual(st, 200)
        self.assertEqual([m["text"] for m in out["messages"]],
                         ["كم مبيعاتي؟", "مبيعاتك ١٢٣ ريال"])

    def test_the_session_survives_a_server_restart(self):
        """Sessions are on disk, so losing the in-memory table must not sign anyone out."""
        token = self.login()
        permissions._TOKENS.clear()          # exactly what a restart does
        st, who = self.call("/api/auth/whoami", {"token": token})
        self.assertEqual(st, 200, who)
        self.assertEqual(who["role"], "owner")

    def test_signing_out_really_ends_the_session(self):
        token = self.login()
        self.assertEqual(self.call("/api/auth/logout", {"token": token})[0], 200)
        permissions._TOKENS.clear()          # make sure it is gone from disk too
        self.assertEqual(self.call("/api/auth/whoami", {"token": token})[0], 403)

    def test_an_expired_session_is_refused_even_from_disk(self):
        token = self.login()
        with permissions._session_store() as db:
            db.execute("UPDATE sessions SET created=created-?, last_seen=last_seen-?",
                       (permissions.TOKEN_TTL_SECONDS + 60, permissions.TOKEN_TTL_SECONDS + 60))
        permissions._TOKENS.clear()
        self.assertEqual(self.call("/api/auth/whoami", {"token": token})[0], 403)

    def test_the_pin_is_never_written_to_the_session_store(self):
        self.login()
        blob = pathlib.Path(self.sess_db).read_bytes()
        self.assertNotIn(OWNER_PIN.encode(), blob)

    def test_the_raw_token_is_never_written_to_disk(self):
        token = self.login()
        self.assertNotIn(token.encode(), pathlib.Path(self.sess_db).read_bytes())

    def test_a_second_sign_in_does_not_invalidate_the_first(self):
        """Two devices (till and phone) can hold their own sessions."""
        a, b = self.login(), self.login()
        self.assertNotEqual(a, b)
        for t in (a, b):
            self.assertEqual(self.call("/api/auth/whoami", {"token": t})[0], 200)


if __name__ == "__main__":
    unittest.main()
