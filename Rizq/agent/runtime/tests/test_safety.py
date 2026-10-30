import ast
import pathlib
import unittest

from rizq_agent import act
from rizq_agent.observe import Observer
from rizq_agent.sim import Shop

PKG = pathlib.Path(__file__).resolve().parents[1] / "rizq_agent"
AGENT_SIDE = ["agent.py", "diagnose.py", "act.py"]


class TestSafety(unittest.TestCase):
    def test_agent_never_reads_ground_truth(self):
        """The agent may only see the system through Observer: no `.faults`, no `._shop`."""
        for name in AGENT_SIDE:
            tree = ast.parse((PKG / name).read_text())
            attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
            self.assertNotIn("faults", attrs, name)
            self.assertNotIn("_shop", attrs, name)

    def test_non_whitelisted_action_is_refused(self):
        obs = Observer(Shop(1))
        with self.assertRaises(PermissionError):
            act.execute(obs, [("drop_database", {})], [])

    def test_every_playbook_step_is_whitelisted(self):
        for steps in act.PLAYBOOKS.values():
            for op, _ in steps:
                self.assertIn(op, act.WHITELIST)

    def test_dry_run_logged_before_every_action(self):
        obs, trace = Observer(Shop(1)), []
        act.execute(obs, act.PLAYBOOKS["sync_corruption"], trace)
        types = [e["type"] for e in trace]
        self.assertEqual(types, ["dry_run", "action"] * 4)


if __name__ == "__main__":
    unittest.main()
