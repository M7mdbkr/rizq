"""Owner-only, local API-key setup and its connection to the agent brain."""
import json
import base64
import io
import os
import unittest
import urllib.error
from unittest import mock

from rizq_agent import copilot, permissions, web


class AISettingsTests(unittest.TestCase):
    def setUp(self):
        self.previous = dict(web.AI_SETTINGS)

    def tearDown(self):
        web.AI_SETTINGS.clear()
        web.AI_SETTINGS.update(self.previous)

    def request(self, body, role="owner", client="127.0.0.1", origin="http://127.0.0.1:8000"):
        handler = web.Handler.__new__(web.Handler)
        handler.path = "/api/ai/settings"
        handler.client_address = (client, 1)
        handler.headers = {"Host": "127.0.0.1:8000", "Origin": origin}
        handler.read_json = lambda: body
        handler.guard = lambda _: None
        handler.send_json = mock.Mock()
        handler.actor = mock.Mock(side_effect=(permissions.PermissionDenied("forbidden") if role != "owner" else None),
                                  return_value={"user_id": "owner", "role": "owner"})
        with mock.patch.object(permissions, "audit"), mock.patch.object(web, "save_ai_settings") as save:
            handler.do_POST()
        self.saved = save.called
        return handler.send_json.call_args

    def test_settings_survive_a_restart_on_this_device_only(self):
        import pathlib, stat, tempfile
        path = pathlib.Path(tempfile.mkdtemp()) / "ai.json"
        with mock.patch.object(web, "AI_SETTINGS_FILE", path):
            web.configure_ai("claude", "", "saved-secret")
            web.save_ai_settings()
            web.configure_ai("auto", "", "")
            self.assertTrue(web.load_ai_settings())
        self.assertEqual((web.AI_SETTINGS["provider"], web.AI_SETTINGS["key"]), ("claude", "saved-secret"))
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_owner_key_is_used_without_being_returned(self):
        with mock.patch.object(web, "check_ai_key") as check:
            response = self.request({"provider": "openai", "key": "test-secret", "model": ""})
        check.assert_called_once_with("openai", "", "test-secret")
        self.assertTrue(self.saved)
        self.assertTrue(response.args[0]["verified"])
        self.assertNotIn("test-secret", json.dumps(response.args))
        brain, provider, _ = web.configured_brain()
        self.assertEqual(provider, "openai")
        self.assertEqual(brain.key, "test-secret")
        self.assertEqual(brain.model, "gpt-4o-mini")
        self.assertTrue(brain.paid)

    def test_staff_remote_and_cross_origin_cannot_change_settings(self):
        for kwargs in ({"role": "staff"}, {"client": "192.168.1.3"}, {"origin": "http://evil.example"}):
            response = self.request({"provider": "openai", "key": "test-secret"}, **kwargs)
            self.assertEqual(response.args[1], 403)
        self.assertEqual(web.AI_SETTINGS, self.previous)

    @staticmethod
    def http_error(code, message=""):
        body = json.dumps({"error": {"message": message}}).encode()
        return urllib.error.HTTPError("https://provider", code, "error", {}, io.BytesIO(body))

    def test_wrong_key_is_rejected_at_save_and_not_stored(self):
        with mock.patch.object(web.urllib.request, "urlopen", side_effect=self.http_error(401, "Incorrect API key sk-wr***ng")), \
             mock.patch.object(web.security, "note_paid_call"):
            response = self.request({"provider": "claude", "key": "sk-wrong", "model": ""})
        self.assertEqual(response.args[1], 400)
        self.assertIn("المفتاح غير صحيح", response.args[0]["error"])
        self.assertNotIn("sk-wr", json.dumps(response.args))
        self.assertEqual(web.AI_SETTINGS, self.previous)

    def test_check_explains_each_provider_failure(self):
        cases = [(self.http_error(404, "model: nope"), "غير متاح"),
                 (self.http_error(400, "Your credit balance is too low"), "Your credit balance is too low"),
                 (self.http_error(429, "You exceeded your current quota"), "الرصيد"),
                 (urllib.error.URLError("offline"), "الإنترنت")]
        for error, expected in cases:
            with mock.patch.object(web.urllib.request, "urlopen", side_effect=error), \
                 mock.patch.object(web.security, "note_paid_call"):
                with self.assertRaises(ValueError) as caught:
                    web.check_ai_key("openai", "nope", "sk-test")
            self.assertIn(expected, str(caught.exception))

    def test_check_sends_the_same_request_shape_as_the_chat(self):
        sent = []
        def fake_urlopen(request, timeout):
            sent.append(request)
            return io.BytesIO(b"{}")
        with mock.patch.object(web.urllib.request, "urlopen", side_effect=fake_urlopen), \
             mock.patch.object(web.security, "note_paid_call") as paid:
            web.check_ai_key("claude", "", "claude-key")
            web.check_ai_key("openai", "gpt-test", "openai-key")
            web.check_ai_key("auto", "", "")
        self.assertEqual(len(sent), 2)
        claude, openai = sent
        self.assertTrue(claude.full_url.endswith("/v1/messages"))
        self.assertEqual(claude.get_header("X-api-key"), "claude-key")
        self.assertEqual(json.loads(claude.data)["model"], "claude-sonnet-4-6")
        self.assertEqual(openai.full_url, "https://api.openai.com/v1/chat/completions")
        self.assertEqual(openai.get_header("Authorization"), "Bearer openai-key")
        self.assertEqual(json.loads(openai.data)["model"], "gpt-test")
        self.assertEqual(paid.call_count, 2)

    def test_key_rejected_during_chat_gets_a_clear_message(self):
        web.configure_ai("openai", "test-model", "revoked-key")
        handler = web.Handler.__new__(web.Handler)
        handler.path = "/api/chat/local"
        handler.read_json = lambda: {"message": "ساعدني أفهم لوحة التحكم", "sqlite_base64": base64.b64encode(web.DEFAULT_DB.read_bytes()).decode()}
        handler.guard = lambda _: None
        handler.actor = lambda *args, **kwargs: {"user_id": "owner", "role": "owner", "name": "Owner"}
        handler.send_json = mock.Mock()
        with mock.patch.object(copilot.urllib.request, "urlopen", side_effect=self.http_error(401)), \
             mock.patch.object(copilot.security, "note_paid_call"), \
             mock.patch.object(web.memory, "recent", return_value=[]):
            handler.do_POST()
        body, status = handler.send_json.call_args.args
        self.assertEqual(status, 400)
        self.assertIn("المفتاح غير صحيح", body["error"])

    def test_provider_change_and_required_key(self):
        with self.assertRaises(ValueError):
            web.configure_ai("claude", "", "")
        web.configure_ai("claude", "", "test-claude-key")
        brain, _, version = web.configured_brain()
        self.assertIsInstance(brain, copilot.ClaudeBrain)
        self.assertEqual(brain.key, "test-claude-key")
        web.configure_ai("rules", "", "")
        self.assertIsInstance(web.configured_brain()[0], copilot.RulesBrain)
        self.assertGreater(web.AI_SETTINGS["version"], version)

    def test_auto_stays_local_even_if_cloud_credentials_exist(self):
        web.configure_ai("auto", "", "")
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "secret", "OPENAI_API_KEY": "secret"}), \
             mock.patch.object(copilot, "local_model", return_value="qwen3:8b"):
            brain, provider, _ = web.configured_brain()
        self.assertEqual(provider, "auto")
        self.assertIsInstance(brain, copilot.OpenAICompatBrain)
        self.assertEqual(brain.url, "http://localhost:11434/v1/chat/completions")
        self.assertFalse(brain.paid)

    def test_cashier_chat_uses_configured_model_and_tools(self):
        web.configure_ai("openai", "test-model", "test-secret")
        handler = web.Handler.__new__(web.Handler)
        handler.path = "/api/chat/local"
        handler.read_json = lambda: {"message": "كم المبيعات؟", "sqlite_base64": base64.b64encode(web.DEFAULT_DB.read_bytes()).decode()}
        handler.guard = lambda _: None
        handler.actor = lambda *args, **kwargs: {"user_id": "owner", "role": "owner", "name": "Owner"}
        handler.send_json = mock.Mock()
        responses = [
            {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
                {"id": "1", "type": "function", "function": {"name": "list_skills", "arguments": "{}"}}]}}]},
            {"choices": [{"message": {"role": "assistant", "content": "المهارات جاهزة."}}]},
        ]
        requests = []
        def fake_urlopen(request, timeout):
            requests.append(request)
            return io.BytesIO(json.dumps(responses.pop(0)).encode())
        with mock.patch.dict(os.environ, {"RIZQ_FORCE_MODEL": "1"}), \
             mock.patch.object(copilot.urllib.request, "urlopen", side_effect=fake_urlopen), \
             mock.patch.object(copilot.security, "note_paid_call"), \
             mock.patch.object(web.memory, "append_exchange"), \
             mock.patch.object(web.memory, "recent", return_value=[]), \
             mock.patch.object(permissions, "audit"), \
             mock.patch.object(web.confidence, "installed_models", return_value=[]):
            handler.do_POST()
        result = handler.send_json.call_args.args[0]
        self.assertEqual(result["model"], "test-model")
        self.assertEqual(len(requests), 2)
        self.assertTrue(result["actions"])
        self.assertEqual(requests[0].get_header("Authorization"), "Bearer test-secret")

    def test_configured_model_handles_unknown_request_without_force_flag(self):
        web.configure_ai("openai", "test-model", "test-secret")
        handler = web.Handler.__new__(web.Handler)
        handler.path = "/api/chat/local"
        handler.read_json = lambda: {"message": "ساعدني أفهم لوحة التحكم", "sqlite_base64": base64.b64encode(web.DEFAULT_DB.read_bytes()).decode()}
        handler.guard = lambda _: None
        handler.actor = lambda *args, **kwargs: {"user_id": "owner", "role": "owner", "name": "Owner"}
        handler.send_json = mock.Mock()
        response = {"choices": [{"message": {"role": "assistant", "content": "كيف أقدر أساعدك؟"}}]}
        with mock.patch.dict(os.environ, {"RIZQ_FORCE_MODEL": "0"}), \
             mock.patch.object(copilot.urllib.request, "urlopen", return_value=io.BytesIO(json.dumps(response).encode())) as request, \
             mock.patch.object(copilot.security, "note_paid_call"), \
             mock.patch.object(web.memory, "append_exchange"), \
             mock.patch.object(web.memory, "recent", return_value=[]), \
             mock.patch.object(web.confidence, "installed_models", return_value=[]):
            handler.do_POST()
        self.assertTrue(request.called)
        self.assertEqual(handler.send_json.call_args.args[0]["model"], "test-model")

    def test_default_auto_routes_greeting_to_local_model(self):
        web.configure_ai("auto", "", "")
        handler = web.Handler.__new__(web.Handler)
        handler.path = "/api/chat/local"
        handler.read_json = lambda: {"message": "الو", "sqlite_base64": base64.b64encode(web.DEFAULT_DB.read_bytes()).decode()}
        handler.guard = lambda _: None
        handler.actor = lambda *args, **kwargs: {"user_id": "owner", "role": "owner", "name": "Owner"}
        handler.send_json = mock.Mock()
        response = {"choices": [{"message": {"role": "assistant", "content": "هلا! كيف أقدر أساعدك؟"}}]}
        with mock.patch.dict(os.environ, {"RIZQ_FORCE_MODEL": "0"}), \
             mock.patch.object(copilot, "local_model", return_value="qwen3:8b"), \
             mock.patch.object(copilot.urllib.request, "urlopen", return_value=io.BytesIO(json.dumps(response).encode())) as request, \
             mock.patch.object(web.memory, "append_exchange"), \
             mock.patch.object(web.memory, "recent", return_value=[]), \
             mock.patch.object(web.confidence, "installed_models", return_value=[]):
            handler.do_POST()
        self.assertTrue(request.called)
        self.assertEqual(handler.send_json.call_args.args[0]["model"], "qwen3:8b")


if __name__ == "__main__":
    unittest.main()
