import unittest

from app.agent import AgentError, dispatch_tool


class AgentSafetyTests(unittest.TestCase):
    def test_dispatch_rejects_another_topic(self):
        with self.assertRaises(AgentError):
            dispatch_tool(None, "allowed-topic", "get_topic_metrics", {"topic_id": "other-topic"})

    def test_dispatch_rejects_unknown_tool(self):
        with self.assertRaises(AgentError):
            dispatch_tool(None, "allowed-topic", "shell", {"topic_id": "allowed-topic"})


if __name__ == "__main__":
    unittest.main()
