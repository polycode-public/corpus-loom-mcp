"""Entity extraction, normalisation, and seed/alias application.

Populates the frozen `entities` / `doc_entities` / `entity_links` tables
(schema owned by corpusindex.db) from a loaded Doc. The indexer calls the one
seam function below once per document, in whatever order it processes docs;
every write here is an idempotent upsert (UNIQUE constraints do the work), so
calling it twice for the same doc_id adds nothing.

    store_doc_entities(conn, doc_id, doc, seeds, aliases) -> None

Extraction rules (DESIGN.md "Entity extraction and normalisation"):

- Mail (doc.meta has any of 'from'/'to'/'cc'): each address -> 'email'
  entity, rel = from/to/cc.
- Git commit (doc.meta has 'author_name' or 'author_email'): 'email' entity
  rel='author' from author_name/author_email, plus rel='committer' from
  committer_name/committer_email when present.
- Filetree (doc.meta has neither of the above): first path segment ->
  'category' entity, rel='category' (only when the path has more than one
  segment).
- Mentions (always): exact case-insensitive substring match of every
  seed person/org name against doc.title and doc.probe.path -> 'person' /
  'org' entity, rel='mentions'.

doc.meta address field shape (mail 'from'/'to'/'cc'): a single address or a
list of addresses; each address is either a plain string -- optionally
"Display Name <email@x>" form, parsed with email.utils.parseaddr, so
RFC-2047 decoding is expected to already have happened upstream -- or a
mapping {'email' | 'address': ..., 'name' | 'display': ... (optional)}.

Normalisation:

- email key: lowercase the whole address, then strip a '+tag' suffix from
  the local part. The raw (as-given) address is a display candidate.
- person / org / category key: lowercase, strip punctuation, collapse
  whitespace (a slug).
- Display names attach to the *email* entity only, most-frequent-form-wins
  (counted in memory for the lifetime of the given connection object).
  Display names never auto-create or auto-merge a 'person' entity -- that
  only happens through an explicit aliases.toml entry.

seeds.toml shape::

    persons = ["Jane Roe", "John Doe"]
    orgs = ["Acme Corp", "Northbank Ltd"]

aliases.toml shape::

    [alias_of]
    "jane@example.com" = "Jane Roe"
    "jane.roe+work@example.com" = "Jane Roe"

    [member_of]
    "Jane Roe" = "Acme Corp"

alias_of maps a raw email address to the person display name it resolves
to; member_of maps a raw person display name to the org display name they
belong to. Both key forms are normalised on load (email key / person slug)
so lookups at apply time are exact-match on the same normalisation used for
entity keys elsewhere in this module.
"""

from __future__ import annotations

import email.utils as _emailutils
import re
import sqlite3
import tomllib
from collections import Counter
from dataclasses import dataclass, field
from os import PathLike
from pathlib import Path
from typing import Any

from corpusindex.adapters.base import Doc

_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WS_RE = re.compile(r"\s+")

MAIL_ADDRESS_FIELDS = ("from", "to", "cc")


def normalize_email_key(address: str) -> str:
    """Lowercase the address and strip a '+tag' suffix from the local part."""
    address = address.strip().lower()
    local, sep, domain = address.partition("@")
    if not sep:
        return address
    local = local.split("+", 1)[0]
    return f"{local}@{domain}"


def normalize_slug(name: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    stripped = _PUNCT_RE.sub(" ", name)
    return _WS_RE.sub(" ", stripped).strip().lower()


@dataclass(frozen=True, slots=True)
class Seeds:
    persons: tuple[str, ...] = ()
    orgs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Aliases:
    alias_of: dict[str, str] = field(default_factory=dict)
    member_of: dict[str, str] = field(default_factory=dict)


def load_seeds(path: str | PathLike[str] | None) -> Seeds:
    if path is None:
        return Seeds()
    p = Path(path)
    if not p.is_file():
        return Seeds()
    with open(p, "rb") as f:
        data = tomllib.load(f)
    persons = tuple(str(x) for x in data.get("persons", []))
    orgs = tuple(str(x) for x in data.get("orgs", []))
    return Seeds(persons=persons, orgs=orgs)


def load_aliases(path: str | PathLike[str] | None) -> Aliases:
    if path is None:
        return Aliases()
    p = Path(path)
    if not p.is_file():
        return Aliases()
    with open(p, "rb") as f:
        data = tomllib.load(f)
    raw_alias_of = data.get("alias_of", {})
    raw_member_of = data.get("member_of", {})
    alias_of = {
        normalize_email_key(str(k)): str(v) for k, v in raw_alias_of.items()
    }
    member_of = {
        normalize_slug(str(k)): str(v) for k, v in raw_member_of.items()
    }
    return Aliases(alias_of=alias_of, member_of=member_of)


_TOML_ESCAPE = str.maketrans({"\\": "\\\\", '"': '\\"'})


def _toml_str(value: str) -> str:
    return f'"{value.translate(_TOML_ESCAPE)}"'


def generate_alias_bootstrap(
    conn: sqlite3.Connection, path: str | PathLike[str], top_n: int = 200
) -> None:
    """Write a review TOML: top-N correspondents by document count, plus
    every git author email, for the operator to prune into aliases.toml."""
    correspondents = conn.execute(
        "SELECT e.key, e.display, COUNT(DISTINCT de.doc_id) AS n "
        "FROM entities e JOIN doc_entities de ON de.entity_id = e.entity_id "
        "WHERE e.kind = 'email' AND de.rel IN ('from', 'to', 'cc') "
        "GROUP BY e.entity_id ORDER BY n DESC, e.key ASC LIMIT ?",
        (top_n,),
    ).fetchall()
    authors = conn.execute(
        "SELECT e.key, e.display, COUNT(DISTINCT de.doc_id) AS n "
        "FROM entities e JOIN doc_entities de ON de.entity_id = e.entity_id "
        "WHERE e.kind = 'email' AND de.rel = 'author' "
        "GROUP BY e.entity_id ORDER BY e.key ASC"
    ).fetchall()

    rows: dict[str, dict[str, Any]] = {}
    for key, display, n in correspondents:
        rows[key] = {"display": display, "n": n, "tags": ["correspondent"]}
    for key, display, n in authors:
        if key in rows:
            rows[key]["tags"].append("git author")
        else:
            rows[key] = {"display": display, "n": n, "tags": ["git author"]}

    lines = [
        "# Alias bootstrap -- review and prune into aliases.toml.",
        f"# Top {top_n} correspondents by document count, plus every git author email.",
        "# Fill in the person display name on the right of each '=', delete rows",
        "# that don't need a person mapping, then add a [member_of] table mapping",
        "# person display names to org display names.",
        "",
        "[alias_of]",
    ]
    for key in sorted(rows, key=lambda k: (-rows[k]["n"], k)):
        row = rows[key]
        comment = f"{row['n']} documents, {'+'.join(row['tags'])}"
        display = row["display"] or key
        lines.append(f"{_toml_str(key)} = {_toml_str(display)}  # {comment}")

    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _get_or_create_entity(
    conn: sqlite3.Connection, kind: str, key: str, display: str | None
) -> int:
    conn.execute(
        "INSERT INTO entities (kind, key, display) VALUES (?, ?, ?) "
        "ON CONFLICT(kind, key) DO NOTHING",
        (kind, key, display),
    )
    row = conn.execute(
        "SELECT entity_id FROM entities WHERE kind = ? AND key = ?", (kind, key)
    ).fetchone()
    assert row is not None
    return row[0]


def _link_doc_entity(conn: sqlite3.Connection, doc_id: int, entity_id: int, rel: str) -> None:
    conn.execute(
        "INSERT INTO doc_entities (doc_id, entity_id, rel) VALUES (?, ?, ?) "
        "ON CONFLICT(doc_id, entity_id, rel) DO NOTHING",
        (doc_id, entity_id, rel),
    )


def _link_entities(conn: sqlite3.Connection, a_id: int, b_id: int, rel: str) -> None:
    conn.execute(
        "INSERT INTO entity_links (a_id, b_id, rel) VALUES (?, ?, ?) "
        "ON CONFLICT(a_id, b_id, rel) DO NOTHING",
        (a_id, b_id, rel),
    )


_DISPLAY_COUNTS: dict[sqlite3.Connection, dict[str, Counter]] = {}


def _display_counts_for(conn: sqlite3.Connection) -> dict[str, Counter]:
    return _DISPLAY_COUNTS.setdefault(conn, {})


def _record_email_display(conn: sqlite3.Connection, key: str, candidate: str | None) -> None:
    if not candidate:
        return
    counts = _display_counts_for(conn).setdefault(key, Counter())
    counts[candidate] += 1
    best = counts.most_common(1)[0][0]
    conn.execute(
        "UPDATE entities SET display = ? WHERE kind = 'email' AND key = ?", (best, key)
    )


def _parse_one_address(item: Any) -> tuple[str, str | None] | None:
    if isinstance(item, dict):
        addr = item.get("email") or item.get("address")
        name = item.get("name") or item.get("display")
        if not addr:
            return None
        return str(addr).strip(), (str(name).strip() if name else None) or None
    if isinstance(item, str):
        name, addr = _emailutils.parseaddr(item)
        if not addr:
            return None
        return addr.strip(), (name.strip() if name else None) or None
    return None


def _parse_address_field(value: Any) -> list[tuple[str, str | None]]:
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    out: list[tuple[str, str | None]] = []
    for item in items:
        parsed = _parse_one_address(item)
        if parsed:
            out.append(parsed)
    return out


def _category_from_path(path: str) -> str | None:
    parts = path.split("/")
    if len(parts) < 2:
        return None
    first = parts[0].strip()
    return first or None


def _apply_member_of(
    conn: sqlite3.Connection, person_entity_id: int, person_key: str, aliases: Aliases
) -> None:
    org_name = aliases.member_of.get(person_key)
    if org_name is None:
        return
    org_entity_id = _get_or_create_entity(conn, "org", normalize_slug(org_name), org_name)
    _link_entities(conn, person_entity_id, org_entity_id, "member_of")


def _apply_alias_of(conn: sqlite3.Connection, email_key: str, aliases: Aliases) -> None:
    person_name = aliases.alias_of.get(email_key)
    if person_name is None:
        return
    person_key = normalize_slug(person_name)
    email_entity_id = _get_or_create_entity(conn, "email", email_key, None)
    person_entity_id = _get_or_create_entity(conn, "person", person_key, person_name)
    _link_entities(conn, email_entity_id, person_entity_id, "alias_of")
    _apply_member_of(conn, person_entity_id, person_key, aliases)


def _store_email(
    conn: sqlite3.Connection, address: str, display_name: str | None, aliases: Aliases
) -> int:
    key = normalize_email_key(address)
    entity_id = _get_or_create_entity(conn, "email", key, None)
    _record_email_display(conn, key, display_name or address)
    _apply_alias_of(conn, key, aliases)
    return entity_id


def _apply_mentions(conn: sqlite3.Connection, doc_id: int, doc: Doc, seeds: Seeds, aliases: Aliases) -> None:
    haystacks = [h for h in (doc.title, doc.probe.path) if h]
    if not haystacks:
        return
    haystack = "\n".join(haystacks).lower()
    for kind, names in (("person", seeds.persons), ("org", seeds.orgs)):
        for name in names:
            if not name or name.strip().lower() not in haystack:
                continue
            key = normalize_slug(name)
            entity_id = _get_or_create_entity(conn, kind, key, name)
            _link_doc_entity(conn, doc_id, entity_id, "mentions")
            if kind == "person":
                _apply_member_of(conn, entity_id, key, aliases)


def store_doc_entities(
    conn: sqlite3.Connection, doc_id: int, doc: Doc, seeds: Seeds, aliases: Aliases
) -> None:
    meta = doc.meta or {}
    is_mail = any(field_name in meta for field_name in MAIL_ADDRESS_FIELDS)
    is_commit = "author_email" in meta or "author_name" in meta

    if is_mail:
        for rel in MAIL_ADDRESS_FIELDS:
            for address, name in _parse_address_field(meta.get(rel)):
                entity_id = _store_email(conn, address, name, aliases)
                _link_doc_entity(conn, doc_id, entity_id, rel)

    if is_commit:
        author_email = meta.get("author_email")
        if author_email:
            entity_id = _store_email(conn, author_email, meta.get("author_name"), aliases)
            _link_doc_entity(conn, doc_id, entity_id, "author")
        committer_email = meta.get("committer_email")
        if committer_email:
            entity_id = _store_email(conn, committer_email, meta.get("committer_name"), aliases)
            _link_doc_entity(conn, doc_id, entity_id, "committer")

    if not is_mail and not is_commit:
        category = _category_from_path(doc.probe.path)
        if category:
            entity_id = _get_or_create_entity(conn, "category", normalize_slug(category), category)
            _link_doc_entity(conn, doc_id, entity_id, "category")

    _apply_mentions(conn, doc_id, doc, seeds, aliases)
