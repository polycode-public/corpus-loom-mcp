"""Incremental indexing: diff each source's discover() output against the
documents table, load/chunk/store changed documents, and delete rows for
documents no longer present.

Entities seam: entities.py is built on a sibling branch and is not present
here. The indexer never imports it at module scope. Instead update_source()
takes an injectable ``store_entities`` callable; when the caller does not
supply one, it defaults to a thin wrapper that imports corpusindex.entities
lazily (inside the wrapper, at call time) and forwards to it exactly as:

    from corpusindex import entities
    entities.store_doc_entities(conn, doc_id, doc, seeds, aliases)

Tests exercise update_source()/update() with a stub store_entities and never
need corpusindex.entities to exist.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import PurePath
from typing import Callable, Iterable

from corpusindex.adapters.base import Doc, DocProbe, SourceAdapter
from corpusindex.config import Config, SourceConfig
from corpusindex.db import has_vec
from corpusindex.extract.chunk import chunk_code, chunk_csv, chunk_prose, chunk_whole
from corpusindex.extract.decode import is_code_extension

logger = logging.getLogger(__name__)

StoreEntities = Callable[[sqlite3.Connection, int, Doc, object, object], None]

_VEC_TABLES = ("chunks_vec_prose", "chunks_vec_code")


@dataclass(slots=True)
class SourceStats:
    added: int = 0
    changed: int = 0
    deleted: int = 0
    unchanged: int = 0
    chunks: int = 0


@dataclass(slots=True)
class UpdateStats:
    sources: dict[str, SourceStats] = field(default_factory=dict)


@dataclass(slots=True)
class _ExistingDoc:
    doc_id: int
    stat_sig: str | None
    content_hash: str


@lru_cache(maxsize=8)
def _load_seeds_cached(path: str | None) -> object:
    from corpusindex import entities

    return entities.load_seeds(path)


@lru_cache(maxsize=8)
def _load_aliases_cached(path: str | None) -> object:
    from corpusindex import entities

    return entities.load_aliases(path)


def _default_store_entities(
    conn: sqlite3.Connection, doc_id: int, doc: Doc, seeds: object, aliases: object
) -> None:
    from corpusindex import entities

    # The update() call site forwards config.seeds/config.aliases, which are
    # paths (or None) — load them here; injected callables may receive either.
    if not hasattr(seeds, "persons"):
        seeds = _load_seeds_cached(str(seeds) if seeds is not None else None)
    if not hasattr(aliases, "alias_of"):
        aliases = _load_aliases_cached(str(aliases) if aliases is not None else None)
    entities.store_doc_entities(conn, doc_id, doc, seeds, aliases)


def _existing_documents(conn: sqlite3.Connection, source_name: str) -> dict[str, _ExistingDoc]:
    rows = conn.execute(
        "SELECT path, doc_id, stat_sig, content_hash FROM documents WHERE source = ?",
        (source_name,),
    )
    return {
        path: _ExistingDoc(doc_id=doc_id, stat_sig=stat_sig, content_hash=content_hash)
        for path, doc_id, stat_sig, content_hash in rows
    }


def _unchanged(probe: DocProbe, existing: _ExistingDoc) -> bool:
    if probe.stat_sig is not None:
        return existing.stat_sig == probe.stat_sig
    if probe.git_sha is not None:
        return existing.content_hash == probe.git_sha
    return False


_MAIL_HEADER_FIELDS = (
    ("From", "from"),
    ("To", "to"),
    ("Cc", "cc"),
    ("Bcc", "bcc"),
    ("Reply-To", "reply_to"),
)


def _mail_header(doc: Doc) -> str:
    subject = doc.title or "(no subject)"
    sender = (doc.meta or {}).get("from", "")
    date = (doc.doc_date or "")[:10]
    return f"{subject} — {sender}, {date}"


def _mail_headers_block(doc: Doc) -> str | None:
    """Plain-text 'Key: value' lines for the synthetic HEADERS chunk: the mail
    address headers, Subject, Date, and comma-joined Gmail Labels. Any field
    absent from doc.meta/title/doc_date is omitted rather than left blank."""
    meta = doc.meta or {}
    lines: list[str] = []
    for label, key in _MAIL_HEADER_FIELDS:
        value = meta.get(key)
        if value:
            lines.append(f"{label}: {value}")
    if doc.title:
        lines.append(f"Subject: {doc.title}")
    if doc.doc_date:
        lines.append(f"Date: {doc.doc_date}")
    labels = meta.get("labels") or []
    if labels:
        lines.append(f"Labels: {', '.join(labels)}")
    if not lines:
        return None
    return "\n".join(lines)


def _build_mail_chunks(doc: Doc) -> list[str]:
    header = _mail_header(doc)
    chunks: list[str] = []
    block = _mail_headers_block(doc)
    if block is not None:
        chunks.append(f"{header}\n\n{block}")
    if doc.text is not None:
        chunks.extend(chunk_prose(doc.text, header))
    return chunks


def _commit_header(source: SourceConfig, probe: DocProbe, doc: Doc) -> str:
    sha = (probe.git_sha or doc.content_hash or "")[:7]
    author = (doc.meta or {}).get("author", "")
    date = (doc.doc_date or "")[:10]
    return f"{source.name} {sha} {author} {date}"


def _build_chunks(source: SourceConfig, doc: Doc) -> list[str]:
    if source.type == "eml_tree":
        return _build_mail_chunks(doc)
    if doc.text is None:
        return []
    probe = doc.probe
    if probe.path.startswith("commit/"):
        return chunk_whole(doc.text, _commit_header(source, probe, doc))
    header = f"{source.name}/{probe.path}"
    if is_code_extension(probe.path):
        return chunk_code(doc.text, header)
    ext = PurePath(probe.path).suffix.lower()
    if ext == ".csv":
        return chunk_csv(doc.text, header)
    return chunk_prose(doc.text, header, markdown=(ext == ".md"))


def _delete_chunk_vec_rows(conn: sqlite3.Connection, chunk_ids: list[int]) -> None:
    if not chunk_ids or not has_vec(conn):
        return
    placeholders = ",".join("?" * len(chunk_ids))
    for table in _VEC_TABLES:
        conn.execute(f"DELETE FROM {table} WHERE chunk_id IN ({placeholders})", chunk_ids)


def _delete_chunks(conn: sqlite3.Connection, doc_id: int) -> None:
    chunk_ids = [
        row[0] for row in conn.execute("SELECT chunk_id FROM chunks WHERE doc_id = ?", (doc_id,))
    ]
    _delete_chunk_vec_rows(conn, chunk_ids)
    conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))


def _delete_document(conn: sqlite3.Connection, doc_id: int) -> None:
    with conn:
        chunk_ids = [
            row[0]
            for row in conn.execute("SELECT chunk_id FROM chunks WHERE doc_id = ?", (doc_id,))
        ]
        _delete_chunk_vec_rows(conn, chunk_ids)
        conn.execute("DELETE FROM documents WHERE doc_id = ?", (doc_id,))


def _insert_document_row(conn: sqlite3.Connection, source: SourceConfig, doc: Doc) -> int:
    cur = conn.execute(
        "INSERT INTO documents"
        " (source, path, content_hash, stat_sig, content_indexed, title, doc_date, bytes, meta)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            source.name,
            doc.probe.path,
            doc.content_hash,
            doc.probe.stat_sig,
            1 if doc.content_indexed else 0,
            doc.title,
            doc.doc_date,
            doc.bytes,
            json.dumps(doc.meta),
        ),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def _update_document_row(
    conn: sqlite3.Connection, doc_id: int, source: SourceConfig, doc: Doc
) -> None:
    conn.execute(
        "UPDATE documents SET content_hash = ?, stat_sig = ?, content_indexed = ?,"
        " title = ?, doc_date = ?, bytes = ?, meta = ? WHERE doc_id = ?",
        (
            doc.content_hash,
            doc.probe.stat_sig,
            1 if doc.content_indexed else 0,
            doc.title,
            doc.doc_date,
            doc.bytes,
            json.dumps(doc.meta),
            doc_id,
        ),
    )


def _index_document(
    conn: sqlite3.Connection,
    config: Config,
    source: SourceConfig,
    doc: Doc,
    existing: _ExistingDoc | None,
    store_entities: StoreEntities,
) -> int:
    with conn:
        if existing is not None:
            _delete_chunks(conn, existing.doc_id)
            doc_id = existing.doc_id
            _update_document_row(conn, doc_id, source, doc)
        else:
            doc_id = _insert_document_row(conn, source, doc)
        chunks = _build_chunks(source, doc)
        for seq, text in enumerate(chunks):
            conn.execute(
                "INSERT INTO chunks (doc_id, seq, text, embedded) VALUES (?, ?, ?, 0)",
                (doc_id, seq, text),
            )
        store_entities(conn, doc_id, doc, config.seeds, config.aliases)
    return len(chunks)


def update_source(
    config: Config,
    conn: sqlite3.Connection,
    source: SourceConfig,
    adapter: SourceAdapter,
    *,
    store_entities: StoreEntities | None = None,
) -> SourceStats:
    """Diff one source's discover() output against the DB and converge it."""
    if store_entities is None:
        store_entities = _default_store_entities

    stats = SourceStats()
    existing = _existing_documents(conn, source.name)
    seen: set[str] = set()

    for probe in adapter.discover():
        seen.add(probe.path)
        existing_row = existing.get(probe.path)
        if existing_row is not None and _unchanged(probe, existing_row):
            stats.unchanged += 1
            continue
        doc = adapter.load(probe)
        stats.chunks += _index_document(conn, config, source, doc, existing_row, store_entities)
        if existing_row is None:
            stats.added += 1
        else:
            stats.changed += 1

    for path, row in existing.items():
        if path not in seen:
            _delete_document(conn, row.doc_id)
            stats.deleted += 1

    logger.info(
        "source %s: added=%d changed=%d deleted=%d unchanged=%d chunks=%d",
        source.name,
        stats.added,
        stats.changed,
        stats.deleted,
        stats.unchanged,
        stats.chunks,
    )
    return stats


def update(
    config: Config,
    conn: sqlite3.Connection,
    adapters: dict[str, SourceAdapter],
    *,
    sources: Iterable[str] | None = None,
    store_entities: StoreEntities | None = None,
) -> UpdateStats:
    names = list(sources) if sources is not None else [s.name for s in config.sources]
    stats = UpdateStats()
    for name in names:
        source = config.source(name)
        adapter = adapters[name]
        stats.sources[name] = update_source(
            config, conn, source, adapter, store_entities=store_entities
        )
    return stats
