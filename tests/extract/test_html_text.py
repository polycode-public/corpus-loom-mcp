from corpusindex.extract.html_text import html_to_text


def test_strips_tags_keeps_text():
    html = "<html><body><p>Hello from <b>Acme Widgets Ltd</b>.</p></body></html>"
    assert html_to_text(html) == "Hello from Acme Widgets Ltd."


def test_drops_script_and_style_content():
    html = (
        "<html><head><style>.x{color:red}</style></head>"
        "<body><script>alert('hi')</script>"
        "<p>Visible text only.</p></body></html>"
    )
    result = html_to_text(html)
    assert "alert" not in result
    assert "color:red" not in result
    assert "Visible text only." in result


def test_unescapes_entities():
    html = "<p>Terms &amp; Conditions &mdash; caf&eacute; &lt;value&gt;</p>"
    result = html_to_text(html)
    assert "Terms & Conditions" in result
    assert "café" in result
    assert "<value>" in result


def test_unescapes_numeric_entities():
    html = "<p>Price: &#8364;100</p>"
    assert "€100" in html_to_text(html)


def test_collapses_whitespace():
    html = "<p>Too    much     whitespace   here</p>"
    assert html_to_text(html) == "Too much whitespace here"


def test_preserves_paragraph_breaks():
    html = "<p>First paragraph.</p><p>Second paragraph.</p>"
    result = html_to_text(html)
    assert result == "First paragraph.\n\nSecond paragraph."


def test_br_becomes_line_break():
    html = "<p>Line one<br>Line two</p>"
    result = html_to_text(html)
    assert "Line one\nLine two" in result


def test_list_items_separated():
    html = "<ul><li>Widget A</li><li>Widget B</li></ul>"
    result = html_to_text(html)
    lines = [l for l in result.split("\n") if l]
    assert "Widget A" in lines
    assert "Widget B" in lines


def test_empty_document_returns_empty_string():
    assert html_to_text("") == ""


def test_no_excessive_blank_lines():
    html = "<div><div><div><p>Nested content</p></div></div></div>"
    result = html_to_text(html)
    assert "\n\n\n" not in result
