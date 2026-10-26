"""Append-only storage of raw fetched responses.

Each stored body goes to ``<root>/<source>/<subdir>/<name>__<fetched>.<ext>.gz``
(gzip of the exact response bytes; PDFs are stored as-is). Every fetch attempt,
stored or not, is appended to ``<root>/_log/<YYYY-MM>.jsonl``. Existing files
are never overwritten.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9), "JST")

# Formats that are already compressed are stored without gzip.
UNCOMPRESSED_EXTS = {"pdf"}


@dataclass
class FetchRecord:
    source: str
    key: str
    url: str
    fetched_at: datetime
    status: int | None
    sha256: str | None = None
    size: int | None = None
    path: str | None = None
    stored: bool = False
    note: str | None = None
    meta: dict | None = None

    def to_json(self) -> dict:
        d = {
            "source": self.source,
            "key": self.key,
            "url": self.url,
            "fetched_at": self.fetched_at.isoformat(timespec="seconds"),
            "status": self.status,
            "sha256": self.sha256,
            "size": self.size,
            "path": self.path,
            "stored": self.stored,
        }
        if self.note:
            d["note"] = self.note
        if self.meta:
            d["meta"] = self.meta
        return d


def stamp(dt: datetime) -> str:
    return dt.astimezone(JST).strftime("%Y%m%dT%H%M%S%z")


class RawStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.log_dir = self.root / "_log"
        self._log_cache: list[dict] | None = None

    def log_entries(self) -> list[dict]:
        if self._log_cache is None:
            entries = []
            if self.log_dir.is_dir():
                for p in sorted(self.log_dir.glob("*.jsonl")):
                    with p.open(encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if line:
                                entries.append(json.loads(line))
            self._log_cache = entries
        return self._log_cache

    def last_stored(self, key: str) -> dict | None:
        for e in reversed(self.log_entries()):
            if e["key"] == key and e.get("stored"):
                return e
        return None

    def has_stored(self, key: str, **meta) -> bool:
        """Whether a body for ``key`` was stored with all given meta values."""
        for e in self.log_entries():
            if e["key"] == key and e.get("stored"):
                m = e.get("meta") or {}
                if all(m.get(k) == v for k, v in meta.items()):
                    return True
        return False

    def append_log(self, rec: FetchRecord) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        month = rec.fetched_at.astimezone(JST).strftime("%Y-%m")
        with (self.log_dir / f"{month}.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec.to_json(), ensure_ascii=False) + "\n")
        self.log_entries().append(rec.to_json())

    def save(
        self,
        *,
        source: str,
        key: str,
        subdir: str,
        name: str,
        ext: str,
        url: str,
        fetched_at: datetime,
        status: int,
        body: bytes,
        dedupe: bool = True,
        meta: dict | None = None,
    ) -> FetchRecord:
        """Store ``body`` unless it is identical to the last stored body for ``key``."""
        sha = hashlib.sha256(body).hexdigest()
        rec = FetchRecord(
            source=source,
            key=key,
            url=url,
            fetched_at=fetched_at,
            status=status,
            sha256=sha,
            size=len(body),
            meta=meta,
        )
        prev = self.last_stored(key) if dedupe else None
        if prev is not None and prev.get("sha256") == sha:
            rec.note = "unchanged"
            rec.path = prev.get("path")
            self.append_log(rec)
            return rec

        filename = f"{name}__{stamp(fetched_at)}.{ext}"
        compress = ext not in UNCOMPRESSED_EXTS
        if compress:
            filename += ".gz"
        path = self.root / source / subdir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        data = gzip.compress(body, mtime=0) if compress else body
        with path.open("xb") as f:  # "x": never overwrite
            f.write(data)
        rec.path = path.relative_to(self.root).as_posix()
        rec.stored = True
        self.append_log(rec)
        return rec

    def log_failure(
        self,
        *,
        source: str,
        key: str,
        url: str,
        fetched_at: datetime,
        status: int | None,
        note: str,
        meta: dict | None = None,
    ) -> FetchRecord:
        rec = FetchRecord(
            source=source,
            key=key,
            url=url,
            fetched_at=fetched_at,
            status=status,
            note=note,
            meta=meta,
        )
        self.append_log(rec)
        return rec

    def read_body(self, rel_path: str) -> bytes:
        p = self.root / rel_path
        data = p.read_bytes()
        return gzip.decompress(data) if p.suffix == ".gz" else data
