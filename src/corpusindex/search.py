"""Hybrid lexical + semantic search: FTS5 BM25, sqlite-vec KNN, RRF fusion.

Decoupled from embedding generation: callers pass precomputed query vectors
(one per model, keyed 'prose'/'code') rather than raw text for the semantic
side. This module never imports voyageai or corpusindex.embed.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

from corpusindex.db import has_vec

RRF_K = 60
CANDIDATE_LIMIT = 50
EXCERPT_WINDOW = 200

_VEC_TABLES = {"prose": "chunks_vec_prose", "code": "chunks_vec_code"}
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


@dataclass(frozen=True, slots=True)
class Hit:
    source: str
    path: str
    title: str | None
    date: str | None
    score: float
    excerpt: str
    chunk_seq: int
    content_indexed: bool


@dataclass(frozen=True, slots=True)
class _ChunkMeta:
    doc_id: int
    seq: int
    text: str
    source: str
    path: str
    title: str | None
    doc_date: str | None
    content_indexed: int


def _fts_query(raw: str) -> str:
    """Quote every token individually so FTS5 operators/syntax in user input
    (unbalanced quotes, AND/OR/NOT, hyphens, colons) are treated as literal
    terms rather than parsed as query syntax."""
    tokens = _TOKEN_RE.findall(raw)
    return " ".join(f'"{t}"' for t in tokens)


def _filter_clause(
    sources: list[str] | None, since: str | None, until: str | None, alias: str
) -> tuple[list[str], list[object]]:
    clauses: list[str] = []
    params: list[object] = []
    if sources:
        placeholders = ",".join("?" * len(sources))
        clauses.append(f"{alias}.source IN ({placeholders})")
        params.extend(sources)
    if since:
        clauses.append(f"{alias}.doc_date >= ?")
        params.append(since)
    if until:
        clauses.append(f"{alias}.doc_date <= ?")
        params.append(until)
    return clauses, params


def _lexical_candidates(
    conn: sqlite3.Connection,
    query: str,
    sources: list[str] | None,
    since: str | None,
    until: str | None,
) -> list[tuple[int, str]]:
    """Top-50 chunk_ids ranked by bm25(), best first, with a snippet excerpt."""
    fts_query = _fts_query(query)
    if not fts_query:
        return []
    clauses, params = _filter_clause(sources, since, until, "d")
    where = " AND ".join(["chunks_fts MATCH ?", *clauses])
    sql = (
        "SELECT c.chunk_id, snippet(chunks_fts, 0, '', '', ' ... ', 12) "
        "FROM chunks_fts "
        "JOIN chunks c ON c.chunk_id = chunks_fts.rowid "
        "JOIN documents d ON d.doc_id = c.doc_id "
        f"WHERE {where} "
        "ORDER BY bm25(chunks_fts) "
        f"LIMIT {CANDIDATE_LIMIT}"
    )
    try:
        rows = conn.execute(sql, [fts_query, *params]).fetchall()
    except sqlite3.OperationalError:
        return []
    return [(row[0], row[1]) for row in rows]


def _import_sqlite_vec():
    try:
        import sqlite_vec
    except ImportError:
        return None
    return sqlite_vec


def _semantic_candidates(
    conn: sqlite3.Connection,
    table: str,
    vector: list[float],
    sources: list[str] | None,
    since: str | None,
    until: str | None,
) -> list[int]:
    """Top-50 chunk_ids by KNN distance, best first, filtered to allowed docs."""
    sqlite_vec = _import_sqlite_vec()
    if sqlite_vec is None:
        return []
    blob = sqlite_vec.serialize_float32(vector)
    try:
        rows = conn.execute(
            f"SELECT chunk_id FROM {table} WHERE embedding MATCH ? AND k = {CANDIDATE_LIMIT} "
            "ORDER BY distance",
            (blob,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    if not rows:
        return []
    chunk_ids = [row[0] for row in rows]
    clauses, params = _filter_clause(sources, since, until, "d")
    placeholders = ",".join("?" * len(chunk_ids))
    where = " AND ".join([f"c.chunk_id IN ({placeholders})", *clauses])
    allowed_rows = conn.execute(
        "SELECT c.chunk_id FROM chunks c JOIN documents d ON d.doc_id = c.doc_id "
        f"WHERE {where}",
        [*chunk_ids, *params],
    ).fetchall()
    allowed = {row[0] for row in allowed_rows}
    return [chunk_id for chunk_id in chunk_ids if chunk_id in allowed]


def _rrf_fuse(ranked_lists: list[list[int]]) -> dict[int, float]:
    scores: dict[int, float] = {}
    for ranked in ranked_lists:
        for rank, chunk_id in enumerate(ranked, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank)
    return scores


def _fetch_chunk_meta(
    conn: sqlite3.Connection, chunk_ids: list[int]
) -> dict[int, _ChunkMeta]:
    if not chunk_ids:
        return {}
    placeholders = ",".join("?" * len(chunk_ids))
    rows = conn.execute(
        "SELECT c.chunk_id, c.doc_id, c.seq, c.text, "
        "d.source, d.path, d.title, d.doc_date, d.content_indexed "
        "FROM chunks c JOIN documents d ON d.doc_id = c.doc_id "
        f"WHERE c.chunk_id IN ({placeholders})",
        chunk_ids,
    ).fetchall()
    return {
        row[0]: _ChunkMeta(
            doc_id=row[1],
            seq=row[2],
            text=row[3],
            source=row[4],
            path=row[5],
            title=row[6],
            doc_date=row[7],
            content_indexed=row[8],
        )
        for row in rows
    }


def _window_excerpt(text: str, width: int = EXCERPT_WINDOW) -> str:
    text = " ".join(text.split())
    if len(text) <= width:
        return text
    return text[:width].rstrip() + " ..."


def search(
    conn: sqlite3.Connection,
    query: str,
    *,
    query_vectors: dict[str, list[float]] | None = None,
    mode: str = "hybrid",
    sources: list[str] | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 10,
) -> list[Hit]:
    if mode not in ("lexical", "semantic", "hybrid"):
        raise ValueError(f"invalid mode: {mode!r}")

    excerpts: dict[int, str] = {}
    ranked_lists: list[list[int]] = []

    if mode in ("lexical", "hybrid"):
        lexical_rows = _lexical_candidates(conn, query, sources, since, until)
        if lexical_rows:
            ranked_lists.append([chunk_id for chunk_id, _ in lexical_rows])
            excerpts.update(lexical_rows)

    if mode in ("semantic", "hybrid") and query_vectors and has_vec(conn):
        for model, table in _VEC_TABLES.items():
            vector = query_vectors.get(model)
            if not vector:
                continue
            chunk_ids = _semantic_candidates(conn, table, vector, sources, since, until)
            if chunk_ids:
                ranked_lists.append(chunk_ids)

    if not ranked_lists:
        return []

    fused = _rrf_fuse(ranked_lists)
    meta = _fetch_chunk_meta(conn, list(fused.keys()))

    best_per_doc: dict[int, tuple[float, int]] = {}
    for chunk_id, score in fused.items():
        chunk_meta = meta.get(chunk_id)
        if chunk_meta is None:
            continue
        current = best_per_doc.get(chunk_meta.doc_id)
        if (
            current is None
            or score > current[0]
            or (score == current[0] and chunk_id < current[1])
        ):
            best_per_doc[chunk_meta.doc_id] = (score, chunk_id)

    hits = []
    for score, chunk_id in best_per_doc.values():
        chunk_meta = meta[chunk_id]
        excerpt = excerpts.get(chunk_id) or _window_excerpt(chunk_meta.text)
        hits.append(
            Hit(
                source=chunk_meta.source,
                path=chunk_meta.path,
                title=chunk_meta.title,
                date=chunk_meta.doc_date,
                score=score,
                excerpt=excerpt,
                chunk_seq=chunk_meta.seq,
                content_indexed=bool(chunk_meta.content_indexed),
            )
        )

    hits.sort(key=lambda h: (-h.score, h.source, h.path))
    return hits[:limit]
