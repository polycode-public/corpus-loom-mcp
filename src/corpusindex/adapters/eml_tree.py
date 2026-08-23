""".eml mail tree adapter (gyb layout: mailbox/YYYY/M/D/<hex>.eml)."""

from __future__ import annotations

import email
import email.policy
import hashlib
import io
import sqlite3
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Iterator

from corpusindex.adapters.base import Doc, DocProbe
from corpusindex.adapters.filetree import SKIP_DIR_NAMES, matches_globs
from corpusindex.config import SourceConfig
from corpusindex.extract.mailbody import extract_body

LABELS_DB_NAME = "msg-db.sqlite"
_FILENAME_COLUMN_HINTS = ("filename", "file_name", "fname")
_LABELS_COLUMN_HINTS = ("labels", "label")


def _load_labels(root: Path) -> dict[str, list[str]]:
    """Best-effort read of gyb's msg-db.sqlite labels, keyed by .eml stem.

    Schema is not part of any stable public contract, so this introspects
    the tables for a filename-like and a labels-like column rather than
    assuming fixed names. Absent db, unreadable db, or no matching table
    all resolve to no labels.
    """
    db_path = root / LABELS_DB_NAME
    if not db_path.is_file():
        return {}
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return {}
    labels: dict[str, list[str]] = {}
    try:
        tables = [
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        ]
        for table in tables:
            columns = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
            lower = {c.lower(): c for c in columns}
            fname_col = next(
                (orig for lc, orig in lower.items() if any(h in lc for h in _FILENAME_COLUMN_HINTS)),
                None,
            )
            label_col = next(
                (orig for lc, orig in lower.items() if any(h in lc for h in _LABELS_COLUMN_HINTS)),
                None,
            )
            if fname_col is None or label_col is None:
                continue
            rows = conn.execute(f"SELECT {fname_col}, {label_col} FROM {table}")
            for fname_val, label_val in rows:
                if not fname_val or not label_val:
                    continue
                stem = Path(str(fname_val)).stem
                items = [s.strip() for s in str(label_val).split(",") if s.strip()]
                if items:
                    labels[stem] = items
    except sqlite3.Error:
        return {}
    finally:
        conn.close()
    return labels


def _fallback_date_from_path(relpath: str) -> str | None:
    parts = PurePosixPath(relpath).parts
    if len(parts) < 4:
        return None
    year, month, day = parts[0], parts[1], parts[2]
    try:
        dt = datetime(int(year), int(month), int(day), tzinfo=timezone.utc)
    except ValueError:
        return None
    return dt.isoformat()


def _header_str(msg: email.message.Message, name: str) -> str | None:
    value = msg.get(name)
    return str(value) if value is not None else None


class EmlTreeAdapter:
    def __init__(self, config: SourceConfig) -> None:
        self.config = config
        self.name = config.name
        self._labels = _load_labels(config.root)

    def discover(self) -> Iterator[DocProbe]:
        root = self.config.root
        for path in sorted(root.rglob("*.eml")):
            if not path.is_file():
                continue
            rel_parts = path.relative_to(root).parts
            if any(part in SKIP_DIR_NAMES for part in rel_parts[:-1]):
                continue
            relpath = "/".join(rel_parts)
            if not matches_globs(relpath, self.config.include, self.config.exclude):
                continue
            st = path.stat()
            stat_sig = f"{st.st_mtime_ns}:{st.st_size}"
            yield DocProbe(source=self.name, path=relpath, stat_sig=stat_sig)

    def load(self, probe: DocProbe) -> Doc:
        path = self.config.root / probe.path
        raw = path.read_bytes()
        msg = email.message_from_binary_file(io.BytesIO(raw), policy=email.policy.default)

        content_hash = hashlib.sha256(raw).hexdigest()

        date_header = msg.get("Date")
        dt = getattr(date_header, "datetime", None) if date_header is not None else None
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            doc_date = dt.astimezone(timezone.utc).isoformat()
        else:
            doc_date = _fallback_date_from_path(probe.path)

        body = extract_body(msg)
        text = body.text if body.text else None

        stem = Path(probe.path).stem
        meta = {
            "message_id": _header_str(msg, "Message-ID"),
            "from": _header_str(msg, "From"),
            "to": _header_str(msg, "To"),
            "cc": _header_str(msg, "Cc"),
            "bcc": _header_str(msg, "Bcc"),
            "reply_to": _header_str(msg, "Reply-To"),
            "attachments": body.attachments,
            "labels": self._labels.get(stem, []),
        }

        return Doc(
            probe=probe,
            content_hash=content_hash,
            title=_header_str(msg, "Subject"),
            doc_date=doc_date,
            bytes=len(raw),
            meta=meta,
            text=text,
        )
