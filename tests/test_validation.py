import unittest

from app.validation import validate_analysis


class ValidationTests(unittest.TestCase):
    def test_highlight_with_missing_reference_is_limited(self):
        class Repository:
            connection = None

        # No repository access is needed before an evidence reference is accepted;
        # use a minimal fake for the empty-evidence branch.
        class EmptyRepository:
            class Result:
                def fetchall(self):
                    return []
            class Connection:
                def execute(self, *args):
                    return EmptyRepository.Result()
            connection = Connection()

        result = validate_analysis(EmptyRepository(), {"topic_id": "topic", "score": 90}, {
            "recommendation": "HIGHLIGHT", "evidence_refs": ["invented"], "risk_flags": [], "uncertainties": []
        }, consulted=[])
        self.assertEqual("MONITOR", result["recommendation"])
        self.assertIn("invalid_evidence_reference", result["risk_flags"])


if __name__ == "__main__":
    unittest.main()

