import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from correctness import Campaign, main


class HarnessValidity(unittest.TestCase):
    def test_wrong_error_cannot_satisfy_zero_count(self):
        c = Campaign.__new__(Campaign)
        c.reset = lambda: None
        c.count = lambda code: 0
        c.assertion = lambda *args: None
        c.sql = lambda text, check=True: subprocess.CompletedProcess(
            [], 0 if check else 3, '', '' if check else "ERROR: 42601: syntax error; LINE 1: ERRCODE='Z1001'")
        with self.assertRaisesRegex(RuntimeError, 'SQLSTATE Z1001'):
            c.severity()

    def test_setup_failure_preserves_unrun_inventory(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'new'
            with patch.object(Campaign, 'setup', side_effect=RuntimeError('injected setup failure')), \
                 patch('sys.argv', ['correctness.py', '--pg-bin', '/no-database', '--workdir', str(output)]), \
                 patch('os.geteuid', return_value=1000):
                with self.assertRaisesRegex(RuntimeError, 'injected setup failure'):
                    main()
            evidence = json.loads((output / 'results.json').read_text())
            self.assertEqual(evidence['summary']['error'], 1)
            self.assertEqual(len(evidence['groups']), 9)
            self.assertEqual(set(evidence['groups'].values()), {'not_run'})
            self.assertEqual(evidence['assertions'][0]['name'], 'setup')


if __name__ == '__main__':
    unittest.main()
