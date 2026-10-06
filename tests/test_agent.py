import unittest

from app.agent import AgentError, _parse_model_json, dispatch_tool


class AgentSafetyTests(unittest.TestCase):
    def test_model_json_can_use_a_markdown_fence(self):
        self.assertEqual({"recommendation": "MONITOR"}, _parse_model_json("```json\n{\"recommendation\": \"MONITOR\"}\n```"))

    def test_dispatch_rejects_another_topic(self):
        with self.assertRaises(AgentError):
            dispatch_tool(None, "allowed-topic", "get_topic_metrics", {"topic_id": "other-topic"})

    def test_dispatch_rejects_unknown_tool(self):
        with self.assertRaises(AgentError):
            dispatch_tool(None, "allowed-topic", "shell", {"topic_id": "allowed-topic"})


if __name__ == "__main__":
    unittest.main()
