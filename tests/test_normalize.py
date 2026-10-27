import csv
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from dam_outlook import normalize as n
from dam_outlook.raw_store import JST, RawStore

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


# Copies of Fukuoka City pages; left out of the public repository (dam_outlook.export).
needs_city_fixtures = unittest.skipUnless(
    all((FIXTURES / f).exists() for f in ("city_dam_text.html", "nippou_2026_10_21.txt")), "no city fixtures")


def amedas_records(start, hours, p1h=1.0, drop=()):
    """Point-file style records every 10 minutes; precipitation24h is left out."""
    data = {}
    t = start
    while t < start + timedelta(hours=hours):
        if t not in drop:
            rec = {"temp": [20.0, 0]}
            if t.minute == 0:
                rec["precipitation1h"] = [p1h, 0]
            data[f"{t:%Y%m%d%H%M%S}"] = rec
        t += timedelta(minutes=10)
    return data


class CityDamTextTest(unittest.TestCase):
    @needs_city_fixtures
    def test_parse(self):
        parsed = n.parse_city_dam_text(fixture("city_dam_text.html"))
        self.assertEqual(parsed["observed_at"], datetime(2026, 10, 23, 9, tzinfo=JST))
        dams = {d["dam_id"]: d for d in parsed["dams"]}
        self.assertEqual(len(dams), 10)
        self.assertEqual(dams["total"]["storage_pct"], 39.47)
        self.assertEqual(dams["total"]["storage_m3"], 30927000)
        self.assertEqual(dams["egawa"]["storage_pct"], 17.72)
        self.assertEqual(dams["gokayama"]["dam_name"], "五ケ山ダム")

    def test_missing_heading_raises(self):
        with self.assertRaises(n.ParseError):
            n.parse_city_dam_text("<html><table></table></html>")


class NippouTest(unittest.TestCase):
    @needs_city_fixtures
    def test_parse(self):
        parsed = n.parse_nippou_text(fixture("nippou_2026_10_21.txt"))
        self.assertEqual(parsed["report_date"], date(2026, 10, 21))
        dams = {d["dam_id"]: d for d in parsed["dams"]}
        self.assertEqual(len(dams), 10)
        self.assertEqual(dams["total"]["storage_pct"], 39.70)
        self.assertEqual(dams["total"]["change_m3"], -152000)
        self.assertIsNone(dams["total"]["precip_mm"])
        self.assertEqual(dams["inono"]["precip_mm"], 2.0)
        self.assertEqual(dams["egawa"]["precip_raw"], "-")
        self.assertEqual(dams["egawa"]["precip_mm"], 0.0)
        self.assertEqual(dams["egawa"]["capacity_m3"], 24000000)
        plants = {p["plant_id"]: p["volume_m3"] for p in parsed["plants"]}
        self.assertEqual(plants["total"], 404500)
        self.assertEqual(sum(v for k, v in plants.items() if k != "total"), plants["total"])

    @needs_city_fixtures
    def test_wrong_value_count_raises(self):
        text = fixture("nippou_2026_10_21.txt").replace("61.31", "")
        with self.assertRaises(n.ParseError):
            n.parse_nippou_text(text)


class JmaTest(unittest.TestCase):
    def test_p1m(self):
        rows = n.parse_jma_p1m(json.loads(fixture("jma_p1m_010900.json")))
        rain = [r for r in rows if r["element"] == "降水量"]
        self.assertEqual(len(rain), 1)
        self.assertEqual((rain[0]["below_pct"], rain[0]["normal_pct"], rain[0]["above_pct"]), (20, 30, 50))
        self.assertEqual(rain[0]["period"], "P1M")
        periods = [r["period"] for r in rows if r["element"] == "気温" and r["period"] != "P1M"]
        self.assertEqual(periods, ["P7D", "P7D", "P14D"])

    def test_weekly(self):
        parsed = n.parse_jma_weekly(json.loads(fixture("jma_forecast_400000.json")))
        self.assertEqual(len(parsed["pops"]), 7)
        self.assertIsNone(parsed["pops"][0]["pop_pct"])
        self.assertEqual(parsed["pops"][4], {
            "report_datetime": "2026-10-25T11:00:00+09:00", "area_code": "400000",
            "date": "2026-10-30", "pop_pct": 50, "reliability": "C",
        })
        self.assertEqual(parsed["precip_normals"][0]["max_mm"], 20.4)


class AmedasTest(unittest.TestCase):
    def test_prefers_24h_value(self):
        t = datetime(2026, 10, 22, 0, tzinfo=JST)
        records = {t: {"1h": 0.0, "24h": 13.5}}
        self.assertEqual(n.daily_precip(records, date(2026, 10, 21), 24), 13.5)

    def test_sums_hourly_when_24h_missing(self):
        start = datetime(2026, 10, 21, 0, 10, tzinfo=JST)
        records = n.parse_amedas_point(amedas_records(start, 24, p1h=0.5))
        self.assertEqual(n.daily_precip(records, date(2026, 10, 21), 24), 12.0)
        # 9to9 ending 10-21 needs 10-20 10:00 onwards
        self.assertIsNone(n.daily_precip(records, date(2026, 10, 21), 9))

    def test_gap_gives_none(self):
        start = datetime(2026, 10, 21, 0, 10, tzinfo=JST)
        gap = {datetime(2026, 10, 21, 5, tzinfo=JST)}
        records = n.parse_amedas_point(amedas_records(start, 24, drop=gap))
        self.assertIsNone(n.daily_precip(records, date(2026, 10, 21), 24))

    def test_bad_quality_is_missing(self):
        t = "20261022000000"
        records = n.parse_amedas_point({t: {"precipitation1h": [1.0, 5], "precipitation24h": [9.0, 5]}})
        self.assertEqual(list(records.values()), [{"1h": None, "24h": None}])


ETRN_S1 = """<table id='tablefix1' class='data2_s'>
<tr><th rowspan="4">日</th><th colspan="2">気圧(hPa)</th><th colspan="3">降水量(mm)</th><th>気温(℃)</th></tr>
<tr><th>現地</th><th>海面</th><th rowspan="2">合計</th><th colspan="2">最大</th><th>平均</th></tr>
<tr><td class="a_print"><a href="x">1</a></td><td>1010.9</td><td>1012.6</td><td>--</td><td>--</td><td>--</td><td>24.5</td></tr>
<tr><td>2</td><td>1009.0</td><td>1011.0</td><td>15.0 )</td><td>5.0</td><td>2.0</td><td>23.0</td></tr>
<tr><td>3</td><td>1009.0</td><td>1011.0</td><td>12.5 ]</td><td>5.0</td><td>2.0</td><td>23.0</td></tr>
<tr><td>4</td><td>1009.0</td><td>1011.0</td><td>×</td><td>×</td><td>×</td><td>23.0</td></tr>
</table>"""


class EtrnTest(unittest.TestCase):
    def test_s1_page(self):
        rows = n.parse_etrn_daily(ETRN_S1, "2025-09")
        self.assertEqual([(r["date"], r["precip_mm"]) for r in rows],
                         [("2025-09-01", 0.0), ("2025-09-02", 15.0), ("2025-09-03", None), ("2025-09-04", None)])

    def test_a1_page_has_precip_first(self):
        html = ("<table id='tablefix1'><tr><th>日</th><th colspan='3'>降水量</th></tr>"
                "<tr><td>1</td><td>13.5</td><td>13.0</td><td>10.5</td></tr></table>")
        self.assertEqual(n.parse_etrn_daily(html, "2025-09")[0]["precip_mm"], 13.5)

    def test_missing_table_raises(self):
        with self.assertRaises(n.ParseError):
            n.parse_etrn_daily("<html>maintenance</html>", "2025-09")


class ExcludedTotalTest(unittest.TestCase):
    @unittest.skipUnless((FIXTURES / "city_dam_status.html").exists(), "no city fixtures")
    def test_status_page(self):
        parsed = n.parse_city_dam_status(fixture("city_dam_status.html"))
        self.assertEqual(parsed["observed_at"], datetime(2026, 10, 27, 9, tzinfo=JST))
        self.assertEqual(parsed["excl"], {"storage_pct": 31.25, "storage_m3": 17211000})
        self.assertEqual(parsed["incl"], {"storage_pct": 42.25, "storage_m3": 33098000})

    def test_status_page_without_totals_raises(self):
        with self.assertRaises(n.ParseError):
            n.parse_city_dam_status("<h3>2026年10月27日 午前9時現在</h3><p>工事中</p>")

    def test_report_text(self):
        text = ("令 和 ８ 年 ９ 月\n生活環境委員会\n１ 水源状況\n９月29日午前９時時点の福岡市関連９ダムの合計貯水量"
                "（渇水対策容量分を除く）は\n 18,333千㎥、貯水率は34.2％\n")
        self.assertEqual(n.parse_report_excl(text), {"date": date(2026, 9, 29), "storage_m3": 18333000, "storage_pct": 34.2})
        jan = text.replace("９ 月", "1 月").replace("９月29日", "12月22日").replace("時点", "現在")
        self.assertEqual(n.parse_report_excl(jan)["date"], date(2025, 12, 22))

    def test_status_page_wins_over_report(self):
        tables = n.Tables()
        d = date(2026, 10, 27)
        n._merge_excl(tables, d, {"storage_pct": 31.2, "storage_m3": 17200000}, "report")
        n._merge_excl(tables, d, {"storage_pct": 31.25, "storage_m3": 17211000, "incl_storage_pct": 42.25,
                                  "incl_storage_m3": 33098000}, "city_status")
        n._merge_excl(tables, d, {"storage_pct": 31.2, "storage_m3": 17200000}, "report")
        row = tables.storage_excl["2026-10-27"]
        self.assertEqual((row["storage_m3"], row["capacity_m3"], row["sources"]), (17211000, 55071000, "city_status;report"))
        self.assertEqual(len(tables.warnings), 2)


class NormalizeRunTest(unittest.TestCase):
    @needs_city_fixtures
    def test_end_to_end_from_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw, out = Path(tmp) / "raw", Path(tmp) / "out"
            store = RawStore(raw)
            at = datetime(2026, 10, 25, 13, tzinfo=JST)
            store.save(source="city_dam_text", key="city_dam_text", subdir="x", name="001", ext="html",
                       url="u", fetched_at=at, status=200, body=fixture("city_dam_text.html").encode())
            store.save(source="jma_p1m", key="jma_p1m", subdir="x", name="p", ext="json",
                       url="u", fetched_at=at, status=200, body=fixture("jma_p1m_010900.json").encode())
            store.save(source="jma_forecast", key="jma_forecast", subdir="x", name="f", ext="json",
                       url="u", fetched_at=at, status=200, body=b"{broken")
            warnings = n.normalize(raw, out).warnings
            self.assertEqual(len(warnings), 1)
            self.assertIn("jma_forecast", warnings[0])
            with (out / "dam_storage.csv").open(encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 10)
            total = next(r for r in rows if r["dam_id"] == "total")
            self.assertEqual((total["date"], total["storage_pct"], total["sources"]), ("2026-10-23", "39.47", "city_text"))
            self.assertTrue((out / "jma_weekly_pop.csv").exists())

    def test_nippou_wins_on_conflict(self):
        tables = n.Tables()
        d = date(2026, 10, 23)
        n._merge_storage(tables, d, {"dam_id": "total", "dam_name": "合計", "storage_pct": 39.4, "storage_m3": 1}, "city_text")
        n._merge_storage(tables, d, {"dam_id": "total", "dam_name": "合計", "storage_pct": 39.47, "storage_m3": 1,
                                     "capacity_m3": 2, "change_m3": 0, "precip_mm": None, "precip_raw": ""}, "nippou")
        row = tables.dam_storage[("2026-10-23", "total")]
        self.assertEqual(row["storage_pct"], 39.47)
        self.assertEqual(row["sources"], "city_text;nippou")
        self.assertEqual(len(tables.warnings), 1)


HOURLY_CSV = (
    "観測時刻,南畑ダム,五ケ山ダム,脊振ダム,曲渕ダム,江川ダム,久原ダム,長谷ダム,猪野ダム,瑞梅寺ダム,合計,\r\n"
    "2026/10/21 10:00,2703,14050,1369,1975,4166,1047,3087,1950,747,31094,\r\n"
    "2026/10/21 09:00,2702,14058,1370,1977,4166,1047,3088,1950,748,31106,\r\n"
    "2026/10/20 09:00,2673,14142,1414,2010,4133,1048,3114,1974,751,31158,\r\n"
)


class DamHourlyCsvTest(unittest.TestCase):
    def test_9am_rows_with_total_summed(self):
        days = n.parse_dam_hourly_csv(HOURLY_CSV)
        self.assertEqual(sorted(days), [date(2026, 10, 20), date(2026, 10, 21)])
        total = {r["dam_id"]: r for r in days[date(2026, 10, 20)]}["total"]
        # the CSV's 合計 says 31158; the dams add up to 31259 like the daily report
        self.assertEqual((total["storage_m3"], total["capacity_m3"], total["storage_pct"]), (31259000, 76877000, 40.66))
        mh = {r["dam_id"]: r for r in days[date(2026, 10, 21)]}["minamihata"]
        self.assertEqual((mh["dam_name"], mh["capacity_m3"], mh["storage_pct"]), ("南畑ダム", 5120000, 52.77))

    def test_capacity_changes_after_flood_season(self):
        self.assertEqual(n.capacity_m3("total", date(2026, 10, 20)), 76877000)
        self.assertEqual(n.capacity_m3("total", date(2026, 10, 21)), 78347000)
        self.assertEqual(n.capacity_m3("total", date(2026, 5, 31)), 78347000)

    def test_unexpected_header_raises(self):
        with self.assertRaises(n.ParseError):
            n.parse_dam_hourly_csv("時刻,A,B\n")

    def test_ranked_below_daily_report(self):
        tables = n.Tables()
        d = date(2026, 10, 21)
        hourly = {r["dam_id"]: r for r in n.parse_dam_hourly_csv(HOURLY_CSV)[d]}["total"]
        n._merge_storage(tables, d, hourly, "bodik_hourly")
        n._merge_storage(tables, d, {"dam_id": "total", "dam_name": "合計", "storage_pct": 39.7, "storage_m3": 31107000,
                                     "capacity_m3": 78347000, "change_m3": -152000, "precip_mm": None, "precip_raw": ""}, "nippou")
        n._merge_storage(tables, d, hourly, "bodik_hourly")
        row = tables.dam_storage[("2026-10-21", "total")]
        self.assertEqual((row["storage_m3"], row["storage_pct"], row["change_m3"]), (31107000, 39.7, -152000))
        self.assertEqual(row["sources"], "bodik_hourly;nippou")
        self.assertEqual(tables.warnings, [])  # 1千m³ apart: a rounding step

        # A preliminary value 100千m³ off: a revision, noted without a warning.
        n._merge_storage(tables, d, {**hourly, "storage_m3": 31007000, "storage_pct": 39.58}, "bodik_hourly")
        self.assertEqual(row["storage_m3"], 31107000)
        self.assertEqual(tables.warnings, [])
        self.assertEqual(len(tables.notes), 1)
        self.assertIn("preliminary value revised", tables.notes[0])
        # Beyond PRELIMINARY_TOLERANCE_M3 it looks like a broken parse: warned.
        n._merge_storage(tables, d, {**hourly, "storage_m3": 30807000, "storage_pct": 39.32}, "bodik_hourly")
        self.assertEqual(len(tables.warnings), 1)

    def test_confirmed_sources_still_conflict(self):
        tables = n.Tables()
        d = date(2026, 10, 27)
        row = {"dam_id": "inono", "dam_name": "猪野ダム", "storage_pct": 54.08, "storage_m3": 1974000}
        n._merge_storage(tables, d, row, "bodik_hourly")
        n._merge_storage(tables, d, {**row, "storage_pct": 55.1, "storage_m3": 2011000}, "city_text")
        self.assertEqual((tables.warnings, len(tables.notes)), ([], 1))
        # A confirmed value differing from the one held (city_text) is a conflict even with bodik in the sources.
        n._merge_storage(tables, d, {**row, "storage_pct": 55.0, "storage_m3": 2008000, "capacity_m3": 3650000,
                                     "change_m3": 0, "precip_mm": None, "precip_raw": ""}, "nippou")
        self.assertEqual(len(tables.warnings), 1)


@unittest.skipUnless(__import__("importlib").util.find_spec("pypdf"), "pypdf not installed")
class PdfTextTest(unittest.TestCase):
    def test_raw_nippou_pdfs_parse(self):
        pdfs = sorted(Path("data/raw/city_nippou").glob("*/*.pdf"))[:3]
        if not pdfs:
            self.skipTest("no raw daily reports")
        for p in pdfs:
            parsed = n.parse_nippou_text(n.pdf_text(p.read_bytes()))
            self.assertEqual(len(parsed["dams"]), 10, p)


if __name__ == "__main__":
    unittest.main()
