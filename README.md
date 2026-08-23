# corpus-loom-mcp

An MCP server and CLI that weaves local mirrors of git repositories, Google Drive, and Gmail into one hybrid-searchable corpus: BM25 + embeddings + entity links, all in a single SQLite file.

**Status: design phase.** The full specification is in [DESIGN.md](DESIGN.md).

## What it does

You point it at local mirrors you already maintain (git checkouts, an rclone-synced Drive folder, gyb Gmail backups) and it builds one `corpus.db`:

- **Lexical search** — SQLite FTS5 / BM25, for exact-token queries: reference numbers, names, code symbols.
- **Semantic search** — Voyage AI embeddings in sqlite-vec, for conceptual queries with no keyword overlap.
- **Fusion** — Reciprocal Rank Fusion across both, optional reranking.
- **Entity linking** — people, organisations, and sources extracted from mail headers, folder paths, and git logs into plain entity/edge tables, so "everything involving X in 2023 across mail, documents, and commits" is one query.

Three interfaces over the same database: a CLI (`corpus search ...`), an MCP server (`search`, `get_document`, `related_entities`) usable from Claude Desktop, Claude Code, or any MCP client, and an incremental `corpus update` hook to run after your mirror syncs.

## Design principles

- **Decoder yes, converter no**: content is indexed only when readable with a plain decoder (charset, MIME, HTML tag-strip). Formats needing conversion (Office, PDF, archives) are indexed by name/path/date only — deliberate, both for signal-to-noise and so data that shouldn't leave your machine, doesn't.
- **One SQLite file, no infrastructure**: no search server, no vector database, no graph engine. Entity links are SQL joins; they export to a graph database if multi-hop traversal is ever needed.
- **Read-only over the mirrors**: syncing is your existing pipeline's job; this only ever reads.

## Licence

[MPL-2.0](LICENSE)
