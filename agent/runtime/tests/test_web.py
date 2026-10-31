"""Local web/API, parked backlog and generic LLM provider configuration tests."""
from __future__ import annotations

import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock

from rizq_agent import copilot
from rizq_agent import zatca
from rizq_agent.web import DEFAULT_DB, validate_database

ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestWebProject(unittest.TestCase):
    def test_zatca_does_not_claim_connection_or_submission(self):
        with mock.patch.dict(os.environ, {"RIZQ_ZATCA_ENV": "sandbox"}):
            status = zatca.readiness()
        self.assertEqual(status["environment"], "sandbox")
        self.assertFalse(status["official_fatoora_connected"])
        self.assertFalse(status["invoice_submission_implemented"])

    def test_default_fixture_is_a_rizq_database(self):
        validate_database(DEFAULT_DB)

    def test_invalid_database_is_rejected(self):
        with tempfile.NamedTemporaryFile() as f:
            f.write(b"not sqlite")
            f.flush()
            with self.assertRaises(ValueError):
                validate_database(pathlib.Path(f.name))

    def test_feature_and_problem_catalogs_are_in_project(self):
        features = json.loads((ROOT / "features" / "parked.json").read_text(encoding="utf-8"))
        problems = json.loads((ROOT / "problems" / "catalog.json").read_text(encoding="utf-8"))
        self.assertTrue(features["features"])
        self.assertTrue(all(x["status"] == "parked" and x["enabled"] is False for x in features["features"]))
        self.assertIn("BUG-1", {x["id"] for x in problems["problems"]})

    def test_compatible_provider_uses_rizq_server_configuration(self):
        cfg = {"RIZQ_LLM_BASE_URL": "http://127.0.0.1:9001/v1", "RIZQ_LLM_MODEL": "demo-model",
               "RIZQ_LLM_API_KEY": "local-test-token"}
        with mock.patch.dict(os.environ, cfg, clear=True):
            brain = copilot.pick_brain("compatible")
        self.assertIsInstance(brain, copilot.OpenAICompatBrain)
        self.assertEqual(brain.url, "http://127.0.0.1:9001/v1/chat/completions")
        self.assertEqual(brain.model, "demo-model")


if __name__ == "__main__":
    unittest.main()
