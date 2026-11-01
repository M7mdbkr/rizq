"""The reply guard: no hidden reasoning, no foreign script, the asker's language, real numbers.

The regression list holds the kinds of replies the owner actually saw from the local model.
The end-to-end tests run copilot.ask against a fake model server, so the guard is proven on
the same path the cashier, the CLI and Telegram use.
"""
import json
import os
import pathlib
import re
import sqlite3
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from rizq_agent import copilot, reply

DEV = str(pathlib.Path(__file__).resolve().parents[1] / "fixtures/rizq_dev.sqlite")
CJK = re.compile(r"[　-鿿가-힯]")
SALES = [("run_skill", {}, {"status": "ok", "skill": "sales_period", "title_ar": "مبيعات فترة",
                             "value": {"days": 1, "orders": 48, "sales": 6915.69, "vat": 902.04}})]


class CleanTests(unittest.TestCase):
    # (bad model text, question, tool outputs, expected substring or None for "must fall back")
    REGRESSIONS = [
        ("您的销售额是 6915.69 里亚尔", "كم مبيعات اليوم؟", SALES, None),
        ("<think>The user wants sales. I will answer.</think>أبشر، مبيعاتك اليوم 6,915.69 ريال.", "كم مبيعات اليوم؟", SALES, "أبشر، مبيعاتك اليوم 6,915.69 ريال."),
        ("<think>I should call run_skill first and then", "كم مبيعات اليوم؟", SALES, None),
        ("okay, so the total is the sum...</think>مبيعاتك اليوم 6,915.69 ريال", "كم مبيعات اليوم؟", SALES, "مبيعاتك اليوم 6,915.69 ريال"),
        ("مبيعاتك اليوم 6,915.69 ريال", "How much did we sell today?", SALES, None),
        ("مبيعاتك اليوم 9,999.00 ريال", "كم مبيعات اليوم؟", SALES, None),
        ('{"name": "run_skill", "arguments": {"name": "sales_period"}}', "كم مبيعات اليوم؟", SALES, None),
        ("<tool_call>run_skill</tool_call>", "كم مبيعات اليوم؟", SALES, None),
        ("Ваши продажи 6915.69", "How much did we sell today?", SALES, None),
        ("**مبيعاتك** 🎉 اليوم 6,915.69 ريال", "كم مبيعات اليوم؟", SALES, "مبيعاتك اليوم 6,915.69 ريال"),
        ("مبيعاتك اليوم ٦٬٩١٥٫٦٩ ريال", "كم مبيعات اليوم؟", SALES, "٦٬٩١٥٫٦٩"),
        ("Sales today: 6,915.69 SAR.\nWould you like a weekly comparison?", "How much did we sell today?", SALES, "Sales today: 6,915.69 SAR."),
        ("مبيعاتك اليوم 6,915.69 ريال\n\nهل ترغب في تقرير مفصل؟", "كم مبيعات اليوم؟", SALES, "مبيعاتك اليوم 6,915.69 ريال"),
        ("", "كم مبيعات اليوم؟", SALES, None),
    ]

    def test_regression_list_of_bad_replies(self):
        for raw, question, outputs, expected in self.REGRESSIONS:
            with self.subTest(raw=raw[:40]):
                text = reply.clean(raw)
                issues = reply.problems(text, question, outputs)
                if expected is None:
                    self.assertTrue(issues, f"should have been discarded: {raw!r}")
                else:
                    self.assertFalse(issues, issues)
                    self.assertIn(expected, text)
                    self.assertNotIn("Would you like", text)
                    self.assertNotIn("هل ترغب", text)
                final = reply.finish(raw, question, type("T", (), {"outputs": outputs})())
                self.assertFalse(CJK.search(final))
                self.assertNotIn("<think>", final)
                self.assertNotIn("</think>", final)

    def test_repeated_lines_end_the_answer(self):
        self.assertEqual(reply.clean("مبيعاتك ممتازة\nمبيعاتك ممتازة\nمبيعاتك ممتازة\nمبيعاتك ممتازة"), "مبيعاتك ممتازة")

    def test_long_answers_are_cut_at_a_sentence(self):
        text = reply.clean("جملة قصيرة. " * 300)
        self.assertLessEqual(len(text), reply.MAX_CHARS)
        self.assertTrue(text.endswith("."))

    def test_language_follows_the_question(self):
        self.assertEqual(reply.language("كم المبيعات؟"), "ar")
        self.assertEqual(reply.language("How are sales?"), "en")
        self.assertEqual(reply.language("ord_zjkx2gs?"), "any")
        english = reply.fallback("How are sales?", [])
        arabic = reply.fallback("كم المبيعات؟", [])
        self.assertFalse(re.search(r"[؀-ۿ]", english))
        self.assertTrue(re.search(r"[؀-ۿ]", arabic))

    def test_fallback_uses_the_verified_skill_figure(self):
        self.assertIn("6,915.69", reply.fallback("كم مبيعات اليوم؟", SALES).replace("٬", ","))
        self.assertEqual(reply.fallback("How much did we sell today?", SALES),
                         "Sales for the last 1 day: 6,915.69 SAR from 48 orders, including 902.04 SAR VAT.")

    def test_small_counts_and_fixed_rates_are_not_data_claims(self):
        self.assertEqual(reply.ungrounded_numbers("آخر 7 أيام، ضريبة 15%، 3 أصناف", []), [])
        self.assertEqual(reply.ungrounded_numbers("المبلغ 1,234.50", []), ["1,234.50"])


def fake_model(final_texts):
    """An OpenAI-compatible server: first asks for the sales skill, then says `final_text`."""
    replies = []
    for text in final_texts:
        replies.append({"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "run_skill",
                                                          "arguments": json.dumps({"name": "sales_period", "params": {"days": 1}})}}]}}]})
        replies.append({"choices": [{"message": {"role": "assistant", "content": text}}]})

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            raw = json.dumps(replies.pop(0)).encode()
            self.send_response(200); self.send_header("Content-Length", str(len(raw))); self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *a):
            pass
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class PipelineTests(unittest.TestCase):
    def ask(self, final_text, question):
        srv = fake_model([final_text])
        out = tempfile.mkdtemp()
        tools = copilot.PosTools(DEV, out, lambda q: False)
        try:
            brain = copilot.OpenAICompatBrain(f"http://127.0.0.1:{srv.server_port}/v1", "qwen3:8b")
            answer = copilot.ask(brain, tools, question, on_event=lambda e: None)
        finally:
            srv.shutdown(); srv.server_close()
        return answer, tools, out

    def reference_sales(self):
        db = sqlite3.connect(DEV)
        latest = db.execute("SELECT date(MAX(created_at)) FROM orders").fetchone()[0]
        total = db.execute("SELECT ROUND(SUM(final_total),2) FROM orders WHERE status='completed' AND date(created_at) > date(?, '-1 day')",
                           (latest,)).fetchone()[0]
        db.close()
        return total

    def test_chinese_reply_is_replaced_by_the_verified_figure_and_logged(self):
        answer, tools, out = self.ask("您今天的销售额很好", "كم مبيعات اليوم؟")
        self.assertFalse(CJK.search(answer))
        expected = f"{self.reference_sales():,.2f}"
        self.assertIn(expected, answer.replace("٬", ","))          # the number equals the reference SQL
        log = pathlib.Path(out, "reply_guard.jsonl").read_text(encoding="utf-8")
        self.assertIn("foreign_script", log)

    def test_invented_number_never_reaches_the_owner(self):
        answer, _, _ = self.ask("أبشر، مبيعاتك اليوم 12,345.67 ريال", "كم مبيعات اليوم؟")
        self.assertNotIn("12,345.67", answer)
        self.assertIn(f"{self.reference_sales():,.2f}", answer.replace("٬", ","))

    def test_good_reply_passes_untouched(self):
        good = f"أبشر، مبيعاتك اليوم {self.reference_sales():,.2f} ريال."
        answer, _, _ = self.ask(good, "كم مبيعات اليوم؟")
        self.assertEqual(answer, good)

    def test_english_question_gets_english(self):
        answer, _, _ = self.ask(f"أبشر، مبيعاتك اليوم {self.reference_sales():,.2f} ريال.", "How much did we sell today?")
        self.assertFalse(re.search(r"[؀-ۿ]{3,}", answer.split(":")[0]))
        self.assertIn(f"{self.reference_sales():,.2f}", answer)

    def test_payment_methods_come_with_their_names(self):
        tools = copilot.PosTools(DEV, tempfile.mkdtemp(), lambda q: False)
        names = {r["payment_method"]: r["name"] for r in tools.t_sales_summary(7)["by_payment_method"]}
        self.assertEqual(names["merzool"], "مرسول")
        self.assertEqual(names["akita"], "كيتا")


if __name__ == "__main__":
    unittest.main()


class TimeBudgetTests(unittest.TestCase):
    def test_a_slow_model_is_stopped_with_a_plain_message(self):
        class Slow:
            def user(self, text): pass
            def step(self): raise AssertionError("should have stopped before another step")
        tools = copilot.PosTools(DEV, tempfile.mkdtemp(), lambda q: False)
        os.environ["RIZQ_ANSWER_SECONDS"] = "-1"
        try:
            answer = copilot.ask(Slow(), tools, "سويلي خصم", on_event=lambda e: None)
        finally:
            os.environ.pop("RIZQ_ANSWER_SECONDS")
        self.assertIn("وقت طويل", answer)

    def test_qwen3_runs_without_long_thinking_in_chat(self):
        self.assertTrue(copilot.OpenAICompatBrain("http://x/v1", "qwen3:8b").msgs[0]["content"].endswith("/no_think"))
        self.assertNotIn("/no_think", copilot.OpenAICompatBrain("http://x/v1", "llama3.1:8b").msgs[0]["content"])


class SmallTalkTests(unittest.TestCase):
    def test_greetings_and_thanks_get_a_warm_reply_without_a_model(self):
        for text in ("هلا كيف حالك", "السلام عليكم", "هلا", "كيفك؟", "شكرا"):
            self.assertTrue(reply.smalltalk(text), text)
            self.assertTrue(re.search(r"[\u0600-\u06FF]", reply.smalltalk(text)))
        self.assertIn("Hi", reply.smalltalk("hello, how are you?"))
        self.assertIsNone(reply.smalltalk("هلا كم مبيعات اليوم"))       # a real question is not small talk


class AnswerOnlyTests(unittest.TestCase):
    def test_answers_carry_no_confidence_or_footnotes(self):
        tools = copilot.PosTools(DEV, tempfile.mkdtemp(), lambda q: False)
        for q in ("كم مبيعات اليوم", "مين اكثر مشتري عندي من العملاء", "المخزون الناقص", "كم قيمة المخزون"):
            answer = copilot.ask(copilot.RulesBrain(), tools, q, on_event=lambda e: None)
            self.assertNotIn("ثقتي", answer, q)
            self.assertNotIn("%", answer.split("\n")[-1], q)
            self.assertFalse(any(line.strip().startswith("—") for line in answer.splitlines()), answer)


class RealClockTests(unittest.TestCase):
    """The live app's "today" is the calendar day, like the dashboard, never the last day with sales."""
    def test_a_day_without_sales_is_not_answered_with_yesterday(self):
        from rizq_agent import phrase, skills
        import sqlite3
        db = sqlite3.connect(DEV); db.row_factory = sqlite3.Row
        try:
            latest = skills.run(db, "sales_period", {"days": 1})
            skills.CLOCK = "real"
            real = skills.run(db, "sales_period", {"days": 1})
        finally:
            skills.CLOCK = "latest"; db.close()
        self.assertGreater(latest["value"]["orders"], 0)          # the fixture's last day had sales
        self.assertEqual(real["value"]["orders"], 0)               # but nothing was sold today
        self.assertEqual(phrase.skill_answer(real), "ما فيه مبيعات اليوم لحد الحين.")

    def test_sales_summary_tool_uses_the_same_today(self):
        from rizq_agent import skills
        tools = copilot.PosTools(DEV, tempfile.mkdtemp(), lambda q: False)
        skills.CLOCK = "real"
        try:
            self.assertEqual(tools.t_sales_summary(1)["per_day"], [])
        finally:
            skills.CLOCK = "latest"


class AppOnlyTests(unittest.TestCase):
    def test_off_topic_requests_are_refused_before_any_model(self):
        from rizq_agent import triage
        for q in ("مين رئيس امريكا", "اكتب لي كود بايثون", "ترجم لي هذي الجملة", "وش الطقس اليوم", "what's the capital of France"):
            v = triage.classify(q)
            self.assertEqual((v["action"], v.get("hard")), ("refuse", True), q)
        self.assertFalse(triage.classify("أنشئ كود خصم VIP بنسبة 20%").get("hard"))   # "كود خصم" is the app, never blocked

    def test_a_model_answer_that_used_no_app_data_is_replaced(self):
        tools = copilot.PosTools(DEV, tempfile.mkdtemp(), lambda q: False)
        answer = reply.finish("باريس هي عاصمة فرنسا وعدد سكانها 2,100,000.", "سؤال عام", tools)
        self.assertIn("أنا مساعد رزق", answer)

    def test_how_to_questions_about_the_app_are_answered_from_the_app(self):
        self.assertIn("أضف موظف", reply.app_help("كيف اضيف موظف جديد؟"))
        self.assertIn("استيراد", reply.app_help("كيف اضيف صنف جديد"))
        self.assertIsNone(reply.app_help("اضيف موظف اسمه احمد"))       # an instruction, not a how-to
