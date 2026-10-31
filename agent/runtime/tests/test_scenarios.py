"""End-to-end scenarios: a real situation in a restaurant, start to finish.

Each class is one story a person would actually live through. They run against a real
HTTP server, over the wire, with no model installed -- which is also what proves the
offline story.
"""
from __future__ import annotations

import base64
import io
import json
import pathlib
import shutil
import socket
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer
from unittest import mock

from rizq_agent import copilot, memory, permissions, security, web

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEV = str(ROOT / "fixtures" / "rizq_dev.sqlite")

OWNER_PIN, CHEF_PIN, TILL_PIN = "90901234", "70700001", "70700002"
STAFF = [("user_owner", "محمد المالك", "owner", OWNER_PIN),
         ("user_chef", "سالم الشيف", "staff", CHEF_PIN),
         ("user_till", "نورة الكاشير", "staff", TILL_PIN)]


class Scenario(unittest.TestCase):
    """A running Rizq with three employees, served over HTTP."""

    @classmethod
    def setUpClass(cls):
        cls.db = tempfile.mkstemp(suffix=".sqlite")[1]
        shutil.copyfile(DEV, cls.db)
        con = sqlite3.connect(cls.db)
        con.execute("DELETE FROM users")
        con.executemany("INSERT INTO users (id,name,role,pin) VALUES (?,?,?,?)", STAFF)
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
        self.tmp = tempfile.mkdtemp()
        self._saved = (permissions.AUDIT_PATH, permissions.SESSION_DB, memory.PATH,
                       security.DATA, security.TOTP_PATH, security.ALERT_PATH)
        d = pathlib.Path(self.tmp)
        permissions.AUDIT_PATH = d / "audit.jsonl"
        permissions.SESSION_DB = d / "sessions.sqlite"
        memory.PATH = d / "chat.sqlite"
        security.DATA = d
        security.TOTP_PATH = d / "2fa.json"
        security.ALERT_PATH = d / "alerts.jsonl"

    def tearDown(self):
        (permissions.AUDIT_PATH, permissions.SESSION_DB, memory.PATH,
         security.DATA, security.TOTP_PATH, security.ALERT_PATH) = self._saved
        permissions.reset_for_tests()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---------------------------------------------------------------- helpers
    def call(self, method, path, body=None, tok=None, raw=False, host=None):
        url = f"http://127.0.0.1:{self.port}{path}"
        req = urllib.request.Request(
            url, data=json.dumps(body).encode() if body is not None else None, method=method)
        req.add_header("Content-Type", "application/json")
        if tok:
            req.add_header("Authorization", "Bearer " + tok)
        if host:
            req.add_header("Host", host)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, (r.read() if raw else json.loads(r.read()))
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read())
            except Exception:
                return e.code, {}

    def sign_in(self, pin, otp=None):
        body = {"pin": pin, "sqlite_base64": self.snapshot}
        if otp:
            body["otp"] = otp
        return self.call("POST", "/api/auth/login", body)

    def ask(self, question, tok, approve=False):
        return self.call("POST", "/api/chat/local",
                         {"message": question, "token": tok, "sqlite_base64": self.snapshot,
                          "session": "", "approve_repairs": approve}, tok)


class S1_ItWorksWithNoInternet(Scenario):
    """SCENARIO 1 — The internet is down and the owner still needs numbers.

    A restaurant's Wi-Fi drops. No cloud API is reachable; no model is even installed.
    The owner must still be able to ask about today's money, and dangerous requests must
    still be refused. Nothing here may touch the network.
    """

    def test_the_owner_gets_real_numbers_with_no_model_and_no_network(self):
        tok = self.sign_in(OWNER_PIN)[1]["token"]
        # Reaching for ANY model now explodes. If a question still gets answered, it
        # was answered without one -- which is exactly the offline guarantee.
        def no_model(*a, **k):
            raise AssertionError("the offline path must not reach for a model")

        with mock.patch.object(copilot, "pick_brain", no_model):
            for question in ("كم مبيعات آخر ٧ أيام؟", "المخزون الناقص",
                             "وش أكثر صنف مبيعًا؟", "صافي الربح",
                             "الضريبة المحصلة هذا الشهر"):
                st, r = self.ask(question, tok)
                self.assertEqual(st, 200, question)
                self.assertEqual(r["provider"], "rules", "must not need a model")
                self.assertTrue(r["answer"].strip(), question)

    def test_offline_answers_carry_real_figures_not_apologies(self):
        tok = self.sign_in(OWNER_PIN)[1]["token"]
        answer = self.ask("كم مبيعات آخر ٧ أيام؟", tok)[1]["answer"]
        self.assertRegex(answer, r"\d", "an offline answer must still contain numbers")

    def test_danger_is_still_refused_offline(self):
        tok = self.sign_in(OWNER_PIN)[1]["token"]
        for bad in ("احذف كل الطلبات", "اعطني الأرقام السرية للموظفين"):
            st, r = self.ask(bad, tok, approve=True)
            self.assertEqual(st, 200)
            self.assertIn("ما أقدر", r["answer"], bad)

    def test_offline_answers_are_instant(self):
        import time
        tok = self.sign_in(OWNER_PIN)[1]["token"]
        t0 = time.time()
        self.ask("صافي الربح", tok)
        self.assertLess(time.time() - t0, 5.0, "the offline path must not hang on a model")


class S2_EveryEmployeeHasTheirOwnKey(Scenario):
    """SCENARIO 2 — Three people, three keys, three different lockers.

    The chef and the till operator each have their own PIN. They get the same app and
    the same assistant, but not the same access. Nobody can borrow a colleague's view,
    and the owner can tell afterwards who did what.
    """

    def test_each_pin_opens_its_own_identity(self):
        seen = {}
        for pin in (OWNER_PIN, CHEF_PIN, TILL_PIN):
            st, who = self.sign_in(pin)
            self.assertEqual(st, 200)
            seen[who["user_id"]] = who["role"]
        self.assertEqual(seen, {"user_owner": "owner", "user_chef": "staff", "user_till": "staff"})

    def test_two_workers_get_the_same_functionality(self):
        chef = self.sign_in(CHEF_PIN)[1]
        till = self.sign_in(TILL_PIN)[1]
        self.assertEqual(sorted(chef["allowed_tools"]), sorted(till["allowed_tools"]))
        self.assertNotEqual(chef["user_id"], till["user_id"])

    def test_a_worker_cannot_see_the_money(self):
        tok = self.sign_in(CHEF_PIN)[1]["token"]
        for method, path, body in (("GET", "/api/scan", None),
                                   ("GET", "/api/zatca/vat-summary?month=2026-10", None),
                                   ("GET", "/api/export", None),
                                   ("POST", "/api/audit", {})):
            self.assertEqual(self.call(method, path, body, tok)[0], 403, path)

    def test_one_worker_cannot_read_another_s_conversation(self):
        chef = self.sign_in(CHEF_PIN)[1]["token"]
        till = self.sign_in(TILL_PIN)[1]["token"]
        memory.append_exchange("user_chef", "سؤال الشيف", "جواب الشيف")
        texts = self.call("POST", "/api/memory/history", {"token": till}, till)[1]["messages"]
        self.assertEqual(texts, [])
        mine = self.call("POST", "/api/memory/history", {"token": chef}, chef)[1]["messages"]
        self.assertIn("سؤال الشيف", [m["text"] for m in mine])

    def test_the_owner_can_see_who_did_what(self):
        chef = self.sign_in(CHEF_PIN)[1]["token"]
        self.call("GET", "/api/scan", None, chef)           # refused, and recorded
        owner = self.sign_in(OWNER_PIN)[1]["token"]
        entries = self.call("POST", "/api/audit", {"token": owner}, owner)[1]["entries"]
        self.assertTrue(any(e.get("user_id") == "user_chef" for e in entries))

    def test_two_employees_can_never_share_a_pin(self):
        clash = tempfile.mkstemp(suffix=".sqlite")[1]
        shutil.copyfile(self.db, clash)
        con = sqlite3.connect(clash)
        con.execute("UPDATE users SET pin=? WHERE id='user_till'", (CHEF_PIN,))
        con.commit(); con.close()
        try:
            self.assertIsNone(permissions.verify_pin(clash, CHEF_PIN),
                              "an ambiguous PIN must not resolve to anyone")
            self.assertEqual(len(permissions.duplicate_pins(clash)), 1)
        finally:
            pathlib.Path(clash).unlink(missing_ok=True)


class S3_TheAccountantAsksForTheBooks(Scenario):
    """SCENARIO 3 — The accountant wants the data, then sends a form back.

    The owner exports everything as one file. Inside it is a form with blanks. They hand
    that form back to the agent and get it returned filled from the database -- without
    a single number being typed by hand, and without any figure being invented.
    """

    def test_the_owner_exports_everything_as_one_file(self):
        tok = self.sign_in(OWNER_PIN)[1]["token"]
        st, blob = self.call("GET", "/api/export", None, tok, raw=True)
        self.assertEqual(st, 200)
        z = zipfile.ZipFile(io.BytesIO(blob))
        names = set(z.namelist())
        for expected in ("README.txt", "data/orders.csv", "data/rizq.sqlite",
                         "logs/audit.jsonl", "reports/integrity.csv",
                         "templates/monthly-report.txt"):
            self.assertIn(expected, names, expected)

    def test_no_employee_pin_leaves_the_building(self):
        tok = self.sign_in(OWNER_PIN)[1]["token"]
        blob = self.call("GET", "/api/export", None, tok, raw=True)[1]
        z = zipfile.ZipFile(io.BytesIO(blob))
        for name in z.namelist():
            body = z.read(name)
            for pin in (OWNER_PIN, CHEF_PIN, TILL_PIN):
                self.assertNotIn(pin.encode(), body, f"{pin} leaked in {name}")

    def test_the_exported_form_comes_back_filled(self):
        tok = self.sign_in(OWNER_PIN)[1]["token"]
        blob = self.call("GET", "/api/export", None, tok, raw=True)[1]
        form = zipfile.ZipFile(io.BytesIO(blob)).read("templates/monthly-report.txt").decode()
        st, filled = self.call("POST", "/api/fill", {"text": form, "token": tok}, tok)
        self.assertEqual(st, 200)
        self.assertGreaterEqual(filled["filled_count"], 10)
        self.assertRegex(filled["text"], r"\d{1,3}(,\d{3})*\.\d\d")

    def test_a_figure_it_cannot_verify_is_left_blank_with_a_reason(self):
        tok = self.sign_in(OWNER_PIN)[1]["token"]
        st, filled = self.call("POST", "/api/fill",
                               {"text": "zakat={{zakat_due}}", "token": tok}, tok)
        self.assertIn("{{zakat_due}}", filled["text"], "it must not invent a tax figure")
        self.assertIn("needs information", filled["unresolved"][0]["reason"])

    def test_a_worker_cannot_export_or_fill(self):
        tok = self.sign_in(TILL_PIN)[1]["token"]
        self.assertEqual(self.call("GET", "/api/export", None, tok)[0], 403)
        self.assertEqual(self.call("POST", "/api/fill", {"text": "{{sales_total}}", "token": tok}, tok)[0], 403)

    def test_exporting_raises_an_alert_the_owner_can_see(self):
        tok = self.sign_in(OWNER_PIN)[1]["token"]
        self.call("GET", "/api/export", None, tok, raw=True)
        kinds = [a["kind"] for a in self.call("POST", "/api/alerts", {"token": tok}, tok)[1]["alerts"]]
        self.assertIn("data_exported", kinds)


class S4_TheTabletOnTheCounter(Scenario):
    """SCENARIO 4 — The same Rizq, opened from an iPad on the shop Wi-Fi.

    The till runs on a laptop; the manager walks around with a tablet. Both must reach
    the same Rizq, each person signing in with their own PIN, and the network must not
    become a way around the locks.
    """

    def test_the_server_can_be_bound_for_other_devices(self):
        import inspect
        src = inspect.getsource(web.main)
        self.assertIn("0.0.0.0", src, "there must be a way to serve other devices")
        self.assertIn('"127.0.0.1"', src, "and loopback must remain the default")

    def test_a_second_device_signs_in_independently(self):
        laptop = self.sign_in(OWNER_PIN)[1]["token"]
        tablet = self.sign_in(OWNER_PIN)[1]["token"]
        self.assertNotEqual(laptop, tablet)
        for tok in (laptop, tablet):
            self.assertEqual(self.call("POST", "/api/auth/whoami", {"token": tok}, tok)[0], 200)

    def test_signing_out_one_device_leaves_the_other_working(self):
        laptop = self.sign_in(OWNER_PIN)[1]["token"]
        tablet = self.sign_in(OWNER_PIN)[1]["token"]
        self.call("POST", "/api/auth/logout", {"token": laptop})
        self.assertEqual(self.call("POST", "/api/auth/whoami", {"token": laptop}, laptop)[0], 403)
        self.assertEqual(self.call("POST", "/api/auth/whoami", {"token": tablet}, tablet)[0], 200)

    def test_reaching_it_over_the_network_still_requires_a_pin(self):
        self.assertEqual(self.call("GET", "/api/scan", None, None, host="192.168.1.50:8000")[0], 403)

    def test_code_changes_are_refused_from_a_public_hostname(self):
        for public in ("8.8.8.8", "203.0.113.9", "1.1.1.1"):
            self.assertFalse(web._is_local_address(public), public)
        self.assertTrue(web._is_local_address("192.168.1.50"))
        self.assertTrue(web._is_local_address("127.0.0.1"))
        self.assertTrue(web._is_local_address("localhost"))


class S5_TheOwnerLocksTheOfficeDoor(Scenario):
    """SCENARIO 5 — A PIN is typed in front of people; the owner wants more.

    PINs get shoulder-surfed across a counter. The owner turns on a second factor, and
    from then on their PIN alone is not enough -- while the staff keep signing in as
    before, because the till cannot wait for a phone.
    """

    def test_two_factor_is_off_until_the_owner_turns_it_on(self):
        self.assertEqual(self.sign_in(OWNER_PIN)[0], 200)
        self.assertFalse(self.sign_in(OWNER_PIN)[1]["two_factor"])

    def test_once_enrolled_the_pin_alone_stops_working(self):
        tok = self.sign_in(OWNER_PIN)[1]["token"]
        secret = self.call("POST", "/api/auth/2fa/enroll", {"token": tok}, tok)[1]["secret"]
        self.assertEqual(self.sign_in(OWNER_PIN)[0], 403)
        self.assertEqual(self.sign_in(OWNER_PIN, otp="000000")[0], 403)
        st, who = self.sign_in(OWNER_PIN, otp=security.totp_code(secret))
        self.assertEqual(st, 200)
        self.assertTrue(who["two_factor"])

    def test_staff_are_not_forced_to_carry_a_phone(self):
        tok = self.sign_in(OWNER_PIN)[1]["token"]
        self.call("POST", "/api/auth/2fa/enroll", {"token": tok}, tok)
        self.assertEqual(self.sign_in(CHEF_PIN)[0], 200)
        self.assertEqual(self.sign_in(TILL_PIN)[0], 200)

    def test_guessing_the_pin_gets_locked_out(self):
        codes = [self.sign_in("00000000")[0] for _ in range(14)]
        self.assertIn(429, codes, "repeated guessing must be rate limited")

    def test_a_stolen_code_cannot_be_replayed(self):
        tok = self.sign_in(OWNER_PIN)[1]["token"]
        secret = self.call("POST", "/api/auth/2fa/enroll", {"token": tok}, tok)[1]["secret"]
        code = security.totp_code(secret)
        self.assertEqual(self.sign_in(OWNER_PIN, otp=code)[0], 200)
        self.assertEqual(self.sign_in(OWNER_PIN, otp=code)[0], 403, "replay must fail")


if __name__ == "__main__":
    unittest.main()
