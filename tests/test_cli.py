import io
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from app import cli


class CliStartupTests(unittest.TestCase):
    def test_cli_script_runs_from_project_root(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, str(root / "app" / "cli.py"), "--help"],
            cwd=root,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, result.returncode, msg=result.stderr)
        self.assertIn("collect", result.stdout)

    def test_stream_keyboard_interrupt_is_clean(self):
        with patch.object(cli, "Repository") as repository_cls, \
             patch.object(cli, "collect_stream", side_effect=KeyboardInterrupt), \
             patch.object(cli, "MastodonHashtagStream"), \
             patch.object(sys, "argv", ["cryptobr", "stream", "--hashtag", "bitcoin", "--max-events", "1"]):
            repository = repository_cls.return_value
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                cli.main()
            self.assertIn('"status": "interrupted"', stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
