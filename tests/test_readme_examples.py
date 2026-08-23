"""Keeps the README's quickstart config honest: extract the fenced
corpus.toml example straight from README.md and parse it for real
against tmp dirs, so a future README edit that breaks the example
fails CI instead of just misleading a new user.
"""

import re
from pathlib import Path

from corpusindex.config import load_config

README = Path(__file__).resolve().parent.parent / "README.md"

_TOML_BLOCK_RE = re.compile(r"```toml\n(.*?)```", re.DOTALL)


def _quickstart_toml() -> str:
    """The first fenced ```toml block in README.md — the full
    docs/mail/repo example under Quickstart. (The Embeddings section's
    embed/embed_exclude snippet is a fragment, not a standalone
    document, so it's deliberately not the one picked up here.)
    """
    text = README.read_text(encoding="utf-8")
    match = _TOML_BLOCK_RE.search(text)
    assert match, "README.md has no fenced ```toml block"
    return match.group(1)


def test_readme_has_a_toml_block():
    toml_text = _quickstart_toml()
    assert "[[sources]]" in toml_text


def test_readme_quickstart_config_parses(tmp_path):
    toml_text = _quickstart_toml()
    (tmp_path / "corpus.toml").write_text(toml_text, encoding="utf-8")

    # The example's source roots (docs, mail/me, repo) are relative paths;
    # load_config only parses and resolves them, it doesn't require the
    # directories to exist, but create them anyway so this test doubles
    # as a check that the example's roots are the ones actually referenced.
    for rel in ("docs", "mail/me", "repo"):
        (tmp_path / rel).mkdir(parents=True, exist_ok=True)

    cfg = load_config(tmp_path / "corpus.toml")

    assert cfg.db == tmp_path / "corpus.db"
    names = {s.name for s in cfg.sources}
    assert names == {"docs", "mail", "repo"}

    docs = cfg.source("docs")
    assert docs.type == "filetree"
    assert docs.root == tmp_path / "docs"

    mail = cfg.source("mail")
    assert mail.type == "eml_tree"
    assert mail.root == tmp_path / "mail/me"

    repo = cfg.source("repo")
    assert repo.type == "gitrepo"
    assert repo.root == tmp_path / "repo"
    assert repo.branch == "main"
