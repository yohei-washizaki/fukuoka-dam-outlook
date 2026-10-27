"""Normalize stored raw responses into daily CSV tables under ``data/normalized/``.

Run: ``PYTHONPATH=src python -m dam_outlook.normalize``

The tables are derived data: they are rebuilt from ``data/raw/`` on every run
and hold only published values (no model output). Parsers are separate pure
functions so that a format change upstream breaks one table, not the run.
"""

from __future__ import annotations

import argparse
import csv
import html
import io
import json
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path

from .raw_store import JST, RawStore

DAM_IDS = {
    "合計": "total",
    "南畑": "minamihata",
    "五ケ山": "gokayama",
    "脊振": "sefuri",
    "曲渕": "magarifuchi",
    "江川": "egawa",
    "久原": "kubara",
    "長谷": "nagatani",
    "猪野": "inono",
    "瑞梅寺": "zuibaiji",
}

PLANT_IDS = {
    "高宮浄水場": "takamiya",
    "夫婦石浄水場": "fufuishi",
    "乙金浄水場": "otogana",
    "多々良浄水場": "tatara",
    "瑞梅寺浄水場": "zuibaiji",
    "下原配水場": "shimobaru",
    "合計": "total",
}


class ParseError(ValueError):
    pass


def _squash(s: str) -> str:
    """NFKC-normalize and drop all whitespace (the PDFs space out every character)."""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s))


def dam_id(name: str) -> str:
    key = _squash(name).replace("ヶ", "ケ").removesuffix("ダム")
    return DAM_IDS.get(key, key)


def _number(token: str) -> float:
    return float(token.replace(",", ""))


# ---------------------------------------------------------------- city text page


class _TableText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows: list[list[str]] = []
        self.headings: list[str] = []
        self._cell: list[str] | None = None
        self._heading: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.rows.append([])
        elif tag in ("td", "th"):
            self._cell = []
        elif tag in ("h1", "h2", "h3"):
            self._heading = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            if self.rows:
                self.rows[-1].append("".join(self._cell).strip())
            self._cell = None
        elif tag in ("h1", "h2", "h3") and self._heading is not None:
            self.headings.append("".join(self._heading).strip())
            self._heading = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)
        if self._heading is not None:
            self._heading.append(data)


def parse_city_dam_text(html: str) -> dict:
    """Parse きょうのダム状況（テキスト版）. Returns observed_at and per-dam rows."""
    p = _TableText()
    p.feed(html)
    observed_at = None
    for h in p.headings:
        m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日(\d{1,2})時現在", _squash(h))
        if m:
            y, mo, d, hh = map(int, m.groups())
            observed_at = datetime(y, mo, d, hh, tzinfo=JST)
            break
    if observed_at is None:
        raise ParseError("observation time heading not found")

    dams = []
    for cells in p.rows:
        texts = [_squash(c) for c in cells]
        if "貯水率" not in texts or "貯水量" not in texts:
            continue
        name = texts[0]
        rate = texts[texts.index("貯水率") + 1]
        volume = texts[texts.index("貯水量") + 1]
        m_rate = re.fullmatch(r"([\d.]+)%", rate)
        m_vol = re.fullmatch(r"([\d,]+)立方メートル", volume)
        if not (m_rate and m_vol):
            raise ParseError(f"unexpected row {cells!r}")
        dams.append(
            {
                "dam_id": dam_id(name),
                "dam_name": name,
                "storage_pct": _number(m_rate.group(1)),
                "storage_m3": int(_number(m_vol.group(1))),
            }
        )
    if not dams:
        raise ParseError("no dam rows found")
    return {"observed_at": observed_at, "dams": dams}


# ---------------------------------------------------------------- city status page (drought reserve)


def _page_text(body: str) -> str:
    return _squash(html.unescape(re.sub(r"<[^>]*>", " ", body)))


def parse_city_dam_status(body: str) -> dict:
    """Parse きょうのダム状況: the 9-dam total excluding and including the drought reserve."""
    text = _page_text(body)
    m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日午前(\d{1,2})時現在", text)
    if not m:
        raise ParseError("observation time not found")
    y, mo, d, hh = map(int, m.groups())
    out = {"observed_at": datetime(y, mo, d, hh, tzinfo=JST)}
    for key, label in (("excl", "除く"), ("incl", "含む")):
        v = re.search(rf"渇水対策容量分{label}9ダム合計の貯水率([\d.]+)%9ダム合計の貯水量([\d,]+)千立方メートル", text)
        if not v:
            raise ParseError(f"total {label} not found")
        out[key] = {"storage_pct": _number(v.group(1)), "storage_m3": int(_number(v.group(2))) * 1000}
    return out


def parse_report_excl(text: str) -> dict:
    """The 9-dam total excluding the drought reserve printed in a 水事情について report."""
    flat = _squash(text)
    m = re.search(r"(\d{1,2})月(\d{1,2})日午前9時(?:現在|時点)の福岡市関連9ダムの合計貯水量"
                  r"\(渇水対策容量分を除く\)は([\d,]+)千m3、貯水率は([\d.]+)%", flat)
    era = re.search(r"令和(\d+)年(\d+)月", flat)
    if not (m and era):
        raise ParseError("excluded total not found")
    mo, d = int(m.group(1)), int(m.group(2))
    year = 2018 + int(era.group(1))
    if mo > int(era.group(2)):  # a January report on a December day
        year -= 1
    return {"date": date(year, mo, d), "storage_m3": int(_number(m.group(3))) * 1000,
            "storage_pct": _number(m.group(4))}


# ---------------------------------------------------------------- daily report PDF


def pdf_text(body: bytes) -> str:
    """Layout-preserving text of a PDF (requires pypdf)."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(body))
    return "\n".join(p.extract_text(extraction_mode="layout") for p in reader.pages)


def _row_values(line: str) -> list[str]:
    """Value tokens after the unit label, e.g. '... (m3) 5,120,000 31,700,000'."""
    line = unicodedata.normalize("NFKC", line)
    tail = re.split(r"\)", line)[-1]
    tokens = tail.split()
    for t in tokens:
        if t != "-" and not re.fullmatch(r"-?[\d,]+(\.\d+)?", t):
            raise ParseError(f"unexpected token {t!r} in {line.strip()!r}")
    return tokens


def parse_nippou_text(text: str) -> dict:
    """Parse the text of 浄水日報. Returns report date, per-dam and per-plant rows."""
    flat = _squash(text)
    m = re.search(r"令和(\d+)年(\d+)月(\d+)日", flat)
    if not m:
        raise ParseError("report date not found")
    r, mo, d = map(int, m.groups())
    report_date = date(2018 + r, mo, d)

    lines = text.splitlines()
    squashed = [_squash(l) for l in lines]

    def find(pred, start: int = 0) -> int:
        for i in range(start, len(squashed)):
            if pred(squashed[i]):
                return i
        raise ParseError("line not found")

    # Dam table: header names, then labelled rows.
    hi = find(lambda s: "南畑" in s and s.endswith("合計"))
    header = squashed[hi].removeprefix("ダム")
    dam_names = re.findall(r".+?ダム|合計", header)
    labels = {
        "precip": "降水量(mm)",
        "capacity": "A利水容量",
        "storage_m3": "B現在利水量",
        "storage_pct": "貯水率",
        "change_m3": "増減水量",
    }
    values = {}
    for key, label in labels.items():
        i = find(lambda s, label=label: s.startswith(label), hi + 1)
        values[key] = _row_values(lines[i])
    n = len(dam_names)
    for key, toks in values.items():
        expected = (n - 1, n) if key == "precip" else (n,)
        if len(toks) not in expected:
            raise ParseError(f"{key}: {len(toks)} values for {n} dams")

    dams = []
    for j, name in enumerate(dam_names):
        precip_raw = values["precip"][j] if j < len(values["precip"]) else ""
        dams.append(
            {
                "dam_id": dam_id(name),
                "dam_name": name,
                "storage_pct": _number(values["storage_pct"][j]),
                "storage_m3": int(_number(values["storage_m3"][j])),
                "capacity_m3": int(_number(values["capacity"][j])),
                "change_m3": int(_number(values["change_m3"][j])),
                "precip_raw": precip_raw,
                # "-" is read as no rain (interpretation, not stated in the report).
                "precip_mm": 0.0 if precip_raw == "-" else (_number(precip_raw) if precip_raw else None),
            }
        )

    # Distribution: plant names, then the 配水量 row (m3 per day, previous 9:00 to 9:00).
    pi = find(lambda s: "浄水場" in s and s.endswith("合計"))
    plant_names = re.findall(r".+?(?:浄水場|配水場)|合計", squashed[pi])
    vi = find(lambda s: s.startswith("配水量") and any(c.isdigit() for c in s))
    vols = lines[vi].split()
    vols = [t for t in vols if re.fullmatch(r"[\d,]+", t)]
    if len(vols) != len(plant_names):
        raise ParseError(f"distribution: {len(vols)} values for {len(plant_names)} plants")
    plants = [
        {"plant_id": PLANT_IDS.get(nm, nm), "plant_name": nm, "volume_m3": int(_number(v))}
        for nm, v in zip(plant_names, vols)
    ]
    return {"report_date": report_date, "dams": dams, "plants": plants}


# ---------------------------------------------------------------- JMA forecasts


def parse_jma_p1m(data: dict) -> list[dict]:
    """Three-category probabilities (below/normal/above) of the 1-month forecast."""
    report = data["reportDatetime"]
    target = data["targetDatetime"]
    rows = []
    for info in data.get("metInfos", []):
        for item in info.get("items", []):
            if "kind" in item and "below" in item:
                rows.append(
                    {
                        "report_datetime": report,
                        "area_code": item.get("areaCode", ""),
                        "element": item["kind"],
                        "period_start": target,
                        "period": data.get("targetDuration", "P1M"),
                        "below_pct": int(item["below"]),
                        "normal_pct": int(item["normal"]),
                        "above_pct": int(item["above"]),
                    }
                )
    starts = data.get("timeDefines", [])
    for info in data.get("timeInfos", []):
        for p in info.get("probabilities", []):
            k = int(p["refId"]) - 1
            start = starts[k]
            if k + 1 < len(starts):
                days = (datetime.fromisoformat(starts[k + 1]) - datetime.fromisoformat(start)).days
            else:
                days = 14  # the last period is 3–4週目
            rows.append(
                {
                    "report_datetime": report,
                    "area_code": info.get("areaCode", ""),
                    "element": p["kind"],
                    "period_start": start,
                    "period": f"P{days}D",
                    "below_pct": int(p["below"]),
                    "normal_pct": int(p["normal"]),
                    "above_pct": int(p["above"]),
                }
            )
    return rows


def parse_jma_weekly(data: list) -> dict:
    """Daily probability of precipitation of the weekly forecast and the 7-day normal range."""
    pops, normals = [], []
    for report in data:
        rd = report["reportDatetime"]
        for ts in report.get("timeSeries", []):
            times = ts.get("timeDefines", [])
            for area in ts.get("areas", []):
                if "reliabilities" not in area or "pops" not in area:
                    continue  # short-term (6-hour) pops and temperatures are left out
                for t, pop, rel in zip(times, area["pops"], area["reliabilities"]):
                    pops.append(
                        {
                            "report_datetime": rd,
                            "area_code": area["area"]["code"],
                            "date": t[:10],
                            "pop_pct": int(pop) if pop else None,
                            "reliability": rel,
                        }
                    )
        for area in report.get("precipAverage", {}).get("areas", []):
            normals.append(
                {
                    "report_datetime": rd,
                    "station_code": area["area"]["code"],
                    "station_name": area["area"]["name"],
                    "min_mm": float(area["min"]),
                    "max_mm": float(area["max"]),
                }
            )
    return {"pops": pops, "precip_normals": normals}


# ---------------------------------------------------------------- AMeDAS


def parse_amedas_point(data: dict) -> dict[datetime, dict]:
    """Precipitation of each full-hour record of one 3-hour block.

    Returns {time: {"1h": mm, "24h": mm}}; a value is None when missing or
    when its quality flag is not 0 (normal).
    """
    out = {}
    for ts, rec in data.items():
        t = datetime.strptime(ts, "%Y%m%d%H%M%S").replace(tzinfo=JST)
        if t.minute != 0:
            continue
        vals = {}
        for k, name in (("1h", "precipitation1h"), ("24h", "precipitation24h")):
            v = rec.get(name)
            vals[k] = float(v[0]) if isinstance(v, list) and v[0] is not None and v[1] == 0 else None
        out[t] = vals
    return out


def daily_precip(records: dict[datetime, dict], d: date, end_hour: int) -> float | None:
    """24-hour precipitation ending at ``end_hour`` on day ``d`` (24 = midnight ending ``d``).

    end_hour=24 is the JMA calendar day (00:10–24:00); end_hour=9 is the
    9:00-to-9:00 total matching the dams' 9時現在 observations. Uses
    precipitation24h at the end time; falls back to summing 24 hourly values
    (upstream point files sometimes lack records).
    """
    end = datetime.combine(d, datetime.min.time(), JST) + timedelta(hours=end_hour)
    rec = records.get(end)
    if rec and rec["24h"] is not None:
        return rec["24h"]
    hours = [records.get(end - timedelta(hours=h)) for h in range(24)]
    if all(r and r["1h"] is not None for r in hours):
        return round(sum(r["1h"] for r in hours), 1)
    return None


# ---------------------------------------------------------------- JMA past daily values (etrn)


class _EtrnTable(HTMLParser):
    """Rows of the ``tablefix1`` table as (text, colspan) cells."""

    def __init__(self):
        super().__init__()
        self.rows: list[list[tuple[str, int]]] = []
        self._depth = 0  # >0 while inside tablefix1
        self._cell: list[str] | None = None
        self._span = 1

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table":
            if self._depth or a.get("id") == "tablefix1":
                self._depth += 1
        elif not self._depth:
            return
        elif tag == "tr":
            self.rows.append([])
        elif tag in ("td", "th"):
            self._cell = []
            self._span = int(a.get("colspan") or 1)

    def handle_endtag(self, tag):
        if tag == "table" and self._depth:
            self._depth -= 1
        elif tag in ("td", "th") and self._cell is not None:
            if self.rows:
                self.rows[-1].append(("".join(self._cell).strip(), self._span))
            self._cell = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


# Marks after a value: ")" quasi-normal (kept); "]" insufficient data, "#" doubtful (dropped).
_ETRN_VALUE = re.compile(r"^(-?\d+(?:\.\d+)?)\s*(\)?)$")


def parse_etrn_daily(html: str, month: str) -> list[dict]:
    """Daily precipitation totals from a ``daily_a1``/``daily_s1`` page for ``month`` (YYYY-MM)."""
    t = _EtrnTable()
    t.feed(html)
    if not t.rows:
        raise ParseError("no tablefix1 table")
    col = 0
    for text, span in t.rows[0]:
        if text.startswith("降水量"):
            break
        col += span
    else:
        raise ParseError("no 降水量 column")
    year, mon = (int(x) for x in month.split("-"))
    rows = []
    for r in t.rows:
        if not r or not r[0][0].isdigit():
            continue
        if len(r) <= col:
            raise ParseError(f"short row {r[0][0]}")
        d = date(year, mon, int(r[0][0]))
        raw = r[col][0]
        if raw == "--":  # no precipitation
            mm = 0.0
        else:
            m = _ETRN_VALUE.match(raw)
            mm = float(m.group(1)) if m else None
        rows.append({"date": d.isoformat(), "precip_mm": mm, "precip_raw": raw})
    if not rows:
        raise ParseError("no daily rows")
    return rows


# ---------------------------------------------------------------- hourly dam CSV (BODIK)

# Water-use capacity per dam in m³, as printed in the daily reports. 南畑 is
# lower from 6/1 to 10/20 (the city's storage chart labels that period's total
# 76,877千m³ and the rest of the year 78,347千m³).
CAPACITY_M3 = {
    "minamihata": 5_120_000, "gokayama": 31_700_000, "sefuri": 3_979_000,
    "magarifuchi": 2_368_000, "egawa": 24_000_000, "kubara": 1_460_000,
    "nagatani": 4_850_000, "inono": 3_650_000, "zuibaiji": 1_220_000,
}
MINAMIHATA_FLOOD_SEASON_M3 = 3_650_000
# Drought reserve (渇水対策容量) within the 9-dam water-use capacity: 五ケ山 16,600千m³
# and 江川's share of the reserve of the 江川・寺内・小石原川 system (6,676千m³), from the
# city's reports (capacity 78,347 including, 55,071 excluding; 76,877 and 53,601 in the
# flood season).
RESERVE_CAPACITY_M3 = 23_276_000


def capacity_m3(dam: str, d: date) -> int:
    if dam == "total":
        return sum(capacity_m3(k, d) for k in CAPACITY_M3)
    if dam == "minamihata" and (6, 1) <= (d.month, d.day) <= (10, 20):
        return MINAMIHATA_FLOOD_SEASON_M3
    return CAPACITY_M3[dam]


def parse_dam_hourly_csv(text: str) -> dict[date, list[dict]]:
    """Daily rows (the 9:00 record, as in the daily report) from the hourly CSV in 千m³.

    The total is the sum of the dams: the CSV's own 合計 column was off by up to
    104千m³ for some hours in 2026-10, while the daily report's total equals the sum.
    """
    rows = list(csv.reader(io.StringIO(text)))
    if not rows or rows[0][0] != "観測時刻":
        raise ParseError("header 観測時刻 not found")
    names = [_squash(c).removesuffix("ダム") for c in rows[0][1:] if c]
    if names[-1] != "合計" or len(names) != 10:
        raise ParseError(f"unexpected columns {rows[0]!r}")
    ids = [dam_id(nm) for nm in names[:-1]]
    out = {}
    for r in rows[1:]:
        if not r or not r[0]:
            continue
        t = datetime.strptime(r[0], "%Y/%m/%d %H:%M")
        if t.hour != 9 or t.minute != 0:
            continue
        d = t.date()
        m3 = {i: int(_number(v)) * 1000 for i, v in zip(ids, r[1:10])}
        m3["total"] = sum(m3.values())
        out[d] = [
            {"dam_id": i, "dam_name": next(k for k, v in DAM_IDS.items() if v == i) + ("" if i == "total" else "ダム"),
             "storage_m3": v, "capacity_m3": capacity_m3(i, d),
             "storage_pct": round(100 * v / capacity_m3(i, d), 2)}
            for i, v in m3.items()
        ]
    if not out:
        raise ParseError("no 9:00 rows")
    return out


# ---------------------------------------------------------------- tables


@dataclass
class Tables:
    dam_storage: dict = field(default_factory=dict)  # (date, dam_id) -> row
    distribution: dict = field(default_factory=dict)
    p1m: dict = field(default_factory=dict)
    weekly_pop: dict = field(default_factory=dict)
    weekly_precip_normal: dict = field(default_factory=dict)
    storage_excl: dict = field(default_factory=dict)  # date -> row
    hourly: dict = field(default_factory=dict)  # station -> {time: {"1h", "24h"}}
    etrn_daily: dict = field(default_factory=dict)  # (date, station) -> row
    warnings: list = field(default_factory=list)
    notes: list = field(default_factory=list)  # expected differences, reported without failing


TABLE_COLUMNS = {
    "dam_storage": [
        "date", "dam_id", "dam_name", "storage_pct", "storage_m3", "capacity_m3",
        "change_m3", "precip_mm", "precip_raw", "sources",
    ],
    "dam_storage_excl": [
        "date", "storage_pct", "storage_m3", "capacity_m3", "incl_storage_pct", "incl_storage_m3", "sources",
    ],
    "distribution": ["date", "plant_id", "plant_name", "volume_m3"],
    "jma_p1m": [
        "report_datetime", "area_code", "element", "period_start", "period",
        "below_pct", "normal_pct", "above_pct",
    ],
    "jma_weekly_pop": ["report_datetime", "area_code", "date", "pop_pct", "reliability"],
    "jma_weekly_precip_normal": ["report_datetime", "station_code", "station_name", "min_mm", "max_mm"],
    "precip_daily": ["date", "station_code", "station_name", "precip_mm", "precip_9to9_mm"],
    "jma_daily_precip": ["date", "station_code", "station_name", "precip_mm", "precip_raw"],
}


# When sources disagree, the first one here wins (and a warning is kept).
STORAGE_SOURCE_RANK = ("nippou", "city_text", "bodik_hourly")
# The hourly CSV is in 千m³ and its rate is computed here, so it differs from the
# daily report by a rounding step on most days; those gaps are not reported.
HOURLY_TOLERANCE_M3 = 2000
# The hourly CSV is preliminary (速報値), and the confirmed values of the daily report and
# the text page sometimes differ (2026-10-27: 37千m³ at 猪野). Such revisions are noted
# without failing the run. A larger gap looks like a broken parse rather than a revision
# (the CSV's own total column was off by up to 104千m³), so it is still warned about.
PRELIMINARY_TOLERANCE_M3 = 200_000


def _gap_kind(cur: dict, row: dict, source: str) -> str:
    """How a differing value compares: "rounding", "revision" (of the preliminary value) or "conflict"."""
    srcs = [s for s in cur["sources"].split(";") if s]
    held = min(srcs, key=STORAGE_SOURCE_RANK.index)  # the source of the value held so far
    if "bodik_hourly" not in (source, held):
        return "conflict"
    gap = abs(int(cur["storage_m3"]) - int(row["storage_m3"]))
    if gap <= HOURLY_TOLERANCE_M3:
        return "rounding"
    return "revision" if gap <= PRELIMINARY_TOLERANCE_M3 else "conflict"


def _merge_storage(tables: Tables, d: date, row: dict, source: str):
    key = (d.isoformat(), row["dam_id"])
    cur = tables.dam_storage.get(key)
    if cur is None:
        cur = {c: "" for c in TABLE_COLUMNS["dam_storage"]}
        cur.update(date=key[0], dam_id=row["dam_id"], dam_name=row["dam_name"], sources="")
        tables.dam_storage[key] = cur
    srcs = set(filter(None, cur["sources"].split(";")))
    wins = all(STORAGE_SOURCE_RANK.index(source) < STORAGE_SOURCE_RANK.index(s) for s in srcs)
    if cur["storage_m3"] != "" and cur["storage_m3"] != row["storage_m3"]:
        kind = _gap_kind(cur, row, source)
        msg = f"dam_storage {key}: {cur['storage_m3']} ({cur['sources']}) vs {row['storage_m3']} ({source})"
        if kind == "revision":
            tables.notes.append(f"{msg}, preliminary value revised")
        elif kind == "conflict":
            tables.warnings.append(msg)
    elif cur["storage_pct"] != "" and cur["storage_pct"] != row["storage_pct"]:
        if "bodik_hourly" not in (source, *srcs):  # the hourly rate is computed here, so it may differ
            tables.warnings.append(f"dam_storage {key} storage_pct: {cur['storage_pct']} ({cur['sources']}) "
                                   f"vs {row['storage_pct']} ({source})")
    for k in ("storage_pct", "storage_m3"):
        if cur[k] != "" and cur[k] != row[k] and not wins:
            continue
        cur[k] = row[k]
    for k in ("capacity_m3", "change_m3", "precip_mm", "precip_raw"):
        if k in row and row[k] is not None and (wins or cur[k] == ""):
            cur[k] = row[k]
    if wins:
        cur["dam_name"] = row["dam_name"]
    cur["sources"] = ";".join(sorted(srcs | {source}))


# When the status page and a report give the same day, the page wins.
EXCL_SOURCE_RANK = ("city_status", "report")


def _merge_excl(tables: Tables, d: date, row: dict, source: str):
    key = d.isoformat()
    cur = tables.storage_excl.get(key)
    new = {"date": key, "capacity_m3": capacity_m3("total", d) - RESERVE_CAPACITY_M3, "sources": source, **row}
    if cur is None:
        tables.storage_excl[key] = new
        return
    if cur["storage_m3"] != new["storage_m3"]:
        tables.warnings.append(f"dam_storage_excl {key}: {cur['storage_m3']} ({cur['sources']}) vs {new['storage_m3']} ({source})")
    srcs = set(cur["sources"].split(";")) | {source}
    if all(EXCL_SOURCE_RANK.index(source) <= EXCL_SOURCE_RANK.index(s) for s in srcs):
        cur.update(new)
    cur["sources"] = ";".join(sorted(srcs))


def build_tables(store: RawStore) -> Tables:
    tables = Tables()
    for e in store.log_entries():
        if not e.get("stored"):
            continue
        source, path = e["source"], e["path"]
        try:
            body = store.read_body(path)
            if source == "city_dam_text":
                parsed = parse_city_dam_text(body.decode("utf-8", errors="replace"))
                for row in parsed["dams"]:
                    _merge_storage(tables, parsed["observed_at"].date(), row, "city_text")
            elif source == "city_dam_status":
                parsed = parse_city_dam_status(body.decode("utf-8", errors="replace"))
                _merge_excl(tables, parsed["observed_at"].date(), {
                    **parsed["excl"], "incl_storage_pct": parsed["incl"]["storage_pct"],
                    "incl_storage_m3": parsed["incl"]["storage_m3"]}, "city_status")
            elif source == "city_reports":
                row = parse_report_excl(pdf_text(body))
                _merge_excl(tables, row.pop("date"), row, "report")
            elif source == "city_nippou":
                parsed = parse_nippou_text(pdf_text(body))
                d = parsed["report_date"]
                if e.get("meta", {}).get("date") not in (None, d.isoformat()):
                    tables.warnings.append(f"{path}: report date {d} differs from URL date")
                for row in parsed["dams"]:
                    _merge_storage(tables, d, row, "nippou")
                for row in parsed["plants"]:
                    tables.distribution[(d.isoformat(), row["plant_id"])] = {"date": d.isoformat(), **row}
            elif source == "bodik_dam_hourly":
                for d, rows in parse_dam_hourly_csv(body.decode("cp932")).items():
                    for row in rows:
                        _merge_storage(tables, d, row, "bodik_hourly")
            elif source == "jma_p1m":
                for row in parse_jma_p1m(json.loads(body)):
                    k = (row["report_datetime"], row["area_code"], row["element"], row["period_start"], row["period"])
                    tables.p1m[k] = row
            elif source == "jma_forecast":
                parsed = parse_jma_weekly(json.loads(body))
                for row in parsed["pops"]:
                    tables.weekly_pop[(row["report_datetime"], row["area_code"], row["date"])] = row
                for row in parsed["precip_normals"]:
                    tables.weekly_precip_normal[(row["report_datetime"], row["station_code"])] = row
            elif source == "jma_amedas_point":
                station = e["meta"]["station"]
                tables.hourly.setdefault(station, {}).update(parse_amedas_point(json.loads(body)))
            elif source == "jma_etrn_daily":
                station = e["meta"]["station"]
                for row in parse_etrn_daily(body.decode("utf-8", errors="replace"), e["meta"]["month"]):
                    tables.etrn_daily[(row["date"], station)] = {"station_code": station, **row}
        except ImportError as ex:
            tables.warnings.append(f"{path}: {ex} (install requirements.txt)")
        except (ParseError, ValueError, KeyError, TypeError, IndexError) as ex:
            tables.warnings.append(f"{path}: {type(ex).__name__}: {ex}")
    return tables


def station_names(store: RawStore) -> dict[str, str]:
    e = store.last_stored("jma_amedas_table")
    if e is None:
        return {}
    try:
        table = json.loads(store.read_body(e["path"]))
        return {code: v.get("kjName", "") for code, v in table.items()}
    except (ValueError, AttributeError):
        return {}


def precip_rows(tables: Tables, names: dict[str, str]) -> list[dict]:
    rows = []
    for station, records in tables.hourly.items():
        days = sorted({(t - timedelta(minutes=1)).date() for t in records})
        for d in days:
            row = {
                "date": d.isoformat(),
                "station_code": station,
                "station_name": names.get(station, ""),
                "precip_mm": daily_precip(records, d, 24),
                "precip_9to9_mm": daily_precip(records, d, 9),
            }
            if row["precip_mm"] is not None or row["precip_9to9_mm"] is not None:
                rows.append(row)
    return rows


def write_csv(path: Path, columns: list[str], rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(rows, key=lambda r: tuple(str(r.get(c, "")) for c in columns))
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({c: "" if r.get(c) is None else r[c] for c in columns})
    path.write_text(buf.getvalue(), encoding="utf-8")


def normalize(raw_dir: Path, out_dir: Path) -> Tables:
    store = RawStore(raw_dir)
    tables = build_tables(store)
    names = station_names(store)
    write_csv(out_dir / "dam_storage.csv", TABLE_COLUMNS["dam_storage"], tables.dam_storage.values())
    write_csv(out_dir / "dam_storage_excl.csv", TABLE_COLUMNS["dam_storage_excl"], tables.storage_excl.values())
    write_csv(out_dir / "distribution.csv", TABLE_COLUMNS["distribution"], tables.distribution.values())
    write_csv(out_dir / "jma_p1m.csv", TABLE_COLUMNS["jma_p1m"], tables.p1m.values())
    write_csv(out_dir / "jma_weekly_pop.csv", TABLE_COLUMNS["jma_weekly_pop"], tables.weekly_pop.values())
    write_csv(
        out_dir / "jma_weekly_precip_normal.csv",
        TABLE_COLUMNS["jma_weekly_precip_normal"],
        tables.weekly_precip_normal.values(),
    )
    write_csv(
        out_dir / "precip_daily.csv",
        TABLE_COLUMNS["precip_daily"],
        precip_rows(tables, names),
    )
    write_csv(
        out_dir / "jma_daily_precip.csv",
        TABLE_COLUMNS["jma_daily_precip"],
        ({**r, "station_name": names.get(r["station_code"], "")} for r in tables.etrn_daily.values()),
    )
    return tables


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    parser.add_argument("--out-dir", type=Path, default=Path("data/normalized"))
    args = parser.parse_args(argv)
    tables = normalize(args.raw_dir, args.out_dir)
    for note in tables.notes:
        print(f"note: {note}")
    for w in tables.warnings:
        print(f"warning: {w}", file=sys.stderr)
    return 1 if tables.warnings else 0


if __name__ == "__main__":
    sys.exit(main())
