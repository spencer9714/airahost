import unittest
from datetime import date, timedelta

from worker.core.dynamic_pricing import (
    compute_demand_adjustment,
    compute_dynamic_pricing_adjustment,
    compute_market_demand_v2,
    compute_time_multiplier,
)
from worker.core.last_minute import compute_last_minute_multiplier


class DynamicPricingTests(unittest.TestCase):
    def test_time_multiplier_buckets(self):
        today = date(2026, 3, 1)
        self.assertEqual(compute_time_multiplier(today, today + timedelta(days=31)), 1.00)
        self.assertEqual(compute_time_multiplier(today, today + timedelta(days=20)), 0.97)
        self.assertEqual(compute_time_multiplier(today, today + timedelta(days=10)), 0.92)
        self.assertEqual(compute_time_multiplier(today, today + timedelta(days=5)), 0.85)
        self.assertEqual(compute_time_multiplier(today, today + timedelta(days=2)), 0.75)

    def test_demand_adjustment_formula(self):
        self.assertEqual(compute_demand_adjustment(0.9), 1.03)
        self.assertEqual(compute_demand_adjustment(0.3), 0.97)
        self.assertEqual(compute_demand_adjustment(-5), 0.90)
        self.assertEqual(compute_demand_adjustment(99), 1.05)

    def test_market_demand_v2_outputs_confidence_and_reasons(self):
        base = date(2026, 3, 1)
        rows = []
        for i in range(7):
            d = base + timedelta(days=i)
            rows.append(
                {
                    "date": d,
                    "baseDailyPrice": 200,
                    "compsUsed": 30 if i != 3 else 8,
                    "priceDistribution": {
                        "p25": 180,
                        "median": 240 if i == 4 else 200,
                        "p75": 210,
                        "min": 150,
                        "max": 280,
                    },
                    "flags": ["peak"] if i == 5 else [],
                }
            )

        out = compute_market_demand_v2(rows)
        self.assertEqual(len(out), 7)
        self.assertIn(out[3]["confidence"], ("low", "medium", "high"))
        self.assertEqual(out[3]["confidence"], "low")
        self.assertTrue(any("Low comps count" in r for r in out[3]["reasons"]))

    def test_unified_adjustment_handles_missing_base_price(self):
        today = date(2026, 2, 25)
        rows = [
            {
                "date": date(2026, 3, 1),
                "baseDailyPrice": None,
                "compsUsed": 5,
                "priceDistribution": {"p25": None, "median": None, "p75": None, "min": None, "max": None},
                "flags": ["missing_data"],
            }
        ]

        out = compute_dynamic_pricing_adjustment(today, rows)
        self.assertEqual(len(out), 1)
        self.assertIsNone(out[0]["priceAfterTimeAdjustment"])
        self.assertIn("missing_data", out[0]["flags"])
        self.assertGreaterEqual(out[0]["dynamicAdjustment"]["finalMultiplier"], 0.65)
        self.assertLessEqual(out[0]["dynamicAdjustment"]["finalMultiplier"], 1.05)

    def test_backward_wrapper_is_thin_and_deterministic(self):
        checkin = date(2026, 3, 1)
        target = date(2026, 3, 2)
        v = compute_last_minute_multiplier(checkin, target, occupancy_signal=0.9)
        self.assertEqual(v, 0.772)


if __name__ == "__main__":
    unittest.main()
