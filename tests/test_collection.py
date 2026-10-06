import tempfile
import unittest
from pathlib import Path

from app.collectors import LocalJsonlFeed, collect_all
from app.repository import Repository


class CollectionTests(unittest.TestCase):
    def test_retry_and_reexecution_do_not_duplicate_posts(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            feed_path = root / "feed.jsonl"
            feed_path.write_text(
                "\n".join([
                    '{"post_id":"1","platform":"synthetic","timestamp":"2026-10-06T14:00:00Z","content":"evento fictício","author_id":"synthetic:a"}',
                    '{"post_id":"2","platform":"synthetic","timestamp":"2026-10-06T14:01:00Z","content":"evento fictício","author_id":"synthetic:b"}',
                    '{"post_id":"3","platform":"synthetic","timestamp":"2026-10-06T14:02:00Z","content":"outro evento","author_id":"synthetic:c"}'
                ]) + "\n",
                encoding="utf-8",
            )
            repository = Repository(str(root / "database.sqlite3"))
            repository.initialize()
            try:
                result = collect_all(repository, LocalJsonlFeed(str(feed_path), page_size=1, fail_on_page=2), "test-feed")
                self.assertEqual("success", result["status"])
                self.assertEqual(3, repository.post_count())
                repeated = collect_all(repository, LocalJsonlFeed(str(feed_path), page_size=1), "test-feed")
                self.assertEqual("already_exhausted", repeated["status"])
                self.assertEqual(3, repository.post_count())
            finally:
                repository.close()


if __name__ == "__main__":
    unittest.main()
