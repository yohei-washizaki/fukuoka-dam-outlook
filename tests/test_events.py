import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from dam_outlook import events as ev
from dam_outlook.raw_store import JST

ROW = {"date": "2026-09-17", "kind": "intake_restriction", "detail": "第3次渇水調整 筑後川取水制限 30%→55%",
       "chikugo_intake_restriction_pct": "55", "source": "https://example.org/a.html", "verified": "false",
       "date_basis": "effective"}

PAGE = """<html><body><nav>福岡地区水道企業団 水質管理目標 渇水調整</nav>
<article><p>令和８年９月 17 日</p><p>本日、第３次渇水調整において、筑後川取水制限量が
３０％から５５％へ強化されることとなった。</p></article></body></html>"""


class EventsTest(unittest.TestCase):
    def test_terms(self):
        t = ev.search_terms(ROW)
        self.assertEqual(t["日付"], ["9月17日", "9/17"])
        self.assertEqual(t["制限率"], ["55%"])
        self.assertIn("第3次", t["内容"])
        self.assertIn("取水制限", t["内容"])
        self.assertNotIn("制限率", ev.search_terms({**ROW, "chikugo_intake_restriction_pct": "0"}))

    def test_html_article_and_wrapped_text(self):
        pages = ev.source_pages(PAGE.encode())
        self.assertNotIn("水質管理目標", pages[0])
        found, lines = ev.excerpts(pages, ev.search_terms(ROW))
        self.assertEqual(found, {"日付": True, "制限率": True, "内容": True})
        self.assertIn("30%から55%へ強化", lines[0])

    def test_review_and_sync(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            csv_path = tmp / "events.csv"
            ev.write_events(list(ROW), [ROW, {**ROW, "date": "2026-09-03", "source": ""}], csv_path)
            _, rows = ev.read_events(csv_path)
            self.assertEqual([ev.event_id(r) for r in ev.pending(rows, tmp / "reviews")], ["2026-09-17_intake_restriction"])

            note = ev.review(rows[0], get=lambda url: (200, PAGE.encode()),
                             now=datetime(2026, 10, 25, 15, tzinfo=JST), raw_root=tmp / "raw", reviews=tmp / "reviews")
            text = note.read_text(encoding="utf-8")
            self.assertIn("event: 2026-09-17_intake_restriction", text)
            self.assertIn("見つかった: 制限率", text)
            self.assertIn("| date_basis | effective |", text)
            self.assertEqual(len(list((tmp / "raw").rglob("source__*.html.gz"))), 1)
            with self.assertRaises(FileExistsError):
                ev.review(rows[0], get=lambda url: (200, PAGE.encode()), raw_root=tmp / "raw", reviews=tmp / "reviews")

            self.assertEqual(ev.sync(csv_path, tmp / "reviews"), ["2026-09-17_intake_restriction"])
            _, rows = ev.read_events(csv_path)
            self.assertEqual([r["verified"] for r in rows], ["true", "false"])
            self.assertEqual(ev.sync(csv_path, tmp / "reviews"), [])

    def test_duplicate_ids_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "events.csv"
            ev.write_events(list(ROW), [ROW, {**ROW, "detail": "other"}], csv_path)
            with self.assertRaises(ValueError):
                ev.read_events(csv_path)

    def test_review_fails_on_http_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            with self.assertRaises(RuntimeError):
                ev.review(ROW, get=lambda url: (404, b""), raw_root=tmp / "raw", reviews=tmp / "reviews")
            self.assertFalse((tmp / "reviews").exists())


if __name__ == "__main__":
    unittest.main()
