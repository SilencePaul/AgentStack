import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.compare_app_tests import compare


class AppComparison(unittest.TestCase):
    def test_detects_regression_without_changing_checked_out_branch(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            def git(*args):
                return subprocess.check_output(['git', '-C', directory, *args], text=True).strip()

            git('init', '-q')
            git('config', 'user.email', 'test@localhost')
            git('config', 'user.name', 'test')
            (repo/'app').mkdir()
            value = repo/'app'/'value'
            value.write_text('ok')
            git('add', '.')
            git('commit', '-qm', 'baseline')
            base = git('rev-parse', 'HEAD')
            value.write_text('broken')
            git('commit', '-qam', 'regression')
            head = git('rev-parse', 'HEAD')
            suite = [sys.executable, '-c', "from pathlib import Path; import sys; sys.exit(Path('value').read_text() != 'ok')"]
            result = compare(directory, base, head, 'app', suite)
            self.assertEqual((result['baseline'], result['trial_merge']), (0, 1))
            self.assertEqual(git('rev-parse', 'HEAD'), head)
            self.assertEqual(git('status', '--porcelain'), '')
