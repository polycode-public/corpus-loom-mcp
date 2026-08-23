"""Sanity checks that the fixture mini-corpus is what later waves expect."""

import email
import email.policy
import subprocess
from pathlib import Path

from tests.fixtures.make_git_fixture import make_git_fixture

FIXTURES = Path(__file__).parent / "fixtures"
MAILBOX = FIXTURES / "mail_tree" / "fixturebox@example.com"


def parse(rel):
    return email.message_from_bytes(
        (MAILBOX / rel).read_bytes(), policy=email.policy.default
    )


def test_mail_tree_layout_dates_not_zero_padded():
    emls = sorted(p.relative_to(MAILBOX) for p in MAILBOX.rglob("*.eml"))
    assert len(emls) == 5
    for p in emls:
        year, month, day, _ = p.parts
        assert not month.startswith("0") and not day.startswith("0")


def test_plain_text_message():
    msg = parse("2024/3/7/17a0c9e4b21f83d5.eml")
    assert msg.get_content_type() == "text/plain"
    body = msg.get_body(preferencelist=("plain",)).get_content()
    assert "INV-2024-0312" in body


def test_multipart_alternative():
    msg = parse("2024/3/7/2f4e8a91c07d3b66.eml")
    assert msg.get_content_type() == "multipart/alternative"
    assert "HB-20441" in msg.get_body(preferencelist=("plain",)).get_content()
    assert "<b>HB-20441</b>" in msg.get_body(preferencelist=("html",)).get_content()


def test_html_only_base64():
    msg = parse("2024/11/2/9c31d7e5a8f2404b.eml")
    assert msg.get_content_type() == "text/html"
    assert msg["Content-Transfer-Encoding"] == "base64"
    assert msg.get_body(preferencelist=("plain",)) is None
    assert "Ledgerworks" in msg.get_body(preferencelist=("html",)).get_content()


def test_iso_8859_1_charset():
    msg = parse("2023/6/9/0b7f3c2d91e6a854.eml")
    assert msg.get_content_charset() == "iso-8859-1"
    body = msg.get_body(preferencelist=("plain",)).get_content()
    assert "£175" in body
    assert "Jürgen" in body
    assert "Søren Åkesson" in msg["From"]


def test_xlsx_attachment_message():
    msg = parse("2025/1/6/6d2a9f0e4c7b1358.eml")
    attachments = list(msg.iter_attachments())
    assert [a.get_filename() for a in attachments] == ["cashflow-december-2024.xlsx"]
    payload = attachments[0].get_payload(decode=True)
    assert payload.startswith(b"PK\x03\x04")
    assert b"not a real xlsx" in payload
    assert "cashflow" in msg.get_body(preferencelist=("plain",)).get_content()


def test_file_tree_contents():
    tree = FIXTURES / "file_tree"
    rels = {str(p.relative_to(tree)) for p in tree.rglob("*") if p.is_file()}
    assert rels == {
        "finance/2023-accounts-summary.txt",
        "finance/bank-statement-2024-q1.csv",
        "finance/vat-return-2024.pdf",
        "minutes/2024-03-board-minutes.md",
        "minutes/agm-notice.html",
    }
    assert (tree / "finance/vat-return-2024.pdf").read_bytes().startswith(b"%PDF-")


def test_make_git_fixture(tmp_path):
    repo = make_git_fixture(tmp_path / "repo")
    log = subprocess.run(
        ["git", "-C", str(repo), "log", "--format=%an|%ae|%s"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip().splitlines()
    assert len(log) == 3
    assert log[0].startswith("Priya Raman|priya.raman@bluegable.example.com|Correct")
    files = subprocess.run(
        ["git", "-C", str(repo), "ls-files"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    assert files == ["docs/pricing.md", "scripts/invoice.py"]
