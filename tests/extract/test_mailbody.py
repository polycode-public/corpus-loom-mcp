import base64
from email import message_from_bytes, policy

from corpusindex.extract.mailbody import extract_body, strip_quoted_history


def _parse(raw: str):
    return message_from_bytes(raw.encode("utf-8"), policy=policy.default)


def test_plain_text_body():
    raw = (
        "From: alice@example.com\r\n"
        "To: bob@example.com\r\n"
        "Subject: Quarterly update\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        "\r\n"
        "Hello Bob, the Acme Widgets Ltd report is attached.\r\n"
    )
    msg = _parse(raw)
    body = extract_body(msg)
    assert "Acme Widgets Ltd report" in body.text
    assert body.attachments == []


def test_html_only_base64_body():
    html = "<html><body><p>Hello from <b>Acme Widgets Ltd</b> support.</p></body></html>"
    encoded = base64.b64encode(html.encode("utf-8")).decode("ascii")
    raw = (
        "From: support@example.com\r\n"
        "To: carol@example.com\r\n"
        "Subject: HTML only\r\n"
        "MIME-Version: 1.0\r\n"
        "Content-Type: text/html; charset=utf-8\r\n"
        "Content-Transfer-Encoding: base64\r\n"
        "\r\n" + encoded + "\r\n"
    )
    msg = _parse(raw)
    body = extract_body(msg)
    assert "Hello from Acme Widgets Ltd support." in body.text


def test_multipart_alternative_prefers_plain():
    raw = (
        "From: dave@example.com\r\n"
        "To: erin@example.com\r\n"
        "Subject: Alt body\r\n"
        "MIME-Version: 1.0\r\n"
        "Content-Type: multipart/alternative; boundary=\"BOUND\"\r\n"
        "\r\n"
        "--BOUND\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        "\r\n"
        "Plain version for Widgetco Ltd.\r\n"
        "--BOUND\r\n"
        "Content-Type: text/html; charset=utf-8\r\n"
        "\r\n"
        "<p>HTML version for Widgetco Ltd.</p>\r\n"
        "--BOUND--\r\n"
    )
    msg = _parse(raw)
    body = extract_body(msg)
    assert "Plain version for Widgetco Ltd." in body.text
    assert "HTML version" not in body.text


def test_iso_8859_1_charset_body():
    text = "Café Nocturne factuur voor Société Générale de Test"
    raw_bytes = (
        "From: finance@example.com\r\n"
        "To: ops@example.com\r\n"
        "Subject: Invoice note\r\n"
        "Content-Type: text/plain; charset=iso-8859-1\r\n"
        "\r\n"
    ).encode("ascii") + text.encode("iso-8859-1") + b"\r\n"
    msg = message_from_bytes(raw_bytes, policy=policy.default)
    body = extract_body(msg)
    assert "Café Nocturne" in body.text
    assert "Société Générale" in body.text


def test_attachment_filenames_collected_without_content():
    raw = (
        "From: frank@example.com\r\n"
        "To: grace@example.com\r\n"
        "Subject: Invoice attached\r\n"
        "MIME-Version: 1.0\r\n"
        "Content-Type: multipart/mixed; boundary=\"BOUND\"\r\n"
        "\r\n"
        "--BOUND\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        "\r\n"
        "Please find the Widgetco Ltd invoice attached.\r\n"
        "--BOUND\r\n"
        "Content-Type: application/pdf\r\n"
        "Content-Disposition: attachment; filename=\"invoice-1234.pdf\"\r\n"
        "Content-Transfer-Encoding: base64\r\n"
        "\r\n" + base64.b64encode(b"not real pdf bytes").decode("ascii") + "\r\n"
        "--BOUND--\r\n"
    )
    msg = _parse(raw)
    body = extract_body(msg)
    assert body.attachments == ["invoice-1234.pdf"]
    assert "not real pdf bytes" not in body.text
    assert "invoice-1234" not in body.text


def test_strip_quoted_history_removes_greater_than_lines():
    text = (
        "Sure, that works for me.\n"
        "\n"
        "> Original message from Widgetco Ltd\n"
        "> was quoted here in full.\n"
    )
    result = strip_quoted_history(text)
    assert result == "Sure, that works for me."


def test_strip_quoted_history_removes_on_wrote_tail():
    text = (
        "Thanks, that's confirmed.\n"
        "\n"
        "On Mon, Jan 5, 2026 at 3:00 PM, Jane Doe <jane@example.com> wrote:\n"
        "> Can you confirm the order for Widgetco Ltd?\n"
        "> Thanks in advance.\n"
    )
    result = strip_quoted_history(text)
    assert result == "Thanks, that's confirmed."
    assert "wrote:" not in result
    assert "Widgetco" not in result


def test_strip_quoted_history_keeps_top_post_only():
    text = "Approved.\nOn Tue, Feb 2, 2026, Sam Roe <sam@example.com> wrote:\nOld content\n"
    assert strip_quoted_history(text) == "Approved."


def test_strip_quoted_history_no_quoting_returns_unchanged():
    text = "No quoting present here at all."
    assert strip_quoted_history(text) == text
