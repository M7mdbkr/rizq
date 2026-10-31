"""Security and review checks for model-generated code proposals.

The model is mocked at `ollama_complete`, so these run with no model installed and
assert the *guards*, not the model's taste.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rizq_agent import code_agent


def reply(search: str, replace: str, summary: str = 'change') -> str:
    return (f'SUMMARY: {summary}\n<<<RIZQ_SEARCH>>>\n{search}\n'
            f'<<<RIZQ_REPLACE>>>\n{replace}\n<<<RIZQ_END>>>')


class CodeAgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.project = self.root / 'Rizq'
        self.project.mkdir()
        self.data = self.project / 'private'
        for name, value in (('PROJECT', self.project), ('DATA', self.data)):
            patcher = mock.patch.object(code_agent, name, value)
            patcher.start(); self.addCleanup(patcher.stop)
        model = mock.patch.object(code_agent, 'code_model', return_value='test-model')
        model.start(); self.addCleanup(model.stop)
        self.addCleanup(self.tmp.cleanup)

    def model_says(self, text):
        return mock.patch.object(code_agent, 'ollama_complete', return_value=text)

    # ------------------------------------------------------------------ auth / paths
    def test_admin_password_and_traversal(self):
        code_agent.configure_password('twelve-chars-plus')
        self.assertTrue(code_agent.authenticate('twelve-chars-plus'))
        self.assertFalse(code_agent.authenticate('incorrect'))
        for path in ('../outside.py', '.env', 'data/password.json', '/tmp/a.py'):
            with self.assertRaises(ValueError):
                code_agent.project_file(path)

    def test_a_short_password_is_refused(self):
        with self.assertRaises(ValueError):
            code_agent.configure_password('short')

    # ------------------------------------------------------------------ propose/apply
    def test_propose_apply_backup_and_stale_protection(self):
        target = self.project / 'example.py'
        target.write_text('value = 1\n')
        with self.model_says(reply('value = 1', 'value = 2', 'change value')):
            proposal = code_agent.generate('example.py', 'change value')
        self.assertIn('+value = 2', proposal['diff'])
        self.assertEqual(target.read_text(), 'value = 1\n', 'propose must not write')

        target.write_text('value = 3\n')
        with self.assertRaises(ValueError):
            code_agent.apply(proposal['id'])          # file changed since the proposal

        target.write_text('value = 1\n')
        result = code_agent.apply(proposal['id'])
        self.assertEqual(target.read_text(), 'value = 2\n')
        self.assertEqual((self.data / 'backups' / proposal['id'] / 'original').read_text(), 'value = 1\n')
        self.assertTrue(result['ok'])
        with self.assertRaises(FileNotFoundError):
            code_agent.apply(proposal['id'])          # a proposal is single-use

    def test_json_replies_are_still_accepted(self):
        """Hosted models often answer in JSON; that must keep working."""
        target = self.project / 'example.py'
        target.write_text('value = 1\n')
        with self.model_says(json.dumps({'summary': 'j', 'content': 'value = 9\n'})):
            proposal = code_agent.generate('example.py', 'change value')
        code_agent.apply(proposal['id'])
        self.assertEqual(target.read_text(), 'value = 9\n')

    # ------------------------------------------------------------------ the guards
    def test_syntactically_invalid_code_is_refused_at_propose_time(self):
        target = self.project / 'example.py'
        target.write_text('value = 1\n')
        with self.model_says(reply('value = 1', 'def nope(')):
            with self.assertRaises(ValueError):
                code_agent.generate('example.py', 'edit')
        self.assertEqual(target.read_text(), 'value = 1\n')

    def test_a_proposal_that_guts_the_file_is_refused(self):
        target = self.project / 'example.py'
        body = 'def keep_me():\n    return 1\n\n\n' + ''.join(f'x{i} = {i}\n' for i in range(40))
        target.write_text(body)
        with self.model_says(json.dumps({'summary': 'oops', 'content': 'x0 = 0\n'})):
            with self.assertRaises(ValueError):
                code_agent.generate('example.py', 'edit')

    def test_a_proposal_that_deletes_a_function_is_refused(self):
        target = self.project / 'example.py'
        target.write_text('def keep_me():\n    return 1\n\n\ndef other():\n    return 2\n')
        gutted = json.dumps({'summary': 'oops', 'content': 'def other():\n    return 2\n' + 'pad = 1\n' * 4})
        with self.model_says(gutted):
            with self.assertRaises(ValueError) as cm:
                code_agent.generate('example.py', 'edit')
        self.assertIn('keep_me', str(cm.exception))

    def test_an_edit_whose_search_text_is_absent_is_refused(self):
        target = self.project / 'example.py'
        target.write_text('value = 1\n')
        with self.model_says(reply('not_in_the_file = 7', 'value = 2')):
            with self.assertRaises(ValueError):
                code_agent.generate('example.py', 'edit')

    def test_an_ambiguous_edit_is_refused(self):
        target = self.project / 'example.py'
        target.write_text('def a():\n    return 1\n\n\ndef b():\n    return 1\n')
        with self.model_says(reply('return 1', 'return 2')):
            with self.assertRaises(ValueError) as cm:
                code_agent.generate('example.py', 'edit')
        self.assertIn('ambiguous', str(cm.exception).lower())

    def test_half_a_line_does_not_match(self):
        """A fragment inside a line must never be spliced in mid-line."""
        target = self.project / 'example.py'
        target.write_text('a = 1\nb = 1\n')
        with self.model_says(reply('= 1', '= 2')):
            with self.assertRaises(ValueError) as cm:
                code_agent.generate('example.py', 'edit')
        self.assertIn('not in the file', str(cm.exception).lower())

    def test_an_edit_with_flattened_indentation_still_applies(self):
        """Models routinely drop the leading indentation of a nested block."""
        target = self.project / 'example.py'
        target.write_text('def f():\n    if x:\n        run(a)\n    return 1\n')
        with self.model_says(reply('run(a)', 'try:\n    run(a)\nexcept OSError:\n    pass')):
            proposal = code_agent.generate('example.py', 'wrap it')
        code_agent.apply(proposal['id'])
        self.assertEqual(target.read_text(),
                         'def f():\n    if x:\n        try:\n            run(a)\n'
                         '        except OSError:\n            pass\n    return 1\n')

    def test_the_model_gets_the_failure_back_and_can_correct_itself(self):
        target = self.project / 'example.py'
        target.write_text('value = 1\n')
        answers = [reply('absent = 0', 'value = 2'), reply('value = 1', 'value = 2')]
        with mock.patch.object(code_agent, 'ollama_complete', side_effect=answers) as m:
            proposal = code_agent.generate('example.py', 'edit')
        self.assertEqual(m.call_count, 2)
        self.assertIn('rejected', m.call_args_list[1].args[2])
        code_agent.apply(proposal['id'])
        self.assertEqual(target.read_text(), 'value = 2\n')

    def test_it_gives_up_after_the_attempt_budget(self):
        target = self.project / 'example.py'
        target.write_text('value = 1\n')
        with self.model_says('complete nonsense, no markers') as m:
            with self.assertRaises(ValueError) as cm:
                code_agent.generate('example.py', 'edit')
        self.assertIn('attempts', str(cm.exception))
        self.assertEqual(target.read_text(), 'value = 1\n')


if __name__ == '__main__':
    unittest.main()
