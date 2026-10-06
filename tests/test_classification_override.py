import tempfile
import unittest
from pathlib import Path

from app.repository import Repository


class ClassificationOverrideTests(unittest.TestCase):
    def test_human_override_survives_later_deterministic_update(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = Repository(str(Path(directory) / "test.sqlite3"))
            repository.initialize()
            try:
                repository.upsert_topic("topic", "Original", "crypto_web3", [], "terms", "v1", "a", "b")
                repository.override_topic_classification("topic", "business_finance", ["crypto_web3"], "ana", "v1")
                repository.upsert_topic("topic", "New title", "sports_entertainment", [], "new terms", "v2", "a", "c")
                topic = repository.connection.execute("SELECT * FROM topics WHERE topic_id='topic'").fetchone()
                self.assertEqual("business_finance", topic["primary_category"])
                self.assertEqual("human_override", topic["classification_method"])
                self.assertEqual("New title", topic["title"])
            finally:
                repository.close()


if __name__ == "__main__":
    unittest.main()
