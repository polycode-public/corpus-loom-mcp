"""SQLite schema and connection management.

Single-file database per DESIGN.md. sqlite-vec is optional: the vec0 virtual
tables are created only when the extension is importable and loadable, and
every other schema object works without it (lexical-only mode).
"""

from __future__ import annotations

import sqlite3
from os import PathLike
from pathlib import Path

SCHEMA_VERSION = "1"

DDL = """
CREATE TABLE documents (
  doc_id          INTEGER PRIMARY KEY,
  source          TEXT NOT NULL,          -- config source name: 'mail', 'drive', 'repo:submit', ...
  path            TEXT NOT NULL,          -- relative to that source's root; commits use 'commit/<sha>'
  content_hash    TEXT NOT NULL,          -- sha256 hex of raw bytes (git blob sha for repo files; commit sha for commits)
  stat_sig        TEXT,                   -- 'mtime:size' fast-path; NULL for git objects
  content_indexed INTEGER NOT NULL,       -- 1 = body chunked below; 0 = metadata-only
  title           TEXT,                   -- mail Subject / file basename / commit summary line
  doc_date        TEXT,                   -- ISO-8601 UTC (Date header / file mtime / author date)
  bytes           INTEGER,
  meta            TEXT,                   -- JSON: mail {message_id,from,to,cc,attachments[],labels[]}, commit {author,files[]}, ...
  UNIQUE (source, path)
);

CREATE TABLE chunks (
  chunk_id  INTEGER PRIMARY KEY,          -- join key for chunks_fts.rowid and chunks_vec.chunk_id
  doc_id    INTEGER NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
  seq       INTEGER NOT NULL,
  text      TEXT NOT NULL,
  embedded  INTEGER NOT NULL DEFAULT 0,   -- 0 = queued for embedding
  UNIQUE (doc_id, seq)
);

CREATE VIRTUAL TABLE chunks_fts USING fts5(
  text, content='chunks', content_rowid='chunk_id',
  tokenize='porter unicode61'
);
CREATE TRIGGER chunks_ai AFTER INSERT ON chunks BEGIN
  INSERT INTO chunks_fts(rowid, text) VALUES (new.chunk_id, new.text);
END;
CREATE TRIGGER chunks_ad AFTER DELETE ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.chunk_id, old.text);
END;

CREATE TABLE entities (
  entity_id INTEGER PRIMARY KEY,
  kind      TEXT NOT NULL,                -- 'email' | 'person' | 'org' | 'category'
  key       TEXT NOT NULL,                -- normalised
  display   TEXT,
  UNIQUE (kind, key)
);

CREATE TABLE doc_entities (               -- document <-> entity
  doc_id    INTEGER NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
  entity_id INTEGER NOT NULL REFERENCES entities(entity_id),
  rel       TEXT NOT NULL,                -- 'from'|'to'|'cc'|'author'|'committer'|'category'|'mentions'
  UNIQUE (doc_id, entity_id, rel)
);

CREATE TABLE entity_links (               -- entity <-> entity
  a_id INTEGER NOT NULL REFERENCES entities(entity_id),
  b_id INTEGER NOT NULL REFERENCES entities(entity_id),
  rel  TEXT NOT NULL,                     -- 'alias_of' (email->person) | 'member_of' (person->org)
  UNIQUE (a_id, b_id, rel)
);

CREATE INDEX idx_documents_date ON documents(doc_date);
CREATE INDEX idx_doc_entities_e ON doc_entities(entity_id, rel);
CREATE TABLE index_meta (key TEXT PRIMARY KEY, value TEXT);  -- schema_version, last_run, model names/dims
"""

# vec rows are deleted explicitly by the indexer when chunks are removed
# (no triggers on virtual tables).
VEC_DDL = """
CREATE VIRTUAL TABLE chunks_vec_prose USING vec0(chunk_id INTEGER PRIMARY KEY, embedding float[1024]);
CREATE VIRTUAL TABLE chunks_vec_code  USING vec0(chunk_id INTEGER PRIMARY KEY, embedding float[1024]);
"""


class SchemaVersionError(RuntimeError):
    """Database schema version does not match this code's SCHEMA_VERSION."""


def _try_load_sqlite_vec(conn: sqlite3.Connection) -> bool:
    try:
        import sqlite_vec
    except ImportError:
        return False
    try:
        conn.enable_load_extension(True)
        try:
            sqlite_vec.load(conn)
        finally:
            conn.enable_load_extension(False)
    except (sqlite3.OperationalError, AttributeError):
        return False
    return True


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def has_vec(conn: sqlite3.Connection) -> bool:
    """True when the vec0 tables exist and the extension is loaded on this connection."""
    if not _table_exists(conn, "chunks_vec_prose"):
        return False
    try:
        conn.execute("SELECT vec_version()")
    except sqlite3.OperationalError:
        return False
    return True


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM index_meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO index_meta(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def connect(db_path: str | PathLike[str]) -> sqlite3.Connection:
    """Open (creating if needed) the index database.

    Applies WAL and foreign_keys, loads sqlite-vec when available, creates the
    schema on a fresh file, and refuses an existing file whose schema_version
    differs from SCHEMA_VERSION.
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    vec_loaded = _try_load_sqlite_vec(conn)

    fresh = not _table_exists(conn, "index_meta")
    if fresh:
        with conn:
            conn.executescript(DDL)
            set_meta(conn, "schema_version", SCHEMA_VERSION)
    else:
        version = get_meta(conn, "schema_version")
        if version != SCHEMA_VERSION:
            conn.close()
            raise SchemaVersionError(
                f"{path}: schema_version {version!r} != expected {SCHEMA_VERSION!r}; "
                "reindex into a fresh database or run a matching corpusindex version"
            )

    if vec_loaded and not _table_exists(conn, "chunks_vec_prose"):
        with conn:
            conn.executescript(VEC_DDL)

    return conn
