import textwrap
from pathlib import Path

import pytest

from corpusindex.config import (
    ConfigError,
    load_config,
)

GOOD = """
db = "index/corpus.db"

[models]
prose = "voyage-3.5"
code = "voyage-code-3"

[entities]
seeds = "seeds.toml"
aliases = "aliases.toml"

[[sources]]
name = "drive"
type = "filetree"
root = "mirror/drive"
include = ["**/*.txt", "**/*.md"]
exclude = ["packages/**"]
convert = ["pdf", ".doc", "DOCX"]
embed_exclude = ["finance/**", "personnel/**"]

[[sources]]
name = "mail"
type = "eml_tree"
root = "/abs/mail"
embed = true

[[sources]]
name = "repo:submit"
type = "gitrepo"
root = "repos/submit"
branch = "main"
embed = false
"""


def write(tmp_path: Path, text: str, name: str = "corpus.toml") -> Path:
    p = tmp_path / name
    p.write_text(textwrap.dedent(text), encoding="utf-8")
    return p


def test_good_config(tmp_path):
    cfg = load_config(write(tmp_path, GOOD))
    assert cfg.db == tmp_path / "index/corpus.db"
    assert cfg.config_dir == tmp_path
    assert cfg.prose_model == "voyage-3.5"
    assert cfg.code_model == "voyage-code-3"
    assert cfg.seeds == tmp_path / "seeds.toml"
    assert cfg.aliases == tmp_path / "aliases.toml"
    assert [s.name for s in cfg.sources] == ["drive", "mail", "repo:submit"]

    drive = cfg.source("drive")
    assert drive.type == "filetree"
    assert drive.root == tmp_path / "mirror/drive"
    assert drive.include == ("**/*.txt", "**/*.md")
    assert drive.exclude == ("packages/**",)
    assert drive.convert == ("pdf", "doc", "docx")
    assert drive.embed is True
    assert drive.embed_exclude == ("finance/**", "personnel/**")

    mail = cfg.source("mail")
    assert mail.root == Path("/abs/mail")
    assert mail.convert == ()
    assert mail.branch is None

    repo = cfg.source("repo:submit")
    assert repo.branch == "main"
    assert repo.embed is False


def test_minimal_defaults(tmp_path):
    cfg = load_config(
        write(
            tmp_path,
            """
            db = "corpus.db"
            [[sources]]
            name = "docs"
            type = "filetree"
            root = "docs"
            """,
        )
    )
    s = cfg.source("docs")
    assert s.include == () and s.exclude == ()
    assert s.embed is True and s.embed_exclude == ()
    assert cfg.prose_model == "voyage-3.5"
    assert cfg.code_model == "voyage-code-3"
    assert cfg.seeds is None and cfg.aliases is None


def test_env_var_fallback(tmp_path, monkeypatch):
    path = write(tmp_path, GOOD, "elsewhere.toml")
    monkeypatch.setenv("CORPUS_INDEX_CONFIG", str(path))
    assert load_config().db == tmp_path / "index/corpus.db"


def test_cwd_fallback(tmp_path, monkeypatch):
    write(tmp_path, GOOD)
    monkeypatch.delenv("CORPUS_INDEX_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    assert load_config().db == tmp_path / "index/corpus.db"


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.toml")


def test_invalid_toml(tmp_path):
    with pytest.raises(ConfigError, match="invalid TOML"):
        load_config(write(tmp_path, "db = [unclosed"))


@pytest.mark.parametrize(
    ("snippet", "match"),
    [
        ("[[sources]]\nname='a'\ntype='filetree'\nroot='r'", "missing required key 'db'"),
        ("db='x.db'", "at least one"),
        ("db='x.db'\n[[sources]]\ntype='filetree'\nroot='r'", "missing required key 'name'"),
        ("db='x.db'\n[[sources]]\nname='a'\nroot='r'", "missing required key 'type'"),
        ("db='x.db'\n[[sources]]\nname='a'\ntype='ftp'\nroot='r'", "not one of"),
        (
            "db='x.db'\n[[sources]]\nname='a'\ntype='filetree'\nroot='r'\nbranch='main'",
            "only valid for type 'gitrepo'",
        ),
        (
            "db='x.db'\n[[sources]]\nname='a'\ntype='filetree'\nroot='r'\n"
            "[[sources]]\nname='a'\ntype='eml_tree'\nroot='s'",
            "duplicate source names",
        ),
        (
            "db='x.db'\n[[sources]]\nname='a'\ntype='filetree'\nroot='r'\ninclude='*.txt'",
            "expected list",
        ),
        (
            "db='x.db'\n[[sources]]\nname='a'\ntype='filetree'\nroot='r'\nembed='yes'",
            "expected bool",
        ),
        (
            "db='x.db'\n[[sources]]\nname='a'\ntype='filetree'\nroot='r'\nfrobnicate=1",
            "unknown keys",
        ),
        ("db='x.db'\ntypo=1\n[[sources]]\nname='a'\ntype='filetree'\nroot='r'", "unknown top-level"),
        (
            "db='x.db'\n[models]\nspeech='m'\n[[sources]]\nname='a'\ntype='filetree'\nroot='r'",
            "models: unknown keys",
        ),
        ("db=7\n[[sources]]\nname='a'\ntype='filetree'\nroot='r'", "expected str"),
    ],
)
def test_bad_configs(tmp_path, snippet, match):
    with pytest.raises(ConfigError, match=match):
        load_config(write(tmp_path, snippet))
