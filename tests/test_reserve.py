import unittest
from datetime import date

from dam_outlook import reserve


def d(day):
    return date(2026, 10, day)


class ReserveTest(unittest.TestCase):
    def setUp(self):
        self.pub = reserve.published([
            {"date": "2026-10-05", "storage_m3": "700", "capacity_m3": "5000", "incl_storage_m3": "", "incl_storage_pct": "",
             "sources": "report"},
            {"date": "2026-10-09", "storage_m3": "500", "capacity_m3": "5000", "incl_storage_m3": "1400",
             "incl_storage_pct": "15.0", "sources": "city_status"},
        ])
        self.chart = [
            {"date": "2026-10-01", "series": "incl_reserve", "storage_m3": "1000"},
            {"date": "2026-10-01", "series": "excl_reserve", "storage_m3": "400"},
            {"date": "2026-10-01", "series": "excl_reserve", "storage_m3": "420"},
            {"date": "2026-10-02", "series": "incl_reserve", "storage_m3": "990"},  # no excluded line
        ]
        self.incl = {d(i): (1000 + 10 * i, 9000) for i in range(1, 12)}

    def test_anchors(self):
        a = reserve.anchors(self.pub, self.chart, self.incl)
        self.assertEqual(sorted(a), [d(1), d(5), d(9)])
        self.assertEqual(a[d(1)]["reserve_m3"], 590)  # chart: 1000 - median(400, 420)
        self.assertEqual(a[d(5)]["reserve_m3"], 350)  # report: total of the day 1050 - 700
        self.assertEqual(a[d(9)]["reserve_m3"], 900)  # status page: its own total 1400 - 500

    def test_reserve_on(self):
        a = reserve.anchors(self.pub, self.chart, self.incl)
        self.assertIsNone(reserve.reserve_on(a, date(2026, 9, 30)))
        self.assertEqual(reserve.reserve_on(a, d(7))["reserve_m3"], 625)  # halfway from 350 to 900
        self.assertEqual(reserve.reserve_on(a, d(11))["source"], "carried from 2026-10-09")
        # Anchors after `until` are not seen: carried from 10/5 instead of interpolated.
        self.assertEqual(reserve.reserve_on(a, d(7), until=d(7))["reserve_m3"], 350)

    def test_history(self):
        h = reserve.history(self.incl, self.pub, reserve.anchors(self.pub, self.chart, self.incl))
        self.assertEqual(h[d(7)], {"storage_m3": 1070 - 625, "capacity_m3": 9000 - reserve.RESERVE_CAPACITY_M3,
                                   "published": False, "reserve_m3": 625})
        self.assertEqual((h[d(9)]["storage_m3"], h[d(9)]["capacity_m3"], h[d(9)]["published"]), (500, 5000, True))


if __name__ == "__main__":
    unittest.main()
