"""LLM layer plumbing + safety. Uses stub backends (no network); real-model numbers come from `eval --ai`."""
from __future__ import annotations

import json
import unittest

from rizq_agent.agent import AgentConfig, run_agent
from rizq_agent.evaluate import _FaultSchedule, build_shop, make_scenario, run_one
from rizq_agent.llm import LLM, parse


def stub(answer: dict | str):
    return lambda system, user: answer if isinstance(answer, str) else json.dumps(answer)


def run(suite, backend, i=0):
    sc = make_scenario(i, "train", suite)
    shop = build_shop(sc)
    return run_agent(_FaultSchedule(shop, sc), AgentConfig(llm=backend)), shop


class TestParse(unittest.TestCase):
    def test_label_outside_closed_set_is_coerced_to_unknown(self):
        self.assertEqual(parse('{"label": "delete_everything", "confidence": 0.99}')["label"], "unknown")

    def test_garbage_is_safe(self):
        d = parse("I think the database is sad")
        self.assertEqual(d["label"], "unknown")
        self.assertTrue(d["coerced"])

    def test_confidence_is_clamped(self):
        self.assertEqual(parse('{"label": "healthy", "confidence": 7}')["confidence"], 1.0)


class TestSafety(unittest.TestCase):
    def test_ai_watch_can_only_escalate(self):
        res, shop = run("blind_no_hint", stub({"label": "sync_corruption", "confidence": 0.9, "root_cause": "v2"}))
        self.assertEqual(res["actions"], ["escalate"])
        self.assertEqual(res["outcome"], "escalated")
        self.assertIn("ai_triage", shop.escalations[0])

    def test_confident_healthy_does_nothing(self):
        res, _ = run("blind_healthy", stub({"label": "healthy", "confidence": 0.95}))
        self.assertEqual(res["actions"], [])

    def test_ai_on_unknown_adds_no_actions(self):
        res, shop = run("unknown", stub({"label": "unknown", "confidence": 0.8, "root_cause": "price edited"}))
        self.assertEqual(res["actions"], ["escalate"])
        self.assertEqual(shop.escalations[0]["ai_triage"]["root_cause"], "price edited")

    def test_malicious_output_cannot_trigger_fix(self):
        res, _ = run("unknown", stub('{"label": "restart_everything", "confidence": 1, "human_action": "rm -rf /"}'))
        self.assertEqual(res["actions"], ["escalate"])

    def test_calls_are_bounded(self):
        calls = []
        def counting(system, user):
            calls.append(1)
            return '{"label": "healthy", "confidence": 0.9}'
        run("blind_healthy", counting)
        self.assertLessEqual(len(calls), 12)

    def test_without_key_or_cache_agent_still_works(self):
        res, _ = run("silent_drop", "cache")
        self.assertEqual(res["outcome"], "recovered")


if __name__ == "__main__":
    unittest.main()
