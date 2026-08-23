"""Plain file tree adapter (Drive mirror deployment)."""

from __future__ import annotations

import fnmatch
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from corpusindex.adapters.base import Doc, DocProbe
from corpusindex.config import SourceConfig
from corpusindex.extract.convert import convert
from corpusindex.extract.decode import classify, decode_text, is_binary
from corpusindex.extract.html_text import html_to_text

BOOKKEEPING_NAMES = frozenset({"msg-db.sqlite"})
SKIP_DIR_NAMES = frozenset({".git"})
_HTML_EXTENSIONS = frozenset({".html", ".htm"})


def matches_globs(relpath: str, include: tuple[str, ...], exclude: tuple[str, ...]) -> bool:
    """True when relpath (POSIX-relative) passes include/exclude glob filters.

    Empty include matches everything; any exclude match rejects. fnmatch
    patterns cross path separators (no special '/' handling), so '*' and
    '**' behave the same in a pattern here.
    """
    if include and not any(fnmatch.fnmatch(relpath, pat) for pat in include):
        return False
    return not any(fnmatch.fnmatch(relpath, pat) for pat in exclude)


def _is_bookkeeping(rel_parts: tuple[str, ...]) -> bool:
    if any(part in SKIP_DIR_NAMES for part in rel_parts[:-1]):
        return True
    return rel_parts[-1] in BOOKKEEPING_NAMES


def _stat_sig(path: Path) -> str:
    st = path.stat()
    return f"{st.st_mtime_ns}:{st.st_size}"


class FiletreeAdapter:
    def __init__(self, config: SourceConfig) -> None:
        self.config = config
        self.name = config.name

    def discover(self) -> Iterator[DocProbe]:
        root = self.config.root
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            rel_parts = path.relative_to(root).parts
            if _is_bookkeeping(rel_parts):
                continue
            relpath = "/".join(rel_parts)
            if not matches_globs(relpath, self.config.include, self.config.exclude):
                continue
            yield DocProbe(source=self.name, path=relpath, stat_sig=_stat_sig(path))

    def load(self, probe: DocProbe) -> Doc:
        path = self.config.root / probe.path
        raw = path.read_bytes()
        content_hash = hashlib.sha256(raw).hexdigest()
        st = path.stat()
        doc_date = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()

        text: str | None = None
        classification = classify(probe.path)
        if classification == "decode" and not is_binary(raw):
            text = decode_text(raw)
            if Path(probe.path).suffix.lower() in _HTML_EXTENSIONS:
                text = html_to_text(text)
        elif classification == "convert":
            ext = Path(probe.path).suffix.lower().lstrip(".")
            if ext in self.config.convert:
                text = convert(path)

        return Doc(
            probe=probe,
            content_hash=content_hash,
            title=path.name,
            doc_date=doc_date,
            bytes=len(raw),
            meta={},
            text=text,
        )
