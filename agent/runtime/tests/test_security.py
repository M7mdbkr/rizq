"""Rate limiting, owner 2FA, the paid-call cap, and alerts."""
from __future__ import annotations

import pathlib
import tempfile
import time
import unittest

from rizq_agent import security


class RateLimiting(unittest.TestCase):
    def setUp(self):
        security.reset_for_tests()

    def test_it_allows_the_budget_then_refuses(self):
        for _ in range(10):
            security.limit("auth", "1.2.3.4")
        with self.assertRaises(security.RateLimited) as cm:
            security.limit("auth", "1.2.3.4")
        self.assertGreater(cm.exception.retry_after, 0)

    def test_clients_are_limited_independently(self):
        for _ in range(10):
            security.limit("auth", "1.1.1.1")
        security.limit("auth", "2.2.2.2")          # must not raise

    def test_buckets_are_limited_independently(self):
        for _ in range(10):
            security.limit("auth", "1.1.1.1")
        security.limit("chat", "1.1.1.1")          # must not raise

    def test_the_window_slides(self):
        lim = security.RateLimiter()
        lim.check("b", "c", 1, 0.2)
        with self.assertRaises(security.RateLimited):
            lim.check("b", "c", 1, 0.2)
        time.sleep(0.25)
        lim.check("b", "c", 1, 0.2)                # must not raise

    def test_every_expensive_route_has_a_bucket(self):
        for bucket in ("auth", "chat", "export", "fill", "scan", "code"):
            self.assertIn(bucket, security.LIMITS, bucket)


class OwnerTwoFactor(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self._old = (security.DATA, security.TOTP_PATH, security.ALERT_PATH, security.SPEND_PATH)
        security.DATA = pathlib.Path(self.dir)
        security.TOTP_PATH = security.DATA / "owner_2fa.json"
        security.ALERT_PATH = security.DATA / "alerts.jsonl"
        security.SPEND_PATH = security.DATA / "spend.json"

    def tearDown(self):
        security.DATA, security.TOTP_PATH, security.ALERT_PATH, security.SPEND_PATH = self._old
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_not_enrolled_by_default(self):
        self.assertFalse(security.is_2fa_enrolled("user_owner"))

    def test_enrolment_returns_a_scannable_secret_once(self):
        out = security.enroll_2fa("user_owner")
        self.assertTrue(security.is_2fa_enrolled("user_owner"))
        self.assertTrue(out["otpauth_uri"].startswith("otpauth://totp/Rizq:user_owner"))
        self.assertIn(out["secret"], out["otpauth_uri"])

    def test_the_current_code_is_accepted(self):
        secret = security.enroll_2fa("user_owner")["secret"]
        self.assertTrue(security.verify_2fa("user_owner", security.totp_code(secret)))

    def test_a_wrong_code_is_refused(self):
        security.enroll_2fa("user_owner")
        for bad in ("000000", "", "abcdef", None, "12345"):
            self.assertFalse(security.verify_2fa("user_owner", bad), bad)

    def test_a_code_cannot_be_replayed(self):
        secret = security.enroll_2fa("user_owner")["secret"]
        code = security.totp_code(secret)
        self.assertTrue(security.verify_2fa("user_owner", code))
        self.assertFalse(security.verify_2fa("user_owner", code), "replay must fail")

    def test_clock_drift_of_one_step_is_tolerated(self):
        secret = security.enroll_2fa("user_owner")["secret"]
        now = time.time()
        self.assertTrue(security.verify_2fa("user_owner", security.totp_code(secret, now - 30), now))

    def test_a_code_from_far_away_is_refused(self):
        secret = security.enroll_2fa("user_owner")["secret"]
        now = time.time()
        self.assertFalse(security.verify_2fa("user_owner", security.totp_code(secret, now - 600), now))

    def test_a_user_who_never_enrolled_cannot_pass(self):
        self.assertFalse(security.verify_2fa("nobody", "123456"))

    def test_disabling_removes_the_secret(self):
        security.enroll_2fa("user_owner")
        self.assertTrue(security.disable_2fa("user_owner"))
        self.assertFalse(security.is_2fa_enrolled("user_owner"))

    def test_the_secret_file_is_owner_readable_only(self):
        security.enroll_2fa("user_owner")
        mode = oct(security.TOTP_PATH.stat().st_mode)[-3:]
        self.assertEqual(mode, "600")

    def test_codes_match_a_known_rfc6238_vector(self):
        """RFC 6238 SHA-1 test vector: secret '12345678901234567890' at t=59."""
        import base64
        secret = base64.b32encode(b"12345678901234567890").decode()
        self.assertEqual(security.totp_code(secret, 59), "287082")


class SpendCap(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self._old = (security.DATA, security.SPEND_PATH, security.ALERT_PATH)
        security.DATA = pathlib.Path(self.dir)
        security.SPEND_PATH = security.DATA / "spend.json"
        security.ALERT_PATH = security.DATA / "alerts.jsonl"

    def tearDown(self):
        security.DATA, security.SPEND_PATH, security.ALERT_PATH = self._old
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_local_models_are_never_counted(self):
        for _ in range(50):
            security.note_paid_call("ollama", max_per_day=3)
        security.note_paid_call("rules", max_per_day=3)

    def test_a_paid_provider_stops_at_the_cap(self):
        for _ in range(3):
            security.note_paid_call("claude", max_per_day=3)
        with self.assertRaises(security.SpendCapReached):
            security.note_paid_call("claude", max_per_day=3)

    def test_hitting_the_cap_raises_an_alert(self):
        for _ in range(2):
            security.note_paid_call("claude", max_per_day=2)
        with self.assertRaises(security.SpendCapReached):
            security.note_paid_call("claude", max_per_day=2)
        self.assertTrue(any(a["kind"] == "spend_cap_reached" for a in security.recent_alerts()))


class Alerts(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self._old = (security.DATA, security.ALERT_PATH)
        security.DATA = pathlib.Path(self.dir)
        security.ALERT_PATH = security.DATA / "alerts.jsonl"

    def tearDown(self):
        security.DATA, security.ALERT_PATH = self._old
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_alerts_are_recorded_and_read_back(self):
        security.alert("privileged_denied", {"user_id": "u1", "tool": "apply_fixes"})
        got = security.recent_alerts()
        self.assertEqual(got[-1]["kind"], "privileged_denied")
        self.assertEqual(got[-1]["detail"]["tool"], "apply_fixes")

    def test_the_alert_file_is_owner_readable_only(self):
        security.alert("data_exported", {})
        self.assertEqual(oct(security.ALERT_PATH.stat().st_mode)[-3:], "600")

    def test_alerting_never_raises(self):
        security.ALERT_PATH = pathlib.Path("/nonexistent-dir-xyz/alerts.jsonl")
        security.DATA = pathlib.Path("/nonexistent-dir-xyz")
        security.alert("whatever", {"a": 1})      # must not raise


if __name__ == "__main__":
    unittest.main()
