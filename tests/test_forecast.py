import csv
import json
import random
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

from dam_outlook import forecast as fc
from dam_outlook import models, reserve
from dam_outlook.normalize import TABLE_COLUMNS
from dam_outlook.raw_store import JST


def write(path, columns, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in columns})


def storage_rows(days):
    """days: list of (date, storage_m3, change_m3, dam_precip or None)."""
    rows = []
    for d, s, ch, p in days:
        rows.append({"date": d, "dam_id": "total", "storage_m3": s, "storage_pct": round(s / 1000, 2),
                     "capacity_m3": 100000, "change_m3": ch, "sources": "nippou"})
        if p is not None:
            rows.append({"date": d, "dam_id": "egawa", "storage_m3": 1, "precip_mm": p,
                         "precip_raw": "-" if p == 0 else p})
    return rows


def excl_rows(days, reserve_m3=0, capacity=100000):
    """Printed totals excluding the reserve for (date, total storage, ...) tuples."""
    return [{"date": d, "storage_m3": s - reserve_m3, "capacity_m3": capacity - reserve.RESERVE_CAPACITY_M3,
             "incl_storage_m3": s, "sources": "city_status"} for d, s, *_ in days]


def no_reserve(test):
    """Run a test case with a zero reserve capacity, so the totals are the excluded totals."""
    patch = mock.patch.object(reserve, "RESERVE_CAPACITY_M3", 0)
    patch.start()
    test.addCleanup(patch.stop)


class BaselineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.norm = Path(self.tmp.name) / "normalized"
        self.out = Path(self.tmp.name) / "forecasts"
        no_reserve(self)

    def tearDown(self):
        self.tmp.cleanup()

    def write_storage(self, days, precip=(), excl=None):
        write(self.norm / "dam_storage.csv", TABLE_COLUMNS["dam_storage"], storage_rows(days))
        write(self.norm / "precip_daily.csv", TABLE_COLUMNS["precip_daily"], list(precip))
        write(self.norm / "dam_storage_excl.csv", TABLE_COLUMNS["dam_storage_excl"],
              excl_rows(days) if excl is None else excl)

    def test_rate_is_median_of_dry_days(self):
        self.write_storage([
            ("2026-10-19", 50000, -300, 0),
            ("2026-10-20", 49700, -100, 0),
            ("2026-10-21", 49600, 900, 12.0),
            ("2026-10-22", 50500, -200, 0),
        ])
        obs = models.load_observations(self.norm)
        r = models.baseline_norain(obs, date(2026, 10, 22), horizon=3)
        self.assertEqual(r["params"]["rate_m3_per_day"], -200)
        self.assertEqual(r["inputs"]["rate_pool"], "dry_days")
        self.assertEqual([x["storage_m3"] for x in r["forecast"]], [50300, 50100, 49900])
        self.assertEqual(r["forecast"][0], {"date": "2026-10-23", "horizon_days": 1, "storage_m3": 50300, "storage_pct": 50.3})

    def test_rate_never_positive_and_storage_floored(self):
        self.write_storage([("2026-10-20", 100, 500, 5.0), ("2026-10-21", 600, 500, 5.0)])
        r = models.baseline_norain(models.load_observations(self.norm), date(2026, 10, 21), horizon=2)
        self.assertEqual(r["inputs"]["rate_pool"], "all_days")
        self.assertEqual(r["params"]["rate_m3_per_day"], 0)

        self.write_storage([(f"2026-10-{d}", 300 - 100 * (d - 19), -100, 0) for d in (19, 20, 21)])
        r = models.baseline_norain(models.load_observations(self.norm), date(2026, 10, 21), horizon=3)
        self.assertEqual([x["storage_m3"] for x in r["forecast"]], [0, 0, 0])

    def test_reserve_subtracted_and_carried_forward(self):
        reserve.RESERVE_CAPACITY_M3 = 20000  # undone by the patch
        days = [("2026-10-23", 50000, -200, 0), ("2026-10-24", 49800, -200, 0), ("2026-10-25", 49600, -200, 0)]
        # Printed on Friday only: the reserve 30000 is carried to the weekend.
        self.write_storage(days, excl=excl_rows(days[:1], reserve_m3=30000))
        r = models.baseline_norain(models.load_observations(self.norm), date(2026, 10, 25), horizon=2)
        self.assertEqual(r["inputs"]["base_excl_storage_m3"], 19600)
        self.assertEqual((r["inputs"]["reserve_m3"], r["inputs"]["reserve_date"]), (30000, "2026-10-23"))
        self.assertEqual(r["inputs"]["reserve_source"], "carried from 2026-10-23")
        self.assertEqual((r["inputs"]["capacity_m3"], r["inputs"]["incl_capacity_m3"]), (80000, 100000))
        self.assertEqual([(x["storage_m3"], x["storage_pct"]) for x in r["forecast"]], [(19400, 24.25), (19200, 24.0)])

        # A printed value on the base date is used as is.
        self.write_storage(days, excl=excl_rows(days[:1], reserve_m3=30000) + excl_rows(days[2:], reserve_m3=29000))
        r = models.baseline_norain(models.load_observations(self.norm), date(2026, 10, 25), horizon=1)
        self.assertEqual((r["inputs"]["base_excl_storage_m3"], r["inputs"]["reserve_m3"]), (20600, 29000))

        # No reserve known up to the base date: no forecast.
        self.write_storage(days, excl=[])
        self.assertIsNone(models.baseline_norain(models.load_observations(self.norm), date(2026, 10, 25)))

    def test_amedas_dryness_and_diff_without_report(self):
        self.write_storage([("2026-10-24", 1000, None, None), ("2026-10-25", 900, None, None)],
                           precip=[{"date": "2026-10-25", "station_code": c, "precip_9to9_mm": 0.0}
                                   for c in models.AMEDAS_STATIONS])
        changes = models.daily_changes(models.load_observations(self.norm), date(2026, 10, 25), 14)
        self.assertEqual(changes, [{"date": "2026-10-25", "change_m3": -100, "dry": True, "dry_source": "amedas"}])

    def test_issue_is_immutable_and_skips_unchanged_inputs(self):
        days = [(f"2026-10-{d}", 1000 - 10 * (d - 19), -10, 0) for d in (19, 20, 21)]
        self.write_storage(days)
        now = datetime(2026, 10, 21, 14, 45, tzinfo=JST)
        written = fc.issue(self.norm, self.out, now)
        self.assertEqual(len(written), 1)
        doc = json.loads(written[0].read_text())
        self.assertEqual((doc["model"], doc["model_version"], doc["base_date"]), ("baseline_norain", "2", "2026-10-21"))
        self.assertEqual(doc["target"], "storage_excl_reserve_m3")
        self.assertEqual(len(doc["forecast"]), models.HORIZON_DAYS)

        later = datetime(2026, 10, 22, 0, 20, tzinfo=JST)
        self.assertEqual(fc.issue(self.norm, self.out, later), [])

        self.write_storage(days + [("2026-10-22", 960, -10, 0)])
        written = fc.issue(self.norm, self.out, later)
        self.assertEqual(len(written), 1)
        self.assertEqual(json.loads(written[0].read_text())["base_sources"], "nippou")

        # Same base date with changed inputs (e.g. a later run filling in rain): nothing new.
        self.write_storage(days + [("2026-10-22", 950, -20, 0)])
        self.assertEqual(fc.issue(self.norm, self.out, later + timedelta(hours=8)), [])


def synthetic_history(norm, days=420, end=date(2026, 10, 22)):
    """Rain and storage generated by a known v1 model: change = -2000 + 300 * rain(t-1) + 50 * slow(t)."""
    rng = random.Random(1)
    dates = [end - timedelta(days=days - 1 - i) for i in range(days)]
    rain = {d: (round(rng.uniform(0, 40), 1) if rng.random() < 0.3 else 0.0) for d in dates}
    slow = models._slow_states(rain)
    s, chart = 500000.0, []
    for d in dates[1:]:
        s += -2000 + 300 * rain[d - timedelta(days=1)] + 50 * slow[d]
        chart.append({"date": d.isoformat(), "series": "incl_reserve", "storage_m3": round(s), "capacity_m3": 2000000})
    write(norm / "dam_storage_chart.csv", ["date", "series", "storage_m3", "capacity_m3"], chart[:-1])
    write(norm / "jma_daily_precip.csv", TABLE_COLUMNS["jma_daily_precip"],
          [{"date": d.isoformat(), "station_code": c, "precip_mm": v} for d, v in rain.items() for c in models.AMEDAS_STATIONS])
    last = chart[-1]
    write(norm / "dam_storage.csv", TABLE_COLUMNS["dam_storage"],
          [{"date": last["date"], "dam_id": "total", "storage_m3": last["storage_m3"], "storage_pct": "",
            "capacity_m3": 2000000, "change_m3": "", "sources": "nippou"}])
    write(norm / "dam_storage_excl.csv", TABLE_COLUMNS["dam_storage_excl"],
          excl_rows([(last["date"], last["storage_m3"])], capacity=2000000))
    return rain


class ScenarioTest(unittest.TestCase):
    def setUp(self):
        no_reserve(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.norm = Path(self.tmp.name)
        self.rain = synthetic_history(self.norm)
        write(self.norm / "jma_p1m.csv", TABLE_COLUMNS["jma_p1m"],
              [{"report_datetime": "2026-10-22T14:30:00+09:00", "element": "降水量", "period": "P1M",
                "period_start": "2026-10-24T00:00:00+09:00", "below_pct": 0, "normal_pct": 0, "above_pct": 100},
               {"report_datetime": "2026-10-29T14:30:00+09:00", "element": "降水量", "period": "P1M",
                "period_start": "2026-10-31T00:00:00+09:00", "below_pct": 100, "normal_pct": 0, "above_pct": 0}])
        self.obs = models.load_observations(self.norm)
        self.base = date(2026, 10, 22)
        self.as_of = datetime(2026, 10, 23, 14, 45, tzinfo=JST)

    def tearDown(self):
        self.tmp.cleanup()

    def test_fit_recovers_known_response(self):
        fit = models.fit_storage_model(self.obs, self.base)
        self.assertAlmostEqual(fit["const_m3_per_day"], -2000, delta=1)
        self.assertEqual([round(q) for q in fit["quick_m3_per_mm"]], [0, 300, 0, 0])
        self.assertAlmostEqual(fit["slow_m3_per_mm"], 50, delta=0.5)
        self.assertAlmostEqual(fit["level_sigma_m3_per_day"], 0, delta=1)
        self.assertEqual(fit["training_last"], "2026-10-22")

    def test_too_little_history(self):
        self.assertIsNone(models.fit_storage_model(self.obs, date(2026, 2, 1)))

    def test_analog_windows_skip_the_base_year(self):
        self.obs["rain_daily"] = {date(y, 10, 1) + timedelta(days=i): 1.0 for y in (2024, 2025, 2026) for i in range(80)}
        windows = models.analog_windows(self.obs["rain_daily"], date(2026, 10, 22), 28)
        self.assertEqual({w["start"][:4] for w in windows}, {"2024", "2025"})
        self.assertTrue(all(len(w["rain_mm"]) == 28 for w in windows))

    def test_tercile_weights(self):
        windows = [{"total_mm": t} for t in (1, 2, 3, 4, 5, 6)]
        w = models._tercile_weights(windows, {"below": 60, "normal": 30, "above": 10})
        self.assertEqual(w, [30, 30, 15, 15, 5, 5])
        self.assertEqual([x["category"] for x in windows], ["below"] * 2 + ["normal"] * 2 + ["above"] * 2)

    def test_forecast_shape_and_reproducible(self):
        # The synthetic rain covers one year only, so give it a second year for analog windows.
        self.obs["rain_daily"].update({d - timedelta(days=365): v for d, v in self.rain.items()})
        r = models.scenario_jma_tercile(self.obs, self.base, self.as_of, horizon=10)
        self.assertEqual(r["params"]["category_weights"], {"below": 0, "normal": 0, "above": 100})
        self.assertEqual(r["inputs"]["p1m_precip"]["report_datetime"], "2026-10-22T14:30:00+09:00")
        self.assertEqual(sorted(r["inputs"]["observed_rain_mm"])[-1], "2026-10-22")
        for row in r["forecast"]:
            self.assertLessEqual(row["storage_m3_p10"], row["storage_m3_p50"])
            self.assertLessEqual(row["storage_m3_p50"], row["storage_m3_p90"])
            self.assertLessEqual(row["storage_m3_p90"], 2000000)
        # Day 1 depends on observed rain (lag 1 = the base day); the drawn rain only enters at lag 0 (~0 here).
        day1, day10 = r["forecast"][0], r["forecast"][-1]
        self.assertLess(day1["storage_m3_p90"] - day1["storage_m3_p10"], 1000)
        self.assertGreater(day10["storage_m3_p90"] - day10["storage_m3_p10"], 5000)
        self.assertEqual(models.scenario_jma_tercile(self.obs, self.base, self.as_of, horizon=10), r)

    def test_without_p1m_uses_equal_weights(self):
        self.obs["rain_daily"].update({d - timedelta(days=365): v for d, v in self.rain.items()})
        self.obs["p1m_precip"] = []
        r = models.scenario_jma_tercile(self.obs, self.base, self.as_of, horizon=3)
        self.assertEqual(r["params"]["category_weights"], {"below": 1, "normal": 1, "above": 1})

    def test_needs_history(self):
        self.obs["history"] = {}
        self.assertIsNone(models.scenario_jma_tercile(self.obs, self.base, self.as_of))


class HindcastTest(unittest.TestCase):
    def test_cut_hides_the_future_and_the_base_year(self):
        from dam_outlook import hindcast
        rain = {date(2025, 1, 1) + timedelta(days=i): 1.0 for i in range(800)}
        hist = {d: (1000, 2000) for d in rain}
        obs = {"total": {}, "dam_precip": {}, "amedas_9to9": {}, "p1m_precip": [{"x": 1}], "history": hist, "rain_daily": rain}
        base = date(2026, 3, 1)
        known = hindcast.cut(obs, base)
        self.assertEqual(max(known["history"]), base)
        self.assertEqual(max(known["total"]), base.isoformat())
        self.assertEqual(known["p1m_precip"], [])
        later = [d for d in known["rain_daily"] if d > base]
        self.assertTrue(later and all(d.year == 2027 for d in later))
