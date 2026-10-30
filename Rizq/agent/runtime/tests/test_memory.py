import tempfile
from pathlib import Path
from unittest import TestCase, mock
from rizq_agent import memory


class TestMemory(TestCase):
    def test_history_persists_and_is_scoped_to_user(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(memory, 'PATH', Path(tmp) / 'history.sqlite'):
            memory.append_exchange('admin', 'question one', 'answer one')
            memory.append_exchange('worker', 'question two', 'answer two')
            self.assertEqual(memory.recent('admin'), [{'role': 'user', 'text': 'question one'}, {'role': 'assistant', 'text': 'answer one'}])
            self.assertEqual(memory.recent('worker')[-1]['text'], 'answer two')
            self.assertEqual(memory.history('admin'), memory.recent('admin'))
            self.assertEqual(memory.recent('missing'), [])
            self.assertEqual(Path(memory.PATH).stat().st_mode & 0o777, 0o600)
