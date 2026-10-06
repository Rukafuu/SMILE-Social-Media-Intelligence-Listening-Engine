import unittest

from app.trends import score_metrics


class TrendScoreTests(unittest.TestCase):
    def test_diverse_growth_outranks_stable_high_volume_topic(self):
        stable = score_metrics(
            {"N": 100, "n": 90, "U": 40, "F": 90, "HHI": 0.03},
            [{"n": 90}, {"n": 90}, {"n": 90}],
        )
        emerging = score_metrics(
            {"N": 40, "n": 38, "U": 30, "F": 36, "HHI": 0.04},
            [{"n": 4}, {"n": 4}, {"n": 4}],
        )
        self.assertEqual("growing", emerging["stage"])
        self.assertGreater(emerging["score"], stable["score"])

    def test_missing_history_is_not_interpreted_as_zero_baseline(self):
        result = score_metrics({"N": 10, "n": 10, "U": 10, "F": 10, "HHI": 0.1}, [{"n": 2}])
        self.assertEqual("insufficient_history", result["stage"])
        self.assertIsNone(result["score"])


if __name__ == "__main__":
    unittest.main()
