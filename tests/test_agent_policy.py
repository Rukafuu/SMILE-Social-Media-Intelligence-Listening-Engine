import unittest

from app.agent import _apply_host_limits, _simulated


class AgentPolicyTests(unittest.TestCase):
    def test_simulated_high_score_stays_monitor(self):
        alert = _simulated({"topic_id": "topic", "topic": "Tema", "score": 90})
        self.assertEqual("MONITOR", alert["recommendation"])

    def test_highlight_requires_evidence_reference(self):
        alert = _apply_host_limits({"recommendation": "HIGHLIGHT", "evidence_refs": []}, {"score": 90})
        self.assertEqual("MONITOR", alert["recommendation"])


if __name__ == "__main__":
    unittest.main()
