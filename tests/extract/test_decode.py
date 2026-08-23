from corpusindex.extract import decode


def test_classify_decoder_set():
    assert decode.classify("notes.txt") == "decode"
    assert decode.classify("report.md") == "decode"
    assert decode.classify("page.html") == "decode"
    assert decode.classify("data.csv") == "decode"


def test_classify_code_extensions():
    assert decode.classify("main.py") == "decode"
    assert decode.classify("App.java") == "decode"
    assert decode.classify("index.ts") == "decode"


def test_classify_convert_set():
    assert decode.classify("contract.pdf") == "convert"
    assert decode.classify("letter.doc") == "convert"
    assert decode.classify("minutes.docx") == "convert"
    assert decode.classify("memo.rtf") == "convert"
    assert decode.classify("memo.odt") == "convert"


def test_classify_spreadsheets_always_metadata():
    assert decode.classify("accounts.xlsx") == "metadata"
    assert decode.classify("accounts.xls") == "metadata"
    assert decode.classify("accounts.ods") == "metadata"


def test_classify_unknown_extension_is_metadata():
    assert decode.classify("photo.png") == "metadata"
    assert decode.classify("archive.zip") == "metadata"
    assert decode.classify("vault.kdbx") == "metadata"


def test_classify_case_insensitive():
    assert decode.classify("REPORT.PDF") == "convert"
    assert decode.classify("NOTES.TXT") == "decode"


def test_is_code_extension():
    assert decode.is_code_extension("main.py") is True
    assert decode.is_code_extension("notes.txt") is False


def test_is_binary_detects_nul_in_first_window():
    raw = b"hello" + b"\x00" + b"world"
    assert decode.is_binary(raw) is True


def test_is_binary_false_for_plain_text():
    raw = "Acme Widgets Ltd — quarterly summary for example.com".encode("utf-8")
    assert decode.is_binary(raw) is False


def test_is_binary_only_sniffs_first_8kib():
    raw = (b"a" * decode.NUL_SNIFF_WINDOW) + b"\x00"
    assert decode.is_binary(raw) is False


def test_decode_text_utf8():
    raw = "Café Nocturne — Møller & Søn".encode("utf-8")
    assert decode.decode_text(raw) == "Café Nocturne — Møller & Søn"


def test_decode_text_declared_charset_fallback():
    text = "Prix : 12,50 € pour Société Générale de Test"
    raw = text.encode("iso-8859-15")
    assert decode.decode_text(raw, declared_charset="iso-8859-15") == text


def test_decode_text_latin1_final_fallback():
    raw = "Café".encode("iso-8859-1")
    result = decode.decode_text(raw)
    assert isinstance(result, str)
    assert len(result) == len(raw)


def test_decode_text_bad_declared_charset_falls_through_to_latin1():
    raw = "Café".encode("iso-8859-1")
    result = decode.decode_text(raw, declared_charset="not-a-real-charset")
    assert isinstance(result, str)
