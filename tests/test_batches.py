import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app.agent import analyze_topic
from app.collectors import LocalJsonlFeed, collect_all
from app.repository import Repository
from app.trends import analyze
from scripts.generate_dataset import build_records


AS_OF = datetime(2026, 10, 6, 15, 0, tzinfo=timezone.utc)


def write_records(path, records, mode="w"):
    import json
    with path.open(mode, encoding="utf-8") as destination:
        for record in records:
            destination.write(json.dumps(record, ensure_ascii=False) + "\n")


class BatchUpdateTests(unittest.TestCase):
    def test_batches_preserve_topic_identity_and_prior_review(self):
        initial = build_records(AS_OF, 42, "initial")
        update = build_records(AS_OF, 42, "update")
        self.assertEqual((AS_OF - timedelta(minutes=15)).isoformat().replace("+00:00", "Z"), initial[-1]["timestamp"])
        self.assertEqual(AS_OF.isoformat().replace("+00:00", "Z"), update[-1]["timestamp"])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            feed_path = root / "feed.jsonl"
            write_records(feed_path, initial)
            repository = Repository(str(root / "database.sqlite3"))
            repository.initialize()
            try:
                collect_all(repository, LocalJsonlFeed(str(feed_path), page_size=50), "synthetic-batches")
                first_results = analyze(repository, AS_OF - timedelta(minutes=15))
                first_aurora = next(row for row in first_results if row["topic_id"] == "token-aurora-protocol")
                with patch.dict(os.environ, {"OPENROUTER_API_KEY": ""}):
                    alert = analyze_topic(repository, first_aurora)
                alert_id = repository.save_alert(first_aurora["topic_id"], alert["analysis_mode"], alert["recommendation"], alert)
                repository.save_review(alert_id, "EDIT", "Manter em observação", "ana")

                write_records(feed_path, update, "a")
                collected = collect_all(repository, LocalJsonlFeed(str(feed_path), page_size=50), "synthetic-batches", reopen_exhausted=True)
                self.assertGreater(collected["inserted"], 0)
                final_results = analyze(repository, AS_OF)
                final_aurora = next(row for row in final_results if row["topic_id"] == "token-aurora-protocol")
                self.assertEqual(first_aurora["topic_id"], final_aurora["topic_id"])
                self.assertEqual("growing", final_aurora["stage"])
                self.assertEqual("EDIT", repository.reviews_for_alert(alert_id)[0]["decision"])
                coverage = repository.latest_collection_run()
                self.assertIn("synthetic_local_jsonl", coverage["coverage"])
            finally:
                repository.close()


if __name__ == "__main__":
    unittest.main()
