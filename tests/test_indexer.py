import sqlite3

import pytest

from corpusindex.adapters.base import Doc, DocProbe
from corpusindex.config import Config, SourceConfig
from corpusindex.db import connect, has_vec
from corpusindex.indexer import SourceStats, UpdateStats, update, update_source


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


class FakeAdapter:
    """A source's discover()/load() over a fixed dict of fictitious entries."""

    forbid_load = False

    def __init__(self, name: str, entries: list[dict]):
        self.name = name
        self.entries = entries
        self.loaded: list[str] = []

    def discover(self):
        for e in self.entries:
            yield DocProbe(
                source=self.name,
                path=e["path"],
                stat_sig=e.get("stat_sig"),
                git_sha=e.get("git_sha"),
            )

    def load(self, probe: DocProbe) -> Doc:
        if self.forbid_load:
            raise AssertionError(f"load() called for probe that should be unchanged: {probe.path}")
        self.loaded.append(probe.path)
        e = next(x for x in self.entries if x["path"] == probe.path)
        return Doc(
            probe=probe,
            content_hash=e["content_hash"],
            title=e.get("title"),
            doc_date=e.get("doc_date"),
            bytes=e.get("bytes"),
            meta=e.get("meta", {}),
            text=e.get("text"),
        )


def _notes_entries():
    return [
        {
            "path": "readme.md",
            "stat_sig": "100:10",
            "content_hash": "h1",
            "title": "readme.md",
            "doc_date": "2024-01-01T00:00:00Z",
            "bytes": 40,
            "meta": {},
            "text": "# Title\n\nSome markdown body about widgets.",
        },
        {
            "path": "script.py",
            "stat_sig": "100:20",
            "content_hash": "h2",
            "title": "script.py",
            "doc_date": "2024-01-02T00:00:00Z",
            "bytes": 25,
            "meta": {},
            "text": "def foo():\n    return 1\n",
        },
        {
            "path": "data.csv",
            "stat_sig": "100:30",
            "content_hash": "h3",
            "title": "data.csv",
            "doc_date": "2024-01-03T00:00:00Z",
            "bytes": 12,
            "meta": {},
            "text": "a,b\n1,2\n3,4\n",
        },
        {
            "path": "logo.png",
            "stat_sig": "100:5",
            "content_hash": "h4",
            "title": "logo.png",
            "doc_date": "2024-01-04T00:00:00Z",
            "bytes": 1024,
            "meta": {"kind": "image"},
            "text": None,
        },
    ]


def _mail_entries():
    return [
        {
            "path": "2024/3/7/abc.eml",
            "stat_sig": "200:40",
            "content_hash": "m1",
            "title": "Invoice overdue",
            "doc_date": "2024-03-07T10:00:00Z",
            "bytes": 200,
            "meta": {"from": "alice@example.com"},
            "text": "Please settle the overdue invoice ref 4471 promptly.",
        }
    ]


def _repo_entries():
    return [
        {
            "path": "commit/aaaaaaaaaaaa1111",
            "git_sha": "aaaaaaaaaaaa1111",
            "content_hash": "aaaaaaaaaaaa1111",
            "title": "Fix invoice bug",
            "doc_date": "2024-01-05T00:00:00Z",
            "bytes": 60,
            "meta": {"author": "Bob <bob@example.com>"},
            "text": "Fix invoice bug\n\nDetailed description of fix.",
        },
        {
            "path": "src/main.py",
            "git_sha": "blobsha1",
            "content_hash": "blobsha1",
            "title": "main.py",
            "doc_date": "2024-01-06T00:00:00Z",
            "bytes": 20,
            "meta": {},
            "text": "print('hello')\n",
        },
    ]


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "corpus.db")
    yield c
    c.close()


@pytest.fixture
def config(tmp_path):
    return Config(
        db=tmp_path / "corpus.db",
        sources=(
            SourceConfig(name="notes", type="filetree", root=tmp_path / "notes"),
            SourceConfig(name="mail", type="eml_tree", root=tmp_path / "mail"),
            SourceConfig(name="repo", type="gitrepo", root=tmp_path / "repo"),
        ),
    )


@pytest.fixture
def adapters():
    return {
        "notes": FakeAdapter("notes", _notes_entries()),
        "mail": FakeAdapter("mail", _mail_entries()),
        "repo": FakeAdapter("repo", _repo_entries()),
    }


class EntityStub:
    def __init__(self):
        self.calls: list[tuple[int, str]] = []

    def __call__(self, conn, doc_id, doc, seeds, aliases):
        self.calls.append((doc_id, doc.probe.path))


def _doc_row(conn, source, path):
    return conn.execute(
        "SELECT doc_id, content_indexed, stat_sig, content_hash, meta FROM documents"
        " WHERE source = ? AND path = ?",
        (source, path),
    ).fetchone()


def _chunk_texts(conn, doc_id):
    return [
        row[0]
        for row in conn.execute(
            "SELECT text FROM chunks WHERE doc_id = ? ORDER BY seq", (doc_id,)
        )
    ]


def _fts_match_count(conn, term):
    return conn.execute(
        "SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH ?", (term,)
    ).fetchone()[0]


def test_first_run_inserts_all_and_fts_matches(conn, config, adapters):
    entities = EntityStub()
    stats = update(config, conn, adapters, store_entities=entities)

    assert isinstance(stats, UpdateStats)
    assert stats.sources["notes"] == SourceStats(added=4, chunks=3)
    assert stats.sources["mail"] == SourceStats(added=1, chunks=1)
    assert stats.sources["repo"] == SourceStats(added=2, chunks=2)

    assert conn.execute("SELECT count(*) FROM documents").fetchone()[0] == 7
    assert _fts_match_count(conn, "widgets") == 1
    assert _fts_match_count(conn, "overdue") == 1
    assert _fts_match_count(conn, "hello") == 1

    readme_id, indexed, *_ = _doc_row(conn, "notes", "readme.md")
    assert indexed == 1
    logo_id, logo_indexed, *_ = _doc_row(conn, "notes", "logo.png")
    assert logo_indexed == 0

    header_row = _chunk_texts(conn, readme_id)[0]
    assert header_row.startswith("notes/readme.md\n\n")

    mail_id = _doc_row(conn, "mail", "2024/3/7/abc.eml")[0]
    mail_header = _chunk_texts(conn, mail_id)[0]
    assert mail_header.startswith("Invoice overdue — alice@example.com, 2024-03-07\n\n")

    commit_id = _doc_row(conn, "repo", "commit/aaaaaaaaaaaa1111")[0]
    commit_header = _chunk_texts(conn, commit_id)[0]
    assert commit_header.startswith("repo aaaaaaa Bob <bob@example.com> 2024-01-05\n\n")

    assert sorted(c[1] for c in entities.calls) == sorted(
        e["path"] for e in _notes_entries() + _mail_entries() + _repo_entries()
    )
    assert (readme_id, "readme.md") in entities.calls
    assert (logo_id, "logo.png") in entities.calls


def test_second_run_is_a_noop(conn, config, adapters):
    update(config, conn, adapters, store_entities=EntityStub())

    noop_adapters = {
        "notes": FakeAdapter("notes", _notes_entries()),
        "mail": FakeAdapter("mail", _mail_entries()),
        "repo": FakeAdapter("repo", _repo_entries()),
    }
    for a in noop_adapters.values():
        a.forbid_load = True

    entities = EntityStub()
    stats = update(config, conn, noop_adapters, store_entities=entities)

    assert stats.sources["notes"] == SourceStats(unchanged=4)
    assert stats.sources["mail"] == SourceStats(unchanged=1)
    assert stats.sources["repo"] == SourceStats(unchanged=2)
    assert entities.calls == []


def test_content_change_replaces_chunks_and_resets_embedded(conn, config, adapters):
    update(config, conn, adapters, store_entities=EntityStub())
    doc_id_before = _doc_row(conn, "notes", "readme.md")[0]

    changed = _notes_entries()
    for e in changed:
        if e["path"] == "readme.md":
            e["stat_sig"] = "999:99"
            e["content_hash"] = "h1-changed"
            e["text"] = "# Title\n\nCompletely different content about refundsxyz."

    changed_adapter = FakeAdapter("notes", changed)
    other = {
        "notes": changed_adapter,
        "mail": FakeAdapter("mail", _mail_entries()),
        "repo": FakeAdapter("repo", _repo_entries()),
    }
    for name in ("mail", "repo"):
        other[name].forbid_load = True

    stats = update(config, conn, other, store_entities=EntityStub())

    assert stats.sources["notes"].changed == 1
    assert stats.sources["notes"].unchanged == 3
    assert changed_adapter.loaded == ["readme.md"]

    doc_id_after, indexed, stat_sig, content_hash, _ = _doc_row(conn, "notes", "readme.md")
    assert doc_id_after == doc_id_before
    assert stat_sig == "999:99"
    assert content_hash == "h1-changed"

    assert _fts_match_count(conn, "widgets") == 0
    assert _fts_match_count(conn, "refundsxyz") == 1

    rows = conn.execute(
        "SELECT embedded FROM chunks WHERE doc_id = ?", (doc_id_after,)
    ).fetchall()
    assert rows and all(r[0] == 0 for r in rows)


def test_deletion_removes_document_chunks_fts_and_vec_rows(conn, config, adapters):
    update(config, conn, adapters, store_entities=EntityStub())
    mail_id, *_ = _doc_row(conn, "mail", "2024/3/7/abc.eml")
    chunk_id = conn.execute(
        "SELECT chunk_id FROM chunks WHERE doc_id = ? LIMIT 1", (mail_id,)
    ).fetchone()[0]

    vec_seeded = False
    if has_vec(conn):
        with conn:
            conn.execute(
                "INSERT INTO chunks_vec_prose (chunk_id, embedding) VALUES (?, ?)",
                (chunk_id, sqlite_vec.serialize_float32([0.1] * 1024)),
            )
        vec_seeded = True

    remaining = {
        "notes": FakeAdapter("notes", _notes_entries()),
        "mail": FakeAdapter("mail", []),
        "repo": FakeAdapter("repo", _repo_entries()),
    }
    remaining["notes"].forbid_load = True
    remaining["repo"].forbid_load = True

    stats = update(config, conn, remaining, store_entities=EntityStub())

    assert stats.sources["mail"] == SourceStats(deleted=1)
    assert _doc_row(conn, "mail", "2024/3/7/abc.eml") is None
    assert conn.execute("SELECT count(*) FROM chunks WHERE doc_id = ?", (mail_id,)).fetchone()[0] == 0
    assert _fts_match_count(conn, "overdue") == 0

    if vec_seeded:
        row = conn.execute(
            "SELECT count(*) FROM chunks_vec_prose WHERE chunk_id = ?", (chunk_id,)
        ).fetchone()[0]
        assert row == 0


def test_commit_docs_are_append_only(conn, config, adapters):
    update(config, conn, adapters, store_entities=EntityStub())

    grown = _repo_entries()
    grown.append(
        {
            "path": "commit/bbbbbbbbbbbb2222",
            "git_sha": "bbbbbbbbbbbb2222",
            "content_hash": "bbbbbbbbbbbb2222",
            "title": "Add refund flow",
            "doc_date": "2024-02-01T00:00:00Z",
            "bytes": 30,
            "meta": {"author": "Carol <carol@example.com>"},
            "text": "Add refund flow",
        }
    )
    grown_adapter = FakeAdapter("repo", grown)
    other = {
        "notes": FakeAdapter("notes", _notes_entries()),
        "mail": FakeAdapter("mail", _mail_entries()),
        "repo": grown_adapter,
    }
    other["notes"].forbid_load = True
    other["mail"].forbid_load = True

    stats = update(config, conn, other, store_entities=EntityStub())

    assert stats.sources["repo"] == SourceStats(added=1, unchanged=2, chunks=1)
    assert grown_adapter.loaded == ["commit/bbbbbbbbbbbb2222"]
    assert conn.execute(
        "SELECT count(*) FROM documents WHERE source = 'repo' AND path LIKE 'commit/%'"
    ).fetchone()[0] == 2


def test_metadata_only_doc_gets_no_chunks(conn, config, adapters):
    update(config, conn, adapters, store_entities=EntityStub())
    doc_id, indexed, *_ = _doc_row(conn, "notes", "logo.png")
    assert indexed == 0
    assert conn.execute("SELECT count(*) FROM chunks WHERE doc_id = ?", (doc_id,)).fetchone()[0] == 0


def test_entities_seam_called_with_lazy_default_and_exact_signature(conn, config, adapters, monkeypatch):
    import sys
    import types

    import corpusindex

    calls = []
    loaded_seeds = object()
    loaded_aliases = object()

    def fake_store_doc_entities(conn, doc_id, doc, seeds, aliases):
        calls.append((doc_id, doc.probe.path, seeds, aliases))

    fake_module = types.SimpleNamespace(
        store_doc_entities=fake_store_doc_entities,
        load_seeds=lambda path: loaded_seeds,
        load_aliases=lambda path: loaded_aliases,
    )
    monkeypatch.setitem(sys.modules, "corpusindex.entities", fake_module)
    monkeypatch.setattr(corpusindex, "entities", fake_module, raising=False)
    from corpusindex.indexer import _load_aliases_cached, _load_seeds_cached

    _load_seeds_cached.cache_clear()
    _load_aliases_cached.cache_clear()

    update(config, conn, adapters)

    # The default seam loads config.seeds/config.aliases paths into value
    # objects before calling store_doc_entities.
    assert len(calls) == 7
    doc_id, path, seeds, aliases = calls[0]
    assert seeds is loaded_seeds
    assert aliases is loaded_aliases


def test_update_source_single_source(conn, config, adapters):
    entities = EntityStub()
    stats = update_source(
        config, conn, config.source("notes"), adapters["notes"], store_entities=entities
    )
    assert stats.added == 4
    assert conn.execute("SELECT count(*) FROM documents").fetchone()[0] == 4


def test_update_respects_sources_filter(conn, config, adapters):
    entities = EntityStub()
    stats = update(config, conn, adapters, sources=["mail"], store_entities=entities)
    assert set(stats.sources) == {"mail"}
    assert conn.execute("SELECT count(*) FROM documents").fetchone()[0] == 1
