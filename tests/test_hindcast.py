import unittest
from datetime import date, timedelta

from dam_outlook import hindcast as h


class BaseDatesTest(unittest.TestCase):
    def test_skips_days_without_rain(self):
        start = date(2026, 9, 29)
        days = {start + timedelta(days=i): 1 for i in range(60)}
        rain = {d: 0.0 for d in days if d != date(2026, 10, 6)}
        self.assertEqual(h.base_dates(days, days, rain, start, 7), [date(2026, 9, 29), date(2026, 10, 13), date(2026, 10, 20), date(2026, 10, 27)])


if __name__ == "__main__":
    unittest.main()
