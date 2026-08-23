"""Embedding drain: model routing, eligibility, batching, and cost planning.

DESIGN.md "Incremental indexing" step 4: drain chunks.embedded=0, batch <=128
chunks and <=100k tokens per Voyage call, retry with backoff, per-chunk
transactional writes (batched here per the task's explicit instruction: one
transaction per batch), resume from the embedded flag. "Embedding privacy":
a source's embed=false, or a document path matching one of its embed_exclude
globs, keeps chunks lexical-only forever -- they are never sent to Voyage.
"""

from __future__ import annotations

import fnmatch
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Literal

from corpusindex.config import Config
from corpusindex.db import has_vec
from corpusindex.extract.decode import is_code_extension

ModelKind = Literal["prose", "code"]

TOKEN_CHARS_PER_TOKEN = 4

# USD per 1,000,000 tokens -- ESTIMATES, confirm against
# https://docs.voyageai.com/docs/pricing before relying on these for real
# spend decisions.
PRICING: dict[str, float] = {
    "voyage-3.5": 0.06,
    "voyage-code-3": 0.18,
}

VEC_TABLE = {"prose": "chunks_vec_prose", "code": "chunks_vec_code"}

ClientFactory = Callable[[], Any]


class EmbedError(RuntimeError):
    """Embedding cannot proceed (missing vector storage, missing API key, ...)."""


@dataclass(slots=True)
class ModelCounts:
    chunks: int = 0
    tokens: int = 0
    cost_usd: float = 0.0


@dataclass(slots=True)
class EmbedPlan:
    per_source: dict[str, dict[str, ModelCounts]] = field(default_factory=dict)
    per_model: dict[str, ModelCounts] = field(
        default_factory=lambda: {"prose": ModelCounts(), "code": ModelCounts()}
    )
    chunks: int = 0
    tokens: int = 0
    cost_usd: float = 0.0


@dataclass(slots=True)
class EmbedStats:
    embedded: int = 0
    batches: int = 0
    tokens: int = 0
    retries: int = 0
    per_model: dict[str, int] = field(default_factory=dict)


def _estimate_tokens(text: str) -> int:
    return len(text) // TOKEN_CHARS_PER_TOKEN


def model_kind_for(path: str) -> ModelKind:
    if path.startswith("commit/"):
        return "prose"
    return "code" if is_code_extension(path) else "prose"


def model_name_for(kind: ModelKind, config: Config) -> str:
    return config.code_model if kind == "code" else config.prose_model


def _excluded(path: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def _eligible_rows(conn, config: Config) -> Iterator[tuple[int, str, str, str]]:
    embed_sources = [s for s in config.sources if s.embed]
    if not embed_sources:
        return
    excludes: dict[str, tuple[str, ...]] = {s.name: s.embed_exclude for s in embed_sources}
    placeholders = ",".join("?" * len(embed_sources))
    rows = conn.execute(
        "SELECT c.chunk_id, c.text, d.source, d.path"
        " FROM chunks c JOIN documents d ON d.doc_id = c.doc_id"
        f" WHERE c.embedded = 0 AND d.source IN ({placeholders})"
        " ORDER BY c.chunk_id",
        [s.name for s in embed_sources],
    )
    for chunk_id, text, source, path in rows:
        if _excluded(path, excludes[source]):
            continue
        yield chunk_id, text, source, path


def plan(config: Config, conn) -> EmbedPlan:
    result = EmbedPlan()
    for _chunk_id, text, source, path in _eligible_rows(conn, config):
        kind = model_kind_for(path)
        tokens = _estimate_tokens(text)
        cost = tokens * PRICING.get(model_name_for(kind, config), 0.0) / 1_000_000

        source_counts = result.per_source.setdefault(source, {}).setdefault(kind, ModelCounts())
        source_counts.chunks += 1
        source_counts.tokens += tokens
        source_counts.cost_usd += cost

        model_counts = result.per_model[kind]
        model_counts.chunks += 1
        model_counts.tokens += tokens
        model_counts.cost_usd += cost

        result.chunks += 1
        result.tokens += tokens
        result.cost_usd += cost
    return result


def _is_batch_too_large_error(exc: Exception) -> bool:
    return "max allowed tokens" in str(exc).lower()


def _embed_batch_split(
    client: object, texts: list[str], model_name: str, max_retries: int, stats: "EmbedStats"
) -> list[list[float]]:
    # Char-based token estimates can undershoot the API's real count; when a
    # batch is rejected for size, bisect and retry the halves.
    try:
        return _embed_batch(client, texts, model_name, max_retries, stats)
    except Exception as exc:
        if len(texts) <= 1 or not _is_batch_too_large_error(exc):
            raise
    mid = len(texts) // 2
    return _embed_batch_split(
        client, texts[:mid], model_name, max_retries, stats
    ) + _embed_batch_split(client, texts[mid:], model_name, max_retries, stats)


def _next_batch(
    conn, config: Config, kind: ModelKind, batch_chunks: int, batch_tokens: int
) -> list[tuple[int, str]]:
    batch: list[tuple[int, str]] = []
    total_tokens = 0
    for chunk_id, text, _source, path in _eligible_rows(conn, config):
        if model_kind_for(path) != kind:
            continue
        tokens = _estimate_tokens(text)
        if kind == "code":
            # Dense code tokenises far below 4 chars/token; budget it at
            # double the char-based estimate to stay under API batch caps.
            tokens *= 2
        if batch and (len(batch) + 1 > batch_chunks or total_tokens + tokens > batch_tokens):
            break
        batch.append((chunk_id, text))
        total_tokens += tokens
        if len(batch) >= batch_chunks:
            break
    return batch


def _parse_dotenv_value(path: Path, key: str) -> str | None:
    if not path.is_file():
        return None
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        found_key, _, value = line.partition("=")
        if found_key.strip() != key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        return value
    return None


def resolve_api_key(config: Config) -> str:
    key = os.environ.get("VOYAGE_API_KEY")
    if key:
        return key
    key = _parse_dotenv_value(config.config_dir / ".env", "VOYAGE_API_KEY")
    if key:
        return key
    raise EmbedError(
        "VOYAGE_API_KEY not set: export it, or add it to a .env file next to the config"
    )


def _default_client(config: Config):
    import voyageai

    return voyageai.Client(api_key=resolve_api_key(config))


def _retryable_exceptions() -> tuple[type[Exception], ...]:
    from voyageai import error as verror

    return (
        verror.RateLimitError,
        verror.ServiceUnavailableError,
        verror.Timeout,
        verror.TryAgain,
        verror.APIConnectionError,
        verror.ServerError,
    )


def _embed_batch(
    client: Any, texts: list[str], model: str, max_retries: int, stats: EmbedStats
) -> list[list[float]]:
    retryable = _retryable_exceptions()
    attempt = 0
    while True:
        try:
            result = client.embed(texts, model=model, input_type="document")
            return list(result.embeddings)
        except retryable:
            attempt += 1
            if attempt > max_retries:
                raise
            stats.retries += 1
            time.sleep(2 ** (attempt - 1))


def drain(
    config: Config,
    conn,
    *,
    client_factory: ClientFactory | None = None,
    batch_chunks: int = 128,
    batch_tokens: int = 100_000,
    max_retries: int = 5,
) -> EmbedStats:
    if not has_vec(conn):
        raise EmbedError("cannot store vectors: sqlite-vec is not loaded on this connection")

    import sqlite_vec

    client = (client_factory or (lambda: _default_client(config)))()
    stats = EmbedStats()

    for kind in ("prose", "code"):
        model_name = model_name_for(kind, config)
        table = VEC_TABLE[kind]
        while True:
            batch = _next_batch(conn, config, kind, batch_chunks, batch_tokens)
            if not batch:
                break
            texts = [text for _chunk_id, text in batch]
            vectors = _embed_batch_split(client, texts, model_name, max_retries, stats)
            with conn:
                for (chunk_id, _text), vector in zip(batch, vectors):
                    conn.execute(
                        f"INSERT OR REPLACE INTO {table} (chunk_id, embedding) VALUES (?, ?)",
                        (chunk_id, sqlite_vec.serialize_float32(list(vector))),
                    )
                    conn.execute("UPDATE chunks SET embedded = 1 WHERE chunk_id = ?", (chunk_id,))
            stats.embedded += len(batch)
            stats.batches += 1
            stats.tokens += sum(_estimate_tokens(t) for t in texts)
            stats.per_model[kind] = stats.per_model.get(kind, 0) + len(batch)

    return stats


def query_embed(
    text: str,
    model_kind: ModelKind,
    config: Config,
    *,
    client_factory: ClientFactory | None = None,
) -> list[float]:
    model_name = model_name_for(model_kind, config)
    client = (client_factory or (lambda: _default_client(config)))()
    result = client.embed([text], model=model_name, input_type="query")
    return list(result.embeddings[0])
