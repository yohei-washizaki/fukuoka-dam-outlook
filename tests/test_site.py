import csv
import re
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from dam_outlook import forecast, site
from dam_outlook.normalize import TABLE_COLUMNS
from dam_outlook.raw_store import JST

from test_forecast import storage_rows, synthetic_history, write


class SiteTest(unittest.TestCase):
    def test_empty_data_renders(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            data = site.load(tmp, tmp / "f", tmp / "v", tmp / "events.csv")
            page = site.render(data, datetime(2026, 10, 25, tzinfo=JST))
            self.assertIn("データがまだありません", page)
            self.assertIn("予測はまだ発行されていません", page)

    def test_page_with_forecasts(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            norm = tmp / "normalized"
            synthetic_history(norm, days=800)  # two years of rain and storage for the scenario model
            days = [(f"2026-10-{d}", 50000 - 100 * (d - 19), -100, 0) for d in (19, 20, 21, 22)]
            write(norm / "dam_storage.csv", TABLE_COLUMNS["dam_storage"], storage_rows(days))
            write(norm / "precip_daily.csv", TABLE_COLUMNS["precip_daily"],
                  [{"date": "2026-10-21", "station_code": c, "precip_9to9_mm": 10.0} for c in ("82182", "82171", "82191")])
            events = tmp / "events.csv"
            with events.open("w", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                w.writerow(["date", "kind", "detail", "chikugo_intake_restriction_pct", "source", "verified"])
                w.writerow(["2026-01-15", "intake_restriction", "取水制限 <15%>", "15", "", "false"])
                w.writerow(["2026-10-21", "intake_restriction", "取水制限 55%", "55", "", "true"])
            forecast.issue(norm, tmp / "f", datetime(2026, 10, 22, 14, 45, tzinfo=JST))
            (norm / "dam_storage_chart.csv").unlink()  # keep the history chart to the published days
            data = site.load(norm, tmp / "f", tmp / "v", events)
            page = site.render(data, datetime(2026, 10, 22, 15, tzinfo=JST))
            self.assertIn("モデル出力", page)
            self.assertIn("公表値", page)
            self.assertIn("&lt;15%&gt;", page)  # escaped
            self.assertIn("未確認", page)
            self.assertEqual(len(re.findall(r'<svg id="(outlook|history)"', page)), 2)
            # Shaded in both charts: 15% (unverified) before 10/21 and 55% from it. Labels shorten to fit.
            self.assertEqual(page.count('class="restrict"'), 4)
            labels = re.findall(r'restrict-label"[^>]*>([^<]*)</text>', page)
            self.assertEqual(sum(x in ("筑後川取水制限 15%（1/15〜）（未確認）", "15%") for x in labels), 2)
            self.assertIn('"筑後川取水制限": "55%"', page)
            self.assertIn("を下回る", page)
            self.assertIn("scenario_jma_tercile v1", page)
            self.assertIn("最小二乗法で推定", page)
            self.assertIn("2,000通り", page)
            self.assertNotIn("過去にさかのぼった答え合わせ", page)  # no hindcast.csv here
            self.assertIn('name="robots" content="noindex', page)
            self.assertIn('<link rel="icon" href="/favicon.svg"', page)
            self.assertIn('<link rel="apple-touch-icon" href="/apple-touch-icon.png"', page)
            self.assertEqual(re.findall(r'<a href="([^"]+)"', page), ["https://www.jma.go.jp/"])


if __name__ == "__main__":
    unittest.main()
