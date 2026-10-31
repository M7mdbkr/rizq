"""Scoring an answer, and refusing to present a weak one as fact."""
from __future__ import annotations

import unittest

from rizq_agent import confidence, rizq, skills

DEV = "fixtures/rizq_dev.sqlite"


class ScoringSkills(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = rizq.open_copy(DEV)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def score(self, name, params=None):
        r = skills.run(self.db, name, params or {})
        return r, confidence.score_skill(r, owner_params=params or {})

    def test_a_verified_answer_with_the_owners_own_period_is_approved(self):
        _, v = self.score("sales_period", {"days": 7})
        self.assertEqual(v.score, 100.0)
        self.assertTrue(v.approved)
        self.assertEqual(v.note_ar(), "", "a solid answer must not nag")

    def test_an_empty_period_is_not_approved(self):
        r, v = self.score("sales_period", {"days": 0})
        self.assertEqual(r["value"]["orders"], 0)
        self.assertLess(v.score, confidence.APPROVAL_THRESHOLD)
        self.assertIn("ما فيه أي طلبات", " ".join(v.penalties))

    def test_a_count_that_is_the_answer_is_not_treated_as_thin_evidence(self):
        """low_stock returning 4 items is the finding, not a small sample."""
        _, v = self.score("low_stock")
        self.assertTrue(v.approved, v.as_dict())

    def test_an_estimate_is_never_silently_approved(self):
        _, v = self.score("zakat_estimate", {"cash": 80000})
        self.assertFalse(v.approved)
        self.assertIn("تقدير", " ".join(v.penalties))

    def test_a_checker_disagreement_scores_zero(self):
        v = confidence.score_skill({"status": "checker_disagrees"})
        self.assertEqual(v.score, 0.0)
        self.assertFalse(v.approved)

    def test_missing_input_scores_zero(self):
        v = confidence.score_skill({"status": "needs_input"})
        self.assertEqual(v.score, 0.0)

    def test_a_weak_answer_explains_itself_in_arabic(self):
        _, v = self.score("sales_period", {"days": 0})
        note = v.note_ar()
        self.assertIn("%", note)
        self.assertTrue(note.startswith("⚠️"))


class ScoringModelAnswers(unittest.TestCase):
    def test_an_answer_with_no_tool_call_is_rejected(self):
        v = confidence.score_model_answer("مبيعاتك ممتازة", [])
        self.assertFalse(v.approved)
        self.assertLess(v.score, 50)

    def test_grounding_in_a_verified_skill_scores_highest(self):
        grounded = confidence.score_model_answer(
            "المبيعات 1,200.00 ر.س", [{"tool": "run_skill", "ok": True}])
        freeform = confidence.score_model_answer(
            "المبيعات 1,200.00 ر.س", [{"tool": "run_sql", "ok": True}])
        self.assertGreater(grounded.score, freeform.score)

    def test_an_answer_with_no_numbers_is_penalised(self):
        with_num = confidence.score_model_answer("المبيعات 1,200.00", [{"tool": "run_skill", "ok": True}])
        without = confidence.score_model_answer("المبيعات جيدة", [{"tool": "run_skill", "ok": True}])
        self.assertGreater(with_num.score, without.score)

    def test_failed_tool_calls_lower_the_score(self):
        clean = confidence.score_model_answer("12", [{"tool": "run_skill", "ok": True}])
        messy = confidence.score_model_answer(
            "12", [{"tool": "run_skill", "ok": True}, {"tool": "run_sql", "ok": False}])
        self.assertGreater(clean.score, messy.score)


class EscalationLadder(unittest.TestCase):
    def test_it_picks_a_bigger_installed_model(self):
        installed = ["qwen3:8b", "qwen3.6:27b", "qwen2.5-coder:7b"]
        self.assertEqual(confidence.bigger_model("qwen3:8b", installed), "qwen3.6:27b")

    def test_it_never_returns_the_model_already_used(self):
        self.assertIsNone(confidence.bigger_model("qwen3:8b", ["qwen3:8b"]))

    def test_it_returns_nothing_when_no_bigger_model_exists(self):
        self.assertIsNone(confidence.bigger_model("qwen3.6:27b", ["qwen3.6:27b"]))

    def test_the_threshold_is_ninety_five(self):
        self.assertEqual(confidence.APPROVAL_THRESHOLD, 95.0)


if __name__ == "__main__":
    unittest.main()
