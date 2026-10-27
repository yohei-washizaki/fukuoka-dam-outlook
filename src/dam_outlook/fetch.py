"""Fetch upstream pages and store them unparsed under ``data/raw/``.

Run: ``PYTHONPATH=src python -m dam_outlook.fetch``

Sources whose past values cannot be re-fetched (the city text and status pages, the daily
water report PDFs, the hourly dam CSV that covers only recent weeks, AMeDAS
point data kept for about ten days) are fetched every
run, and missing recent dates are backfilled while upstream still has them.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from .raw_store import JST, RawStore

USER_AGENT = "dam-outlook/0.1 (personal research; daily fetch)"
TIMEOUT = 30

CITY_DAM_TEXT_URL = "https://www.city.fukuoka.lg.jp/..."
# きょうのダム状況: the 9-dam total with and without the drought reserve (渇水対策容量),
# weekdays 9:00, current values only.
CITY_DAM_STATUS_URL = "https://www.city.fukuoka.lg.jp/..."
CITY_NIPPOU_URL = (
    "https://www.city.fukuoka.lg.jp/..."
    "nippou_{d:%Y_%m_%d}.pdf"
)
# 福岡市関連９ダム貯水量 on BODIK (Fukuoka City, CC BY): hourly storage per dam,
# Shift_JIS CSV, newest first. On 2026-10-26 it held every hour since 10/01 00:00.
BODIK_DAM_HOURLY_URL = (
    "https://data.bodik.jp/dataset/d54fb22e-5b64-485c-8816-69f27ed1aaf1/"
    "resource/a5b26052-26d1-4c7a-b63f-5736de453bc1/download"
)
JMA_P1M_URL = "https://www.jma.go.jp/bosai/season/data/P1M/010900.json"
JMA_FORECAST_URL = "https://www.jma.go.jp/bosai/forecast/data/forecast/400000.json"
JMA_AMEDAS_TABLE_URL = "https://www.jma.go.jp/bosai/amedas/const/amedastable.json"
JMA_AMEDAS_LATEST_URL = "https://www.jma.go.jp/bosai/amedas/data/latest_time.txt"
JMA_AMEDAS_POINT_URL = (
    "https://www.jma.go.jp/bosai/amedas/data/point/{code}/{t:%Y%m%d_%H}.json"
)

# 福岡, 前原, 太宰府
AMEDAS_STATIONS = ("82182", "82171", "82191")

# Monthly pages of daily values (過去の気象データ検索). 福岡 is a manned station
# (s1 page), the others are AMeDAS points (a1 page).
JMA_ETRN_DAILY_URL = (
    "https://www.data.jma.go.jp/stats/etrn/view/daily_{kind}1.php"
    "?prec_no=82&block_no={block}&year={year}&month={month}&day=&view="
)
ETRN_STATIONS = {"82182": ("s", "47807"), "82171": ("a", "0785"), "82191": ("a", "1141")}
ETRN_START = (2006, 1)  # 20 years of rain for the analog windows (models.py)
ETRN_SETTLE_DAYS = 10  # a month is fetched once, this many days after it ends

# Upstream retention observed on 2026-10-25: daily reports about one week,
# AMeDAS point files about ten days.
# Reports whose charts are digitized for past storage (see digitize.py).
STORAGE_CHART_SOURCES = Path("data/curated/storage_chart_sources.csv")

NIPPOU_BACKFILL_DAYS = 10
AMEDAS_BACKFILL_DAYS = 9

# (status, body); status is None on network error with body holding the message.
Getter = Callable[[str], tuple[int | None, bytes]]


def http_get(url: str) -> tuple[int | None, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return None, str(e).encode()


class Fetcher:
    def __init__(
        self,
        store: RawStore,
        get: Getter = http_get,
        now: Callable[[], datetime] = lambda: datetime.now(JST),
        delay: float = 0.3,
    ):
        self.store = store
        self.get = get
        self.now = now
        self.delay = delay
        self.errors: list[str] = []
        self._requested = False

    def _get(self, url: str) -> tuple[datetime, int | None, bytes]:
        if self._requested and self.delay:
            time.sleep(self.delay)
        self._requested = True
        fetched_at = self.now()
        status, body = self.get(url)
        return fetched_at, status, body

    def _fail(self, source, key, url, fetched_at, status, note, meta=None):
        self.store.log_failure(
            source=source,
            key=key,
            url=url,
            fetched_at=fetched_at,
            status=status,
            note=note,
            meta=meta,
        )

    def fetch_snapshot(self, source: str, url: str, name: str, ext: str):
        """Fetch a page that only shows current values; store it if it changed."""
        fetched_at, status, body = self._get(url)
        if status != 200:
            note = body.decode(errors="replace")[:200] if status is None else "http error"
            self._fail(source, source, url, fetched_at, status, note)
            self.errors.append(f"{source}: {status} {note}")
            return None
        return self.store.save(
            source=source,
            key=source,
            subdir=fetched_at.strftime("%Y/%m"),
            name=name,
            ext=ext,
            url=url,
            fetched_at=fetched_at,
            status=status,
            body=body,
        )

    def fetch_city_dam_text(self):
        return self.fetch_snapshot("city_dam_text", CITY_DAM_TEXT_URL, "001", "html")

    def fetch_city_dam_status(self):
        return self.fetch_snapshot("city_dam_status", CITY_DAM_STATUS_URL, "002", "html")

    def fetch_bodik_dam_hourly(self):
        return self.fetch_snapshot("bodik_dam_hourly", BODIK_DAM_HOURLY_URL, "dam_hourly", "csv")

    def fetch_jma_p1m(self):
        return self.fetch_snapshot("jma_p1m", JMA_P1M_URL, "P1M_010900", "json")

    def fetch_jma_forecast(self):
        return self.fetch_snapshot("jma_forecast", JMA_FORECAST_URL, "forecast_400000", "json")

    def fetch_jma_amedas_table(self):
        return self.fetch_snapshot("jma_amedas_table", JMA_AMEDAS_TABLE_URL, "amedastable", "json")

    def fetch_city_nippou(self, days: int = NIPPOU_BACKFILL_DAYS):
        """Try each recent weekday not yet stored. 404 is expected (holidays, not yet published)."""
        source = "city_nippou"
        today = self.now().date()
        results = []
        for i in range(days, -1, -1):
            d = today - timedelta(days=i)
            if d.weekday() >= 5:
                continue
            key = f"{source}/{d.isoformat()}"
            if self.store.has_stored(key):
                continue
            url = CITY_NIPPOU_URL.format(d=d)
            fetched_at, status, body = self._get(url)
            meta = {"date": d.isoformat()}
            if status == 200 and body.startswith(b"%PDF"):
                results.append(
                    self.store.save(
                        source=source,
                        key=key,
                        subdir=f"{d:%Y}",
                        name=f"nippou_{d:%Y_%m_%d}",
                        ext="pdf",
                        url=url,
                        fetched_at=fetched_at,
                        status=status,
                        body=body,
                        dedupe=False,
                        meta=meta,
                    )
                )
            elif status == 404:
                self._fail(source, key, url, fetched_at, status, "not published", meta)
            else:
                note = "not a pdf" if status == 200 else "http error"
                if status is None:
                    note = body.decode(errors="replace")[:200]
                self._fail(source, key, url, fetched_at, status, note, meta)
                self.errors.append(f"{key}: {status} {note}")
        return results

    def fetch_city_reports(self, sources: Path = STORAGE_CHART_SOURCES):
        """Fetch each listed report PDF once; reports are not revised, so a stored one is not fetched again."""
        source = "city_reports"
        if not sources.exists():
            return []
        with sources.open(encoding="utf-8", newline="") as f:
            urls = [r["url"] for r in csv.DictReader(f) if r.get("url")]
        results = []
        for url in urls:
            name = Path(urlparse(url).path).stem
            key = f"{source}/{name}"
            if self.store.has_stored(key):
                continue
            fetched_at, status, body = self._get(url)
            if status == 200 and body.startswith(b"%PDF"):
                results.append(self.store.save(source=source, key=key, subdir=".", name=name, ext="pdf", url=url,
                                               fetched_at=fetched_at, status=status, body=body, dedupe=False))
            else:
                note = "not a pdf" if status == 200 else "http error"
                self._fail(source, key, url, fetched_at, status, note)
                self.errors.append(f"{key}: {status} {note}")
        return results

    def fetch_jma_etrn_daily(self, stations: dict = ETRN_STATIONS, start: tuple[int, int] = ETRN_START):
        """Fetch each settled month of daily values once per station."""
        source = "jma_etrn_daily"
        last = (self.now() - timedelta(days=ETRN_SETTLE_DAYS)).replace(day=1) - timedelta(days=1)
        results = []
        for code, (kind, block) in stations.items():
            y, m = start
            while (y, m) <= (last.year, last.month):
                month = f"{y:04d}-{m:02d}"
                key = f"{source}/{code}/{month}"
                if not self.store.has_stored(key):
                    url = JMA_ETRN_DAILY_URL.format(kind=kind, block=block, year=y, month=m)
                    fetched_at, status, body = self._get(url)
                    if status == 200 and b"tablefix1" in body:
                        results.append(self.store.save(source=source, key=key, subdir=code, name=month, ext="html",
                                                       url=url, fetched_at=fetched_at, status=status, body=body,
                                                       dedupe=False, meta={"station": code, "month": month}))
                    else:
                        note = "no data table" if status == 200 else "http error"
                        self._fail(source, key, url, fetched_at, status, note)
                        self.errors.append(f"{key}: {status} {note}")
                y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        return results

    def fetch_amedas_latest_time(self) -> datetime | None:
        source = "jma_amedas_latest"
        fetched_at, status, body = self._get(JMA_AMEDAS_LATEST_URL)
        if status != 200:
            self._fail(source, source, JMA_AMEDAS_LATEST_URL, fetched_at, status, "http error")
            self.errors.append(f"{source}: {status}")
            return None
        self.store.save(
            source=source,
            key=source,
            subdir=fetched_at.strftime("%Y/%m"),
            name="latest_time",
            ext="txt",
            url=JMA_AMEDAS_LATEST_URL,
            fetched_at=fetched_at,
            status=status,
            body=body,
        )
        try:
            return datetime.fromisoformat(body.decode().strip())
        except ValueError:
            self.errors.append(f"{source}: unparsable {body[:50]!r}")
            return None

    def fetch_amedas_points(
        self,
        stations=AMEDAS_STATIONS,
        days: int = AMEDAS_BACKFILL_DAYS,
    ):
        """Fetch every complete 3-hour block in the backfill window not yet stored complete."""
        latest = self.fetch_amedas_latest_time()
        if latest is None:
            return []
        latest = latest.astimezone(JST)
        start = datetime.combine(latest.date() - timedelta(days=days), datetime.min.time(), JST)
        blocks = []
        t = start
        # A block HH holds records HH:00..HH+2:50.
        while t + timedelta(hours=2, minutes=50) <= latest:
            blocks.append(t)
            t += timedelta(hours=3)

        source = "jma_amedas_point"
        results = []
        for code in stations:
            for t in blocks:
                key = f"{source}/{code}/{t:%Y%m%d_%H}"
                if self.store.has_stored(key, complete=True):
                    continue
                url = JMA_AMEDAS_POINT_URL.format(code=code, t=t)
                fetched_at, status, body = self._get(url)
                meta = {"station": code, "block": t.isoformat()}
                if status != 200:
                    self._fail(source, key, url, fetched_at, status, "http error", meta)
                    if status != 404:  # 404: older than upstream retention
                        self.errors.append(f"{key}: {status}")
                    continue
                meta["complete"] = _amedas_block_complete(body, t)
                results.append(
                    self.store.save(
                        source=source,
                        key=key,
                        subdir=f"{code}/{t:%Y/%m}",
                        name=f"{t:%Y%m%d_%H}",
                        ext="json",
                        url=url,
                        fetched_at=fetched_at,
                        status=status,
                        body=body,
                        meta=meta,
                    )
                )
        return results

    def run(self, only: set[str] | None = None) -> None:
        steps = {
            "city_dam_text": self.fetch_city_dam_text,
            "city_dam_status": self.fetch_city_dam_status,
            "city_nippou": self.fetch_city_nippou,
            "bodik_dam_hourly": self.fetch_bodik_dam_hourly,
            "jma_p1m": self.fetch_jma_p1m,
            "jma_forecast": self.fetch_jma_forecast,
            "jma_amedas_table": self.fetch_jma_amedas_table,
            "jma_amedas_point": self.fetch_amedas_points,
            "city_reports": self.fetch_city_reports,
            "jma_etrn_daily": self.fetch_jma_etrn_daily,
        }
        for name, step in steps.items():
            if only and name not in only:
                continue
            try:
                step()
            except Exception as e:  # keep other sources running
                self.errors.append(f"{name}: {type(e).__name__}: {e}")


def _amedas_block_complete(body: bytes, block_start: datetime) -> bool:
    """Whether the block JSON has the last 10-minute record of the block."""
    try:
        data = json.loads(body)
    except ValueError:
        return False
    last = block_start + timedelta(hours=2, minutes=50)
    return isinstance(data, dict) and f"{last:%Y%m%d%H%M%S}" in data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    parser.add_argument("--only", nargs="*", help="source names to fetch")
    args = parser.parse_args(argv)

    fetcher = Fetcher(RawStore(args.raw_dir))
    fetcher.run(set(args.only) if args.only else None)
    for e in fetcher.errors:
        print(f"error: {e}", file=sys.stderr)
    return 1 if fetcher.errors else 0


if __name__ == "__main__":
    sys.exit(main())
