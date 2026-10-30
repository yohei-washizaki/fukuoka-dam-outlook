"""Forecast models over the normalized tables.

Every model is a pure function ``fn(obs, base, as_of)`` returning
``{"inputs", "params", "forecast"}`` or None when its inputs are missing.
``inputs`` holds every value the forecast used, so a stored forecast can be
replayed and audited.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import random
import statistics
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

from . import reserve

# What the forecasts predict: the 9-dam total excluding the drought reserve (from v2).
TARGET = "storage_excl_reserve_m3"
HORIZON_DAYS = 28
RATE_WINDOW_DAYS = 14
MIN_DRY_DAYS = 3
AMEDAS_STATIONS = ("82182", "82171", "82191")
THRESHOLDS_PCT = (35, 30, 25, 20)


# ---------------------------------------------------------------- inputs


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _float(s: str) -> float | None:
    return float(s) if s not in ("", None) else None


def load_observations(normalized_dir: Path) -> dict:
    """Total storage, capacity, daily change and dryness per observation date."""
    storage = read_csv(normalized_dir / "dam_storage.csv")
    precip = read_csv(normalized_dir / "precip_daily.csv")

    total: dict[str, dict] = {}
    dam_precip: dict[str, list[float | None]] = {}
    for r in storage:
        if r["dam_id"] == "total":
            total[r["date"]] = {
                "storage_m3": int(r["storage_m3"]),
                "storage_pct": _float(r["storage_pct"]),
                "capacity_m3": int(r["capacity_m3"]) if r["capacity_m3"] else None,
                "change_m3": int(r["change_m3"]) if r["change_m3"] else None,
                "sources": r.get("sources", ""),
            }
        elif r["precip_raw"] != "":
            dam_precip.setdefault(r["date"], []).append(_float(r["precip_mm"]))

    amedas: dict[str, dict[str, float | None]] = {}
    for r in precip:
        amedas.setdefault(r["date"], {})[r["station_code"]] = _float(r["precip_9to9_mm"])

    p1m = [
        {
            "report_datetime": r["report_datetime"],
            "period_start": r["period_start"],
            "below_pct": int(r["below_pct"]),
            "normal_pct": int(r["normal_pct"]),
            "above_pct": int(r["above_pct"]),
        }
        for r in read_csv(normalized_dir / "jma_p1m.csv")
        if r["element"] == "降水量" and r["period"] == "P1M"
    ]
    history = load_history(normalized_dir, total)
    return {
        "total": total,
        "dam_precip": dam_precip,
        "amedas_9to9": amedas,
        "p1m_precip": p1m,
        "history": history,
        "rain_daily": load_rain_daily(normalized_dir),
        **load_excl(normalized_dir, history),
    }


def load_excl(normalized_dir: Path, history: dict[date, tuple[int, int | None]]) -> dict:
    """Published totals excluding the drought reserve, the reserve anchors, and the daily series (reserve.py)."""
    pub = reserve.published(read_csv(normalized_dir / "dam_storage_excl.csv"))
    anch = reserve.anchors(pub, read_csv(normalized_dir / "dam_storage_chart.csv"), history)
    return {"excl_published": pub, "reserve_anchors": anch, "excl_history": reserve.history(history, pub, anch)}


def load_history(normalized_dir: Path, total: dict[str, dict]) -> dict[date, tuple[int, int | None]]:
    """Total storage (including the drought reserve) and capacity per day: chart readings
    (median across reports), published values win."""
    readings: dict[str, list[float]] = {}
    capacity: dict[str, int] = {}
    for r in read_csv(normalized_dir / "dam_storage_chart.csv"):
        if r["series"] == "incl_reserve":
            readings.setdefault(r["date"], []).append(float(r["storage_m3"]))
            capacity[r["date"]] = int(r["capacity_m3"])
    out = {date.fromisoformat(d): (round(statistics.median(v)), capacity[d]) for d, v in readings.items()}
    for d, cur in total.items():
        out[date.fromisoformat(d)] = (cur["storage_m3"], cur["capacity_m3"] or _capacity_from_pct(cur))
    return out


def load_rain_daily(normalized_dir: Path) -> dict[date, float]:
    """Mean calendar-day precipitation of the AMeDAS stations, when all have a value.

    Past months come from the JMA daily tables (jma_daily_precip.csv); recent
    days from the AMeDAS point data (precip_daily.csv).
    """
    by: dict[str, dict[str, float]] = {}
    for r in read_csv(normalized_dir / "precip_daily.csv"):
        if r["precip_mm"] != "":
            by.setdefault(r["date"], {})[r["station_code"]] = float(r["precip_mm"])
    for r in read_csv(normalized_dir / "jma_daily_precip.csv"):
        if r["precip_mm"] != "":
            by.setdefault(r["date"], {})[r["station_code"]] = float(r["precip_mm"])
    out = {}
    for d, st in by.items():
        vals = [st.get(c) for c in AMEDAS_STATIONS]
        if None not in vals:
            out[date.fromisoformat(d)] = round(sum(vals) / len(vals), 2)
    return out


def amedas_mean_9to9(obs: dict, d: date) -> float | None:
    """Mean 9:00-to-9:00 precipitation of the AMeDAS stations, when all have a value."""
    st = obs["amedas_9to9"].get(d.isoformat(), {})
    vals = [st.get(c) for c in AMEDAS_STATIONS]
    if any(v is None for v in vals):
        return None
    return round(sum(vals) / len(vals), 2)


def daily_changes(obs: dict, base: date, window: int) -> list[dict]:
    """Day-over-day change of total storage (m3) ending on each day in the window.

    Uses the daily report's 増減水量 when present, else the difference from
    the previous day's observation. A day is dry when every dam reported no
    rain, or (without a report) every AMeDAS station had 0 mm from 9:00 to 9:00.
    """
    total = obs["total"]
    out = []
    for i in range(window - 1, -1, -1):
        d = base - timedelta(days=i)
        cur = total.get(d.isoformat())
        if cur is None:
            continue
        change = cur["change_m3"]
        if change is None:
            prev = total.get((d - timedelta(days=1)).isoformat())
            if prev is None:
                continue
            change = cur["storage_m3"] - prev["storage_m3"]
        dams = obs["dam_precip"].get(d.isoformat())
        if dams:
            dry = all(v == 0 for v in dams)
            dry_source = "nippou"
        else:
            st = obs["amedas_9to9"].get(d.isoformat(), {})
            vals = [st.get(c) for c in AMEDAS_STATIONS]
            dry = all(v == 0 for v in vals)
            dry_source = "amedas" if all(v is not None for v in vals) else "unknown"
        out.append({"date": d.isoformat(), "change_m3": change, "dry": dry, "dry_source": dry_source})
    return out


# ---------------------------------------------------------------- models


def rate_pool(changes: list[dict]) -> tuple[list[int], str]:
    """Daily changes used for the no-rain rate: dry days, or all days if too few are dry."""
    dry = [c["change_m3"] for c in changes if c["dry"]]
    if len(dry) >= MIN_DRY_DAYS:
        return dry, "dry_days"
    return [c["change_m3"] for c in changes], "all_days"


def excl_base(obs: dict, base: date) -> dict | None:
    """Storage excluding the drought reserve on the base date, and the reserve held for the horizon.

    The models move the total storage (the water itself); the forecast of the
    excluded storage is that minus the reserve, which is assumed to stay as on
    the base date. The base value is the printed one when there is one, else
    the total minus the latest reserve anchor up to the base date.
    """
    cur = obs["total"].get(base.isoformat())
    if cur is None:
        return None
    capacity = cur["capacity_m3"] or _capacity_from_pct(cur)
    pub = obs["excl_published"].get(base)
    if pub:
        excl, anchor = pub["storage_m3"], {"date": base, "source": pub["sources"]}
    else:
        anchor = reserve.reserve_on(obs["reserve_anchors"], base, until=base)
        if anchor is None:
            return None
        excl = max(cur["storage_m3"] - anchor["reserve_m3"], 0)
    return {
        "base_excl_storage_m3": excl,
        "reserve_m3": cur["storage_m3"] - excl,
        "reserve_date": anchor["date"].isoformat(),
        "reserve_source": anchor["source"],
        "capacity_m3": capacity - reserve.RESERVE_CAPACITY_M3 if capacity else None,
        "incl_capacity_m3": capacity,
    }


def _excl(total_m3: float, ex: dict) -> float:
    """Total storage to storage excluding the reserve, within [0, capacity]."""
    return min(max(total_m3 - ex["reserve_m3"], 0), ex["capacity_m3"] or float("inf"))


def baseline_norain(obs: dict, base: date, as_of: datetime | None = None, horizon: int = HORIZON_DAYS) -> dict | None:
    """No-rain baseline: extrapolate the recent dry-day decline of total storage.

    rate = median daily change over dry days in the last RATE_WINDOW_DAYS
    (all days if fewer than MIN_DRY_DAYS are dry), capped at 0 so the
    baseline never assumes inflow. The reserve is subtracted (excl_base) and
    storage is floored at 0.
    """
    cur = obs["total"].get(base.isoformat())
    ex = excl_base(obs, base)
    if cur is None or ex is None:
        return None
    changes = daily_changes(obs, base, RATE_WINDOW_DAYS)
    pool, pool_name = rate_pool(changes)
    if not pool:
        return None
    rate = min(statistics.median(pool), 0)
    capacity = ex["capacity_m3"]
    series = []
    for h in range(1, horizon + 1):
        s = _excl(cur["storage_m3"] + rate * h, ex)
        series.append(
            {
                "date": (base + timedelta(days=h)).isoformat(),
                "horizon_days": h,
                "storage_m3": round(s),
                "storage_pct": round(100 * s / capacity, 2) if capacity else None,
            }
        )
    return {
        "inputs": {
            "base_date": base.isoformat(),
            "base_storage_m3": cur["storage_m3"],
            **ex,
            "daily_changes": changes,
            "rate_pool": pool_name,
        },
        "params": {"rate_m3_per_day": rate, "window_days": RATE_WINDOW_DAYS, "min_dry_days": MIN_DRY_DAYS},
        "forecast": series,
    }


def _capacity_from_pct(cur: dict) -> int | None:
    if cur.get("storage_pct"):
        return round(cur["storage_m3"] * 100 / cur["storage_pct"])
    return None


def _latest(rows: list[dict], as_of: datetime) -> dict | None:
    rows = [r for r in rows if datetime.fromisoformat(r["report_datetime"]) <= as_of]
    return max(rows, key=lambda r: r["report_datetime"]) if rows else None


N_SIMS = 2000

# v1 storage model, chosen by hindcasts from 2023-08 to 2026-09 (docs/models.md):
# change(t) = level + sum_j quick_j * rain(t - j) + slow_coef * slow(t)
QUICK_LAGS = 4  # rain on the day and the 3 days before
SLOW_TAU_DAYS = 10.0  # e-folding time of the slow store fed by older rain
SLOW_WARMUP_DAYS = 120
FULL_SHARE = 0.93  # days after storage above this share of capacity are left out of the fit (spill)
LEVEL_WINDOW_DAYS = 28
MIN_LEVEL_DAYS = 7
MIN_TRAINING_DAYS = 180
ANALOG_HALF_WINDOW_DAYS = 30


def _lstsq(X: list[list[float]], y: list[float]) -> list[float]:
    """Ordinary least squares by Gauss-Jordan on the normal equations (few columns)."""
    k = len(X[0])
    A = [[sum(r[i] * r[j] for r in X) for j in range(k)] for i in range(k)]
    b = [sum(r[i] * t for r, t in zip(X, y)) for i in range(k)]
    for i in range(k):
        p = max(range(i, k), key=lambda r: abs(A[r][i]))
        A[i], A[p], b[i], b[p] = A[p], A[i], b[p], b[i]
        if A[i][i] == 0:
            raise ValueError("singular training matrix")
        for r in range(k):
            if r != i:
                f = A[r][i] / A[i][i]
                A[r] = [a - f * c for a, c in zip(A[r], A[i])]
                b[r] -= f * b[i]
    return [b[i] / A[i][i] for i in range(k)]


def _slow_states(rain: dict[date, float]) -> dict[date, float]:
    """slow(t) = sum over j >= QUICK_LAGS of rain(t - j) * exp(-(j - QUICK_LAGS) / tau) / tau; missing rain counts as 0."""
    out, b = {}, 0.0
    if not rain:
        return out
    decay = math.exp(-1 / SLOW_TAU_DAYS)
    d, last = min(rain), max(rain)
    while d <= last + timedelta(days=QUICK_LAGS):
        b = b * decay + rain.get(d - timedelta(days=QUICK_LAGS), 0.0) / SLOW_TAU_DAYS
        out[d] = b
        d += timedelta(days=1)
    return out


def fit_storage_model(obs: dict, base: date) -> dict | None:
    """Fit the v1 storage model on days up to ``base``; None when there are too few days."""
    hist, rain = obs["history"], obs["rain_daily"]
    if not rain:
        return None
    slow = _slow_states(rain)
    first = min(rain) + timedelta(days=SLOW_WARMUP_DAYS)
    rows = []
    for t in sorted(hist):
        prev = t - timedelta(days=1)
        if t > base or t < first or prev not in hist:
            continue
        s_prev, cap_prev = hist[prev]
        if cap_prev and s_prev / cap_prev > FULL_SHARE:
            continue
        quick = [rain.get(t - timedelta(days=j)) for j in range(QUICK_LAGS)]
        if None in quick:
            continue
        rows.append({"date": t, "change_m3": hist[t][0] - s_prev, "x": quick + [slow[t]]})
    if len(rows) < MIN_TRAINING_DAYS:
        return None
    coef = _lstsq([[1.0] + r["x"] for r in rows], [r["change_m3"] for r in rows])
    for r in rows:
        r["resid"] = r["change_m3"] - coef[0] - sum(c * x for c, x in zip(coef[1:], r["x"]))
    resid = {r["date"]: r["resid"] for r in rows}

    def window(t: date, days: range) -> list[float]:
        return [resid[t + timedelta(days=j)] for j in days if t + timedelta(days=j) in resid]

    # Spread of the level: how far the next 28 days' mean departs from the last 28 days' median.
    drifts = []
    for t in resid:
        past, fut = window(t, range(-LEVEL_WINDOW_DAYS + 1, 1)), window(t, range(1, LEVEL_WINDOW_DAYS + 1))
        if len(past) >= MIN_LEVEL_DAYS and len(fut) >= LEVEL_WINDOW_DAYS // 2:
            drifts.append(statistics.fmean(fut) - statistics.median(past))
    recent = window(base, range(-LEVEL_WINDOW_DAYS + 1, 1))
    level = statistics.median(recent) + coef[0] if len(recent) >= MIN_LEVEL_DAYS else coef[0]
    digest = hashlib.sha256(json.dumps(
        [[r["date"].isoformat(), r["change_m3"], r["x"]] for r in rows]).encode()).hexdigest()
    return {
        "const_m3_per_day": coef[0],
        "quick_m3_per_mm": coef[1:1 + QUICK_LAGS],
        "slow_m3_per_mm": coef[1 + QUICK_LAGS],
        "level_m3_per_day": level,
        "level_source": "recent_residuals" if len(recent) >= MIN_LEVEL_DAYS else "const",
        "level_days": len(recent),
        "level_sigma_m3_per_day": statistics.pstdev(drifts) if len(drifts) > 1 else 0.0,
        "training_days": len(rows),
        "training_first": rows[0]["date"].isoformat(),
        "training_last": rows[-1]["date"].isoformat(),
        "training_sha256": digest,
        "slow_state": slow,
    }


def _storage_path(fit: dict, s0: float, base: date, horizon: int, level: float,
                  rain: dict[date, float], future: dict[date, float], ex: dict) -> list[float]:
    """Storage excluding the reserve for each day after ``base``, from the fitted storage model.

    Rain is the observed value when there is one, else ``future`` (0 when absent).
    Total storage is capped to [0, capacity] before the reserve is subtracted.
    """
    decay = math.exp(-1 / SLOW_TAU_DAYS)
    quick, slow_coef = fit["quick_m3_per_mm"], fit["slow_m3_per_mm"]
    total_capacity = ex["incl_capacity_m3"]

    def rain_at(d: date) -> float:
        return rain[d] if d in rain else future.get(d, 0.0)

    s, b, path = s0, fit["slow_state"][base], []
    for h in range(1, horizon + 1):
        t = base + timedelta(days=h)
        b = b * decay + rain_at(t - timedelta(days=QUICK_LAGS)) / SLOW_TAU_DAYS
        change = level + sum(q * rain_at(t - timedelta(days=j)) for j, q in enumerate(quick)) + slow_coef * b
        s = min(max(s + change, 0), total_capacity or float("inf"))
        path.append(_excl(s, ex))
    return path


def _fit_params(fit: dict) -> dict:
    """The fitted values for ``params``, rounded."""
    params = {k: v for k, v in fit.items() if k not in ("slow_state", "training_sha256")}
    for k in ("const_m3_per_day", "slow_m3_per_mm", "level_m3_per_day", "level_sigma_m3_per_day"):
        params[k] = round(params[k], 1)
    params["quick_m3_per_mm"] = [round(v, 1) for v in params["quick_m3_per_mm"]]
    params.update({"slow_tau_days": SLOW_TAU_DAYS, "full_share": FULL_SHARE})
    return params


def analog_windows(rain: dict[date, float], base: date, horizon: int) -> list[dict]:
    """Observed rain of the ``horizon`` days after dates near the same calendar day in other years."""
    out = []
    for y in sorted({d.year for d in rain}):
        if y == base.year:
            continue
        anchor = date(y, base.month, min(base.day, 28 if base.month == 2 else base.day))
        for off in range(-ANALOG_HALF_WINDOW_DAYS, ANALOG_HALF_WINDOW_DAYS + 1):
            start = anchor + timedelta(days=off)
            seq = [rain.get(start + timedelta(days=h)) for h in range(1, horizon + 1)]
            if None not in seq:
                out.append({"start": start.isoformat(), "rain_mm": seq, "total_mm": sum(seq)})
    return out


def _tercile_weights(windows: list[dict], weights: dict[str, float]) -> list[float]:
    """Weight each window by the JMA probability of its tercile, split evenly within the tercile."""
    totals = sorted(w["total_mm"] for w in windows)
    lo, hi = totals[len(totals) // 3], totals[(2 * len(totals)) // 3]
    cats = ["below" if w["total_mm"] < lo else "normal" if w["total_mm"] < hi else "above" for w in windows]
    counts = {c: cats.count(c) for c in weights}
    for w, c in zip(windows, cats):
        w["category"] = c
    return [weights[c] / counts[c] for c in cats]


def scenario_jma_tercile(obs: dict, base: date, as_of: datetime, horizon: int = HORIZON_DAYS) -> dict | None:
    """Probabilistic outlook of storage excluding the drought reserve, with rain from past years, weighted by the JMA terciles.

    The storage model is fitted on the history up to the base date
    (fit_storage_model). Each simulation draws one past rain sequence of the
    same season (analog_windows), with probability set by the 1-month
    forecast's tercile for that sequence's total (equal without a forecast),
    and one level offset from N(0, sigma), where sigma is how much the level
    moved over 28 days in the history. Days already observed after the base
    date use the observed rain. Total storage is capped to [0, capacity];
    the reserve is then subtracted (excl_base).
    """
    cur = obs["total"].get(base.isoformat())
    ex = excl_base(obs, base)
    if cur is None or ex is None:
        return None
    capacity = ex["capacity_m3"]
    fit = fit_storage_model(obs, base)
    rain = obs["rain_daily"]
    if fit is None:
        return None
    windows = analog_windows(rain, base, horizon)
    if len(windows) < 3:
        return None
    p1m = _latest(obs["p1m_precip"], as_of)
    weights = (
        {"below": p1m["below_pct"], "normal": p1m["normal_pct"], "above": p1m["above_pct"]}
        if p1m
        else {"below": 1, "normal": 1, "above": 1}
    )
    win_w = _tercile_weights(windows, weights)
    recent_rain = {d.isoformat(): rain[d] for d in sorted(rain) if base - timedelta(days=QUICK_LAGS) < d}
    s0 = cur["storage_m3"]
    inputs = {
        "base_date": base.isoformat(),
        "base_storage_m3": s0,
        **ex,
        "training_sha256": fit["training_sha256"],
        "slow_state_mm": round(fit["slow_state"][base], 4),
        "observed_rain_mm": recent_rain,
        # The windows are rebuilt from jma_daily_precip.csv; the digest pins which ones were used.
        "analog_windows_sha256": hashlib.sha256(json.dumps(
            [[w["start"], w["rain_mm"]] for w in windows]).encode()).hexdigest(),
        "analog_categories": {c: sum(w["category"] == c for w in windows) for c in weights},
        "p1m_precip": p1m,
    }
    seed = int(hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()[:16], 16)
    rng = random.Random(seed)
    sigma = fit["level_sigma_m3_per_day"]
    paths = []
    for _ in range(N_SIMS):
        w = rng.choices(windows, weights=win_w)[0]
        level = fit["level_m3_per_day"] + rng.gauss(0, sigma)
        future = {base + timedelta(days=h + 1): v for h, v in enumerate(w["rain_mm"])}
        paths.append(_storage_path(fit, s0, base, horizon, level, rain, future, ex))

    series = []
    for h in range(1, horizon + 1):
        vals = sorted(p[h - 1] for p in paths)
        q = lambda f: vals[min(int(f * len(vals)), len(vals) - 1)]
        row = {
            "date": (base + timedelta(days=h)).isoformat(),
            "horizon_days": h,
            "storage_m3_p10": round(q(0.1)),
            "storage_m3_p50": round(q(0.5)),
            "storage_m3_p90": round(q(0.9)),
        }
        if capacity:
            row["storage_pct_p50"] = round(100 * q(0.5) / capacity, 2)
            row["prob_below_pct"] = {
                str(t): round(sum(v < capacity * t / 100 for v in vals) / len(vals), 3) for t in THRESHOLDS_PCT
            }
        series.append(row)
    params = _fit_params(fit)
    params.update({
        "category_weights": weights,
        "analog_half_window_days": ANALOG_HALF_WINDOW_DAYS,
        "analog_windows_n": len(windows),
        "n_sims": N_SIMS,
        "seed": seed,
    })
    return {"inputs": inputs, "params": params, "forecast": series}


def scenario_norain(obs: dict, base: date, as_of: datetime | None = None, horizon: int = HORIZON_DAYS) -> dict | None:
    """Storage excluding the drought reserve if no rain falls after the base date.

    The same storage model as scenario_jma_tercile (fit_storage_model), run
    once with no future rain and no level offset: the inflow of the rain
    already fallen fades on its fitted delay, and storage then falls at the
    fitted level. Days already observed after the base date use the observed
    rain. This is the no-rain line on the site; baseline_norain stays the
    naive reference for verification.
    """
    cur = obs["total"].get(base.isoformat())
    ex = excl_base(obs, base)
    if cur is None or ex is None:
        return None
    fit = fit_storage_model(obs, base)
    if fit is None:
        return None
    rain = obs["rain_daily"]
    capacity = ex["capacity_m3"]
    path = _storage_path(fit, cur["storage_m3"], base, horizon, fit["level_m3_per_day"], rain, {}, ex)
    series = [
        {
            "date": (base + timedelta(days=h)).isoformat(),
            "horizon_days": h,
            "storage_m3": round(s),
            "storage_pct": round(100 * s / capacity, 2) if capacity else None,
        }
        for h, s in enumerate(path, start=1)
    ]
    inputs = {
        "base_date": base.isoformat(),
        "base_storage_m3": cur["storage_m3"],
        **ex,
        "training_sha256": fit["training_sha256"],
        "slow_state_mm": round(fit["slow_state"][base], 4),
        "observed_rain_mm": {d.isoformat(): rain[d] for d in sorted(rain) if base - timedelta(days=QUICK_LAGS) < d},
    }
    return {"inputs": inputs, "params": _fit_params(fit), "forecast": series}


# name -> (version, function). Bump the version whenever a model's behavior changes.
MODELS: dict[str, tuple[str, Callable[[dict, date, datetime], dict | None]]] = {
    "baseline_norain": ("2", baseline_norain),
    "scenario_jma_tercile": ("2", scenario_jma_tercile),
    "scenario_norain": ("1", scenario_norain),
}
