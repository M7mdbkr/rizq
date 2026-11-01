"""Workflows: each runs on the agent's checked tools, changes nothing, and is audited."""
import base64
import hashlib
import pathlib
import tempfile
import unittest
from unittest import mock

from rizq_agent import copilot, permissions, skills, web, workflows

DEV = str(pathlib.Path(__file__).resolve().parents[1] / "fixtures/rizq_dev.sqlite")


class WorkflowTests(unittest.TestCase):
    def tools(self, actor=None):
        return copilot.PosTools(DEV, tempfile.mkdtemp(), lambda q: False, actor=actor)

    def test_every_workflow_runs_and_reports(self):
        for name, title in workflows.TITLES.items():
            result = workflows.run(name, self.tools())
            self.assertTrue(result["ok"], name)
            self.assertEqual(result["title"], title)
            self.assertTrue(result["report"].strip(), name)

    def test_day_close_numbers_match_the_verified_skill(self):
        tools = self.tools()
        report = workflows.run("day_close", tools)["report"]
        sales = skills.run(tools.db, "sales_period", {"days": 1})["value"]["sales"]
        self.assertIn(f"{sales:,.2f}", report)
        self.assertIn("تم التحقق من الأرقام مرتين", report)

    def test_on_the_real_clock_an_empty_day_says_so(self):
        skills.CLOCK = "real"
        try:
            report = workflows.run("day_close", self.tools())["report"]
        finally:
            skills.CLOCK = "latest"
        self.assertTrue(report.startswith("ما فيه مبيعات اليوم"))

    def test_workflows_never_change_the_database(self):
        before = hashlib.sha256(pathlib.Path(DEV).read_bytes()).hexdigest()
        for name in workflows.TITLES:
            workflows.run(name, self.tools())
        self.assertEqual(hashlib.sha256(pathlib.Path(DEV).read_bytes()).hexdigest(), before)

    def test_runs_are_audited_and_unknown_names_refused(self):
        with mock.patch.object(permissions, "audit") as audit:
            workflows.run("low_stock", self.tools(actor={"user_id": "o", "role": "owner"}))
        self.assertIn("workflow.low_stock", [c.args[1] for c in audit.call_args_list])
        with self.assertRaises(ValueError):
            workflows.run("delete_everything", self.tools())

    def test_route_is_owner_only(self):
        handler = web.Handler.__new__(web.Handler)
        handler.path = "/api/workflows/run"
        handler.read_json = lambda: {"name": "low_stock", "sqlite_base64": base64.b64encode(pathlib.Path(DEV).read_bytes()).decode()}
        handler.guard = lambda _: None
        handler.send_json = mock.Mock()
        handler.actor = mock.Mock(side_effect=permissions.PermissionDenied("owner only"))
        handler.do_POST()
        self.assertEqual(handler.send_json.call_args.args[1], 403)
        handler.actor = mock.Mock(return_value={"user_id": "o", "role": "owner"})
        with mock.patch.object(permissions, "audit"):
            handler.do_POST()
        self.assertEqual(handler.send_json.call_args.args[0]["title"], "تنبيه المخزون")


if __name__ == "__main__":
    unittest.main()
