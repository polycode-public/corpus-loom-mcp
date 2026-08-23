"""FastMCP stdio server exposing search, get_document, and related_entities.

DESIGN.md "Interfaces / MCP server": three read-only tools over the same
SQLite index the CLI queries. Tool logic lives in plain module-level
functions (search, get_document, related_entities) so tests can call them
directly without a running server; main() wires a lazily-opened, shared
sqlite3 connection and registers those same functions as FastMCP tools.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import asdict
from typing import Any

from fastmcp import FastMCP

from corpusindex.config import Config, load_config
from corpusindex.db import connect, has_vec
from corpusindex.embed import query_embed
from corpusindex.search import search as run_search

SERVER_NAME = "corpus-loom"

_KIND_PRIORITY = ("email", "person", "org", "category")

_state: dict[str, Any] = {"conn": None, "config": None}


def configure(config: Config | None = None, conn: sqlite3.Connection | None = None) -> None:
    """Set the config and/or connection used by the tool functions below.

    Tests inject a pre-seeded connection directly; main() supplies only a
    config and lets _connection() open the db lazily on first use.
    """
    _state["config"] = config
    _state["conn"] = conn


def _connection() -> sqlite3.Connection:
    conn = _state.get("conn")
    if conn is not None:
        return conn
    config = _state.get("config")
    if config is None:
        raise RuntimeError("corpusindex.mcp_server: call configure() before using a tool")
    conn = connect(config.db)
    _state["conn"] = conn
    return conn


def _query_vectors(config: Config | None, query: str) -> dict[str, list[float]]:
    if config is None:
        return {}
    vectors: dict[str, list[float]] = {}
    for kind in ("prose", "code"):
        try:
            vectors[kind] = query_embed(query, kind, config)
        except Exception:
            continue
    return vectors


def search(
    query: str,
    sources: list[str] = [],
    since: str = "",
    until: str = "",
    mode: str = "hybrid",
    limit: int = 10,
) -> dict[str, Any]:
    conn = _connection()
    config = _state.get("config")

    query_vectors: dict[str, list[float]] | None = None
    effective_mode = mode
    if mode in ("semantic", "hybrid"):
        query_vectors = _query_vectors(config, query) if has_vec(conn) else {}
        if mode == "semantic" and not query_vectors:
            effective_mode = "lexical"

    hits = run_search(
        conn,
        query,
        query_vectors=query_vectors,
        mode=effective_mode,
        sources=list(sources) if sources else None,
        since=since or None,
        until=until or None,
        limit=limit,
    )
    return {"hits": [asdict(hit) for hit in hits]}


def get_document(source: str, path: str, max_chars: int = 20000) -> dict[str, Any]:
    conn = _connection()
    row = conn.execute(
        "SELECT doc_id, title, doc_date, meta, content_indexed FROM documents"
        " WHERE source = ? AND path = ?",
        (source, path),
    ).fetchone()
    if row is None:
        return {"error": f"no such document: source={source!r} path={path!r}"}

    doc_id, title, doc_date, meta_json, content_indexed = row
    meta = json.loads(meta_json) if meta_json else None

    content: str | None = None
    truncated = False
    if content_indexed:
        chunk_rows = conn.execute(
            "SELECT text FROM chunks WHERE doc_id = ? ORDER BY seq", (doc_id,)
        ).fetchall()
        full_text = "\n\n".join(text for (text,) in chunk_rows)
        if len(full_text) > max_chars:
            content = full_text[:max_chars]
            truncated = True
        else:
            content = full_text

    return {
        "source": source,
        "path": path,
        "title": title,
        "date": doc_date,
        "meta": meta,
        "content_indexed": bool(content_indexed),
        "content": content,
        "truncated": truncated,
    }


def _find_entity(
    conn: sqlite3.Connection, key: str, kind: str
) -> tuple[int, str, str, str | None] | None:
    if kind:
        row = conn.execute(
            "SELECT entity_id, kind, key, display FROM entities WHERE key = ? AND kind = ?",
            (key, kind),
        ).fetchone()
        return tuple(row) if row else None

    rows = conn.execute(
        "SELECT entity_id, kind, key, display FROM entities WHERE key = ?", (key,)
    ).fetchall()
    if not rows:
        return None
    by_kind = {row[1]: row for row in rows}
    for candidate_kind in _KIND_PRIORITY:
        if candidate_kind in by_kind:
            return tuple(by_kind[candidate_kind])
    return tuple(rows[0])


def related_entities(key: str, kind: str = "", limit: int = 20) -> dict[str, Any]:
    conn = _connection()
    found = _find_entity(conn, key, kind)
    if found is None:
        suffix = f" kind={kind!r}" if kind else ""
        return {"error": f"no such entity: key={key!r}{suffix}"}
    entity_id, e_kind, e_key, e_display = found

    link_rows = conn.execute(
        "SELECT el.rel, el.a_id, el.b_id,"
        " ea.kind, ea.key, ea.display, eb.kind, eb.key, eb.display"
        " FROM entity_links el"
        " JOIN entities ea ON ea.entity_id = el.a_id"
        " JOIN entities eb ON eb.entity_id = el.b_id"
        " WHERE el.a_id = ? OR el.b_id = ?",
        (entity_id, entity_id),
    ).fetchall()
    links = []
    for rel, a_id, _b_id, a_kind, a_key, a_display, b_kind, b_key, b_display in link_rows:
        if a_id == entity_id:
            other_kind, other_key, other_display = b_kind, b_key, b_display
        else:
            other_kind, other_key, other_display = a_kind, a_key, a_display
        links.append({"rel": rel, "kind": other_kind, "key": other_key, "display": other_display})

    doc_rows = conn.execute(
        "SELECT d.source, d.path, d.title, d.doc_date, de.rel"
        " FROM doc_entities de JOIN documents d ON d.doc_id = de.doc_id"
        " WHERE de.entity_id = ?"
        " ORDER BY d.doc_date DESC"
        " LIMIT ?",
        (entity_id, limit),
    ).fetchall()
    documents = [
        {"source": source, "path": path, "title": title, "date": date, "rel": rel}
        for source, path, title, date, rel in doc_rows
    ]

    return {
        "entity": {"kind": e_kind, "key": e_key, "display": e_display},
        "links": links,
        "documents": documents,
    }


def build_app() -> FastMCP:
    app = FastMCP(SERVER_NAME)
    app.tool(search)
    app.tool(get_document)
    app.tool(related_entities)
    return app


def main() -> None:
    parser = argparse.ArgumentParser(prog="corpus-mcp")
    parser.add_argument("--config", default=None, help="path to corpus.toml")
    args = parser.parse_args()

    config = load_config(args.config)
    configure(config=config)

    app = build_app()
    app.run(transport="stdio")


if __name__ == "__main__":
    main()
