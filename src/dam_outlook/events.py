"""Prepare source reviews for curated events and sync their verified state.

Run: ``PYTHONPATH=src python -m dam_outlook.events {pending,review,sync}``

An event in ``data/curated/events.csv`` becomes verified when a human merges
the pull request that adds its review note under
``data/curated/event_reviews/<event id>.md``. ``review`` fetches the source,
stores it unparsed under ``data/raw/event_sources/<event id>/`` and writes the
note with automatically extracted excerpts; ``sync`` sets ``verified=true``
for every event whose note is on the current branch.
"""

from __future__ import annotations

import argparse
import csv
import io
import re
import sys
import unicodedata
from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path

from .fetch import http_get
from .normalize import pdf_text
from .raw_store import JST, RawStore

EVENTS = Path("data/curated/events.csv")
REVIEWS = Path("data/curated/event_reviews")
RAW = Path("data/raw/event_sources")
MAX_EXCERPTS = 5


def read_events(path: Path = EVENTS) -> tuple[list[str], list[dict]]:
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    ids = [event_id(r) for r in rows]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        raise ValueError(f"duplicate event ids in {path}: {', '.join(dup)}")
    return list(reader.fieldnames or []), rows


def write_events(columns: list[str], rows: list[dict], path: Path = EVENTS) -> None:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    path.write_text(buf.getvalue(), encoding="utf-8")


def event_id(row: dict) -> str:
    return f"{row['date']}_{row['kind']}"


def pending(rows: list[dict], reviews: Path = REVIEWS) -> list[dict]:
    """Unverified events with a source and no review note yet."""
    return [
        r for r in rows
        if r["verified"] != "true" and r["source"] and not (reviews / f"{event_id(r)}.md").exists()
    ]


# ---------------------------------------------------------------- text and excerpts


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in ("p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def source_pages(body: bytes) -> list[str]:
    """Text per page (PDF) or a single page (HTML), NFKC-normalized."""
    if body.startswith(b"%PDF"):
        from pypdf import PdfReader

        pages = [p.extract_text() or "" for p in PdfReader(io.BytesIO(body)).pages]
    else:
        html = body.decode("utf-8", errors="replace")
        # Prefer the article body so site navigation does not produce matches.
        m = re.search(r"<article\b.*?</article>", html, re.S)
        p = _Text()
        p.feed(m.group(0) if m else html)
        pages = ["".join(p.parts)]
    return [unicodedata.normalize("NFKC", t) for t in pages]


VOCABULARY = ("渇水調整", "取水制限", "送水制限", "給水制限", "渇水対策本部", "渇水対策会議", "減圧給水", "解除", "継続", "強化", "設置", "開始")


def search_terms(row: dict) -> dict[str, list[str]]:
    d = date.fromisoformat(row["date"])
    terms = {"日付": [f"{d.month}月{d.day}日", f"{d.month}/{d.day}"]}
    pct = row.get("chikugo_intake_restriction_pct")
    if pct and pct != "0":
        terms["制限率"] = [f"{pct}%"]
    detail = re.sub(r"\s+", "", unicodedata.normalize("NFKC", row["detail"]))
    words = [v for v in VOCABULARY if v in detail]
    words += re.findall(r"第\d+次", detail)
    rest = re.sub("|".join(VOCABULARY) + r"|第\d+次|[\d%→]+", " ", detail).split()
    words += [w for w in rest if len(w) >= 3]
    if words:
        terms["内容"] = words
    return terms


def excerpts(pages: list[str], terms: dict[str, list[str]]) -> tuple[dict[str, bool], list[str]]:
    """Which term groups occur, and text windows around matches that hit the most groups.

    Whitespace is removed before matching because PDF text breaks lines and
    spaces out digits ("9月 17 日").
    """
    found = {k: False for k in terms}
    windows = []  # (groups hit, page, start, text)
    for n, text in enumerate(pages, 1):
        flat = re.sub(r"\s+", "", text)
        spans = []
        for ts in terms.values():
            for t in ts:
                for m in re.finditer(re.escape(t), flat):
                    spans.append((max(m.start() - 60, 0), min(m.end() + 80, len(flat))))
        merged = []
        for a, b in sorted(spans):
            if merged and a <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        for a, b in merged:
            chunk = flat[a:b]
            groups = {k for k, ts in terms.items() if any(t in chunk for t in ts)}
            found.update({k: True for k in groups})
            windows.append((len(groups), n, a, chunk))
    best = sorted(windows, key=lambda w: (-w[0], w[1], w[2]))[:MAX_EXCERPTS]
    return found, [f"p.{n}: …{chunk}…" for _, n, _, chunk in sorted(best, key=lambda w: (w[1], w[2]))]


# ---------------------------------------------------------------- review note


def review(row: dict, get=http_get, now: datetime | None = None,
           raw_root: Path = RAW, reviews: Path = REVIEWS) -> Path:
    eid = event_id(row)
    now = now or datetime.now(JST)
    store = RawStore(raw_root / eid)
    status, body = get(row["source"])
    if status != 200:
        store.log_failure(source="event_source", key=eid, url=row["source"], fetched_at=now,
                          status=status, note="http error")
        raise RuntimeError(f"{eid}: source returned {status}")
    ext = "pdf" if body.startswith(b"%PDF") else "html"
    rec = store.save(source="event_source", key=eid, subdir=".", name="source", ext=ext,
                     url=row["source"], fetched_at=now, status=status, body=body, dedupe=False)
    try:
        pages = source_pages(body)
    except Exception as e:  # keep the note even when text extraction fails
        pages = []
        extraction_error = f"{type(e).__name__}: {e}"
    else:
        extraction_error = None
    terms = search_terms(row)
    found, lines = excerpts(pages, terms)

    raw_path = (raw_root / eid / rec.path).as_posix()
    check = "\n".join(
        f"- {'見つかった' if found[k] else '**見つからない**'}: {k}（{' / '.join(ts)}）" for k, ts in terms.items()
    )
    quoted = "\n".join(f"> {l}" for l in lines) or "> （該当する行なし）"
    if extraction_error:
        quoted = f"> 本文を取り出せませんでした（{extraction_error}）。出典を直接確認してください。"
    note = f"""---
event: {eid}
date: {row['date']}
kind: {row['kind']}
detail: {row['detail']}
chikugo_intake_restriction_pct: {row.get('chikugo_intake_restriction_pct', '')}
date_basis: {row.get('date_basis', '')}
source: {row['source']}
source_sha256: {rec.sha256}
raw_path: {raw_path}
fetched_at: {now.isoformat(timespec='seconds')}
prepared_by: dam_outlook.events (automatic; not a verification)
---

# 出来事の照合: {row['date']} {row['detail']}

この PR をマージすると、`events.csv` のこの行を照合済み（`verified=true`）として扱います。

## 確認すること

1. 出典を開く: {row['source']}
2. 日付・内容・制限率が、下の CSV の値と一致しているか確かめる。`date` は効力を持ち始めた日（制限の開始日時・組織の設置日）で、決まった日や発表日ではない（例: 9/17 に決まり「9月18日(金)9:00~」に強化 → 9/18）。出典に実施日がない場合だけ決まった日とし、`date_basis` が `effective`（実施日）か `decided`（決まった日）かも確かめる。
3. 一致していればマージする。違っていれば、この PR で `data/curated/events.csv` を直してからマージするか、理由を書いて PR を閉じる。

| 列 | 値 |
|---|---|
| date | {row['date']} |
| kind | {row['kind']} |
| detail | {row['detail']} |
| chikugo_intake_restriction_pct | {row.get('chikugo_intake_restriction_pct', '')} |
| date_basis | {row.get('date_basis', '')} |

## 自動照合（参考）

出典の本文から語句を機械的に探した結果です。日付は `date` の日だけを探すので、発表日や決まった日が別の日付で書かれていても見つかったことにはなりません。見つからなくても誤りとは限りません。

{check}

## 抜粋（自動抽出）

{quoted}
"""
    reviews.mkdir(parents=True, exist_ok=True)
    path = reviews / f"{eid}.md"
    with path.open("x", encoding="utf-8") as f:
        f.write(note)
    return path


def sync(path: Path = EVENTS, reviews: Path = REVIEWS) -> list[str]:
    """Mark events verified when their review note exists. Returns the changed ids."""
    columns, rows = read_events(path)
    changed = []
    for r in rows:
        if r["verified"] != "true" and (reviews / f"{event_id(r)}.md").exists():
            r["verified"] = "true"
            changed.append(event_id(r))
    if changed:
        write_events(columns, rows, path)
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("pending", help="print ids of events that need a review")
    rv = sub.add_parser("review", help="fetch the source and write the review note")
    rv.add_argument("event_id")
    sub.add_parser("sync", help="set verified=true for events with a review note")
    args = parser.parse_args(argv)

    _, rows = read_events()
    if args.cmd == "pending":
        for r in pending(rows):
            print(event_id(r))
        return 0
    if args.cmd == "review":
        row = next((r for r in rows if event_id(r) == args.event_id), None)
        if row is None:
            print(f"unknown event {args.event_id}", file=sys.stderr)
            return 2
        print(review(row))
        return 0
    for eid in sync():
        print(f"verified {eid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
