import csv
import re
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from dam_outlook import forecast, site
from dam_outlook.normalize import TABLE_COLUMNS
from dam_outlook.raw_store import JST

from test_forecast import excl_rows, no_reserve, storage_rows, synthetic_history, write


class SiteTest(unittest.TestCase):
    def test_empty_data_renders(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            data = site.load(tmp, tmp / "f", tmp / "v", tmp / "events.csv")
            page = site.render(data, datetime(2026, 10, 25, tzinfo=JST))
            self.assertIn("データがまだありません", page)
            self.assertIn("予想はまだ発行されていません", page)
            self.assertNotIn('class="water"', page)  # no published rate, no water

    def test_page_with_forecasts(self):
        no_reserve(self)
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            norm = tmp / "normalized"
            synthetic_history(norm, days=800)  # two years of rain and storage for the scenario model
            days = [(f"2026-10-{d}", 50000 - 100 * (d - 19), -100, 0) for d in (19, 20, 21, 22)]
            write(norm / "dam_storage.csv", TABLE_COLUMNS["dam_storage"], storage_rows(days))
            write(norm / "dam_storage_excl.csv", TABLE_COLUMNS["dam_storage_excl"], excl_rows(days))
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
            # Each chart is drawn wide and phone-sized; CSS shows one.
            self.assertEqual(re.findall(r'<svg id="([^"]+)"', page), ["outlook", "outlook-n", "history", "history-n"])
            # Shaded in all four: 15% (unverified) before 10/21 and 55% from it. Labels shorten to fit.
            self.assertEqual(page.count('class="restrict"'), 8)
            labels = re.findall(r'restrict-label"[^>]*>([^<]*)</text>', page)
            # The 15% segment of the phone-sized outlook chart is too narrow for any label.
            self.assertEqual(sum(x in ("取水制限 15%（1/15〜）（未確認）", "取水制限 15%（未確認）", "15%") for x in labels), 3)
            # Summary tiles: the published rate, then the model's 28-day forecast.
            self.assertEqual(re.findall(r'<div class="stat (\w+)">', page), ["fact", "model"])
            self.assertIn("前回（10/21）から", page)
            # The background fills to the latest published rate.
            latest = data["excl"][site.published_excl(data)[-1]]["pct"]
            self.assertIn(f'<div class="fill" style="--level:{latest:.2f}">', page)
            self.assertIn("4週間後（11/19）の予想", page)
            self.assertIn('<p class="headline"><span class="sent"><span class="ph">4週間後（11/19）の</span><span class="ph">貯水率は、</span>', page)
            self.assertIn('でしょう。</span>', page)
            self.assertIn('"筑後川取水制限": "55%"', page)
            self.assertIn("を下回る", page)
            self.assertIn("scenario_jma_tercile v2", page)
            self.assertIn("最小二乗法で推定", page)
            self.assertIn("2,000通り", page)
            self.assertNotIn("過去にさかのぼった検証", page)  # no hindcast.csv here
            self.assertNotIn('name="robots"', page)  # indexed since 2026-10-26
            self.assertIn('<link rel="icon" href="favicon.svg"', page)
            self.assertIn('<link rel="apple-touch-icon" href="apple-touch-icon.png"', page)
            self.assertEqual(re.findall(r'<a href="([^"]+)"', page), ["https://www.jma.go.jp/", "https://github.com/yohei-washizaki/fukuoka-dam-outlook"])

    def test_excl_lines(self):
        def v(pct, published=False, sources=""):
            return {"pct": pct, "published": published, "sources": sources}
        excl = {"2026-10-22": v(30.0), "2026-10-23": v(30.5, True, "report"), "2026-10-24": v(31.0),
                "2026-10-26": v(32.0, True, "city_status"), "2026-10-27": v(32.5, True, "city_status"),
                "2026-10-28": v(32.4), "2026-10-29": v(32.3)}
        lines = site.excl_lines({"excl": excl}, date(2026, 10, 23))
        before, after, solid = lines
        # Estimates up to the first printed status-page day, the printed days solid, estimates after.
        self.assertEqual([d for d, _ in before["points"]], ["2026-10-23", "2026-10-24", "2026-10-26"])
        self.assertFalse(before["hover_last"])
        self.assertEqual([d for d, _ in solid["points"]], ["2026-10-26", "2026-10-27"])
        self.assertEqual([d for d, _ in after["points"]], ["2026-10-27", "2026-10-28", "2026-10-29"])
        self.assertFalse(after["hover_first"])
        # Without the status page everything is an estimate.
        only = site.excl_lines({"excl": {k: x for k, x in excl.items() if k < "2026-10-26"}}, date(2026, 10, 1))
        self.assertEqual([l["name"] for l in only], ["推定値"])

    def test_headline_trend(self):
        # Within FLAT_PT (inclusive) is flat; beyond it, up or down.
        self.assertEqual(site.trend_phrase(39.47, 41.47)[0], "ほぼ横ばいの")
        self.assertEqual(site.trend_phrase(39.47, 37.47)[0], "ほぼ横ばいの")
        self.assertEqual(site.trend_phrase(39.47, 41.6)[1], "上がるでしょう。")
        self.assertEqual(site.trend_phrase(39.47, 37.3)[1], "下がるでしょう。")
        self.assertEqual(site.trend_phrase(39.47, 40.0)[-1], "なるでしょう。")
        self.assertEqual(site.no_rain_phrase(39.47, 32.4)[0], "雨が降らなければ、")
        self.assertEqual(site.no_rain_phrase(39.47, 32.4)[-1], "下がります。")
        # Lines break only between phrases.
        self.assertEqual(site.phrases("4週間後は", "予想しています。"),
                         '<span class="sent"><span class="ph">4週間後は</span><span class="ph">予想しています。</span></span>')


if __name__ == "__main__":
    unittest.main()
