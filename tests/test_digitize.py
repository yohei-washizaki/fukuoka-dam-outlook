import io
import unittest
from datetime import date
from pathlib import Path

from dam_outlook import digitize as dg

REPORT = sorted(Path("data/raw/city_reports").glob("260930_suido-hokoku__*.pdf"))


def chart_pdf() -> bytes:
    """A one-page PDF with an axis 0-30 (thousand m³), a full-year line from 10 to 20 and a flat line at 25."""
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    ops = ["q 0.851 G 0.75 w"]
    for i in range(4):  # grid every 10 at y = 100, 130, 160, 190
        ops.append(f"100 {100 + 30 * i} m 500 {100 + 30 * i} l S")
    ops.append("Q q 1 0 0 RG 1.5 w 100 130 m")
    ops += [f"{100 + 400 * i / 364:.2f} {130 + 30 * i / 364:.2f} l" for i in range(1, 365)]
    ops.append("S Q q 0 G 1.5 w 100 175 m")
    ops += [f"{100 + 400 * i / 364:.2f} 175 l" for i in range(1, 365)]
    ops.append("S Q")
    for i, label in enumerate(["0", "10,000", "20,000", "30,000"]):
        ops.append(f"BT /F1 9 Tf {95 - 4.5 * len(label):.1f} {97 + 30 * i} Td ({label}) Tj ET")
    ops.append("BT /F1 9 Tf 506 155 Td (R7) Tj ET")
    w = PdfWriter()
    page = w.add_blank_page(600, 300)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): w._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data("\n".join(ops).encode())
    page[NameObject("/Contents")] = w._add_object(stream)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


class DigitizeTest(unittest.TestCase):
    def test_fiscal_dates(self):
        r6 = dg.fiscal_dates(6)  # FY2024: April 2024 to March 2025, no leap day
        self.assertEqual((r6[0], r6[-1]), (date(2024, 4, 1), date(2025, 3, 31)))
        r5 = dg.fiscal_dates(5)  # FY2023 has 2024-02-29: slots from there on are ambiguous
        self.assertEqual(r5[333], date(2024, 2, 28))
        self.assertIsNone(r5[334])

    def test_read_synthetic_chart(self):
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(chart_pdf()))
        page = reader.pages[0]
        words, paths = dg.page_words(page), dg.page_paths(page, reader)
        ladders = dg.axis_ladders(words)
        self.assertEqual([[v for v, _ in l] for l in ladders], [[0, 10000, 20000, 30000]])
        chart = dg.read_chart(paths, words, ladders[0])
        self.assertEqual(list(chart.series), [(7, False)])  # the flat line is the capacity, not data
        values = chart.series[(7, False)]
        self.assertAlmostEqual(values[0], 10000, delta=5)
        self.assertAlmostEqual(values[364], 20000, delta=5)

    @unittest.skipUnless(REPORT, "report PDF not in data/raw")
    def test_report_matches_printed_values(self):
        rows = dg.digitize_pdf(REPORT[0].read_bytes())
        got = {(r["date"], r["series"]): r["storage_m3"] for r in rows}
        # Values printed in the same report (thousand m³).
        for d, series, printed in [("2026-09-29", "incl_reserve", 33914), ("2026-09-29", "excl_reserve", 18333)]:
            self.assertAlmostEqual(got[(d, series)], printed * 1000, delta=30000)
        self.assertEqual({r["fiscal_year"] for r in rows}, {"R5", "R6", "R7", "R8"})


if __name__ == "__main__":
    unittest.main()
