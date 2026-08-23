import tomllib

import pytest

from corpusindex.adapters.base import Doc, DocProbe
from corpusindex.db import connect
from corpusindex.entities import (
    Aliases,
    Seeds,
    generate_alias_bootstrap,
    load_aliases,
    load_seeds,
    normalize_email_key,
    normalize_slug,
    store_doc_entities,
)


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "corpus.db")
    yield c
    c.close()


def insert_doc(conn, source, path, title=None, content_hash="deadbeef"):
    with conn:
        cur = conn.execute(
            "INSERT INTO documents (source, path, content_hash, content_indexed, title)"
            " VALUES (?, ?, ?, 0, ?)",
            (source, path, content_hash, title),
        )
    return cur.lastrowid


def fetch_entity(conn, kind, key):
    return conn.execute(
        "SELECT entity_id, display FROM entities WHERE kind = ? AND key = ?", (kind, key)
    ).fetchone()


def fetch_rels(conn, doc_id):
    return set(
        conn.execute(
            "SELECT rel, e.kind, e.key FROM doc_entities de "
            "JOIN entities e ON e.entity_id = de.entity_id WHERE de.doc_id = ?",
            (doc_id,),
        ).fetchall()
    )


EMPTY = Seeds()
NO_ALIASES = Aliases()


def test_normalize_email_key_strips_tag_and_lowercases():
    assert normalize_email_key("Jane.Roe+billing@Example.COM") == "jane.roe@example.com"
    assert normalize_email_key("plain@example.com") == "plain@example.com"
    assert normalize_email_key("not-an-address") == "not-an-address"


def test_normalize_slug_strips_punctuation_and_collapses_whitespace():
    assert normalize_slug("  Acme,  Inc.  ") == "acme inc"
    assert normalize_slug("Jane   Roe") == "jane roe"
    assert normalize_slug("O'Brien-Smith") == "o brien smith"


def test_mail_from_to_cc_creates_email_entities_with_rels(conn):
    doc_id = insert_doc(conn, "mail", "mail/inbox/1.eml", title="Invoice question")
    doc = Doc(
        probe=DocProbe(source="mail", path="mail/inbox/1.eml"),
        content_hash="h1",
        title="Invoice question",
        meta={
            "from": "Jane Roe <jane.roe+work@example.com>",
            "to": [{"email": "billing@acme-example.com", "name": "Acme Billing"}],
            "cc": ["ops@example.com", "ops@example.com"],
        },
    )
    store_doc_entities(conn, doc_id, doc, EMPTY, NO_ALIASES)

    rels = fetch_rels(conn, doc_id)
    assert ("from", "email", "jane.roe@example.com") in rels
    assert ("to", "email", "billing@acme-example.com") in rels
    assert ("cc", "email", "ops@example.com") in rels

    from_entity = fetch_entity(conn, "email", "jane.roe@example.com")
    assert from_entity is not None
    assert from_entity[1] == "Jane Roe"

    to_entity = fetch_entity(conn, "email", "billing@acme-example.com")
    assert to_entity[1] == "Acme Billing"


def test_display_name_most_frequent_wins(conn):
    key = "terry@example.com"
    for i, name in enumerate(["DIY Customer Service", "DIY Customer Service", "Terry"]):
        doc_id = insert_doc(conn, "mail", f"mail/inbox/{i}.eml")
        doc = Doc(
            probe=DocProbe(source="mail", path=f"mail/inbox/{i}.eml"),
            content_hash=f"h{i}",
            meta={"from": {"email": key, "name": name}},
        )
        store_doc_entities(conn, doc_id, doc, EMPTY, NO_ALIASES)

    entity = fetch_entity(conn, "email", key)
    assert entity[1] == "DIY Customer Service"


def test_git_commit_author_and_committer(conn):
    doc_id = insert_doc(conn, "repo:submit", "commit/abc123")
    doc = Doc(
        probe=DocProbe(source="repo:submit", path="commit/abc123"),
        content_hash="abc123",
        meta={
            "author_name": "Jane Roe",
            "author_email": "jane.roe@example.com",
            "committer_name": "CI Bot",
            "committer_email": "ci-bot@example.com",
        },
    )
    store_doc_entities(conn, doc_id, doc, EMPTY, NO_ALIASES)

    rels = fetch_rels(conn, doc_id)
    assert ("author", "email", "jane.roe@example.com") in rels
    assert ("committer", "email", "ci-bot@example.com") in rels
    assert fetch_entity(conn, "email", "jane.roe@example.com")[1] == "Jane Roe"

    assert ("category", "category", "commit") not in rels


def test_git_commit_without_committer(conn):
    doc_id = insert_doc(conn, "repo:submit", "commit/def456")
    doc = Doc(
        probe=DocProbe(source="repo:submit", path="commit/def456"),
        content_hash="def456",
        meta={"author_name": "John Doe", "author_email": "john@example.com"},
    )
    store_doc_entities(conn, doc_id, doc, EMPTY, NO_ALIASES)

    rels = fetch_rels(conn, doc_id)
    assert ("author", "email", "john@example.com") in rels
    assert not any(rel == "committer" for rel, _, _ in rels)


def test_category_from_filetree_path(conn):
    doc_id = insert_doc(conn, "drive", "finance/2023/invoice-114.pdf")
    doc = Doc(
        probe=DocProbe(source="drive", path="finance/2023/invoice-114.pdf"),
        content_hash="hh",
        title="invoice-114.pdf",
        meta={},
    )
    store_doc_entities(conn, doc_id, doc, EMPTY, NO_ALIASES)

    rels = fetch_rels(conn, doc_id)
    assert ("category", "category", "finance") in rels
    assert fetch_entity(conn, "category", "finance")[1] == "finance"


def test_no_category_for_top_level_filetree_path(conn):
    doc_id = insert_doc(conn, "drive", "MANIFEST.md")
    doc = Doc(
        probe=DocProbe(source="drive", path="MANIFEST.md"),
        content_hash="hh",
        title="MANIFEST.md",
        meta={},
    )
    store_doc_entities(conn, doc_id, doc, EMPTY, NO_ALIASES)

    rels = fetch_rels(conn, doc_id)
    assert not any(rel == "category" for rel, _, _ in rels)


def test_mentions_from_seeds_in_title_and_path(conn):
    seeds = Seeds(persons=("Jane Roe",), orgs=("Acme Corp",))
    doc_id = insert_doc(conn, "drive", "finance/Acme Corp contract.pdf")
    doc = Doc(
        probe=DocProbe(source="drive", path="finance/Acme Corp contract.pdf"),
        content_hash="hh",
        title="Letter from Jane Roe re contract",
        meta={},
    )
    store_doc_entities(conn, doc_id, doc, seeds, NO_ALIASES)

    rels = fetch_rels(conn, doc_id)
    assert ("mentions", "person", "jane roe") in rels
    assert ("mentions", "org", "acme corp") in rels


def test_mentions_case_insensitive_no_false_positive(conn):
    seeds = Seeds(persons=("Bob Smith",))
    doc_id = insert_doc(conn, "drive", "finance/report.pdf")
    doc = Doc(
        probe=DocProbe(source="drive", path="finance/report.pdf"),
        content_hash="hh",
        title="Quarterly report",
        meta={},
    )
    store_doc_entities(conn, doc_id, doc, seeds, NO_ALIASES)

    rels = fetch_rels(conn, doc_id)
    assert not any(rel == "mentions" for rel, _, _ in rels)


def test_alias_of_and_member_of_links_created(conn):
    aliases = Aliases(
        alias_of={"jane.roe@example.com": "Jane Roe"},
        member_of={"jane roe": "Acme Corp"},
    )
    doc_id = insert_doc(conn, "mail", "mail/inbox/1.eml")
    doc = Doc(
        probe=DocProbe(source="mail", path="mail/inbox/1.eml"),
        content_hash="h1",
        meta={"from": "jane.roe+work@example.com"},
    )
    store_doc_entities(conn, doc_id, doc, EMPTY, aliases)

    email_entity = fetch_entity(conn, "email", "jane.roe@example.com")
    person_entity = fetch_entity(conn, "person", "jane roe")
    org_entity = fetch_entity(conn, "org", "acme corp")
    assert email_entity is not None
    assert person_entity is not None
    assert person_entity[1] == "Jane Roe"
    assert org_entity is not None
    assert org_entity[1] == "Acme Corp"

    links = set(
        conn.execute("SELECT a_id, b_id, rel FROM entity_links").fetchall()
    )
    assert (email_entity[0], person_entity[0], "alias_of") in links
    assert (person_entity[0], org_entity[0], "member_of") in links


def test_member_of_applies_to_seed_mentioned_persons(conn):
    seeds = Seeds(persons=("Jane Roe",))
    aliases = Aliases(member_of={"jane roe": "Acme Corp"})
    doc_id = insert_doc(conn, "drive", "finance/note.pdf")
    doc = Doc(
        probe=DocProbe(source="drive", path="finance/note.pdf"),
        content_hash="hh",
        title="Note about Jane Roe",
        meta={},
    )
    store_doc_entities(conn, doc_id, doc, seeds, aliases)

    person_entity = fetch_entity(conn, "person", "jane roe")
    org_entity = fetch_entity(conn, "org", "acme corp")
    links = set(conn.execute("SELECT a_id, b_id, rel FROM entity_links").fetchall())
    assert (person_entity[0], org_entity[0], "member_of") in links


def test_idempotent_running_twice_adds_nothing(conn):
    seeds = Seeds(persons=("Jane Roe",))
    aliases = Aliases(
        alias_of={"jane.roe@example.com": "Jane Roe"},
        member_of={"jane roe": "Acme Corp"},
    )
    doc_id = insert_doc(conn, "mail", "mail/inbox/1.eml")
    doc = Doc(
        probe=DocProbe(source="mail", path="mail/inbox/1.eml"),
        content_hash="h1",
        title="Note from Jane Roe",
        meta={"from": "jane.roe@example.com", "to": ["ops@example.com"]},
    )

    store_doc_entities(conn, doc_id, doc, seeds, aliases)
    counts_after_first = (
        conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0],
        conn.execute("SELECT COUNT(*) FROM doc_entities").fetchone()[0],
        conn.execute("SELECT COUNT(*) FROM entity_links").fetchone()[0],
    )

    store_doc_entities(conn, doc_id, doc, seeds, aliases)
    counts_after_second = (
        conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0],
        conn.execute("SELECT COUNT(*) FROM doc_entities").fetchone()[0],
        conn.execute("SELECT COUNT(*) FROM entity_links").fetchone()[0],
    )

    assert counts_after_first == counts_after_second


def test_load_seeds_round_trip(tmp_path):
    path = tmp_path / "seeds.toml"
    path.write_text(
        'persons = ["Jane Roe", "John Doe"]\n'
        'orgs = ["Acme Corp", "Northbank Ltd"]\n',
        encoding="utf-8",
    )
    seeds = load_seeds(path)
    assert seeds.persons == ("Jane Roe", "John Doe")
    assert seeds.orgs == ("Acme Corp", "Northbank Ltd")


def test_load_seeds_missing_file_returns_empty(tmp_path):
    seeds = load_seeds(tmp_path / "missing.toml")
    assert seeds == Seeds()
    assert load_seeds(None) == Seeds()


def test_load_aliases_round_trip_and_normalises_keys(tmp_path):
    path = tmp_path / "aliases.toml"
    path.write_text(
        "[alias_of]\n"
        '"Jane.Roe+work@Example.com" = "Jane Roe"\n'
        "\n"
        "[member_of]\n"
        '"Jane Roe" = "Acme Corp"\n',
        encoding="utf-8",
    )
    aliases = load_aliases(path)
    assert aliases.alias_of == {"jane.roe@example.com": "Jane Roe"}
    assert aliases.member_of == {"jane roe": "Acme Corp"}


def test_load_aliases_missing_file_returns_empty(tmp_path):
    assert load_aliases(tmp_path / "missing.toml") == Aliases()
    assert load_aliases(None) == Aliases()


def test_generate_alias_bootstrap_content(conn, tmp_path):
    for i in range(3):
        doc_id = insert_doc(conn, "mail", f"mail/inbox/{i}.eml")
        doc = Doc(
            probe=DocProbe(source="mail", path=f"mail/inbox/{i}.eml"),
            content_hash=f"h{i}",
            meta={"from": "frequent@example.com", "to": ["rare@example.com"]},
        )
        store_doc_entities(conn, doc_id, doc, EMPTY, NO_ALIASES)

    commit_doc_id = insert_doc(conn, "repo:submit", "commit/aaa")
    commit_doc = Doc(
        probe=DocProbe(source="repo:submit", path="commit/aaa"),
        content_hash="aaa",
        meta={"author_name": "Git Author", "author_email": "gitauthor@example.com"},
    )
    store_doc_entities(conn, commit_doc_id, commit_doc, EMPTY, NO_ALIASES)

    out_path = tmp_path / "bootstrap.toml"
    generate_alias_bootstrap(conn, out_path, top_n=200)

    text = out_path.read_text(encoding="utf-8")
    assert "[alias_of]" in text
    data = tomllib.loads(text)
    assert "frequent@example.com" in data["alias_of"]
    assert "rare@example.com" in data["alias_of"]
    assert "gitauthor@example.com" in data["alias_of"]

    freq_line_index = text.index("frequent@example.com")
    rare_line_index = text.index("rare@example.com")
    assert freq_line_index < rare_line_index


def test_generate_alias_bootstrap_respects_top_n(conn, tmp_path):
    for i in range(5):
        doc_id = insert_doc(conn, "mail", f"mail/inbox/many-{i}.eml")
        doc = Doc(
            probe=DocProbe(source="mail", path=f"mail/inbox/many-{i}.eml"),
            content_hash=f"m{i}",
            meta={"from": f"person{i}@example.com"},
        )
        store_doc_entities(conn, doc_id, doc, EMPTY, NO_ALIASES)

    out_path = tmp_path / "bootstrap.toml"
    generate_alias_bootstrap(conn, out_path, top_n=2)
    data = tomllib.loads(out_path.read_text(encoding="utf-8"))
    assert len(data["alias_of"]) == 2
