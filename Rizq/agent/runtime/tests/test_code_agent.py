"""Security and review checks for model-generated code proposals."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rizq_agent import code_agent


class CodeAgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.project = self.root / 'Rizq'
        self.project.mkdir()
        self.data = self.project / 'private'
        self.patch_project = mock.patch.object(code_agent, 'PROJECT', self.project)
        self.patch_data = mock.patch.object(code_agent, 'DATA', self.data)
        self.patch_project.start(); self.patch_data.start()
        self.addCleanup(self.patch_project.stop); self.addCleanup(self.patch_data.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_admin_password_and_traversal(self):
        code_agent.configure_password('twelve-chars-plus')
        self.assertTrue(code_agent.authenticate('twelve-chars-plus'))
        self.assertFalse(code_agent.authenticate('incorrect'))
        for path in ('../outside.py', '.env', 'data/password.json', '/tmp/a.py'):
            with self.assertRaises(ValueError): code_agent.project_file(path)

    def test_propose_apply_backup_and_stale_protection(self):
        target = self.project / 'example.py'
        target.write_text('value = 1\n')
        brain = mock.Mock(spec=code_agent.copilot.OpenAICompatBrain)
        brain.step.return_value = {'text': json.dumps({'summary': 'change value', 'content': 'value = 2\n'})}
        with mock.patch.object(code_agent.copilot, 'pick_brain', return_value=brain):
            proposal = code_agent.generate('example.py', 'change value')
        self.assertIn('+value = 2', proposal['diff'])
        self.assertEqual(target.read_text(), 'value = 1\n')
        target.write_text('value = 3\n')
        with self.assertRaises(ValueError): code_agent.apply(proposal['id'])
        target.write_text('value = 1\n')
        result = code_agent.apply(proposal['id'])
        self.assertEqual(target.read_text(), 'value = 2\n')
        self.assertEqual((self.data / 'backups' / proposal['id'] / 'original').read_text(), 'value = 1\n')
        self.assertTrue(result['ok'])
        with self.assertRaises(FileNotFoundError): code_agent.apply(proposal['id'])

    def test_invalid_code_is_not_applied(self):
        target = self.project / 'example.py'; target.write_text('value = 1\n')
        brain = mock.Mock(spec=code_agent.copilot.OpenAICompatBrain)
        brain.step.return_value = {'text': json.dumps({'summary': 'bad', 'content': 'def nope(\n'})}
        with mock.patch.object(code_agent.copilot, 'pick_brain', return_value=brain):
            proposal = code_agent.generate('example.py', 'edit')
        with self.assertRaises(SyntaxError): code_agent.apply(proposal['id'])
        self.assertEqual(target.read_text(), 'value = 1\n')

if __name__ == '__main__': unittest.main()
