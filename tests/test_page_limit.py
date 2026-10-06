import tempfile
import unittest
from pathlib import Path

from app.collectors import LocalJsonlFeed, collect_all
from app.repository import Repository


class PageLimitTests(unittest.TestCase):
    def test_page_limit_persists_checkpoint_for_a_later_round(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            feed = root / "feed.jsonl"
            feed.write_text("\n".join(
                '{"post_id":"%s","platform":"synthetic","timestamp":"2026-10-06T15:00:00Z","content":"x","author_id":"synthetic:%s"}' % (index, index)
                for index in range(3)
            ) + "\n", encoding="utf-8")
            repository = Repository(str(root / "test.sqlite3"))
            repository.initialize()
            try:
                limited = collect_all(repository, LocalJsonlFeed(str(feed), page_size=1), "limited", max_pages=1)
                self.assertEqual("partial_page_limit", limited["status"])
                self.assertEqual(1, repository.post_count())
                resumed = collect_all(repository, LocalJsonlFeed(str(feed), page_size=1), "limited")
                self.assertEqual("success", resumed["status"])
                self.assertEqual(3, repository.post_count())
            finally:
                repository.close()


if __name__ == "__main__":
    unittest.main()
