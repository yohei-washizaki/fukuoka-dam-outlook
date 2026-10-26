"""Issue forecasts from the normalized tables and store them immutably.

Run: ``PYTHONPATH=src python -m dam_outlook.forecast``

Each forecast is one JSON file under ``data/forecasts/<YYYY>/<MM>/`` with its
issue time, model name and version, the exact inputs it used, and the
predicted series. Files are created with exclusive mode and never edited. Each
model version issues one forecast per base date: the first run that sees a new
observation date issues it, and later runs on the same base date add nothing,
even when other inputs (rain, the 1-month forecast, past reports) were filled
in since.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date, datetime
from pathlib import Path

from .models import MODELS, load_observations
from .raw_store import JST, stamp


# ---------------------------------------------------------------- storage


def inputs_sha256(inputs: dict) -> str:
    return hashlib.sha256(json.dumps(inputs, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def load_forecasts(forecast_dir: Path) -> list[dict]:
    out = []
    for p in sorted(forecast_dir.glob("*/*/*.json")):
        with p.open(encoding="utf-8") as f:
            out.append(json.load(f))
    return out


def issue(normalized_dir: Path, forecast_dir: Path, now: datetime) -> list[Path]:
    obs = load_observations(normalized_dir)
    if not obs["total"]:
        return []
    base = date.fromisoformat(max(obs["total"]))
    existing = load_forecasts(forecast_dir)
    written = []
    for name, (version, fn) in MODELS.items():
        result = fn(obs, base, now)
        if result is None:
            continue
        if any(f["model"] == name and f["model_version"] == version and f["base_date"] == base.isoformat()
               for f in existing):
            continue
        doc = {
            "model": name,
            "model_version": version,
            "issued_at": now.isoformat(timespec="seconds"),
            "base_date": base.isoformat(),
            "target": "total_storage_m3",
            "base_sources": obs["total"][base.isoformat()]["sources"],
            "inputs_sha256": inputs_sha256(result["inputs"]),
            **result,
        }
        path = forecast_dir / now.strftime("%Y/%m") / f"{stamp(now)}__{name}-v{version}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as f:  # never overwrite
            json.dump(doc, f, ensure_ascii=False, indent=1)
            f.write("\n")
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--normalized-dir", type=Path, default=Path("data/normalized"))
    parser.add_argument("--forecast-dir", type=Path, default=Path("data/forecasts"))
    args = parser.parse_args(argv)
    for p in issue(args.normalized_dir, args.forecast_dir, datetime.now(JST)):
        print(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
