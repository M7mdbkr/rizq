"""Offline mode (no model without internet), chat sessions, and the payment device flow."""
import base64
import os
import pathlib
import tempfile
import unittest
from unittest import mock

from rizq_agent import copilot, memory, payments, web


def chat(message, conversation=None, online="1"):
    handler = web.Handler.__new__(web.Handler)
    handler.path = "/api/chat/local"
    body = {"message": message, "sqlite_base64": base64.b64encode(web.DEFAULT_DB.read_bytes()).decode()}
    if conversation:
        body["conversation"] = conversation
    handler.read_json = lambda: body
    handler.guard = lambda _: None
    handler.actor = lambda *a, **k: {"user_id": "owner-test", "role": "owner", "name": "Owner"}
    handler.send_json = mock.Mock()
    with mock.patch.dict(os.environ, {"RIZQ_ONLINE": online, "RIZQ_FORCE_MODEL": "0"}):
        handler.do_POST()
    return handler.send_json.call_args.args[0]


class OfflineTests(unittest.TestCase):
    def setUp(self):
        self.previous = dict(web.AI_SETTINGS)
        web.configure_ai("auto", "", "")
        self.mem = mock.patch.object(memory, "PATH", pathlib.Path(tempfile.mkdtemp()) / "m.sqlite")
        self.mem.start()

    def tearDown(self):
        self.mem.stop()
        web.AI_SETTINGS.clear()
        web.AI_SETTINGS.update(self.previous)

    def test_without_internet_no_model_is_called(self):
        with mock.patch.object(copilot, "ask", wraps=copilot.ask) as ask, \
             mock.patch.object(copilot.OpenAICompatBrain, "step", side_effect=AssertionError("model called offline")):
            result = chat("ساعدني أفهم لوحة التحكم", online="0")
        self.assertTrue(result["offline"])
        self.assertIn("ما فيه إنترنت", result["answer"])
        ask.assert_not_called()

    def test_without_internet_verified_answers_still_work(self):
        result = chat("كم مبيعات اليوم", online="0")
        self.assertEqual(result["provider"], "rules")
        self.assertRegex(result["answer"], r"\d")

    def test_english_offline_message(self):
        self.assertIn("no internet", chat("Help me understand the dashboard", online="0")["answer"])


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.mem = mock.patch.object(memory, "PATH", pathlib.Path(tempfile.mkdtemp()) / "m.sqlite")
        self.mem.start()

    def tearDown(self):
        self.mem.stop()

    def test_conversations_are_separate_and_resumable(self):
        memory.append_exchange("u1", "كم المبيعات؟", "6,915.69", "c-one")
        memory.append_exchange("u1", "المخزون الناقص", "4 أصناف", "c-two")
        memory.append_exchange("u1", "وأمس؟", "5,000", "c-one")          # back to the first one
        convs = memory.conversations("u1")
        self.assertEqual([c["id"] for c in convs], ["c-one", "c-two"])
        self.assertEqual(convs[0]["title"], "كم المبيعات؟")
        self.assertEqual(convs[0]["messages"], 4)
        self.assertRegex(convs[0]["started"], r"^\d\d:\d\d$")
        self.assertEqual([m["text"] for m in memory.history("u1", "c-one")], ["كم المبيعات؟", "6,915.69", "وأمس؟", "5,000"])
        self.assertEqual(len(memory.recent("u1", 12, "c-two")), 2)        # model context stays in its session

    def test_users_never_see_each_others_conversations(self):
        memory.append_exchange("u1", "سر", "x", "c1")
        self.assertEqual(memory.conversations("u2"), [])
        self.assertEqual(memory.history("u2", "c1"), [])

    def test_old_messages_without_a_session_are_grouped_by_day(self):
        with memory.connect() as db:
            db.execute("INSERT INTO messages(user_id,role,text) VALUES ('u1','user','قديم')")
        self.assertTrue(memory.conversations("u1")[0]["id"].startswith("day-"))

    def test_chat_saves_into_the_given_conversation(self):
        chat("كم مبيعات اليوم", conversation="c-abc")
        self.assertEqual(memory.conversations("owner-test")[0]["id"], "c-abc")
        self.assertIn("error", chat("كم مبيعات اليوم", conversation="../etc"))   # odd ids are refused


class PaymentTests(unittest.TestCase):
    def setUp(self):
        payments.RECEIPTS.clear()
        self.sleep = mock.Mock()

    def test_confirmed_payment_creates_one_receipt(self):
        result = payments.take_payment(payments.SimulatorAdapter(), "o1", 115.0, sleep=self.sleep)
        self.assertEqual(result["status"], "paid")
        self.assertEqual(result["receipt"]["amount"], 115.0)

    def test_delayed_then_confirmed(self):
        result = payments.take_payment(payments.SimulatorAdapter(confirm_after=2), "o2", 50.0, sleep=self.sleep)
        self.assertEqual(result["status"], "paid")
        self.assertEqual(len(result["evidence"]), 3)
        self.assertEqual([c.args[0] for c in self.sleep.call_args_list], [1.0, 2.0])   # backoff

    def test_never_confirmed_means_no_receipt_and_an_alert(self):
        result = payments.take_payment(payments.SimulatorAdapter(outcome="never"), "o3", 50.0, checks=3, sleep=self.sleep)
        self.assertEqual(result["status"], "unconfirmed")
        self.assertNotIn("receipt", result)
        self.assertIn("ما صدرت فاتورة", result["alert"])
        self.assertEqual(payments.RECEIPTS, {})

    def test_duplicate_event_never_creates_a_second_receipt(self):
        adapter = payments.SimulatorAdapter()
        first = payments.take_payment(adapter, "o4", 20.0, sleep=self.sleep)
        second = payments.take_payment(adapter, "o4", 20.0, sleep=self.sleep)
        self.assertEqual(second["status"], "duplicate")
        self.assertEqual(second["receipt"], first["receipt"])
        self.assertEqual(len(payments.RECEIPTS), 1)

    def test_wrong_amount_is_escalated_not_fixed(self):
        result = payments.take_payment(payments.SimulatorAdapter(amount_offset=-5), "o5", 100.0, sleep=self.sleep)
        self.assertEqual(result["status"], "escalated")
        self.assertEqual(payments.RECEIPTS, {})

    def test_declined_payment(self):
        result = payments.take_payment(payments.SimulatorAdapter(outcome="declined"), "o6", 10.0, sleep=self.sleep)
        self.assertEqual(result["status"], "declined")

    def test_device_settings_are_validated_and_tested_before_saving(self):
        with mock.patch.object(payments, "CONFIG", pathlib.Path(tempfile.mkdtemp()) / "d.json"):
            with self.assertRaises(ValueError):
                payments.save_and_test({"type": "network", "host": "8.8.8.8", "port": 80})     # not the shop network
            with self.assertRaises(ValueError):
                payments.save_and_test({"type": "network", "host": "not-an-ip", "port": 80})
            with self.assertRaises(ValueError):
                payments.save_and_test({"type": "network", "host": "127.0.0.1", "port": 1})      # nothing listening
            self.assertEqual(payments.load(), {"type": "none"})
            self.assertTrue(payments.save_and_test({"type": "simulator"})["connected"])
            self.assertEqual(payments.load()["type"], "simulator")


if __name__ == "__main__":
    unittest.main()
