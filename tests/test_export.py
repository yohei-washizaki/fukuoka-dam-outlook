import tempfile
import unittest
from pathlib import Path

from dam_outlook import export


class ExportTest(unittest.TestCase):
    def test_copies_only_included_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, out = Path(tmp) / "repo", Path(tmp) / "out"
            for rel in ("src/dam_outlook/models.py", "src/dam_outlook/__pycache__/models.pyc",
                        "src/dam_outlook/assets/public_README.md", "tests/fixtures/city_dam_text.html",
                        "tests/fixtures/jma_p1m_010900.json", "data/raw/city_dam_text/a.html.gz",
                        "data/normalized/dam_storage.csv", "data/curated/events.csv",
                        "data/curated/event_reviews/x.md", "requirements.txt"):
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_text(rel, encoding="utf-8")
            (root / "data/curated/events.csv").write_text(
                "source\nhttps://www.city.fukuoka.lg.jp/...\nhttps://www.f-suiki.or.jp/x/\n", encoding="utf-8")
            (out / ".git").mkdir(parents=True)
            (out / "stale.txt").write_text("old", encoding="utf-8")
            export.export(root, out)
            got = sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file())
            self.assertEqual(got, [
                "README.md", "data/curated/events.csv", "data/normalized/dam_storage.csv",
                "requirements.txt", "src/dam_outlook/models.py", "tests/fixtures/jma_p1m_010900.json",
            ])
            self.assertTrue((out / ".git").is_dir())
            self.assertEqual((out / "data/curated/events.csv").read_text(encoding="utf-8"),
                             "source\nhttps://www.city.fukuoka.lg.jp/...\nhttps://www.f-suiki.or.jp/x/\n")


if __name__ == "__main__":
    unittest.main()
