import json
import unittest
from unittest.mock import patch

from app.collectors import MastodonHashtagFeed, SourceAccessError, strip_html


class _Response:
    def __init__(self, payload, link=""):
        self._payload = payload
        self.headers = {"Link": link}

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class MastodonConnectorTests(unittest.TestCase):
    def test_html_is_normalized_and_api_cursor_is_preserved(self):
        payload = [{
            "id": "42", "created_at": "2026-10-06T15:00:00Z", "content": "<p>ETF <b>fictício</b></p>",
            "url": "https://example.social/@ana/42", "uri": "https://example.social/users/ana/statuses/42",
            "account": {"id": "ana"}, "favourites_count": 3, "replies_count": 1, "reblogs_count": 2,
        }]
        with patch("app.collectors.urllib.request.urlopen", return_value=_Response(payload, '<https://example.social/api/v1/timelines/tag/crypto?max_id=41>; rel="next"')):
            page = MastodonHashtagFeed("https://example.social").fetch_page("crypto", None)
        self.assertFalse(page.exhausted)
        self.assertEqual("41", page.next_cursor)
        self.assertEqual("ETF fictício", page.items[0]["content"])
        self.assertEqual("mastodon:ana", page.items[0]["author_id"])
        self.assertFalse(page.items[0]["is_synthetic"])

    def test_html_text_extraction(self):
        self.assertEqual("um dois", strip_html("<p>um <strong>dois</strong></p>"))


if __name__ == "__main__":
    unittest.main()
