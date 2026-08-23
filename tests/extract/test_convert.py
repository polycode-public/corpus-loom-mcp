import os
import stat
import sys

import pytest

from corpusindex.extract import convert


def _write_fake_tool(bin_dir, name: str, script: str):
    tool_path = bin_dir / name
    tool_path.write_text(script)
    tool_path.chmod(tool_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return tool_path


@pytest.fixture
def empty_path(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    return tmp_path


def test_convert_pdf_returns_none_when_tool_missing(empty_path, tmp_path):
    fake_pdf = tmp_path / "letter.pdf"
    fake_pdf.write_bytes(b"%PDF-fake")
    assert convert.convert_pdf(fake_pdf) is None


def test_convert_pdf_returns_extracted_text(monkeypatch, tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_tool(
        bin_dir, "pdftotext",
        "#!/bin/sh\necho 'Minutes of the Widgetco Ltd board meeting.'\n",
    )
    monkeypatch.setenv("PATH", str(bin_dir))
    fake_pdf = tmp_path / "minutes.pdf"
    fake_pdf.write_bytes(b"%PDF-fake")
    result = convert.convert_pdf(fake_pdf)
    assert result == "Minutes of the Widgetco Ltd board meeting."


def test_convert_pdf_returns_none_on_nonzero_exit(monkeypatch, tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_tool(bin_dir, "pdftotext", "#!/bin/sh\nexit 1\n")
    monkeypatch.setenv("PATH", str(bin_dir))
    fake_pdf = tmp_path / "broken.pdf"
    fake_pdf.write_bytes(b"%PDF-fake")
    assert convert.convert_pdf(fake_pdf) is None


def test_convert_pdf_returns_none_on_empty_output(monkeypatch, tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_tool(bin_dir, "pdftotext", "#!/bin/sh\nexit 0\n")
    monkeypatch.setenv("PATH", str(bin_dir))
    fake_pdf = tmp_path / "empty.pdf"
    fake_pdf.write_bytes(b"%PDF-fake")
    assert convert.convert_pdf(fake_pdf) is None


def test_convert_doc_uses_textutil(monkeypatch, tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_tool(
        bin_dir, "textutil",
        "#!/bin/sh\necho 'Letter to Acme Widgets Ltd regarding renewal.'\n",
    )
    monkeypatch.setenv("PATH", str(bin_dir))
    fake_doc = tmp_path / "letter.docx"
    fake_doc.write_bytes(b"fake docx bytes")
    result = convert.convert_doc(fake_doc)
    assert result == "Letter to Acme Widgets Ltd regarding renewal."


def test_convert_doc_returns_none_when_no_tools_available(empty_path, tmp_path):
    fake_doc = tmp_path / "letter.doc"
    fake_doc.write_bytes(b"fake doc bytes")
    assert convert.convert_doc(fake_doc) is None


def test_convert_never_raises_on_missing_file(empty_path, tmp_path):
    missing = tmp_path / "does-not-exist.pdf"
    assert convert.convert_pdf(missing) is None
    assert convert.convert(missing) is None


def test_convert_dispatches_by_extension(monkeypatch, tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_tool(bin_dir, "pdftotext", "#!/bin/sh\necho 'pdf text for Widgetco Ltd'\n")
    _write_fake_tool(bin_dir, "textutil", "#!/bin/sh\necho 'doc text for Widgetco Ltd'\n")
    monkeypatch.setenv("PATH", str(bin_dir))
    pdf_path = tmp_path / "a.pdf"
    pdf_path.write_bytes(b"x")
    docx_path = tmp_path / "a.docx"
    docx_path.write_bytes(b"x")
    assert convert.convert(pdf_path) == "pdf text for Widgetco Ltd"
    assert convert.convert(docx_path) == "doc text for Widgetco Ltd"


def test_convert_never_converts_spreadsheets(monkeypatch, tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_tool(bin_dir, "pdftotext", "#!/bin/sh\necho 'should not be used'\n")
    _write_fake_tool(bin_dir, "textutil", "#!/bin/sh\necho 'should not be used'\n")
    monkeypatch.setenv("PATH", str(bin_dir))
    xlsx_path = tmp_path / "accounts.xlsx"
    xlsx_path.write_bytes(b"x")
    xls_path = tmp_path / "accounts.xls"
    xls_path.write_bytes(b"x")
    ods_path = tmp_path / "accounts.ods"
    ods_path.write_bytes(b"x")
    assert convert.convert(xlsx_path) is None
    assert convert.convert(xls_path) is None
    assert convert.convert(ods_path) is None
