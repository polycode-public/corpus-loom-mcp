import json
import re
from pathlib import Path

import pytest

from corpusindex import config as config_
from corpusindex import db as db_
from corpusindex import embed as embed_
from corpusindex.cli import main
from tests.fixtures.make_git_fixture import make_git_fixture

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FILE_TREE = FIXTURES / "file_tree"
MAIL_TREE = FIXTURES / "mail_tree"


@pytest.fixture
def corpus_toml(tmp_path) -> Path:
    git_repo = make_git_fixture(tmp_path / "gitrepo")
    toml_path = tmp_path / "corpus.toml"
    toml_path.write_text(
        f"""
db = "corpus.db"

[[sources]]
name = "files"
type = "filetree"
root = "{FILE_TREE.as_posix()}"

[[sources]]
name = "mail"
type = "eml_tree"
root = "{MAIL_TREE.as_posix()}"

[[sources]]
name = "repo"
type = "gitrepo"
root = "{git_repo.as_posix()}"
"""
    )
    return toml_path


@pytest.fixture
def updated_corpus(corpus_toml, capsys) -> Path:
    rc = main(["update", "--config", str(corpus_toml), "--no-embed"])
    assert rc == 0
    capsys.readouterr()
    return corpus_toml


# --- update ---------------------------------------------------------------


def test_update_no_embed_indexes_all_three_sources(corpus_toml, capsys):
    rc = main(["update", "--config", str(corpus_toml), "--no-embed"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "files: added=5" in out
    assert "mail: added=5" in out
    assert "repo: added=5" in out
    assert "embed:" not in out


def test_update_unknown_source_exits_1(corpus_toml, capsys):
    rc = main(["update", "--config", str(corpus_toml), "--source", "bogus", "--no-embed"])
    err = capsys.readouterr().err

    assert rc == 1
    assert "bogus" in err


def test_update_single_source_flag(corpus_toml, capsys):
    rc = main(["update", "--config", str(corpus_toml), "--source", "files", "--no-embed"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "files: added=5" in out
    assert "mail:" not in out
    assert "repo:" not in out


def test_rerunning_update_is_a_noop(updated_corpus, capsys):
    rc = main(["update", "--config", str(updated_corpus), "--no-embed"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "files: added=0 changed=0 deleted=0 unchanged=5" in out
    assert "mail: added=0 changed=0 deleted=0 unchanged=5" in out
    assert "repo: added=0 changed=0 deleted=0 unchanged=5" in out


# --- search -----------------------------------------------------------------


def test_search_lexical_finds_known_fixture_string(updated_corpus, capsys):
    rc = main(["search", "--config", str(updated_corpus), "--mode", "lexical", "INV-2024-0312"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "INV-2024-0312" in out
    assert "mail/fixturebox@example.com/2024/3/7/17a0c9e4b21f83d5.eml" in out


def test_search_json_round_trips(updated_corpus, capsys):
    rc = main(
        ["search", "--config", str(updated_corpus), "--mode", "lexical", "--json", "INV-2024-0312"]
    )
    out = capsys.readouterr().out

    assert rc == 0
    hits = json.loads(out)
    assert isinstance(hits, list)
    assert hits
    for hit in hits:
        assert set(hit) == {
            "source",
            "path",
            "title",
            "date",
            "score",
            "excerpt",
            "chunk_seq",
            "content_indexed",
        }
    assert any(h["path"].endswith("17a0c9e4b21f83d5.eml") for h in hits)


def test_search_lexical_does_not_call_query_embed(updated_corpus, capsys, monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("query_embed should not be called in lexical mode")

    monkeypatch.setattr(embed_, "query_embed", _boom)
    rc = main(["search", "--config", str(updated_corpus), "--mode", "lexical", "INV-2024-0312"])
    assert rc == 0


def test_search_semantic_degrades_to_lexical_without_key(updated_corpus, capsys, monkeypatch):
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    rc = main(["search", "--config", str(updated_corpus), "--mode", "semantic", "INV-2024-0312"])
    captured = capsys.readouterr()

    assert rc == 0
    assert "degrading to lexical" in captured.err
    assert "INV-2024-0312" in captured.out


def test_search_rerank_flag_emits_notice_and_still_returns_hits(updated_corpus, capsys):
    rc = main(
        ["search", "--config", str(updated_corpus), "--mode", "lexical", "--rerank", "INV-2024-0312"]
    )
    captured = capsys.readouterr()

    assert rc == 0
    assert "rerank" in captured.err
    assert "not yet implemented" in captured.err
    assert "INV-2024-0312" in captured.out


def test_search_source_filter(updated_corpus, capsys):
    rc = main(
        [
            "search",
            "--config",
            str(updated_corpus),
            "--mode",
            "lexical",
            "--source",
            "mail",
            "--json",
            "INV-2024-0312",
        ]
    )
    hits = json.loads(capsys.readouterr().out)
    assert hits
    assert all(h["source"] == "mail" for h in hits)


# --- doc --------------------------------------------------------------------


def test_doc_prints_reconstructed_content(updated_corpus, capsys):
    rc = main(
        [
            "doc",
            "--config",
            str(updated_corpus),
            "mail",
            "fixturebox@example.com/2024/3/7/17a0c9e4b21f83d5.eml",
        ]
    )
    out = capsys.readouterr().out

    assert rc == 0
    assert "content_indexed: True" in out
    assert "PO-7719" in out


def test_doc_json(updated_corpus, capsys):
    rc = main(
        [
            "doc",
            "--config",
            str(updated_corpus),
            "--json",
            "mail",
            "fixturebox@example.com/2024/3/7/17a0c9e4b21f83d5.eml",
        ]
    )
    result = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert result["content_indexed"] is True
    assert "PO-7719" in result["content"]
    assert result["title"] == "Invoice INV-2024-0312 query"


def test_doc_not_found_exits_1(updated_corpus, capsys):
    rc = main(["doc", "--config", str(updated_corpus), "mail", "does/not/exist.eml"])
    err = capsys.readouterr().err

    assert rc == 1
    assert "not found" in err


# --- entity -----------------------------------------------------------------


def test_entity_card_shows_fixture_correspondent(updated_corpus, capsys):
    rc = main(["entity", "--config", str(updated_corpus), "priya.raman@bluegable.example.com"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "Priya Raman" in out
    assert "INV-2024-0312" in out


def test_entity_json(updated_corpus, capsys):
    rc = main(
        ["entity", "--config", str(updated_corpus), "--json", "priya.raman@bluegable.example.com"]
    )
    result = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert result["entity"]["kind"] == "email"
    assert result["entity"]["key"] == "priya.raman@bluegable.example.com"
    assert any(d["rel"] == "author" for d in result["documents"])
    assert any(d["rel"] == "from" for d in result["documents"])


def test_entity_not_found_exits_1(updated_corpus, capsys):
    rc = main(["entity", "--config", str(updated_corpus), "nobody@nowhere.example.com"])
    err = capsys.readouterr().err

    assert rc == 1
    assert "not found" in err


# --- stats --------------------------------------------------------------


def test_stats_counts_reconcile_with_fixture_corpus(updated_corpus, capsys):
    rc = main(["stats", "--config", str(updated_corpus), "--json"])
    result = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert result["sources"]["files"]["documents"] == 5
    assert result["sources"]["mail"]["documents"] == 5
    assert result["sources"]["repo"]["documents"] == 5
    assert result["db_bytes"] > 0


def test_stats_human_output(updated_corpus, capsys):
    rc = main(["stats", "--config", str(updated_corpus)])
    out = capsys.readouterr().out

    assert rc == 0
    assert "files: documents=5" in out
    assert "mail: documents=5" in out
    assert "repo: documents=5" in out
    assert "db size:" in out


# --- embed --------------------------------------------------------------


def test_embed_dry_run_zero_cost_when_nothing_eligible(corpus_toml, capsys):
    rc = main(["embed", "--config", str(corpus_toml), "--dry-run"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "total: chunks=0 tokens=0 cost_usd=0.0000" in out


def test_embed_dry_run_matches_plan_and_constructs_no_client(updated_corpus, capsys, monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("dry-run must never construct an embedding client")

    monkeypatch.setattr(embed_, "_default_client", _boom)

    cfg = config_.load_config(str(updated_corpus))
    conn = db_.connect(cfg.db)
    try:
        expected = embed_.plan(cfg, conn)
    finally:
        conn.close()

    rc = main(["embed", "--config", str(updated_corpus), "--dry-run"])
    out = capsys.readouterr().out

    assert rc == 0
    match = re.search(r"total: chunks=(\d+) tokens=(\d+) cost_usd=([\d.]+)", out)
    assert match is not None
    assert int(match.group(1)) == expected.chunks
    assert int(match.group(2)) == expected.tokens
    assert float(match.group(3)) == pytest.approx(expected.cost_usd, abs=1e-4)
    assert expected.chunks > 0


# --- bootstrap-aliases --------------------------------------------------------


def test_bootstrap_aliases_writes_review_file(updated_corpus, capsys):
    rc = main(["bootstrap-aliases", "--config", str(updated_corpus), "--top", "50"])
    out = capsys.readouterr().out

    out_path = updated_corpus.parent / "aliases-bootstrap.toml"
    assert rc == 0
    assert str(out_path) in out
    assert out_path.is_file()
    content = out_path.read_text()
    assert "priya.raman@bluegable.example.com" in content


# --- config resolution / exit codes ------------------------------------------


def test_missing_config_exits_2(capsys):
    rc = main(["stats", "--config", "/nonexistent/corpus.toml"])
    err = capsys.readouterr().err

    assert rc == 2
    assert "error:" in err


def test_invalid_config_exits_2(tmp_path, capsys):
    bad = tmp_path / "corpus.toml"
    bad.write_text("this is not valid toml [[[")
    rc = main(["stats", "--config", str(bad)])
    err = capsys.readouterr().err

    assert rc == 2
    assert "error:" in err


def test_env_var_config_resolution(corpus_toml, capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("CORPUS_INDEX_CONFIG", str(corpus_toml))
    monkeypatch.chdir(tmp_path)
    rc = main(["update", "--no-embed"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "files: added=5" in out


def test_explicit_config_flag_wins_over_env_var(corpus_toml, tmp_path, capsys, monkeypatch):
    other = tmp_path / "other.toml"
    other.write_text("this is not valid toml [[[")
    monkeypatch.setenv("CORPUS_INDEX_CONFIG", str(other))
    rc = main(["update", "--config", str(corpus_toml), "--no-embed"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "files: added=5" in out
