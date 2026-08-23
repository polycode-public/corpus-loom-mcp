from corpusindex.extract.chunk import chunk_code, chunk_csv, chunk_prose, chunk_whole


def test_chunk_prose_short_text_single_chunk():
    text = "Widgetco Ltd shipped the order to example.com on schedule."
    chunks = chunk_prose(text, header="«source/notes.txt»")
    assert len(chunks) == 1
    assert chunks[0].startswith("«source/notes.txt»\n\n")
    assert text in chunks[0]


def test_chunk_prose_empty_text_returns_no_chunks():
    assert chunk_prose("", header="«source/empty.txt»") == []


def test_chunk_prose_splits_on_target_with_overlap():
    paragraphs = [f"Paragraph {i} about Widgetco Ltd operations in region {i}." * 20 for i in range(12)]
    text = "\n\n".join(paragraphs)
    chunks = chunk_prose(text, header="H", target_tokens=200, overlap_tokens=20)
    assert len(chunks) > 1
    for c in chunks:
        assert c.startswith("H\n\n")
    joined_bodies = "".join(chunks)
    assert "Paragraph 0" in joined_bodies
    assert "Paragraph 11" in joined_bodies


def test_chunk_prose_overlap_repeats_trailing_content():
    paragraphs = [f"Section {i}: Widgetco Ltd notes go here in some detail padding." for i in range(8)]
    text = "\n\n".join(paragraphs)
    chunks = chunk_prose(text, header="H", target_tokens=30, overlap_tokens=10)
    assert len(chunks) >= 2
    first_body = chunks[0].split("\n\n", 1)[1]
    second_body = chunks[1].split("\n\n", 1)[1]
    first_paragraphs = [p for p in first_body.split("\n\n") if p]
    second_paragraphs = [p for p in second_body.split("\n\n") if p]
    assert first_paragraphs[-1] == second_paragraphs[0]


def test_chunk_prose_markdown_splits_at_headings():
    text = (
        "# Introduction\n\nThis section introduces Widgetco Ltd.\n\n"
        "## Background\n\nSome background details about the company.\n\n"
        "# Conclusion\n\nFinal thoughts on the matter.\n"
    )
    chunks = chunk_prose(text, header="H", markdown=True, target_tokens=1000, overlap_tokens=100)
    assert any(c.count("# Introduction") for c in chunks)
    assert any("# Conclusion" in c for c in chunks)
    intro_chunk = next(c for c in chunks if "# Introduction" in c)
    assert "Background" in intro_chunk or any("Background" in c for c in chunks)


def test_chunk_prose_markdown_heading_forces_new_chunk_when_small_target():
    text = "# One\n\nContent one padding text here to add length.\n\n# Two\n\nContent two padding text here too."
    chunks = chunk_prose(text, header="H", markdown=True, target_tokens=15, overlap_tokens=0)
    assert len(chunks) >= 2
    assert any("# One" in c and "# Two" not in c for c in chunks)
    assert any("# Two" in c and "# One" not in c for c in chunks)


def test_chunk_csv_reprefixes_header_row_each_chunk():
    header_row = "id,name,amount"
    rows = [f"{i},Widgetco Ltd item {i},{i * 10}" for i in range(50)]
    text = "\n".join([header_row] + rows)
    chunks = chunk_csv(text, header="«drive/export.csv»", target_tokens=20, overlap_tokens=0)
    assert len(chunks) > 1
    for c in chunks:
        assert c.startswith("«drive/export.csv»\n\n")
        assert header_row in c


def test_chunk_csv_single_chunk_when_small():
    text = "id,name\n1,Acme Widgets Ltd"
    chunks = chunk_csv(text, header="H")
    assert len(chunks) == 1
    assert "id,name" in chunks[0]
    assert "1,Acme Widgets Ltd" in chunks[0]


def test_chunk_csv_header_only_no_data_rows():
    text = "id,name\n"
    chunks = chunk_csv(text, header="H")
    assert chunks == ["H\n\nid,name"]


def test_chunk_code_splits_at_unindented_lines():
    text = (
        "def alpha():\n"
        "    return 1\n"
        "\n\n"
        "def beta():\n"
        "    return 2\n"
    )
    chunks = chunk_code(text, header="«repo/mod.py»", target_tokens=7)
    assert len(chunks) >= 2
    assert any("def alpha" in c for c in chunks)
    assert any("def beta" in c for c in chunks)
    alpha_chunk = next(c for c in chunks if "def alpha" in c)
    assert "def beta" not in alpha_chunk


def test_chunk_code_no_overlap_between_chunks():
    text = "\n\n".join(f"def fn{i}():\n    return {i}" for i in range(10))
    chunks = chunk_code(text, header="H", target_tokens=10)
    assert len(chunks) > 1
    bodies = [c.split("\n\n", 1)[1] for c in chunks]
    for i in range(len(bodies) - 1):
        assert bodies[i].strip().splitlines()[-1] != bodies[i + 1].strip().splitlines()[0]


def test_chunk_code_small_file_single_chunk():
    text = "def only():\n    return 42\n"
    chunks = chunk_code(text, header="«repo/tiny.py»")
    assert len(chunks) == 1
    assert "def only" in chunks[0]


def test_chunk_code_empty_returns_no_chunks():
    assert chunk_code("", header="H") == []


def test_chunk_whole_single_chunk():
    text = "Fix the invoicing bug reported by Acme Widgets Ltd."
    chunks = chunk_whole(text, header="«repo abc1234 author 2026-01-01»")
    assert len(chunks) == 1
    assert chunks[0] == "«repo abc1234 author 2026-01-01»\n\n" + text


def test_chunk_whole_empty_returns_no_chunks():
    assert chunk_whole("   ", header="H") == []
