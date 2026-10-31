"""Free local models: device check, one-click download, and the bigger-model ladder."""
import base64
import io
import json
import os
import time
import unittest
from unittest import mock

from rizq_agent import confidence, copilot, models, permissions, web

SMALL = {"ram_gb": 8.0, "disk_free_gb": 100.0}
MID = {"ram_gb": 16.0, "disk_free_gb": 100.0}
BIG = {"ram_gb": 64.0, "disk_free_gb": 500.0}


class DeviceCheckTests(unittest.TestCase):
    def test_an_8gb_laptop_runs_the_main_model_but_not_the_next_one(self):
        self.assertTrue(models.fits("qwen3:8b", SMALL))
        self.assertFalse(models.fits("qwen3.5:9b", SMALL))
        self.assertEqual(models.alternative("qwen3.5:9b", SMALL), "qwen3:8b")

    def test_a_big_model_on_16gb_is_replaced_by_the_largest_that_fits(self):
        self.assertFalse(models.fits("gpt-oss:20b", MID))
        self.assertEqual(models.alternative("gpt-oss:20b", MID), "qwen3.5:9b")

    def test_free_disk_counts_too(self):
        tight = {"ram_gb": 32.0, "disk_free_gb": 4.0}
        self.assertFalse(models.fits("qwen3:8b", tight))
        self.assertEqual(models.alternative("qwen3:8b", tight), "qwen3.5:2b")

    def test_nothing_fits_a_tiny_device(self):
        self.assertIsNone(models.alternative("qwen3:8b", {"ram_gb": 2.0, "disk_free_gb": 100.0}))

    def test_status_recommends_the_strongest_main_model_that_fits(self):
        with mock.patch.object(models, "device", return_value=SMALL), \
             mock.patch.object(models, "installed", return_value=["qwen3.5:4b"]):
            info = models.status()
        self.assertEqual(info["recommended"], "qwen3:8b")
        rows = {m["name"]: m for m in info["models"]}
        self.assertTrue(rows["qwen3.5:4b"]["installed"])
        self.assertFalse(rows["gpt-oss:20b"]["fits"])
        self.assertEqual(rows["gpt-oss:20b"]["alternative"], "qwen3:8b")

    def test_runnable_drops_installed_models_the_device_cannot_load(self):
        self.assertEqual(models.runnable(["qwen3:8b", "gpt-oss:20b", "my-own:latest"], SMALL),
                         ["qwen3:8b", "my-own:latest"])


class DownloadTests(unittest.TestCase):
    def setUp(self):
        models.PULLS.clear()

    def wait(self, name):
        for _ in range(100):
            if models.PULLS[name]["state"] != "downloading":
                return models.PULLS[name]
            time.sleep(0.02)
        self.fail("download never finished")

    def test_a_model_too_big_for_the_device_is_refused_with_an_alternative(self):
        with mock.patch.object(models, "device", return_value=MID):
            with self.assertRaises(ValueError) as caught:
                models.start_pull("gpt-oss:20b")
        self.assertEqual(caught.exception.alternative, "qwen3.5:9b")
        self.assertIn("qwen3.5:9b", str(caught.exception))
        self.assertEqual(models.PULLS, {})

    def test_unknown_names_are_refused(self):
        with self.assertRaises(ValueError):
            models.start_pull("evil; rm -rf /")

    def test_ollama_not_running_is_explained(self):
        with mock.patch.object(models, "device", return_value=BIG), \
             mock.patch.object(models, "installed", return_value=None):
            with self.assertRaises(ValueError) as caught:
                models.start_pull("qwen3:8b")
        self.assertIn("Ollama", str(caught.exception))

    def test_download_reports_progress_then_done(self):
        lines = [{"status": "pulling manifest"}, {"status": "downloading", "total": 200, "completed": 50},
                 {"status": "downloading", "total": 200, "completed": 200}, {"status": "success"}]
        sent = []
        def opener(request, timeout):
            sent.append(json.loads(request.data))
            return io.BytesIO(b"\n".join(json.dumps(l).encode() for l in lines))
        with mock.patch.object(models, "device", return_value=BIG), \
             mock.patch.object(models, "installed", return_value=[]):
            models.start_pull("qwen3:8b", opener=opener)
        state = self.wait("qwen3:8b")
        self.assertEqual((state["state"], state["percent"]), ("done", 100))
        self.assertEqual(sent[0]["model"], "qwen3:8b")

    def test_an_ollama_error_is_shown_not_swallowed(self):
        opener = lambda request, timeout: io.BytesIO(json.dumps({"error": "no space left on device"}).encode())
        with mock.patch.object(models, "device", return_value=BIG), \
             mock.patch.object(models, "installed", return_value=[]):
            models.start_pull("qwen3:8b", opener=opener)
        state = self.wait("qwen3:8b")
        self.assertEqual(state["state"], "error")
        self.assertIn("no space left", state["detail"])

    def test_a_cut_stream_is_not_reported_as_done(self):
        opener = lambda request, timeout: io.BytesIO(json.dumps({"status": "downloading", "total": 10, "completed": 3}).encode())
        with mock.patch.object(models, "device", return_value=BIG), \
             mock.patch.object(models, "installed", return_value=[]):
            models.start_pull("qwen3:8b", opener=opener)
        self.assertEqual(self.wait("qwen3:8b")["state"], "error")


class ModelRoutesTests(unittest.TestCase):
    def request(self, path, body, role="owner", client="127.0.0.1", origin="http://127.0.0.1:8000"):
        handler = web.Handler.__new__(web.Handler)
        handler.path = path
        handler.client_address = (client, 1)
        handler.headers = {"Host": "127.0.0.1:8000", "Origin": origin}
        handler.read_json = lambda: body
        handler.guard = lambda _: None
        handler.send_json = mock.Mock()
        handler.actor = mock.Mock(side_effect=(permissions.PermissionDenied("forbidden") if role != "owner" else None),
                                  return_value={"user_id": "owner", "role": "owner"})
        with mock.patch.object(permissions, "audit"):
            handler.do_POST()
        return handler.send_json.call_args

    def test_only_the_owner_on_this_device_can_list_or_download(self):
        for kwargs in ({"role": "staff"}, {"client": "192.168.1.3"}, {"origin": "http://evil.example"}):
            for path in ("/api/ai/models", "/api/ai/models/pull"):
                with mock.patch.object(models, "start_pull") as pull:
                    response = self.request(path, {"name": "qwen3:8b"}, **kwargs)
                self.assertEqual(response.args[1], 403, (path, kwargs))
                pull.assert_not_called()

    def test_owner_sees_the_catalog(self):
        with mock.patch.object(models, "device", return_value=SMALL), \
             mock.patch.object(models, "installed", return_value=[]):
            body = self.request("/api/ai/models", {}).args[0]
        self.assertEqual(body["device"], SMALL)
        self.assertEqual(len(body["models"]), len(models.CATALOG))

    def test_refused_download_returns_the_alternative(self):
        with mock.patch.object(models, "device", return_value=MID):
            body, status = self.request("/api/ai/models/pull", {"name": "gpt-oss:20b"}).args
        self.assertEqual(status, 400)
        self.assertEqual(body["alternative"], "qwen3.5:9b")


class LadderTests(unittest.TestCase):
    def setUp(self):
        self.previous = dict(web.AI_SETTINGS)

    def tearDown(self):
        web.AI_SETTINGS.clear()
        web.AI_SETTINGS.update(self.previous)

    def test_escalation_goes_one_step_up_and_skips_models_too_big_to_wait_for(self):
        installed = ["qwen3:8b", "qwen3.5:9b", "gpt-oss:20b", "qwen3.6:27b"]
        self.assertEqual(confidence.bigger_model("qwen3:8b", installed), "qwen3.5:9b")
        on_24gb = {"ram_gb": 24.0, "disk_free_gb": 500.0}
        self.assertEqual(models.quick(installed, on_24gb), ["qwen3:8b", "qwen3.5:9b"])

    def test_the_ladder_never_steps_down(self):
        self.assertIsNone(confidence.bigger_model("qwen3.6:27b", ["qwen3.6:27b", "gpt-oss:20b", "qwen3:8b"]))
        self.assertEqual(confidence.bigger_model("gpt-oss:20b", ["qwen3.6:27b", "gpt-oss:20b"]), "qwen3.6:27b")

    def chat(self, answers, scores, installed):
        web.configure_ai("auto", "", "")
        handler = web.Handler.__new__(web.Handler)
        handler.path = "/api/chat/local"
        handler.read_json = lambda: {"message": "ليش مبيعات الأسبوع نازلة؟",
                                     "sqlite_base64": base64.b64encode(web.DEFAULT_DB.read_bytes()).decode()}
        handler.guard = lambda _: None
        handler.actor = lambda *a, **k: {"user_id": "owner", "role": "owner", "name": "Owner"}
        handler.send_json = mock.Mock()
        verdicts = [confidence.Verdict(s, [], []) for s in scores]
        with mock.patch.dict(os.environ, {"RIZQ_FORCE_MODEL": "1"}), \
             mock.patch.object(copilot, "local_model", return_value="qwen3:8b"), \
             mock.patch.object(copilot, "ask", side_effect=answers), \
             mock.patch.object(confidence, "score_model_answer", side_effect=verdicts), \
             mock.patch.object(confidence, "installed_models", return_value=installed), \
             mock.patch.object(models, "device", return_value=SMALL), \
             mock.patch.object(web.memory, "append_exchange"), \
             mock.patch.object(web.memory, "recent", return_value=[]):
            handler.do_POST()
        return handler.send_json.call_args.args[0]

    def test_light_model_says_it_handed_the_question_to_a_bigger_one(self):
        result = self.chat(["ما أدري", "السبب: نقص الطلبات يوم الخميس"], [60, 97], ["qwen3:8b", "qwen3.5:9b"])
        # qwen3.5:9b does not fit an 8 GB device, so it must not be used...
        self.assertIsNone(result["escalated_to"])
        self.assertIn("تنزّل", result["answer"])
        result = self.chat(["ما أدري", "السبب: نقص الطلبات يوم الخميس"], [60, 97], ["qwen3:8b", "my-model:latest", "qwen3.5:4b"])
        self.assertIsNone(result["escalated_to"])      # nothing bigger on the ladder

    def test_escalation_note_when_a_bigger_model_answers(self):
        with mock.patch.object(models, "device", return_value=BIG):
            web.configure_ai("auto", "", "")
        with mock.patch.object(models, "quick", side_effect=lambda names, dev=None: names):
            result = self.chat(["ما أدري", "السبب: نقص الطلبات يوم الخميس"], [60, 97], ["qwen3:8b", "gpt-oss:20b"])
        self.assertEqual(result["escalated_to"], "gpt-oss:20b")
        self.assertTrue(result["answer"].startswith("النموذج الخفيف (qwen3:8b)"))
        self.assertIn("نقص الطلبات", result["answer"])


if __name__ == "__main__":
    unittest.main()
