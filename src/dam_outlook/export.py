"""Write the public copy of this repository: code, docs, tables, forecasts and scores.

Run: ``PYTHONPATH=src python -m dam_outlook.export <out_dir>``

Only the paths in INCLUDE are copied; ``data/raw/`` and the city page
fixtures stay private, because Fukuoka City does not allow its pages to be
reproduced. Links to individual city pages also need the city's consent, so
their URLs are masked to the city's top page plus ``...`` (MASKED_URL).
Everything in ``out_dir`` except ``.git`` is replaced, so files
dropped here disappear from the public copy too. The workflow
``.github/workflows/public.yml`` commits the result to the public repository.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

PUBLIC_REPO = "yohei-washizaki/fukuoka-dam-outlook"
INCLUDE = [
    "src/dam_outlook",
    "tests",
    "docs",
    "data/normalized",
    "data/forecasts",
    "data/verification",
    "data/curated/events.csv",
    "data/curated/storage_chart_sources.csv",
    "requirements.txt",
    "pyproject.toml",
]
EXCLUDE = {
    "tests/fixtures/city_dam_text.html",  # copies of city pages
    "tests/fixtures/nippou_2026_10_21.txt",
    "src/dam_outlook/assets/public_README.md",  # becomes README.md
}
README = "src/dam_outlook/assets/public_README.md"
CITY_URL = re.compile(r"https?://www\.city\.fukuoka\.lg\.jp/[A-Za-z0-9._~/?#@!$&*+;=%:-]*")
MASKED_URL = "https://www.city.fukuoka.lg.jp/..."
TEXT_SUFFIXES = {".py", ".md", ".csv", ".json", ".txt", ".html", ".toml"}


def mask(text: str) -> str:
    return CITY_URL.sub(MASKED_URL, text)


def files(root: Path) -> list[Path]:
    out = []
    for inc in INCLUDE:
        p = root / inc
        for f in sorted(p.rglob("*")) if p.is_dir() else [p]:
            rel = f.relative_to(root)
            if f.is_file() and rel.as_posix() not in EXCLUDE and "__pycache__" not in rel.parts:
                out.append(rel)
    return out


def export(root: Path, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    for p in out_dir.iterdir():
        if p.name == ".git":
            continue
        shutil.rmtree(p) if p.is_dir() else p.unlink()
    written = files(root)
    for rel in written:
        (out_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        if rel.suffix in TEXT_SUFFIXES:
            (out_dir / rel).write_text(mask((root / rel).read_text(encoding="utf-8")), encoding="utf-8")
        else:
            shutil.copy2(root / rel, out_dir / rel)
    shutil.copy2(root / README, out_dir / "README.md")
    return written + [Path("README.md")]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    print(f"{len(export(args.root, args.out_dir))} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
