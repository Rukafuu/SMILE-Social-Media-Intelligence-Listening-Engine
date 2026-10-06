import json
import tempfile
import unittest
from pathlib import Path

from app.collectors import MastodonHashtagFeed, collect_stream, exchange_mastodon_authorization_code, parse_sse
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

    def test_streaming_host_uses_instance_configuration(self):
        class Response:
            def read(self):
                return b'{"configuration":{"urls":{"streaming":"wss://stream.example.social"}}}'
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
        from unittest.mock import patch
        from app.collectors import MastodonHashtagStream
        with patch("app.collectors.urllib.request.urlopen", return_value=Response()) as opener:
            stream = MastodonHashtagStream("https://example.social", "token")
            self.assertEqual("https://stream.example.social", stream._streaming_base_url())
            self.assertNotIn("Authorization", opener.call_args.args[0].headers)
            self.assertIn("/api/v2/instance", opener.call_args.args[0].full_url)

    def test_credentials_check_never_returns_token(self):
        class Response:
            def read(self):
                return b'{"id":"42"}'
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
        from unittest.mock import patch
        from app.collectors import MastodonHashtagStream
        with patch("app.collectors.urllib.request.urlopen", return_value=Response()):
            result = MastodonHashtagStream("https://example.social", "secret-value").verify_credentials()
        self.assertEqual({"instance": "https://example.social", "account_id": "42", "valid": "true"}, result)

    def test_authorization_code_exchange_requests_user_token(self):
        class Response:
            def read(self):
                return b'{"access_token":"secret", "scope":"profile read:statuses"}'
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
        from unittest.mock import patch
        with patch("app.collectors.urllib.request.urlopen", return_value=Response()) as opener:
            token = exchange_mastodon_authorization_code("https://example.social", "client", "secret", "code", "urn:ietf:wg:oauth:2.0:oob")
        self.assertEqual("secret", token["access_token"])
        request = opener.call_args.args[0]
        self.assertEqual("https://example.social/oauth/token", request.full_url)
        self.assertIn(b"grant_type=authorization_code", request.data)


if __name__ == "__main__":
    unittest.main()
