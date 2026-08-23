import sqlite3
import sys

import pytest

from corpusindex.config import Config, SourceConfig
from corpusindex.db import connect
from corpusindex.embed import (
    PRICING,
    EmbedError,
    drain,
    model_kind_for,
    plan,
    query_embed,
    resolve_api_key,
)


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


@pytest.fixture(autouse=True)
def _no_real_api_key(monkeypatch):
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)


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
            SourceConfig(name="off", type="filetree", root=tmp_path / "off", embed=False),
            SourceConfig(
                name="main",
                type="filetree",
                root=tmp_path / "main",
                embed=True,
                embed_exclude=("finance/**",),
            ),
        ),
        config_dir=tmp_path,
    )


def _insert_doc(conn, source, path, chunk_texts):
    with conn:
        cur = conn.execute(
            "INSERT INTO documents (source, path, content_hash, content_indexed)"
            " VALUES (?, ?, 'h', 1)",
            (source, path),
        )
        doc_id = cur.lastrowid
        for seq, text in enumerate(chunk_texts):
            conn.execute(
                "INSERT INTO chunks (doc_id, seq, text, embedded) VALUES (?, ?, ?, 0)",
                (doc_id, seq, text),
            )
    return doc_id


def _seed_mixed(conn):
    _insert_doc(conn, "off", "off/whatever.txt", ["never sent, source disabled"])
    _insert_doc(conn, "main", "notes/readme.md", ["alpha beta gamma delta widgets"])
    _insert_doc(conn, "main", "src/app.py", ["def foo():\n    return 1\n"])
    _insert_doc(conn, "main", "commit/aaaaaaaaaaaa", ["repo aaaaaaa Bob 2024-01-05\n\nFix bug"])
    _insert_doc(conn, "main", "finance/secret.md", ["excluded prose finance content"])
    _insert_doc(conn, "main", "finance/ledger.py", ["excluded_code = True"])


class _FakeResult:
    def __init__(self, embeddings):
        self.embeddings = embeddings


def _vector_for(text: str) -> list[float]:
    return [float((len(text) + i) % 11) for i in range(1024)]


class FakeClient:
    def __init__(self, fail_calls: int = 0, raise_on_call: int | None = None, raise_exc=None):
        self.calls: list[tuple[list[str], str, str]] = []
        self.fail_calls = fail_calls
        self.raise_on_call = raise_on_call
        self.raise_exc = raise_exc
        self._retryable_raised = 0

    def embed(self, texts, model=None, input_type=None):
        self.calls.append((list(texts), model, input_type))
        call_no = len(self.calls)
        if self.raise_on_call is not None and call_no == self.raise_on_call:
            raise self.raise_exc
        if self._retryable_raised < self.fail_calls:
            self._retryable_raised += 1
            from voyageai import error as verror

            raise verror.RateLimitError("rate limited")
        return _FakeResult([_vector_for(t) for t in texts])


# --- plan() -----------------------------------------------------------------


def test_plan_math_and_source_exclusion(conn, config):
    _seed_mixed(conn)
    result = plan(config, conn)

    assert result.chunks == 3
    assert "off" not in result.per_source
    assert result.per_source["main"]["prose"].chunks == 2  # readme + commit
    assert result.per_source["main"]["code"].chunks == 1  # app.py

    texts = ["alpha beta gamma delta widgets", "repo aaaaaaa Bob 2024-01-05\n\nFix bug"]
    expected_prose_tokens = sum(len(t) // 4 for t in texts)
    assert result.per_model["prose"].tokens == expected_prose_tokens
    expected_prose_cost = expected_prose_tokens * PRICING[config.prose_model] / 1_000_000
    assert result.per_model["prose"].cost_usd == pytest.approx(expected_prose_cost)

    code_text = "def foo():\n    return 1\n"
    expected_code_tokens = len(code_text) // 4
    assert result.per_model["code"].tokens == expected_code_tokens
    expected_code_cost = expected_code_tokens * PRICING[config.code_model] / 1_000_000
    assert result.per_model["code"].cost_usd == pytest.approx(expected_code_cost)

    assert result.tokens == expected_prose_tokens + expected_code_tokens
    assert result.cost_usd == pytest.approx(expected_prose_cost + expected_code_cost)


def test_plan_excludes_finance_glob(conn, config):
    _seed_mixed(conn)
    result = plan(config, conn)
    rows = conn.execute("SELECT text FROM chunks").fetchall()
    finance_texts = {r[0] for r in rows if "excluded" in r[0]}
    assert finance_texts  # sanity: the excluded rows exist in the db at all
    assert result.chunks == 3  # readme, app.py, commit -- never the two finance chunks


def test_model_kind_for_commit_is_always_prose():
    assert model_kind_for("commit/deadbeef") == "prose"
    assert model_kind_for("src/app.py") == "code"
    assert model_kind_for("notes/readme.md") == "prose"


# --- drain() eligibility + routing -------------------------------------------


@pytest.mark.skipif(not HAVE_VEC, reason="sqlite-vec not installed")
def test_drain_eligibility_and_routing(conn, config):
    _seed_mixed(conn)
    client = FakeClient()
    stats = drain(config, conn, client_factory=lambda: client)

    assert stats.embedded == 3
    assert stats.per_model == {"prose": 2, "code": 1}

    all_sent = [t for texts, _m, _it in client.calls for t in texts]
    assert "excluded prose finance content" not in all_sent
    assert "excluded_code = True" not in all_sent
    assert "alpha beta gamma delta widgets" in all_sent
    assert "def foo():\n    return 1\n" in all_sent

    models_used = {model for _texts, model, input_type in client.calls}
    assert models_used == {config.prose_model, config.code_model}
    assert all(input_type == "document" for _t, _m, input_type in client.calls)

    prose_row = conn.execute(
        "SELECT c.chunk_id FROM chunks c JOIN documents d ON d.doc_id = c.doc_id"
        " WHERE d.path = 'notes/readme.md'"
    ).fetchone()
    code_row = conn.execute(
        "SELECT c.chunk_id FROM chunks c JOIN documents d ON d.doc_id = c.doc_id"
        " WHERE d.path = 'src/app.py'"
    ).fetchone()
    assert conn.execute(
        "SELECT count(*) FROM chunks_vec_prose WHERE chunk_id = ?", prose_row
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT count(*) FROM chunks_vec_code WHERE chunk_id = ?", code_row
    ).fetchone()[0] == 1

    remaining = conn.execute("SELECT count(*) FROM chunks WHERE embedded = 0").fetchone()[0]
    assert remaining == 3  # the two excluded finance chunks and the disabled-source chunk


@pytest.mark.skipif(not HAVE_VEC, reason="sqlite-vec not installed")
def test_drain_excludes_disabled_source_from_calls(conn, config):
    _seed_mixed(conn)
    client = FakeClient()
    drain(config, conn, client_factory=lambda: client)
    all_sent = [t for texts, _m, _it in client.calls for t in texts]
    assert "never sent, source disabled" not in all_sent


@pytest.mark.skipif(not HAVE_VEC, reason="sqlite-vec not installed")
def test_drain_batch_chunk_limit_splits(conn, config):
    for i in range(3):
        _insert_doc(conn, "main", f"src/mod{i}.py", [f"print({i})\n"])
    client = FakeClient()
    stats = drain(config, conn, client_factory=lambda: client, batch_chunks=2, batch_tokens=1_000_000)

    assert stats.embedded == 3
    code_calls = [c for c in client.calls if c[1] == config.code_model]
    assert [len(texts) for texts, _m, _it in code_calls] == [2, 1]


@pytest.mark.skipif(not HAVE_VEC, reason="sqlite-vec not installed")
def test_drain_batch_token_limit_splits(conn, config):
    chunk_text = "x" * 400  # 100 tokens at 4 chars/token
    for i in range(3):
        _insert_doc(conn, "main", f"notes/big{i}.md", [chunk_text])
    client = FakeClient()
    stats = drain(config, conn, client_factory=lambda: client, batch_chunks=128, batch_tokens=250)

    assert stats.embedded == 3
    prose_calls = [c for c in client.calls if c[1] == config.prose_model]
    assert [len(texts) for texts, _m, _it in prose_calls] == [2, 1]


@pytest.mark.skipif(not HAVE_VEC, reason="sqlite-vec not installed")
def test_drain_transactional_resume_does_not_resend_committed_batch(conn, config):
    _insert_doc(conn, "main", "src/one.py", ["print('one')\n"])
    _insert_doc(conn, "main", "src/two.py", ["print('two')\n"])

    killer = FakeClient(raise_on_call=2, raise_exc=RuntimeError("killed"))
    with pytest.raises(RuntimeError, match="killed"):
        drain(config, conn, client_factory=lambda: killer, batch_chunks=1, batch_tokens=1_000_000)

    assert len(killer.calls) == 2
    remaining = conn.execute("SELECT count(*) FROM chunks WHERE embedded = 0").fetchone()[0]
    assert remaining == 1

    resumer = FakeClient()
    stats = drain(config, conn, client_factory=lambda: resumer, batch_chunks=1, batch_tokens=1_000_000)

    assert stats.embedded == 1
    assert len(resumer.calls) == 1
    resent_texts = [t for texts, _m, _it in resumer.calls for t in texts]
    first_call_texts = killer.calls[0][0]
    assert not set(resent_texts) & set(first_call_texts)


@pytest.mark.skipif(not HAVE_VEC, reason="sqlite-vec not installed")
def test_drain_retries_with_backoff_then_succeeds(conn, config, monkeypatch):
    _insert_doc(conn, "main", "notes/one.md", ["hello world content here"])
    sleeps: list[float] = []
    monkeypatch.setattr("corpusindex.embed.time.sleep", lambda s: sleeps.append(s))

    flaky = FakeClient(fail_calls=2)
    stats = drain(config, conn, client_factory=lambda: flaky, max_retries=5)

    assert stats.embedded == 1
    assert stats.retries == 2
    assert sleeps == [1, 2]


@pytest.mark.skipif(not HAVE_VEC, reason="sqlite-vec not installed")
def test_drain_gives_up_after_max_retries(conn, config, monkeypatch):
    _insert_doc(conn, "main", "notes/one.md", ["hello world content here"])
    monkeypatch.setattr("corpusindex.embed.time.sleep", lambda s: None)

    always_fails = FakeClient(fail_calls=999)
    with pytest.raises(Exception):
        drain(config, conn, client_factory=lambda: always_fails, max_retries=2)


def test_drain_raises_when_vec_unavailable(tmp_path, config, monkeypatch):
    monkeypatch.setitem(sys.modules, "sqlite_vec", None)
    conn = connect(tmp_path / "novec.db")
    monkeypatch.undo()
    try:
        _insert_doc(conn, "main", "notes/one.md", ["hello world"])
        with pytest.raises(EmbedError, match="vector"):
            drain(config, conn, client_factory=lambda: FakeClient())
    finally:
        conn.close()


# --- query_embed --------------------------------------------------------------


def test_query_embed_uses_query_input_type_and_model_routing(config):
    client = FakeClient()
    vec_prose = query_embed("some prose", "prose", config, client_factory=lambda: client)
    vec_code = query_embed("def f(): pass", "code", config, client_factory=lambda: client)

    assert len(vec_prose) == 1024
    assert len(vec_code) == 1024
    assert client.calls[0] == (["some prose"], config.prose_model, "query")
    assert client.calls[1] == (["def f(): pass"], config.code_model, "query")


# --- API key resolution --------------------------------------------------------


def test_resolve_api_key_from_env(config, monkeypatch):
    monkeypatch.setenv("VOYAGE_API_KEY", "env-key")
    assert resolve_api_key(config) == "env-key"


def test_resolve_api_key_from_dotenv_plain_and_quoted(tmp_path):
    (tmp_path / ".env").write_text('OTHER=1\nVOYAGE_API_KEY="quoted-key"\n')
    config = Config(db=tmp_path / "corpus.db", sources=(), config_dir=tmp_path)
    assert resolve_api_key(config) == "quoted-key"


def test_resolve_api_key_from_dotenv_unquoted(tmp_path):
    (tmp_path / ".env").write_text("VOYAGE_API_KEY=plain-key\n")
    config = Config(db=tmp_path / "corpus.db", sources=(), config_dir=tmp_path)
    assert resolve_api_key(config) == "plain-key"


def test_resolve_api_key_env_wins_over_dotenv(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("VOYAGE_API_KEY=dotenv-key\n")
    monkeypatch.setenv("VOYAGE_API_KEY", "env-key")
    config = Config(db=tmp_path / "corpus.db", sources=(), config_dir=tmp_path)
    assert resolve_api_key(config) == "env-key"


def test_resolve_api_key_missing_raises(tmp_path):
    config = Config(db=tmp_path / "corpus.db", sources=(), config_dir=tmp_path)
    with pytest.raises(EmbedError):
        resolve_api_key(config)
