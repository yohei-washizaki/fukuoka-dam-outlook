import gzip
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from dam_outlook import fetch
from dam_outlook.fetch import Fetcher
from dam_outlook.raw_store import JST, RawStore


class FakeWeb:
    def __init__(self, pages):
        self.pages = pages  # url -> (status, body)
        self.requested = []

    def __call__(self, url):
        self.requested.append(url)
        return self.pages.get(url, (404, b"not found"))


def amedas_block(t, complete=True):
    n = 18 if complete else 10
    return json.dumps(
        {f"{t + timedelta(minutes=10 * i):%Y%m%d%H%M%S}": {"precipitation10m": [0.0, 0]} for i in range(n)}
    ).encode()


class FetchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.clock = datetime(2026, 10, 26, 14, 0, tzinfo=JST)  # Monday

    def tearDown(self):
        self.tmp.cleanup()

    def fetcher(self, web):
        return Fetcher(RawStore(self.root), get=web, now=lambda: self.clock, delay=0)

    def test_snapshot_stored_gzipped_and_deduplicated(self):
        web = FakeWeb({fetch.CITY_DAM_TEXT_URL: (200, b"<html>39.47</html>")})
        f = self.fetcher(web)
        rec = f.fetch_city_dam_text()
        self.assertTrue(rec.stored)
        self.assertEqual(rec.path, "city_dam_text/2026/10/001__20261026T140000+0900.html.gz")
        self.assertEqual(gzip.decompress((self.root / rec.path).read_bytes()), b"<html>39.47</html>")

        self.clock += timedelta(hours=1)
        rec2 = self.fetcher(web).fetch_city_dam_text()
        self.assertFalse(rec2.stored)
        self.assertEqual(rec2.note, "unchanged")

        web.pages[fetch.CITY_DAM_TEXT_URL] = (200, b"<html>39.10</html>")
        self.clock += timedelta(hours=1)
        rec3 = self.fetcher(web).fetch_city_dam_text()
        self.assertTrue(rec3.stored)
        self.assertEqual(len(list((self.root / "city_dam_text/2026/10").iterdir())), 2)

        log = (self.root / "_log/2026-10.jsonl").read_text().splitlines()
        self.assertEqual(len(log), 3)

    def test_snapshot_error_is_logged_and_reported(self):
        f = self.fetcher(FakeWeb({fetch.JMA_P1M_URL: (None, b"timed out")}))
        self.assertIsNone(f.fetch_jma_p1m())
        self.assertEqual(len(f.errors), 1)
        entry = json.loads((self.root / "_log/2026-10.jsonl").read_text())
        self.assertFalse(entry["stored"])
        self.assertIsNone(entry["status"])

    def test_nippou_backfills_weekdays_once(self):
        url_fri = fetch.CITY_NIPPOU_URL.format(d=datetime(2026, 10, 23))
        url_mon = fetch.CITY_NIPPOU_URL.format(d=datetime(2026, 10, 26))
        web = FakeWeb({url_fri: (200, b"%PDF-1.7 fri")})
        f = self.fetcher(web)
        f.fetch_city_nippou()
        self.assertEqual(f.errors, [])
        self.assertTrue(all("_10_24" not in u and "_10_25" not in u for u in web.requested))
        self.assertIn(url_mon, web.requested)
        self.assertTrue((self.root / "city_nippou/2026/nippou_2026_10_23__20261026T140000+0900.pdf").exists())

        web.requested.clear()
        self.fetcher(web).fetch_city_nippou()
        self.assertNotIn(url_fri, web.requested)
        self.assertIn(url_mon, web.requested)

    def test_city_reports_fetched_once(self):
        sources = self.root / "sources.csv"
        sources.write_text("url,title\nhttps://example.org/docs/r1.pdf,a\nhttps://example.org/docs/r2.pdf,b\n", encoding="utf-8")
        web = FakeWeb({"https://example.org/docs/r1.pdf": (200, b"%PDF-1.7 r1")})
        f = self.fetcher(web)
        f.fetch_city_reports(sources)
        self.assertTrue((self.root / "city_reports/r1__20261026T140000+0900.pdf").exists())
        self.assertEqual(len(f.errors), 1)  # r2 is missing
        web.requested.clear()
        self.fetcher(web).fetch_city_reports(sources)
        self.assertEqual(web.requested, ["https://example.org/docs/r2.pdf"])

    def test_etrn_months_fetched_once_until_settled(self):
        url = lambda m: fetch.JMA_ETRN_DAILY_URL.format(kind="a", block="0785", year=2026, month=m)
        web = FakeWeb({url(8): (200, b"<table id='tablefix1'>aug</table>"), url(9): (200, b"<html>no table</html>")})
        f = self.fetcher(web)
        f.fetch_jma_etrn_daily(stations={"82171": ("a", "0785")}, start=(2026, 8))
        self.assertEqual(web.requested, [url(8), url(9)])  # October is not settled on 10/26
        self.assertTrue((self.root / "jma_etrn_daily/82171/2026-08__20261026T140000+0900.html.gz").exists())
        self.assertEqual(len(f.errors), 1)
        web.requested.clear()
        self.fetcher(web).fetch_jma_etrn_daily(stations={"82171": ("a", "0785")}, start=(2026, 8))
        self.assertEqual(web.requested, [url(9)])

    def test_nippou_non_pdf_is_error(self):
        url = fetch.CITY_NIPPOU_URL.format(d=datetime(2026, 10, 26))
        f = self.fetcher(FakeWeb({url: (200, b"<html>maintenance</html>")}))
        f.fetch_city_nippou(days=0)
        self.assertEqual(len(f.errors), 1)

    def test_amedas_fetches_complete_blocks_and_retries_incomplete(self):
        latest = datetime(2026, 10, 26, 6, 0, tzinfo=JST)
        pages = {fetch.JMA_AMEDAS_LATEST_URL: (200, latest.isoformat().encode())}
        blocks = [datetime(2026, 10, 25, h, tzinfo=JST) for h in range(0, 24, 3)]
        blocks.append(datetime(2026, 10, 26, 0, tzinfo=JST))
        for t in blocks:
            url = fetch.JMA_AMEDAS_POINT_URL.format(code="82182", t=t)
            pages[url] = (200, amedas_block(t, complete=t.hour != 21))
        web = FakeWeb(pages)
        f = self.fetcher(web)
        recs = f.fetch_amedas_points(stations=("82182",), days=1)
        self.assertEqual(f.errors, [])
        # 2026-10-26 03h block ends 05:50 <= 06:00 and is requested (404 here); 06h is not.
        requested = [u for u in web.requested if "/point/" in u]
        self.assertIn(fetch.JMA_AMEDAS_POINT_URL.format(code="82182", t=datetime(2026, 10, 26, 3)), requested)
        self.assertNotIn(fetch.JMA_AMEDAS_POINT_URL.format(code="82182", t=datetime(2026, 10, 26, 6)), requested)
        self.assertEqual(len(recs), 9)

        web.requested.clear()
        self.fetcher(web).fetch_amedas_points(stations=("82182",), days=1)
        requested = [u for u in web.requested if "/point/" in u]
        self.assertEqual(
            requested,
            [
                fetch.JMA_AMEDAS_POINT_URL.format(code="82182", t=datetime(2026, 10, 25, 21)),
                fetch.JMA_AMEDAS_POINT_URL.format(code="82182", t=datetime(2026, 10, 26, 3)),
            ],
        )

    def test_store_never_overwrites(self):
        store = RawStore(self.root)
        kw = dict(source="s", key="s", subdir="x", name="n", ext="txt", url="u",
                  fetched_at=self.clock, status=200, dedupe=False)
        store.save(body=b"a", **kw)
        with self.assertRaises(FileExistsError):
            store.save(body=b"b", **kw)


if __name__ == "__main__":
    unittest.main()
