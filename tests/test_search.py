import sqlite3

import pytest

from corpusindex.adapters.base import Doc, DocProbe
from corpusindex.config import Config, SourceConfig
from corpusindex.db import connect, has_vec
from corpusindex.indexer import update
from corpusindex.search import Hit, search


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


def _insert_doc(
    conn,
    source,
    path,
    *,
    title=None,
    doc_date=None,
    content_indexed=1,
    content_hash=None,
):
    cur = conn.execute(
        "INSERT INTO documents (source, path, content_hash, content_indexed, title, doc_date)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (source, path, content_hash or f"hash-{source}-{path}", content_indexed, title, doc_date),
    )
    return cur.lastrowid


def _insert_chunk(conn, doc_id, seq, text):
    cur = conn.execute(
        "INSERT INTO chunks (doc_id, seq, text) VALUES (?, ?, ?)",
        (doc_id, seq, text),
    )
    return cur.lastrowid


def _seed_corpus(conn):
    with conn:
        doc_a = _insert_doc(
            conn, "repo:submit", "widget.py",
            title="Widget module", doc_date="2026-01-05",
        )
        _insert_chunk(conn, doc_a, 0, "class WidgetFactory builds a widgetfactory instance for tests")

        doc_b = _insert_doc(
            conn, "mail", "msg1", title="Support ticket", doc_date="2026-03-10",
        )
        _insert_chunk(conn, doc_b, 0, "customer requested a refund for the wrong package purchased")

        doc_c = _insert_doc(
            conn, "drive", "contract.pdf", title="Contract", doc_date="2025-12-01",
        )
        _insert_chunk(conn, doc_c, 0, "the quarterly invoice remains overdue pending payment")

        doc_d = _insert_doc(
            conn, "repo:submit", "helper.py", title="Helper module", doc_date="2026-02-14",
        )
        _insert_chunk(conn, doc_d, 0, "alpha beta gamma placeholder text with no signal")
        _insert_chunk(conn, doc_d, 1, "delta epsilon widgetfactory appears again in this chunk")

    return {"a": doc_a, "b": doc_b, "c": doc_c, "d": doc_d}


def test_lexical_relevance_exact_token(conn):
    _seed_corpus(conn)
    hits = search(conn, "widgetfactory", mode="lexical")
    assert hits
    paths = {h.path for h in hits}
    assert "widget.py" in paths
    assert all(isinstance(h, Hit) for h in hits)
    top = hits[0]
    assert top.source in ("repo:submit",)
    assert top.excerpt


def test_lexical_no_match_returns_empty(conn):
    _seed_corpus(conn)
    assert search(conn, "nonexistentxyzterm", mode="lexical") == []


def test_source_filter(conn):
    _seed_corpus(conn)
    hits = search(conn, "refund", mode="lexical", sources=["mail"])
    assert hits
    assert all(h.source == "mail" for h in hits)
    assert search(conn, "refund", mode="lexical", sources=["drive"]) == []


def test_date_filters(conn):
    _seed_corpus(conn)
    hits = search(
        conn, "widgetfactory", mode="lexical", since="2026-02-01", until="2026-12-31"
    )
    paths = {h.path for h in hits}
    assert "widget.py" not in paths
    assert "helper.py" in paths


def test_since_until_exclude_all(conn):
    _seed_corpus(conn)
    hits = search(conn, "invoice", mode="lexical", since="2026-01-01")
    assert hits == []


def test_malformed_fts_query_safe(conn):
    _seed_corpus(conn)
    for bad in ['"unterminated', "foo AND (bar", "* wildcard nonsense (", 'NEAR("x)', "----"]:
        hits = search(conn, bad, mode="lexical")
        assert isinstance(hits, list)


def test_empty_query_safe(conn):
    _seed_corpus(conn)
    assert search(conn, "", mode="lexical") == []
    assert search(conn, "   ", mode="hybrid") == []


def test_semantic_mode_without_vectors_returns_empty(conn):
    _seed_corpus(conn)
    assert search(conn, "widgetfactory", mode="semantic", query_vectors=None) == []
    assert search(conn, "widgetfactory", mode="semantic", query_vectors={}) == []


def test_hybrid_degradation_without_vectors(conn):
    _seed_corpus(conn)
    hits = search(conn, "widgetfactory", mode="hybrid", query_vectors=None)
    lexical_hits = search(conn, "widgetfactory", mode="lexical")
    assert [h.path for h in hits] == [h.path for h in lexical_hits]


def test_invalid_mode_raises(conn):
    _seed_corpus(conn)
    with pytest.raises(ValueError):
        search(conn, "widgetfactory", mode="bogus")


def test_collapse_to_document(conn):
    docs = _seed_corpus(conn)
    hits = search(conn, "widgetfactory", mode="lexical", limit=10)
    helper_hits = [h for h in hits if h.path == "helper.py"]
    assert len(helper_hits) == 1
    assert helper_hits[0].chunk_seq == 1


@pytest.mark.skipif(not HAVE_VEC, reason="sqlite-vec not installed")
def test_tie_break_deterministic(conn):
    # Two documents each rank #1 in exactly one of two disjoint ranked lists
    # (lexical vs. semantic), so their fused RRF scores land exactly equal;
    # the tie must then resolve by (source, path) ascending.
    with conn:
        doc_z = _insert_doc(conn, "repo:a", "z.py", doc_date="2026-01-01")
        _insert_chunk(conn, doc_z, 0, "onlylexicalmatch appears in this chunk alone")
        doc_y = _insert_doc(conn, "repo:a", "y.py", doc_date="2026-01-01")
        chunk_y = _insert_chunk(conn, doc_y, 0, "unrelated content with no lexical overlap")

    vec = [0.2] * 1024
    with conn:
        conn.execute(
            "INSERT INTO chunks_vec_prose (chunk_id, embedding) VALUES (?, ?)",
            (chunk_y, sqlite_vec.serialize_float32(vec)),
        )
    hits = search(
        conn, "onlylexicalmatch", mode="hybrid", query_vectors={"prose": vec}
    )
    assert len(hits) == 2
    assert hits[0].score == hits[1].score
    assert [h.path for h in hits] == ["y.py", "z.py"]


def test_limit_respected(conn):
    with conn:
        for i in range(5):
            doc = _insert_doc(conn, "repo:a", f"file{i}.py", doc_date="2026-01-01")
            _insert_chunk(conn, doc, 0, "shared common searchtoken content")
    hits = search(conn, "searchtoken", mode="lexical", limit=2)
    assert len(hits) == 2


@pytest.mark.skipif(not HAVE_VEC, reason="sqlite-vec not installed")
def test_rrf_ordering_with_fake_vectors(conn):
    assert has_vec(conn)
    docs = _seed_corpus(conn)

    lexical_only_chunk = conn.execute(
        "SELECT chunk_id FROM chunks WHERE doc_id = ?", (docs["a"],)
    ).fetchone()[0]
    semantic_only_chunk = conn.execute(
        "SELECT chunk_id FROM chunks WHERE doc_id = ?", (docs["b"],)
    ).fetchone()[0]

    query_vec = [0.1] * 1024
    close_vec = [0.1] * 1024
    far_vec = [-0.9] * 1024

    with conn:
        conn.execute(
            "INSERT INTO chunks_vec_prose (chunk_id, embedding) VALUES (?, ?)",
            (semantic_only_chunk, sqlite_vec.serialize_float32(close_vec)),
        )
        conn.execute(
            "INSERT INTO chunks_vec_prose (chunk_id, embedding) VALUES (?, ?)",
            (lexical_only_chunk, sqlite_vec.serialize_float32(far_vec)),
        )

    hits = search(
        conn,
        "widgetfactory",
        mode="hybrid",
        query_vectors={"prose": query_vec},
        limit=10,
    )
    paths = [h.path for h in hits]
    assert "widget.py" in paths
    assert "msg1" in paths


@pytest.mark.skipif(not HAVE_VEC, reason="sqlite-vec not installed")
def test_semantic_mode_uses_vectors(conn):
    docs = _seed_corpus(conn)
    chunk_id = conn.execute(
        "SELECT chunk_id FROM chunks WHERE doc_id = ?", (docs["c"],)
    ).fetchone()[0]
    vec = [0.3] * 1024
    with conn:
        conn.execute(
            "INSERT INTO chunks_vec_prose (chunk_id, embedding) VALUES (?, ?)",
            (chunk_id, sqlite_vec.serialize_float32(vec)),
        )
    hits = search(conn, "irrelevant text", mode="semantic", query_vectors={"prose": vec})
    assert hits
    assert hits[0].path == "contract.pdf"


@pytest.mark.skipif(not HAVE_VEC, reason="sqlite-vec not installed")
def test_semantic_respects_source_filter(conn):
    docs = _seed_corpus(conn)
    chunk_id = conn.execute(
        "SELECT chunk_id FROM chunks WHERE doc_id = ?", (docs["c"],)
    ).fetchone()[0]
    vec = [0.3] * 1024
    with conn:
        conn.execute(
            "INSERT INTO chunks_vec_prose (chunk_id, embedding) VALUES (?, ?)",
            (chunk_id, sqlite_vec.serialize_float32(vec)),
        )
    hits = search(
        conn, "irrelevant text", mode="semantic", query_vectors={"prose": vec},
        sources=["mail"],
    )
    assert hits == []


class _MailAdapter:
    name = "mail"

    def __init__(self, entries):
        self.entries = entries

    def discover(self):
        for e in self.entries:
            yield DocProbe(source=self.name, path=e["path"], stat_sig="1:1")

    def load(self, probe):
        e = next(x for x in self.entries if x["path"] == probe.path)
        return Doc(
            probe=probe,
            content_hash=e["path"],
            title=e.get("title"),
            doc_date=e.get("doc_date"),
            meta=e.get("meta", {}),
            text=e.get("text"),
        )


def test_label_query_finds_mail_doc_via_headers_chunk(conn):
    config = Config(
        db=":memory:",
        sources=(SourceConfig(name="mail", type="eml_tree", root="."),),
    )
    adapter = _MailAdapter(
        [
            {
                "path": "2026/1/1/labelled.eml",
                "title": "Quarterly renewal reminder",
                "doc_date": "2026-01-01T09:00:00+00:00",
                "meta": {
                    "from": "billing@example.com",
                    "to": "customer@example.com",
                    "labels": ["IMPORTANT", "Finance/Renewals"],
                },
                "text": "This is a routine reminder with no distinctive body terms.",
            }
        ]
    )
    update(config, conn, {"mail": adapter}, store_entities=lambda *a, **k: None)

    hits = search(conn, "Renewals", mode="lexical")
    assert any(h.path == "2026/1/1/labelled.eml" for h in hits)

    top = next(h for h in hits if h.path == "2026/1/1/labelled.eml")
    assert top.chunk_seq == 0
    matched_chunk = conn.execute(
        "SELECT c.text FROM chunks c JOIN documents d ON d.doc_id = c.doc_id"
        " WHERE d.path = ? AND c.seq = ?",
        ("2026/1/1/labelled.eml", top.chunk_seq),
    ).fetchone()[0]
    assert "Labels: IMPORTANT, Finance/Renewals" in matched_chunk
