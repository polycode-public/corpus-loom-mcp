import re
import shutil
import sqlite3
from pathlib import Path

from corpusindex.adapters.eml_tree import EmlTreeAdapter
from corpusindex.config import SourceConfig

FIXTURE_ROOT = Path(__file__).parent.parent / "fixtures" / "mail_tree" / "fixturebox@example.com"

STAT_SIG_RE = re.compile(r"^\d+:\d+$")

ALL_PATHS = {
    "2023/6/9/0b7f3c2d91e6a854.eml",
    "2024/11/2/9c31d7e5a8f2404b.eml",
    "2024/3/7/17a0c9e4b21f83d5.eml",
    "2024/3/7/2f4e8a91c07d3b66.eml",
    "2025/1/6/6d2a9f0e4c7b1358.eml",
}


def _config(root=FIXTURE_ROOT, **overrides) -> SourceConfig:
    fields = dict(name="mail", type="eml_tree", root=root)
    fields.update(overrides)
    return SourceConfig(**fields)


def _discover_paths(adapter: EmlTreeAdapter) -> set[str]:
    return {probe.path for probe in adapter.discover()}


def _load(adapter: EmlTreeAdapter, path: str):
    probe = next(p for p in adapter.discover() if p.path == path)
    return adapter.load(probe)


def test_discover_finds_all_messages_non_padded_dirs():
    adapter = EmlTreeAdapter(_config())
    assert _discover_paths(adapter) == ALL_PATHS


def test_discover_stat_sig_format():
    adapter = EmlTreeAdapter(_config())
    for probe in adapter.discover():
        assert probe.git_sha is None
        assert STAT_SIG_RE.match(probe.stat_sig)


def test_discover_include_glob():
    adapter = EmlTreeAdapter(_config(include=("2024/*",)))
    assert _discover_paths(adapter) == {
        "2024/11/2/9c31d7e5a8f2404b.eml",
        "2024/3/7/17a0c9e4b21f83d5.eml",
        "2024/3/7/2f4e8a91c07d3b66.eml",
    }


def test_discover_exclude_glob():
    adapter = EmlTreeAdapter(_config(exclude=("2024/*",)))
    assert _discover_paths(adapter) == {
        "2023/6/9/0b7f3c2d91e6a854.eml",
        "2025/1/6/6d2a9f0e4c7b1358.eml",
    }


def test_html_only_body_decoded_and_stripped():
    adapter = EmlTreeAdapter(_config())
    doc = _load(adapter, "2024/11/2/9c31d7e5a8f2404b.eml")
    assert doc.content_indexed
    assert "Ledgerworks autumn update" in doc.text
    assert "faster VAT reporting" in doc.text
    assert "<h1>" not in doc.text
    assert "console.log" not in doc.text
    assert doc.title == "Ledgerworks autumn update"
    assert doc.meta["attachments"] == []


def test_iso_8859_1_body_decoded():
    adapter = EmlTreeAdapter(_config())
    doc = _load(adapter, "2023/6/9/0b7f3c2d91e6a854.eml")
    assert doc.content_indexed
    assert "£175" in doc.text
    assert "Jürgen" in doc.text
    assert "Søren Åkesson" in doc.meta["from"]
    assert "£175" in doc.title


def test_multipart_alternative_prefers_plain():
    adapter = EmlTreeAdapter(_config())
    doc = _load(adapter, "2024/3/7/2f4e8a91c07d3b66.eml")
    assert doc.content_indexed
    assert "HB-20441" in doc.text
    assert "<b>" not in doc.text


def test_attachment_name_captured_without_content():
    adapter = EmlTreeAdapter(_config())
    doc = _load(adapter, "2025/1/6/6d2a9f0e4c7b1358.eml")
    assert doc.meta["attachments"] == ["cashflow-december-2024.xlsx"]
    assert "PK" not in (doc.text or "")
    assert "cashflow" in doc.text


def test_meta_fields_and_content_hash():
    import hashlib

    adapter = EmlTreeAdapter(_config())
    doc = _load(adapter, "2024/3/7/17a0c9e4b21f83d5.eml")
    raw = (FIXTURE_ROOT / "2024/3/7/17a0c9e4b21f83d5.eml").read_bytes()
    assert doc.content_hash == hashlib.sha256(raw).hexdigest()
    assert doc.meta["message_id"] == "<caf9x17q3k.fsf@mail.bluegable.example.com>"
    assert "priya.raman@bluegable.example.com" in doc.meta["from"]
    assert "fixturebox@example.com" in doc.meta["to"]
    assert doc.meta["cc"] is None
    assert doc.meta["labels"] == []
    assert doc.doc_date == "2024-03-07T09:41:20+00:00"


def test_fallback_date_from_path_when_date_header_missing(tmp_path):
    mailbox = tmp_path / "mailbox"
    day_dir = mailbox / "2022" / "7" / "4"
    day_dir.mkdir(parents=True)
    (day_dir / "nodate.eml").write_bytes(
        b"From: sender@example.com\r\n"
        b"To: fixturebox@example.com\r\n"
        b"Subject: No date header here\r\n"
        b"MIME-Version: 1.0\r\n"
        b"Content-Type: text/plain; charset=\"utf-8\"\r\n"
        b"\r\n"
        b"Body with no Date header.\r\n"
    )
    adapter = EmlTreeAdapter(_config(root=mailbox))
    doc = _load(adapter, "2022/7/4/nodate.eml")
    assert doc.doc_date == "2022-07-04T00:00:00+00:00"


def test_synthetic_msg_db_labels(tmp_path):
    mailbox = tmp_path / "mailbox"
    shutil.copytree(FIXTURE_ROOT, mailbox)

    db_path = mailbox / "msg-db.sqlite"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE uids ("
        "message_num INTEGER PRIMARY KEY, "
        "message_filename TEXT, "
        "labels TEXT)"
    )
    conn.execute(
        "INSERT INTO uids (message_filename, labels) VALUES (?, ?)",
        ("2024/3/7/17a0c9e4b21f83d5.eml", "INBOX,IMPORTANT"),
    )
    conn.commit()
    conn.close()

    adapter = EmlTreeAdapter(_config(root=mailbox))
    labelled = _load(adapter, "2024/3/7/17a0c9e4b21f83d5.eml")
    assert labelled.meta["labels"] == ["INBOX", "IMPORTANT"]

    unlabelled = _load(adapter, "2024/11/2/9c31d7e5a8f2404b.eml")
    assert unlabelled.meta["labels"] == []


def test_missing_msg_db_gives_empty_labels():
    adapter = EmlTreeAdapter(_config())
    doc = _load(adapter, "2024/3/7/17a0c9e4b21f83d5.eml")
    assert doc.meta["labels"] == []
