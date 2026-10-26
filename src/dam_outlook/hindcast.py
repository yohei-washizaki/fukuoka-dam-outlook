"""Hindcast the models from past base dates, using only data available before each.

Run: ``PYTHONPATH=src python -m dam_outlook.hindcast`` (a few minutes)

Past storage comes from the report charts (dam_storage_chart.csv), so the
hindcast can start long before the daily fetch did. For each base date the
observations are cut: rain after the base date, and the base date's whole
year, are removed, so neither the observed future rain nor its season leaks
into the analog windows. Past 1-month forecasts are not stored, so the
scenario model uses equal tercile weights. The no-rain baseline judges dry
days from the calendar-day AMeDAS mean instead of the daily report.

Writes ``data/verification/hindcast.csv`` (rebuilt every run). Hindcasts are
evaluation only; they are not issued forecasts and are not stored as such.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from .models import HORIZON_DAYS, MODELS, load_observations
from .normalize import write_csv
from .raw_store import JST

START = date(2023, 8, 1)  # the training data covers every month from here
STEP_DAYS = 7
HORIZONS = (7, 14, 28)
COLUMNS = [
    "model", "model_version", "horizon_days", "n", "mae_m3", "bias_m3", "crps_approx_m3",
    "p10_p90_coverage", "first_base", "last_base", "generated_at",
]


def cut(obs: dict, base: date) -> dict:
    """Observations as they could have been known on ``base`` (see module docstring)."""
    hist = obs["history"]
    rain = {d: v for d, v in obs["rain_daily"].items() if d <= base or d.year != base.year and d > base + timedelta(days=HORIZON_DAYS)}
    total = {}
    for d, (s, cap) in hist.items():
        if d <= base:
            total[d.isoformat()] = {"storage_m3": s, "storage_pct": None, "capacity_m3": cap, "change_m3": None}
    amedas = {d.isoformat(): {c: v for c in ("82182", "82171", "82191")} for d, v in rain.items() if d <= base}
    return {
        **obs,
        "total": total,
        "dam_precip": {},
        "amedas_9to9": amedas,
        "p1m_precip": [],
        "history": {d: v for d, v in hist.items() if d <= base},
        "rain_daily": rain,
    }


def _quantile_points(row: dict) -> list[tuple[float, float]]:
    if "storage_m3" in row:
        return [(q, row["storage_m3"]) for q in (0.1, 0.5, 0.9)]
    return [(0.1, row["storage_m3_p10"]), (0.5, row["storage_m3_p50"]), (0.9, row["storage_m3_p90"])]


def hindcast(normalized_dir: Path, start: date = START, step: int = STEP_DAYS) -> list[dict]:
    obs = load_observations(normalized_dir)
    hist = obs["history"]
    if not hist:
        return []
    bases = []
    d = start
    while d + timedelta(days=max(HORIZONS)) <= max(hist):
        if d in hist and all(d + timedelta(days=h) in hist for h in HORIZONS):
            bases.append(d)
        d += timedelta(days=step)
    scores: dict[tuple, list[tuple[float, float, bool]]] = {}
    for base in bases:
        known = cut(obs, base)
        as_of = datetime.combine(base, datetime.min.time(), JST) + timedelta(hours=15)
        for name, (version, fn) in MODELS.items():
            result = fn(known, base, as_of)
            if result is None:
                continue
            for row in result["forecast"]:
                h = row["horizon_days"]
                if h not in HORIZONS:
                    continue
                actual = hist[base + timedelta(days=h)][0]
                qs = _quantile_points(row)
                pinball = statistics.fmean(max(q * (actual - x), (q - 1) * (actual - x)) for q, x in qs)
                err = qs[1][1] - actual
                inside = qs[0][1] <= actual <= qs[2][1]
                scores.setdefault((name, version, h), []).append((err, 2 * pinball, inside, base))
    generated = datetime.now(JST).isoformat(timespec="seconds")
    rows = []
    for (name, version, h), v in sorted(scores.items()):
        rows.append({
            "model": name,
            "model_version": version,
            "horizon_days": h,
            "n": len(v),
            "mae_m3": round(statistics.fmean(abs(e) for e, *_ in v)),
            "bias_m3": round(statistics.fmean(e for e, *_ in v)),
            "crps_approx_m3": round(statistics.fmean(c for _, c, *_ in v)),
            "p10_p90_coverage": round(sum(i for *_, i, _ in v) / len(v), 3),
            "first_base": min(b for *_, b in v).isoformat(),
            "last_base": max(b for *_, b in v).isoformat(),
            "generated_at": generated,
        })
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--normalized-dir", type=Path, default=Path("data/normalized"))
    parser.add_argument("--out", type=Path, default=Path("data/verification/hindcast.csv"))
    args = parser.parse_args(argv)
    rows = hindcast(args.normalized_dir)
    write_csv(args.out, COLUMNS, rows)
    for r in rows:
        print(f'{r["model"]} v{r["model_version"]} h{r["horizon_days"]}: MAE {r["mae_m3"]} '
              f'CRPS~ {r["crps_approx_m3"]} in80 {r["p10_p90_coverage"]} n={r["n"]}')
    return 0


if __name__ == "__main__":
    sys.exit(main())
