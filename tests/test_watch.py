import io
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from app import cli


class WatchTests(unittest.TestCase):
    def test_bounded_watch_collects_and_analyzes_a_closed_window(self):
        with patch.object(cli, "Repository") as repository_cls, \
             patch.object(cli, "LocalJsonlFeed"), \
             patch.object(cli, "collect_all", return_value={"status": "success"}) as collect, \
             patch.object(cli, "analyze", return_value=[{"topic_id": "topic"}]) as analyze, \
             patch.object(sys, "argv", ["cryptobr", "watch", "--max-cycles", "1"]):
            output = io.StringIO()
            with redirect_stdout(output):
                cli.main()
            self.assertEqual(1, collect.call_count)
            self.assertEqual(1, analyze.call_count)
            self.assertIn('"status": "watch_cycle_completed"', output.getvalue())
            self.assertTrue(repository_cls.return_value.close.called)


if __name__ == "__main__":
    unittest.main()
