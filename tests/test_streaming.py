import json
import tempfile
import unittest
from pathlib import Path

from app.collectors import MastodonHashtagFeed, collect_stream, parse_sse
from app.repository import Repository


class StreamingTests(unittest.TestCase):
    def test_sse_parser_keeps_only_complete_events(self):
        events = list(parse_sse(["event: update\n", "data: {\"id\":\"1\"}\n", "\n", ": heartbeat\n"]))
        self.assertEqual([{"event": "update", "data": '{"id":"1"}'}], events)

    def test_stream_collection_is_idempotent(self):
        payload = {"id": "1", "created_at": "2026-10-06T15:00:00Z", "content": "<p>Bitcoin</p>",
                   "account": {"id": "ana"}, "url": "https://example.social/@ana/1", "favourites_count": 0,
                   "replies_count": 0, "reblogs_count": 0}
        class Stream:
            base_url = "https://example.social"
            def events(self, hashtag):
                yield MastodonHashtagFeed._normalize(payload)
        with tempfile.TemporaryDirectory() as directory:
            repository = Repository(str(Path(directory) / "stream.sqlite3"))
            repository.initialize()
            try:
                first = collect_stream(repository, Stream(), "stream", "bitcoin", max_events=1)
                second = collect_stream(repository, Stream(), "stream", "bitcoin", max_events=1)
                self.assertEqual(1, first["inserted"])
                self.assertEqual(0, second["inserted"])
                self.assertEqual(1, repository.post_count())
            finally:
                repository.close()


if __name__ == "__main__":
    unittest.main()
