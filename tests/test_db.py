import sqlite3
import sys

import pytest

from corpusindex.db import SCHEMA_VERSION, SchemaVersionError, connect, get_meta, has_vec, set_meta

def _vec_loadable() -> bool:
    try:
        import sqlite_vec
    except ImportError:
        return False
    probe = sqlite3.connect(":memory:")
    try:
        probe.enable_load_extension(True)
        sqlite_vec.load(probe)
    except (AttributeError, sqlite3.OperationalError):
        return False
    finally:
        probe.close()
    return True


HAVE_VEC = _vec_loadable()
if HAVE_VEC:
    import sqlite_vec


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "corpus.db")
    yield c
    c.close()


def object_names(conn, typ):
    return {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = ?", (typ,)
        )
    }


def insert_doc_with_chunk(conn, text="the quarterly invoice was overdue"):
    with conn:
        cur = conn.execute(
            "INSERT INTO documents (source, path, content_hash, content_indexed)"
            " VALUES ('t', 'a.txt', 'deadbeef', 1)"
        )
        doc_id = cur.lastrowid
        conn.execute(
            "INSERT INTO chunks (doc_id, seq, text) VALUES (?, 0, ?)", (doc_id, text)
        )
    return doc_id


def test_schema_objects_exist(conn):
    tables = object_names(conn, "table")
    for t in (
        "documents",
        "chunks",
        "chunks_fts",
        "entities",
        "doc_entities",
        "entity_links",
        "index_meta",
    ):
        assert t in tables
    assert {"chunks_ai", "chunks_ad"} <= object_names(conn, "trigger")
    assert {"idx_documents_date", "idx_doc_entities_e"} <= object_names(conn, "index")


def test_wal_and_version(conn):
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert get_meta(conn, "schema_version") == SCHEMA_VERSION


def test_meta_upsert(conn):
    set_meta(conn, "last_run", "2026-08-23T00:00:00Z")
    set_meta(conn, "last_run", "2026-08-24T00:00:00Z")
    assert get_meta(conn, "last_run") == "2026-08-24T00:00:00Z"


def test_fts_trigger_on_insert(conn):
    insert_doc_with_chunk(conn)
    rows = conn.execute(
        "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH 'invoice'"
    ).fetchall()
    assert len(rows) == 1


def test_fts_stemming(conn):
    insert_doc_with_chunk(conn, "we invoiced the customer for ref 47119")
    assert conn.execute(
        "SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH 'invoicing'"
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH '47119'"
    ).fetchone()[0] == 1


def test_fts_trigger_on_cascade_delete(conn):
    doc_id = insert_doc_with_chunk(conn)
    with conn:
        conn.execute("DELETE FROM documents WHERE doc_id = ?", (doc_id,))
    assert conn.execute("SELECT count(*) FROM chunks").fetchone()[0] == 0
    assert conn.execute(
        "SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH 'invoice'"
    ).fetchone()[0] == 0


def test_unique_source_path(conn):
    insert_doc_with_chunk(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO documents (source, path, content_hash, content_indexed)"
            " VALUES ('t', 'a.txt', 'other', 0)"
        )


def test_reopen_same_version(tmp_path):
    path = tmp_path / "corpus.db"
    connect(path).close()
    conn = connect(path)
    assert get_meta(conn, "schema_version") == SCHEMA_VERSION
    conn.close()


def test_version_guard(tmp_path):
    path = tmp_path / "corpus.db"
    conn = connect(path)
    with conn:
        set_meta(conn, "schema_version", "999")
    conn.close()
    with pytest.raises(SchemaVersionError, match="999"):
        connect(path)


def test_without_sqlite_vec(tmp_path, monkeypatch):
    """Lexical-only mode: everything except vec tables works with no sqlite_vec."""
    monkeypatch.setitem(sys.modules, "sqlite_vec", None)  # forces ImportError
    conn = connect(tmp_path / "novec.db")
    try:
        assert not has_vec(conn)
        assert "chunks_vec_prose" not in object_names(conn, "table")
        insert_doc_with_chunk(conn)
        assert conn.execute(
            "SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH 'invoice'"
        ).fetchone()[0] == 1
    finally:
        conn.close()


@pytest.mark.skipif(not HAVE_VEC, reason="sqlite-vec not installed")
def test_vec_tables_created(conn):
    assert has_vec(conn)
    tables = object_names(conn, "table")
    assert {"chunks_vec_prose", "chunks_vec_code"} <= tables
    conn.execute(
        "INSERT INTO chunks_vec_prose (chunk_id, embedding) VALUES (?, ?)",
        (1, sqlite_vec.serialize_float32([0.5] * 1024)),
    )
    row = conn.execute(
        "SELECT chunk_id FROM chunks_vec_prose WHERE embedding MATCH ? AND k = 1",
        (sqlite_vec.serialize_float32([0.5] * 1024),),
    ).fetchone()
    assert row == (1,)


@pytest.mark.skipif(not HAVE_VEC, reason="sqlite-vec not installed")
def test_vec_tables_added_on_upgrade(tmp_path, monkeypatch):
    """A db created lexical-only gains vec tables once sqlite-vec is available."""
    path = tmp_path / "upgrade.db"
    monkeypatch.setitem(sys.modules, "sqlite_vec", None)
    connect(path).close()
    monkeypatch.undo()
    conn = connect(path)
    try:
        assert has_vec(conn)
    finally:
        conn.close()


def test_vec_load_failure_degrades(tmp_path, monkeypatch):
    """A broken sqlite_vec module degrades to lexical-only instead of crashing."""

    class Broken:
        @staticmethod
        def load(conn):
            raise sqlite3.OperationalError("no loadable extension support")

    monkeypatch.setitem(sys.modules, "sqlite_vec", Broken())
    conn = connect(tmp_path / "broken.db")
    try:
        assert not has_vec(conn)
        assert "chunks_vec_prose" not in object_names(conn, "table")
    finally:
        conn.close()
