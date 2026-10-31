"""Copilot: LLM brain <-> POS tools. Fake local model servers (Ollama-style and Claude-style) script the LLM,
including a malicious one, to prove that safety is enforced by the tools, not by the prompt."""
import http.server
import json
import os
import pathlib
import threading
import unittest

from rizq_agent import copilot, rizq

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEV = str(ROOT / "fixtures" / "rizq_dev.sqlite")
OUT = str(ROOT / "results" / "_test")


def fake_server(script):
    """Serves scripted replies in order, records requests. Works for /v1/chat/completions and /v1/messages."""
    seen = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            seen.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            body = json.dumps(script.pop(0)).encode()
            self.send_response(200); self.send_header("content-type", "application/json"); self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, seen


def oa_call(name, args, i=0):
    return {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
        {"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}}]}


def oa_text(t):
    return {"choices": [{"message": {"role": "assistant", "content": t}}]}


class TestTools(unittest.TestCase):
    def setUp(self):
        self.t = copilot.PosTools(DEV, OUT, confirm=lambda q: False)

    def count(self):
        return self.t.db.execute("SELECT COUNT(*) FROM orders").fetchone()[0]

    def test_sql_is_read_only(self):
        n = self.count()
        for bad in ["DELETE FROM orders", "SELECT 1; DELETE FROM orders", "UPDATE orders SET vat_amount=0",
                    "WITH x AS (SELECT 1) DELETE FROM orders", "DROP TABLE orders",
                    "SELECT name, pin FROM users", "SELECT * FROM users"]:
            self.assertIn("error", self.t.call("run_sql", {"query": bad}), bad)
        self.assertEqual(n, self.count())

    def test_select_works(self):
        out = self.t.call("run_sql", {"query": "SELECT COUNT(*) FROM orders"})
        self.assertEqual(out["rows"][0][0], self.count())

    def test_unknown_tool_refused(self):
        self.assertIn("error", self.t.call("drop_database", {}))

    def test_apply_needs_human_yes(self):
        db = rizq.open_copy(DEV)
        db.execute("UPDATE order_items SET total=total+5 WHERE rowid=1"); db.commit()
        t = copilot.PosTools(db, OUT, confirm=lambda q: False)
        self.assertEqual(t.call("apply_fixes", {})["applied"], 0)
        t.confirm = lambda q: True
        out = t.call("apply_fixes", {})
        self.assertGreater(out["applied"], 0)
        self.assertEqual(out["verification"]["auto_findings_left"], 0)


class TestBrains(unittest.TestCase):
    def test_local_model_tool_loop_and_malicious_sql(self):
        """Ollama/LM Studio protocol: model scans, tries to DELETE, then answers. Orders must be intact."""
        srv, seen = fake_server([oa_call("scan_pos", {}, 0), oa_call("run_sql", {"query": "DELETE FROM orders"}, 1),
                                 oa_text("done")])
        tools = copilot.PosTools(DEV, OUT, confirm=lambda q: False)
        n = tools.db.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        brain = copilot.OpenAICompatBrain(f"http://127.0.0.1:{srv.server_port}/v1", "qwen2.5:7b")
        self.assertEqual(copilot.ask(brain, tools, "check", on_event=lambda e: None), "done")
        srv.shutdown(); srv.server_close()
        self.assertEqual(n, tools.db.execute("SELECT COUNT(*) FROM orders").fetchone()[0])
        self.assertEqual(seen[0]["tools"][0]["function"]["name"], "scan_pos")      # tools were offered
        self.assertEqual(seen[2]["messages"][-1]["role"], "tool")                  # results were fed back
        self.assertIn("only ONE read-only SELECT", seen[2]["messages"][-1]["content"])

    def test_claude_protocol_tool_loop(self):
        srv, seen = fake_server([
            {"content": [{"type": "tool_use", "id": "t1", "name": "get_order", "input": {"order_id": "ord_zjkx2gs"}}]},
            {"content": [{"type": "text", "text": "الطلب بتاريخ مستقبلي"}]}])
        os.environ["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{srv.server_port}"
        try:
            brain = copilot.ClaudeBrain("claude-sonnet-5-5")
            brain.key = "test"
            out = copilot.ask(brain, copilot.PosTools(DEV, OUT), "ord_zjkx2gs?", on_event=lambda e: None)
        finally:
            os.environ.pop("ANTHROPIC_BASE_URL"); srv.shutdown(); srv.server_close()
        self.assertEqual(out, "الطلب بتاريخ مستقبلي")
        self.assertEqual(seen[1]["messages"][-1]["content"][0]["type"], "tool_result")

    def test_step_budget_stops_runaway_model(self):
        srv, _ = fake_server([oa_call("scan_pos", {}, i) for i in range(copilot.MAX_STEPS + 2)])
        brain = copilot.OpenAICompatBrain(f"http://127.0.0.1:{srv.server_port}/v1", "m")
        out = copilot.ask(brain, copilot.PosTools(DEV, OUT), "loop", on_event=lambda e: None)
        srv.shutdown(); srv.server_close()
        self.assertIn("step budget", out)

    def test_rules_brain_offline(self):
        out = copilot.ask(copilot.RulesBrain(), copilot.PosTools(DEV, OUT), "افحص", on_event=lambda e: None)
        self.assertIn("الفحص", out)


if __name__ == "__main__":
    unittest.main()
