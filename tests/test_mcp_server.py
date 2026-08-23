import json

import pytest

from corpusindex import mcp_server
from corpusindex.config import Config, SourceConfig
from corpusindex.db import connect


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "corpus.db")
    yield c
    c.close()


@pytest.fixture(autouse=True)
def _reset_state():
    mcp_server.configure(config=None, conn=None)
    yield
    mcp_server.configure(config=None, conn=None)


def _insert_doc(
    conn,
    source,
    path,
    *,
    title=None,
    doc_date=None,
    content_indexed=1,
    meta=None,
    content_hash=None,
):
    cur = conn.execute(
        "INSERT INTO documents (source, path, content_hash, content_indexed, title, doc_date, meta)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            source,
            path,
            content_hash or f"hash-{source}-{path}",
            content_indexed,
            title,
            doc_date,
            json.dumps(meta) if meta is not None else None,
        ),
    )
    return cur.lastrowid


def _insert_chunk(conn, doc_id, seq, text):
    cur = conn.execute(
        "INSERT INTO chunks (doc_id, seq, text) VALUES (?, ?, ?)", (doc_id, seq, text)
    )
    return cur.lastrowid


def _insert_entity(conn, kind, key, display=None):
    cur = conn.execute(
        "INSERT INTO entities (kind, key, display) VALUES (?, ?, ?)", (kind, key, display)
    )
    return cur.lastrowid


def _link_doc_entity(conn, doc_id, entity_id, rel):
    conn.execute(
        "INSERT INTO doc_entities (doc_id, entity_id, rel) VALUES (?, ?, ?)",
        (doc_id, entity_id, rel),
    )


def _link_entities(conn, a_id, b_id, rel):
    conn.execute(
        "INSERT INTO entity_links (a_id, b_id, rel) VALUES (?, ?, ?)", (a_id, b_id, rel)
    )


def test_search_returns_seeded_doc(conn):
    with conn:
        doc = _insert_doc(conn, "repo:submit", "widget.py", title="Widget", doc_date="2026-01-05")
        _insert_chunk(conn, doc, 0, "class WidgetFactory builds a widgetfactory instance for tests")
    mcp_server.configure(conn=conn)

    result = mcp_server.search("widgetfactory")

    assert list(result.keys()) == ["hits"]
    hits = result["hits"]
    assert hits
    hit = hits[0]
    assert hit["source"] == "repo:submit"
    assert hit["path"] == "widget.py"
    assert set(hit) == {
        "source", "path", "title", "date", "score", "excerpt", "chunk_seq", "content_indexed",
    }


def test_search_respects_limit(conn):
    with conn:
        for i in range(5):
            doc = _insert_doc(conn, "repo:a", f"file{i}.py", doc_date="2026-01-01")
            _insert_chunk(conn, doc, 0, "shared common searchtoken content")
    mcp_server.configure(conn=conn)

    result = mcp_server.search("searchtoken", limit=2)

    assert len(result["hits"]) == 2


def test_search_respects_sources(conn):
    with conn:
        doc_mail = _insert_doc(conn, "mail", "msg1", doc_date="2026-01-01")
        _insert_chunk(conn, doc_mail, 0, "refund requested for wrong package")
        doc_drive = _insert_doc(conn, "drive", "contract.pdf", doc_date="2026-01-01")
        _insert_chunk(conn, doc_drive, 0, "refund clause appears in the contract terms")
    mcp_server.configure(conn=conn)

    mail_only = mcp_server.search("refund", sources=["mail"])
    assert mail_only["hits"]
    assert all(h["source"] == "mail" for h in mail_only["hits"])

    drive_only = mcp_server.search("refund", sources=["drive"])
    assert drive_only["hits"]
    assert all(h["source"] == "drive" for h in drive_only["hits"])


def test_search_hybrid_degrades_silently_without_api_key(conn, tmp_path, monkeypatch):
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    with conn:
        doc = _insert_doc(conn, "repo:a", "widget.py", doc_date="2026-01-01")
        _insert_chunk(conn, doc, 0, "widgetfactory appears here for a lexical match")

    config = Config(
        db=tmp_path / "unused.db",
        sources=(SourceConfig(name="repo:a", type="gitrepo", root=tmp_path),),
        config_dir=tmp_path,
    )
    mcp_server.configure(config=config, conn=conn)

    result = mcp_server.search("widgetfactory", mode="hybrid")
    lexical_only = mcp_server.search("widgetfactory", mode="lexical")

    assert [h["path"] for h in result["hits"]] == [h["path"] for h in lexical_only["hits"]]

    semantic_result = mcp_server.search("widgetfactory", mode="semantic")
    assert [h["path"] for h in semantic_result["hits"]] == [h["path"] for h in lexical_only["hits"]]


def test_get_document_content_join_and_truncation(conn):
    with conn:
        doc = _insert_doc(
            conn, "repo:a", "notes.md", title="Notes", doc_date="2026-01-01",
            content_indexed=1, meta={"author": "me"},
        )
        _insert_chunk(conn, doc, 0, "first chunk text")
        _insert_chunk(conn, doc, 1, "second chunk text")
    mcp_server.configure(conn=conn)

    result = mcp_server.get_document("repo:a", "notes.md")
    assert result["content"] == "first chunk text\n\nsecond chunk text"
    assert result["truncated"] is False
    assert result["meta"] == {"author": "me"}
    assert result["content_indexed"] is True
    assert result["title"] == "Notes"
    assert result["date"] == "2026-01-01"

    truncated = mcp_server.get_document("repo:a", "notes.md", max_chars=5)
    assert truncated["truncated"] is True
    assert truncated["content"] == "first"


def test_get_document_metadata_only(conn):
    with conn:
        _insert_doc(
            conn, "drive", "spreadsheet.xlsx", title="Sheet", doc_date="2026-01-01",
            content_indexed=0, meta={"bytes": 123},
        )
    mcp_server.configure(conn=conn)

    result = mcp_server.get_document("drive", "spreadsheet.xlsx")
    assert result["content_indexed"] is False
    assert result["content"] is None
    assert result["truncated"] is False
    assert result["meta"] == {"bytes": 123}


def test_get_document_not_found(conn):
    mcp_server.configure(conn=conn)

    result = mcp_server.get_document("nowhere", "missing.txt")

    assert "error" in result


def test_related_entities_links_both_directions(conn):
    with conn:
        doc = _insert_doc(conn, "mail", "msg1", title="Hello", doc_date="2026-02-01")
        email_id = _insert_entity(conn, "email", "jane@example.com", "Jane Roe")
        person_id = _insert_entity(conn, "person", "jane roe", "Jane Roe")
        org_id = _insert_entity(conn, "org", "acme corp", "Acme Corp")
        _link_doc_entity(conn, doc, email_id, "from")
        _link_entities(conn, email_id, person_id, "alias_of")
        _link_entities(conn, person_id, org_id, "member_of")
    mcp_server.configure(conn=conn)

    from_email = mcp_server.related_entities("jane@example.com", kind="email")
    assert from_email["entity"] == {"kind": "email", "key": "jane@example.com", "display": "Jane Roe"}
    assert {"rel": "alias_of", "kind": "person", "key": "jane roe", "display": "Jane Roe"} in from_email["links"]
    assert from_email["documents"] == [
        {"source": "mail", "path": "msg1", "title": "Hello", "date": "2026-02-01", "rel": "from"}
    ]

    from_person = mcp_server.related_entities("jane roe", kind="person")
    rels = {(link["rel"], link["kind"], link["key"]) for link in from_person["links"]}
    assert ("alias_of", "email", "jane@example.com") in rels
    assert ("member_of", "org", "acme corp") in rels


def test_related_entities_documents_newest_first_and_limited(conn):
    with conn:
        entity_id = _insert_entity(conn, "category", "finance", "Finance")
        doc_old = _insert_doc(conn, "drive", "old.pdf", doc_date="2020-01-01")
        doc_new = _insert_doc(conn, "drive", "new.pdf", doc_date="2026-01-01")
        doc_mid = _insert_doc(conn, "drive", "mid.pdf", doc_date="2023-01-01")
        for d in (doc_old, doc_new, doc_mid):
            _link_doc_entity(conn, d, entity_id, "category")
    mcp_server.configure(conn=conn)

    result = mcp_server.related_entities("finance", kind="category", limit=2)

    assert [d["path"] for d in result["documents"]] == ["new.pdf", "mid.pdf"]


def test_related_entities_kind_disambiguation(conn):
    with conn:
        _insert_entity(conn, "category", "shared-key", "Shared Category")
        _insert_entity(conn, "org", "shared-key", "Shared Org")
        _insert_entity(conn, "person", "shared-key", "Shared Person")
        _insert_entity(conn, "email", "shared-key", "shared@example.com")
    mcp_server.configure(conn=conn)

    result = mcp_server.related_entities("shared-key")

    assert result["entity"]["kind"] == "email"


def test_related_entities_not_found(conn):
    mcp_server.configure(conn=conn)

    result = mcp_server.related_entities("nope")

    assert "error" in result

    with conn:
        _insert_entity(conn, "org", "acme corp", "Acme Corp")
    wrong_kind = mcp_server.related_entities("acme corp", kind="person")
    assert "error" in wrong_kind


def test_build_app_registers_tools_without_running_server():
    fastmcp = pytest.importorskip("fastmcp")
    from corpusindex.mcp_server import build_app

    app = build_app()

    assert isinstance(app, fastmcp.FastMCP)
