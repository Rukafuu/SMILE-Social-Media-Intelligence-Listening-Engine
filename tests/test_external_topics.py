import unittest

from app.clustering import match_event


class ExternalTopicTests(unittest.TestCase):
    def test_bitcoin_external_sample_has_an_explicitly_heuristic_topic(self):
        event = match_event("A public post discusses #Bitcoin and market liquidity")
        self.assertEqual("external-bitcoin-discussion", event.topic_id)
        self.assertIn("heurístico externo", event.reason)


if __name__ == "__main__":
    unittest.main()
