"""Score issued forecasts against observed total storage.

Run: ``PYTHONPATH=src python -m dam_outlook.verify``

Writes derived tables under ``data/verification/`` (rebuilt every run):

- ``scores.csv``: one row per forecast and verified target date.
- ``summary.csv``: per model and horizon bucket, with the skill against the
  no-rain baseline on the same (base_date, date) pairs.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

from .forecast import load_forecasts
from .models import THRESHOLDS_PCT, read_csv
from .normalize import write_csv

BASELINE = "baseline_norain"
HORIZON_BUCKETS = ((1, 7), (8, 14), (15, 28))

SCORE_COLUMNS = [
    "model", "model_version", "issued_at", "base_date", "date", "horizon_days",
    "predicted_m3", "actual_m3", "error_m3", "in_p10_p90",
    *[f"brier_{t}" for t in THRESHOLDS_PCT],
]
SUMMARY_COLUMNS = [
    "model", "model_version", "horizon", "n", "mae_m3", "bias_m3", "baseline_mae_m3", "mae_skill",
    "p10_p90_coverage", "brier_mean", "brier_n",
]


def actual_storage(normalized_dir: Path) -> dict[str, int]:
    return {
        r["date"]: int(r["storage_m3"])
        for r in read_csv(normalized_dir / "dam_storage.csv")
        if r["dam_id"] == "total"
    }


def score(forecasts: list[dict], actual: dict[str, int]) -> list[dict]:
    rows = []
    for f in forecasts:
        capacity = f.get("inputs", {}).get("capacity_m3")
        for p in f["forecast"]:
            a = actual.get(p["date"])
            if a is None:
                continue
            pred = p.get("storage_m3", p.get("storage_m3_p50"))
            row = {
                "model": f["model"],
                "model_version": f["model_version"],
                "issued_at": f["issued_at"],
                "base_date": f["base_date"],
                "date": p["date"],
                "horizon_days": p["horizon_days"],
                "predicted_m3": pred,
                "actual_m3": a,
                "error_m3": pred - a,
            }
            if "storage_m3_p10" in p:
                row["in_p10_p90"] = int(p["storage_m3_p10"] <= a <= p["storage_m3_p90"])
            if capacity and "prob_below_pct" in p:
                for t in THRESHOLDS_PCT:
                    prob = p["prob_below_pct"].get(str(t))
                    if prob is not None:
                        outcome = 1 if a < capacity * t / 100 else 0
                        row[f"brier_{t}"] = round((prob - outcome) ** 2, 4)
            rows.append(row)
    return rows


def _bucket(h: int) -> str | None:
    for lo, hi in HORIZON_BUCKETS:
        if lo <= h <= hi:
            return f"{lo}-{hi}"
    return None


def summarize(rows: list[dict]) -> list[dict]:
    baseline_err = {
        (r["base_date"], r["date"]): abs(r["error_m3"]) for r in rows if r["model"] == BASELINE
    }
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        b = _bucket(r["horizon_days"])
        if b:
            groups.setdefault((r["model"], r["model_version"], b), []).append(r)
    out = []
    for (model, version, bucket), rs in sorted(groups.items()):
        errs = [r["error_m3"] for r in rs]
        paired = [(abs(r["error_m3"]), baseline_err[(r["base_date"], r["date"])])
                  for r in rs if (r["base_date"], r["date"]) in baseline_err]
        cover = [r["in_p10_p90"] for r in rs if r.get("in_p10_p90") is not None]
        briers = [r[k] for r in rs for k in r if k.startswith("brier_") and r[k] is not None]
        row = {
            "model": model,
            "model_version": version,
            "horizon": bucket,
            "n": len(rs),
            "mae_m3": round(statistics.fmean(abs(e) for e in errs)),
            "bias_m3": round(statistics.fmean(errs)),
        }
        if paired and model != BASELINE:
            mae, base_mae = (statistics.fmean(x) for x in zip(*paired))
            row["baseline_mae_m3"] = round(base_mae)
            # 1 - MAE/MAE_baseline: > 0 means better than the no-rain baseline.
            row["mae_skill"] = round(1 - mae / base_mae, 3) if base_mae else None
        if cover:
            row["p10_p90_coverage"] = round(statistics.fmean(cover), 3)
        if briers:
            row["brier_mean"] = round(statistics.fmean(briers), 4)
            row["brier_n"] = len(briers)
        out.append(row)
    return out


def verify(normalized_dir: Path, forecast_dir: Path, out_dir: Path) -> tuple[list[dict], list[dict]]:
    rows = score(load_forecasts(forecast_dir), actual_storage(normalized_dir))
    summary = summarize(rows)
    write_csv(out_dir / "scores.csv", SCORE_COLUMNS, rows)
    write_csv(out_dir / "summary.csv", SUMMARY_COLUMNS, summary)
    return rows, summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--normalized-dir", type=Path, default=Path("data/normalized"))
    parser.add_argument("--forecast-dir", type=Path, default=Path("data/forecasts"))
    parser.add_argument("--out-dir", type=Path, default=Path("data/verification"))
    args = parser.parse_args(argv)
    rows, summary = verify(args.normalized_dir, args.forecast_dir, args.out_dir)
    print(f"{len(rows)} scored points, {len(summary)} summary rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
