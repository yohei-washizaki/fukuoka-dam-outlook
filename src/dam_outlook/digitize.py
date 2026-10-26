"""Read past total storage off the vector charts in Fukuoka City's water reports.

Run: ``PYTHONPATH=src python -m dam_outlook.digitize``

The water bureau's reports to the city council (水事情について) chart the total
storage of the 9 dams for each fiscal year (April to March), one line per year.
The charts are vector graphics, so the line vertices can be read from the PDF
and mapped back to values through the axis labels. The result is an estimate,
not a published value: it is kept in its own table
(``data/normalized/dam_storage_chart.csv``) and never mixed into
``dam_storage.csv``.

Checked against values printed in the same reports, the estimates are within
about 20 thousand m³ (0.03% of capacity).
"""

from __future__ import annotations

import argparse
import io
import re
import statistics
import sys
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from .normalize import write_csv
from .raw_store import RawStore

SOURCE = "city_reports"
METHOD = "chart-vector v1"
TITLE_KEY = "関連9ダムの貯水状況"  # the chart of storage including the drought reserve
SLOTS = 365  # the x axis has one slot per day of a 365-day fiscal year

COLUMNS = ["date", "series", "storage_m3", "capacity_m3", "fiscal_year", "report_url", "raw_path", "page", "method"]


@dataclass
class Stroke:
    points: list[tuple[float, float]]
    stroke: tuple
    width: float
    dashed: bool


@dataclass
class Word:
    text: str
    x: float
    y: float


@dataclass
class _State:
    ctm: tuple = (1, 0, 0, 1, 0, 0)
    stroke: tuple = (0,)
    width: float = 1.0
    dashed: bool = False


def _mul(m, n):
    """Matrix product m × n of PDF matrices (a b c d e f)."""
    a, b, c, d, e, f = m
    A, B, C, D, E, F = n
    return (a * A + b * C, a * B + b * D, c * A + d * C, c * B + d * D, e * A + f * C + E, e * B + f * D + F)


def _apply(m, x, y):
    a, b, c, d, e, f = m
    return a * x + c * y + e, b * x + d * y + f


def page_paths(page, reader) -> list[Stroke]:
    """Stroked paths of a page in page coordinates (curves reduced to their end points)."""
    from pypdf.generic import ContentStream

    out: list[Stroke] = []
    st, stack = _State(), []
    sub: list[tuple[float, float]] = []
    subs: list[list[tuple[float, float]]] = []
    for args, op in ContentStream(page.get_contents(), reader).operations:
        if op == b"q":
            stack.append(_State(st.ctm, st.stroke, st.width, st.dashed))
        elif op == b"Q" and stack:
            st = stack.pop()
        elif op == b"cm":
            st.ctm = _mul(tuple(float(v) for v in args), st.ctm)
        elif op == b"w":
            st.width = float(args[0])
        elif op == b"d":
            st.dashed = bool(args[0])
        elif op in (b"RG", b"G", b"K", b"SC", b"SCN"):
            st.stroke = tuple(round(float(v), 3) for v in args if not hasattr(v, "startswith"))
        elif op == b"m":
            if sub:
                subs.append(sub)
            sub = [_apply(st.ctm, float(args[0]), float(args[1]))]
        elif op in (b"l", b"c", b"v", b"y"):
            sub.append(_apply(st.ctm, float(args[-2]), float(args[-1])))
        elif op == b"re":
            x, y, w, h = (float(v) for v in args)
            subs.append([_apply(st.ctm, *p) for p in ((x, y), (x + w, y), (x + w, y + h), (x, y + h))])
        elif op in (b"S", b"s", b"B", b"B*", b"b", b"b*", b"f", b"F", b"f*", b"n"):
            if sub:
                subs.append(sub)
            if op in (b"S", b"s", b"B", b"B*", b"b", b"b*"):
                # The width scales with the CTM; charts here are not rotated.
                width = st.width * abs(st.ctm[0])
                out += [Stroke(s, st.stroke, width, st.dashed) for s in subs if len(s) >= 2]
            sub, subs = [], []
    return out


def page_words(page) -> list[Word]:
    """Text runs with the page position of their start."""
    words = []

    def visit(text, cm, tm, *_):
        t = unicodedata.normalize("NFKC", text).strip()
        if t:
            words.append(Word(t, *_apply(_mul(tm, cm), 0, 0)))

    page.extract_text(visitor_text=visit)
    return words


# ---------------------------------------------------------------- chart reading

NUM = re.compile(r"\d{1,3}(,\d{3})*")
YEAR = re.compile(r"R(\d{1,2})")


@dataclass
class Chart:
    y_a: float  # value = y_a + y_b * page_y (thousand m³)
    y_b: float
    x0: float  # page x of slot 0 and slot SLOTS - 1
    x1: float
    series: dict[tuple[int, bool], dict[int, float]] = field(default_factory=dict)  # (reiwa year, dashed) -> slot -> value

    def slot(self, x: float) -> int:
        return round((x - self.x0) / (self.x1 - self.x0) * (SLOTS - 1))


def _fit(pairs):
    xs, ys = zip(*pairs)
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    b = sum((x - mx) * (y - my) for x, y in pairs) / sum((x - mx) ** 2 for x in xs)
    return my - b * mx, b


def axis_ladders(words: list[Word]) -> list[list[tuple[float, float]]]:
    """y-axis labels (value, page y) of each chart: right-aligned numbers rising evenly upwards."""
    groups: dict[int, list[Word]] = {}
    for w in words:
        if NUM.fullmatch(w.text):
            # Labels are right-aligned, so group on their (approximate) right edge.
            groups.setdefault(round(w.x + 4.5 * len(w.text)), []).append(w)
    ladders = []
    for ws in groups.values():
        run: list[tuple[float, float]] = []
        for v, y in sorted({(int(w.text.replace(",", "")), w.y) for w in ws}, key=lambda p: p[1]):
            if run and v <= run[-1][0]:
                if len(run) >= 4:
                    ladders.append(run)
                run = []
            run.append((v, y))
        if len(run) >= 4:
            ladders.append(run)
    return ladders


def read_chart(paths: list[Stroke], words: list[Word], ladder: list[tuple[float, float]]) -> Chart | None:
    lo_y, hi_y = min(y for _, y in ladder), max(y for _, y in ladder)
    # Labels sit about 3 pt below their grid line; snap each to the nearest long horizontal line.
    grid = [p.points[0][1] for p in paths
            if len(p.points) == 2 and abs(p.points[0][1] - p.points[1][1]) < 0.01
            and abs(p.points[0][0] - p.points[1][0]) > 100]
    pairs = []
    for v, y in ladder:
        near = [g for g in grid if abs(g - (y + 3)) < 5]
        pairs.append((min(near, key=lambda g: abs(g - (y + 3))) if near else y + 3, v))
    y_a, y_b = _fit(pairs)
    margin = (hi_y - lo_y) / max(len(ladder) - 1, 1)
    inside = [p for p in paths if all(lo_y - margin <= y <= hi_y + margin for _, y in p.points)]
    groups: dict[tuple, list[tuple[float, float]]] = {}
    for p in inside:
        if len(p.points) >= 3 and p.width >= 1.2:
            groups.setdefault((p.stroke, p.dashed), []).extend(p.points)
    full = [pts for pts in groups.values() if len({round(x, 1) for x, _ in pts}) >= SLOTS - 2]
    if not full:
        return None
    x0 = min(x for pts in full for x, _ in pts)
    x1 = max(x for pts in full for x, _ in pts)
    chart = Chart(y_a, y_b, x0, x1)
    # Lines that change over the year are data; a flat line is the capacity.
    data = {k: pts for k, pts in groups.items() if statistics.pstdev(y for _, y in pts) > 0.5}
    labels = [(int(YEAR.fullmatch(w.text).group(1)), w) for w in words
              if YEAR.fullmatch(w.text) and x0 - 5 <= w.x <= x1 + 40 and lo_y - margin <= w.y <= hi_y + margin]
    # Each year label names the line nearest to it; labels repeat for the dashed line.
    pairs = sorted(
        (min((x - w.x - 6) ** 2 + (y - w.y - 3) ** 2 for x, y in pts), year, key)
        for year, w in labels for key, pts in data.items()
    )
    used_keys, used_labels = set(), set()
    for _, year, key in pairs:
        if key in used_keys or (year, key[1]) in used_labels:
            continue
        used_keys.add(key)
        used_labels.add((year, key[1]))
        chart.series[(year, key[1])] = {chart.slot(x): round(y_a + y_b * y, 1) for x, y in data[key]}
    return chart


def fiscal_dates(reiwa: int) -> list[date | None]:
    """Dates of the chart slots of fiscal year Reiwa ``reiwa``; slots after 2/28 are unknown in leap years."""
    start = date(2018 + reiwa, 4, 1)
    days = [start + timedelta(days=i) for i in range(SLOTS)]
    leap = (date(start.year + 1, 3, 1) - date(start.year + 1, 2, 28)).days == 2
    return [d if not (leap and d >= date(start.year + 1, 2, 29)) else None for d in days]


CAPACITY = re.compile(r"\((\d+)/(\d+)~(\d+)/(\d+)\)利水容量([\d,]+)千m3")


def capacity_schedule(text: str) -> list[tuple[tuple[int, int], tuple[int, int], int]]:
    """Seasonal capacities printed above the chart, e.g. (6/1~10/20)利水容量76,877千㎥, in m³."""
    flat = re.sub(r"\s", "", unicodedata.normalize("NFKC", text))
    return [((int(m1), int(d1)), (int(m2), int(d2)), int(v.replace(",", "")) * 1000)
            for m1, d1, m2, d2, v in CAPACITY.findall(flat)]


def capacity_on(schedule, d: date) -> int | None:
    md = (d.month, d.day)
    for start, end, v in schedule:
        if (start <= md <= end) if start <= end else (md >= start or md <= end):
            return v
    return None


def digitize_pdf(body: bytes) -> list[dict]:
    """Rows (date, series, storage_m3, fiscal_year, page) for every 9-dam chart in a report.

    Solid lines include the drought reserve, dashed ones exclude it (as labelled in the reports).
    """
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(body))
    rows = []
    for n, page in enumerate(reader.pages, 1):
        words = page_words(page)
        titles = [w.y for w in words if TITLE_KEY in w.text]
        if not titles:
            continue
        # The 9-dam chart is the axis whose top label is nearest below the title.
        ladders = [l for l in axis_ladders(words) if l[-1][1] < titles[0]]
        if not ladders:
            continue
        ladder = max(ladders, key=lambda l: l[-1][1])
        paths = page_paths(page, reader)
        # The schedule is the one in force when the report was written; earlier years are assumed the same.
        schedule = capacity_schedule(page.extract_text())
        chart = read_chart(paths, words, ladder)
        if not chart:
            continue
        for (year, dashed), values in sorted(chart.series.items()):
            dates = fiscal_dates(year)
            for slot, v in sorted(values.items()):
                if 0 <= slot < SLOTS and dates[slot]:
                    rows.append({
                        "date": dates[slot].isoformat(),
                        "series": "excl_reserve" if dashed else "incl_reserve",
                        "storage_m3": round(v) * 1000,
                        "capacity_m3": capacity_on(schedule, dates[slot]) if dashed is False else None,
                        "fiscal_year": f"R{year}",
                        "page": n,
                    })
    return rows


def digitize(raw_dir: Path, out_path: Path) -> list[dict]:
    store = RawStore(raw_dir)
    rows = []
    for e in store.log_entries():
        if e.get("source") != SOURCE or not e.get("stored"):
            continue
        for r in digitize_pdf(store.read_body(e["path"])):
            rows.append({**r, "report_url": e["url"], "raw_path": e["path"], "method": METHOD})
    rows.sort(key=lambda r: (r["date"], r["series"], r["raw_path"]))
    write_csv(out_path, COLUMNS, rows)
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    parser.add_argument("--out", type=Path, default=Path("data/normalized/dam_storage_chart.csv"))
    args = parser.parse_args(argv)
    rows = digitize(args.raw_dir, args.out)
    print(f"{len(rows)} chart points from {len({r['raw_path'] for r in rows})} reports")
    return 0


if __name__ == "__main__":
    sys.exit(main())
