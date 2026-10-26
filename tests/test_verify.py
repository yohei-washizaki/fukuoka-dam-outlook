import tempfile
import unittest
from pathlib import Path

from dam_outlook import verify as v


def fc(model, base, rows, capacity=1000):
    return {"model": model, "model_version": "1", "issued_at": base + "T14:45:00+09:00",
            "base_date": base, "inputs": {"capacity_m3": capacity}, "forecast": rows}


class VerifyTest(unittest.TestCase):
    def setUp(self):
        self.actual = {"2026-10-24": 300, "2026-10-25": 250}
        self.forecasts = [
            fc("baseline_norain", "2026-10-23", [
                {"date": "2026-10-24", "horizon_days": 1, "storage_m3": 280},
                {"date": "2026-10-25", "horizon_days": 2, "storage_m3": 260},
                {"date": "2026-10-26", "horizon_days": 3, "storage_m3": 240},
            ]),
            fc("scenario_jma_tercile", "2026-10-23", [
                {"date": "2026-10-24", "horizon_days": 1, "storage_m3_p10": 290, "storage_m3_p50": 295,
                 "storage_m3_p90": 310, "prob_below_pct": {"35": 0.9, "30": 0.2, "25": 0.0, "20": 0.0}},
                {"date": "2026-10-25", "horizon_days": 2, "storage_m3_p10": 255, "storage_m3_p50": 260,
                 "storage_m3_p90": 270, "prob_below_pct": {"35": 1.0, "30": 0.5, "25": 0.1, "20": 0.0}},
            ]),
        ]

    def test_score(self):
        rows = v.score(self.forecasts, self.actual)
        self.assertEqual(len(rows), 4)
        b = rows[0]
        self.assertEqual((b["predicted_m3"], b["error_m3"]), (280, -20))
        self.assertNotIn("in_p10_p90", b)
        s1, s2 = rows[2], rows[3]
        self.assertEqual((s1["error_m3"], s1["in_p10_p90"]), (-5, 1))
        self.assertEqual(s2["in_p10_p90"], 0)
        # actual 300 < 350 (35%) -> outcome 1; 300 >= 300 (30%) -> outcome 0
        self.assertEqual((s1["brier_35"], s1["brier_30"]), (0.01, 0.04))
        self.assertEqual(s2["brier_25"], 0.01)  # 250 is not below 250

    def test_summary_skill_against_baseline(self):
        summary = v.summarize(v.score(self.forecasts, self.actual))
        by_model = {r["model"]: r for r in summary}
        self.assertEqual(by_model["baseline_norain"]["mae_m3"], 15)
        s = by_model["scenario_jma_tercile"]
        self.assertEqual((s["horizon"], s["n"], s["mae_m3"], s["baseline_mae_m3"]), ("1-7", 2, 8, 15))
        self.assertEqual(s["mae_skill"], round(1 - 7.5 / 15, 3))
        self.assertEqual(s["p10_p90_coverage"], 0.5)
        self.assertEqual(s["brier_n"], 8)

    def test_verify_writes_tables(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            rows, summary = v.verify(tmp / "normalized", tmp / "forecasts", tmp / "out")
            self.assertEqual((rows, summary), ([], []))
            self.assertTrue((tmp / "out" / "summary.csv").exists())


if __name__ == "__main__":
    unittest.main()
