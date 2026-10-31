"""Running the test suite must not touch anything a real user depends on.

This exists because it already happened: reset_for_tests() deleted from the real
sessions store, so running the tests signed out every signed-in user and the assistant
started demanding a PIN from someone the cashier was still showing as signed in.
"""
from __future__ import annotations

import pathlib
import sqlite3
import tempfile
import unittest

from rizq_agent import permissions


class TestsDoNotTouchProduction(unittest.TestCase):
    def test_reset_refuses_to_clear_the_live_session_store(self):
        live = permissions.DEFAULT_SESSION_DB
        live.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(str(live))
        db.execute("CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, "
                   "user_id TEXT NOT NULL, name TEXT NOT NULL, role TEXT NOT NULL, "
                   "created REAL NOT NULL, last_seen REAL NOT NULL)")
        db.execute("INSERT OR REPLACE INTO sessions VALUES ('canary','u','n','owner',0,0)")
        db.commit(); db.close()
        try:
            permissions.reset_for_tests()          # the exact call every test setUp makes
            db = sqlite3.connect(str(live))
            left = db.execute("SELECT COUNT(*) FROM sessions WHERE token_hash='canary'").fetchone()[0]
            db.close()
            self.assertEqual(left, 1, "a test run signed out a real user")
        finally:
            db = sqlite3.connect(str(live))
            db.execute("DELETE FROM sessions WHERE token_hash='canary'")
            db.commit(); db.close()

    def test_reset_still_clears_a_redirected_store(self):
        path = pathlib.Path(tempfile.mkstemp(suffix=".sqlite")[1])
        old = permissions.SESSION_DB
        permissions.SESSION_DB = path
        try:
            permissions._store_session("t", {"user_id": "u", "name": "n", "role": "owner"}, 0.0)
            permissions.reset_for_tests()
            with permissions._session_store() as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 0)
        finally:
            permissions.SESSION_DB = old
            path.unlink(missing_ok=True)

    def test_the_default_points_at_the_project_data_directory(self):
        self.assertTrue(str(permissions.DEFAULT_SESSION_DB).endswith("data/sessions.sqlite"))


if __name__ == "__main__":
    unittest.main()
