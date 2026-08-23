import re
from pathlib import Path

import pytest

from corpusindex.adapters.base import DocProbe
from corpusindex.adapters.filetree import FiletreeAdapter
from corpusindex.config import SourceConfig

FIXTURE_ROOT = Path(__file__).parent.parent / "fixtures" / "file_tree"

STAT_SIG_RE = re.compile(r"^\d+:\d+$")


def _config(**overrides) -> SourceConfig:
    fields = dict(name="files", type="filetree", root=FIXTURE_ROOT)
    fields.update(overrides)
    return SourceConfig(**fields)


def _discover_paths(adapter: FiletreeAdapter) -> set[str]:
    return {probe.path for probe in adapter.discover()}


def test_discover_all_files():
    adapter = FiletreeAdapter(_config())
    assert _discover_paths(adapter) == {
        "finance/2023-accounts-summary.txt",
        "finance/bank-statement-2024-q1.csv",
        "finance/vat-return-2024.pdf",
        "minutes/2024-03-board-minutes.md",
        "minutes/agm-notice.html",
    }


def test_discover_stat_sig_format():
    adapter = FiletreeAdapter(_config())
    for probe in adapter.discover():
        assert isinstance(probe, DocProbe)
        assert probe.source == "files"
        assert probe.git_sha is None
        assert STAT_SIG_RE.match(probe.stat_sig)


def test_discover_include_glob():
    adapter = FiletreeAdapter(_config(include=("minutes/*",)))
    assert _discover_paths(adapter) == {
        "minutes/2024-03-board-minutes.md",
        "minutes/agm-notice.html",
    }


def test_discover_exclude_glob():
    adapter = FiletreeAdapter(_config(exclude=("finance/*",)))
    assert _discover_paths(adapter) == {
        "minutes/2024-03-board-minutes.md",
        "minutes/agm-notice.html",
    }


def test_discover_include_and_exclude():
    adapter = FiletreeAdapter(_config(include=("*.md", "*.csv"), exclude=("finance/*",)))
    assert _discover_paths(adapter) == {"minutes/2024-03-board-minutes.md"}


def _load(adapter: FiletreeAdapter, path: str):
    probe = next(p for p in adapter.discover() if p.path == path)
    return adapter.load(probe)


def test_decode_gate_txt():
    adapter = FiletreeAdapter(_config())
    doc = _load(adapter, "finance/2023-accounts-summary.txt")
    assert doc.content_indexed
    assert "Turnover" in doc.text
    assert doc.title == "2023-accounts-summary.txt"
    assert doc.doc_date is not None
    assert len(doc.content_hash) == 64
    assert doc.bytes == (FIXTURE_ROOT / "finance/2023-accounts-summary.txt").stat().st_size


def test_decode_gate_csv():
    adapter = FiletreeAdapter(_config())
    doc = _load(adapter, "finance/bank-statement-2024-q1.csv")
    assert doc.content_indexed
    assert "Date,Description,Reference" in doc.text


def test_decode_gate_markdown():
    adapter = FiletreeAdapter(_config())
    doc = _load(adapter, "minutes/2024-03-board-minutes.md")
    assert doc.content_indexed
    assert "Board minutes" in doc.text


def test_decode_gate_html_strips_tags():
    adapter = FiletreeAdapter(_config())
    doc = _load(adapter, "minutes/agm-notice.html")
    assert doc.content_indexed
    assert "Annual General Meeting" in doc.text
    assert "<html>" not in doc.text
    assert "<b>" not in doc.text
    assert "console.log" not in doc.text
    assert "margin: 2em" not in doc.text


def test_convert_gate_not_configured_is_metadata_only():
    adapter = FiletreeAdapter(_config())
    doc = _load(adapter, "finance/vat-return-2024.pdf")
    assert not doc.content_indexed
    assert doc.text is None


def test_convert_gate_configured_graceful_metadata_only():
    adapter = FiletreeAdapter(_config(convert=("pdf",)))
    doc = _load(adapter, "finance/vat-return-2024.pdf")
    assert not doc.content_indexed
    assert doc.text is None


def test_metadata_only_for_unconfigured_extension_defaults_empty_meta():
    adapter = FiletreeAdapter(_config())
    doc = _load(adapter, "finance/vat-return-2024.pdf")
    assert doc.meta == {}
    assert doc.title == "vat-return-2024.pdf"


def test_content_hash_is_sha256_of_raw_bytes():
    import hashlib

    adapter = FiletreeAdapter(_config())
    doc = _load(adapter, "finance/2023-accounts-summary.txt")
    raw = (FIXTURE_ROOT / "finance/2023-accounts-summary.txt").read_bytes()
    assert doc.content_hash == hashlib.sha256(raw).hexdigest()


def test_skips_git_and_bookkeeping(tmp_path):
    root = tmp_path / "tree"
    (root / ".git").mkdir(parents=True)
    (root / ".git" / "config").write_text("ignore me")
    (root / "docs").mkdir()
    (root / "docs" / "note.txt").write_text("keep me")
    (root / "msg-db.sqlite").write_bytes(b"")

    adapter = FiletreeAdapter(_config(root=root))
    assert _discover_paths(adapter) == {"docs/note.txt"}
