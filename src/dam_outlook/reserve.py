"""Storage excluding the drought reserve (渇水対策容量分を除く): published values and estimates.

How much of the stored water counts as the reserve is an operating account, not
something the storage itself tells: it was drawn down in the 2026 drought, and
江川's share is apportioned from the 江川・寺内・小石原川 system. It is known only
as the difference between the totals including and excluding it, on days both
are known ("anchors"): days the city's status page or a report printed the
excluded total, and days the report charts give both lines (2025-04 on).

Between anchors the reserve is interpolated linearly; after the last one it is
carried forward. Storage excluding the reserve is the total storage minus the
reserve. Only the printed values are published; everything else is an estimate.
"""

from __future__ import annotations

import bisect
import statistics
from datetime import date

from .normalize import RESERVE_CAPACITY_M3


def _medians(chart_rows: list[dict], series: str) -> dict[date, float]:
    by: dict[str, list[float]] = {}
    for r in chart_rows:
        if r["series"] == series:
            by.setdefault(r["date"], []).append(float(r["storage_m3"]))
    return {date.fromisoformat(d): statistics.median(v) for d, v in by.items()}


def published(rows: list[dict]) -> dict[date, dict]:
    """Rows of dam_storage_excl.csv by date."""
    return {
        date.fromisoformat(r["date"]): {
            "storage_m3": int(r["storage_m3"]),
            "capacity_m3": int(r["capacity_m3"]),
            "incl_storage_m3": int(r["incl_storage_m3"]) if r.get("incl_storage_m3") else None,
            "incl_storage_pct": float(r["incl_storage_pct"]) if r.get("incl_storage_pct") else None,
            "sources": r["sources"],
        }
        for r in rows
    }


def anchors(pub: dict[date, dict], chart_rows: list[dict], incl: dict[date, tuple]) -> dict[date, dict]:
    """Reserve stored (m³) on each day both totals are known.

    A printed excluded total is paired with the total printed beside it, else
    the total of that day in ``incl`` (published values and chart readings).
    """
    excl_chart, incl_chart = _medians(chart_rows, "excl_reserve"), _medians(chart_rows, "incl_reserve")
    out = {d: {"reserve_m3": round(incl_chart[d] - v), "date": d, "source": "chart"}
           for d, v in excl_chart.items() if d in incl_chart}
    for d, p in pub.items():
        total = p["incl_storage_m3"] or (incl[d][0] if d in incl else None)
        if total is not None:
            out[d] = {"reserve_m3": total - p["storage_m3"], "date": d, "source": p["sources"]}
    return out


def reserve_on(anch: dict[date, dict], d: date, until: date | None = None) -> dict | None:
    """The reserve on ``d``: that day's anchor, else linear between the anchors around it,
    else the last anchor before it. Anchors after ``until`` are not used."""
    days = sorted(a for a in anch if until is None or a <= until)
    i = bisect.bisect_left(days, d)
    if i < len(days) and days[i] == d:
        return anch[d]
    if i == 0:
        return None
    prev = anch[days[i - 1]]
    if i == len(days):
        return {**prev, "source": f"carried from {prev['date']}"}
    nxt = anch[days[i]]
    f = (d - prev["date"]).days / (nxt["date"] - prev["date"]).days
    return {"reserve_m3": round(prev["reserve_m3"] + f * (nxt["reserve_m3"] - prev["reserve_m3"])), "date": d,
            "source": f"interpolated {prev['date']}..{nxt['date']}"}


def history(incl: dict[date, tuple], pub: dict[date, dict], anch: dict[date, dict]) -> dict[date, dict]:
    """Storage excluding the reserve per day: printed values, else total minus the reserve on that day."""
    out = {}
    for d, (s, cap) in incl.items():
        r = reserve_on(anch, d)
        if r is not None:
            out[d] = {"storage_m3": max(s - r["reserve_m3"], 0),
                      "capacity_m3": cap - RESERVE_CAPACITY_M3 if cap else None,
                      "published": False, "reserve_m3": r["reserve_m3"]}
    for d, p in pub.items():
        out[d] = {"storage_m3": p["storage_m3"], "capacity_m3": p["capacity_m3"], "published": True,
                  "sources": p["sources"], "incl_storage_pct": p["incl_storage_pct"],
                  "reserve_m3": anch[d]["reserve_m3"] if d in anch else None}
    return out
