import tempfile
import unittest

from app.repository import Repository


class ReviewTests(unittest.TestCase):
    def test_review_history_persists(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = Repository(directory + "/review.sqlite3")
            repository.initialize()
            try:
                repository.connection.execute("INSERT INTO topics VALUES ('topic', 'Topic', NULL, '[]', 'rules', 'test', 'v1', 'now', 'now', 'now')")
                alert_id = repository.save_alert("topic", "simulated", "MONITOR", {"topic_id": "topic"})
                repository.save_review(alert_id, "EDIT", "Resumo corrigido", "ana")
                history = repository.reviews_for_alert(alert_id)
                self.assertEqual("EDIT", history[0]["decision"])
                self.assertEqual("Resumo corrigido", history[0]["revised_summary"])
            finally:
                repository.close()


if __name__ == "__main__":
    unittest.main()
