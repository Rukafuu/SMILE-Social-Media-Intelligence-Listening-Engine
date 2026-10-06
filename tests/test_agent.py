import unittest

from app.agent import AgentError, _parse_model_json, dispatch_tool, grounded_payload_for_topic


class AgentSafetyTests(unittest.TestCase):
    def test_model_json_can_use_a_markdown_fence(self):
        self.assertEqual({"recommendation": "MONITOR"}, _parse_model_json("```json\n{\"recommendation\": \"MONITOR\"}\n```"))

    def test_model_json_can_have_a_short_text_prefix(self):
        self.assertEqual({"recommendation": "MONITOR"}, _parse_model_json("Here is the result: {\"recommendation\": \"MONITOR\"}"))

    def test_grounded_payload_uses_real_evidence(self):
        payload = grounded_payload_for_topic({"topic_id": "topic-1", "topic": "Teste"}, [
            {"post_id": "synthetic-1", "is_synthetic": 1, "cited_source": None},
            {"post_id": "real-2", "is_synthetic": 0, "cited_source": "https://example.com"},
        ])
        self.assertEqual(["synthetic-1", "real-2"], payload["evidence_refs"])
        self.assertEqual("deterministic_no_llm", payload["analysis_mode"])
        self.assertIn("MONITOR", payload["recommendation"])

    def test_dispatch_rejects_another_topic(self):
        with self.assertRaises(AgentError):
            dispatch_tool(None, "allowed-topic", "get_topic_metrics", {"topic_id": "other-topic"})

    def test_dispatch_rejects_unknown_tool(self):
        with self.assertRaises(AgentError):
            dispatch_tool(None, "allowed-topic", "shell", {"topic_id": "allowed-topic"})


if __name__ == "__main__":
    unittest.main()

