# corpus-loom-mcp

An MCP server and CLI that weaves local mirrors of git repositories, Google Drive, and Gmail into one hybrid-searchable corpus: BM25 + embeddings + entity links, all in a single SQLite file.

**Status: working alpha.** The three interfaces below (CLI, MCP server, `corpus update` hook) all work end to end, but expect rough edges. The full specification is in [DESIGN.md](DESIGN.md).

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

## Install

Not on PyPI yet. Install straight from git:

```sh
pip install "corpus-loom-mcp @ git+https://github.com/polycode-public/corpus-loom-mcp"
```

or with [pipx](https://pipx.pypa.io/), to keep it isolated from your other Python projects:

```sh
pipx install "corpus-loom-mcp @ git+https://github.com/polycode-public/corpus-loom-mcp"
```

Both give you the `corpus` and `corpus-mcp` commands. To hack on the engine itself, see [Development](#development) below.

## Quickstart

Point it at any mix of a plain file tree, a `.eml` mail tree (the layout [gyb](https://github.com/GAM-team/got-your-back) produces), and a git repo. Write a `corpus.toml`:

```toml
db = "corpus.db"

[[sources]]
name = "docs"
type = "filetree"
root = "docs"

[[sources]]
name = "mail"
type = "eml_tree"
root = "mail/me"

[[sources]]
name = "repo"
type = "gitrepo"
root = "repo"
branch = "main"
```

Paths are resolved relative to the config file's own directory. Then, from that directory:

```sh
corpus update --no-embed          # walk all sources, build the lexical index, skip embeddings
corpus search "refund" --mode lexical
corpus stats                      # per-source document/chunk counts and db size
corpus doc docs notes.md          # full content or metadata for one document
corpus entity alice@example.com   # entity card: links + recent documents
```

`corpus doc` and `corpus entity` take `--json` for scripting. Re-running `corpus update` is incremental: unchanged files are skipped by mtime/size, changed ones are re-chunked, and rows for anything deleted from the mirror are removed.

## Embeddings

Semantic search needs a [Voyage AI](https://www.voyageai.com/) API key. Set `VOYAGE_API_KEY` in your environment, or drop it in a `.env` file next to `corpus.toml`:

```
VOYAGE_API_KEY=pa-...
```

Check the cost before spending anything:

```sh
corpus embed --dry-run
```

This reports chunk and token counts and a projected cost per source, with no API calls made. `corpus update` embeds by default once you're happy with the estimate; add `--no-embed` to skip it, or run `corpus embed` on its own later to drain whatever's queued.

To keep specific content out of the embedding calls (it stays fully lexically searchable and entity-linked, just never sent to Voyage), set per-source knobs:

```toml
[[sources]]
name = "drive"
type = "filetree"
root = "drive"
embed = false                        # never embed this source
embed_exclude = ["finance/**", "personnel/**"]   # or just these paths within it
```

## MCP

`corpus-mcp` is a stdio MCP server exposing `search`, `get_document`, and `related_entities` over one config's database.

Claude Code:

```sh
claude mcp add corpus-loom -- corpus-mcp --config /path/to/corpus.toml
```

Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "corpus-loom": {
      "command": "corpus-mcp",
      "args": ["--config", "/path/to/corpus.toml"]
    }
  }
}
```

It's read-only over your mirrors — the only file it ever writes is the database, and only via `corpus update` / `corpus embed`.

## Sources reference

Three adapter `type`s, set per `[[sources]]` entry. Common keys: `name`, `type`, `root`, `include`/`exclude` (glob lists, matched against the path relative to `root`), `embed` (bool, default `true`), `embed_exclude` (glob list).

| type | Reads | Extra keys |
|---|---|---|
| `filetree` | A plain directory tree (e.g. an rclone-synced Drive folder) | `convert` — list of extensions (`"pdf"`, `"doc"`, `"docx"`) to run through `pdftotext`/`textutil` instead of skipping; default none |
| `eml_tree` | A gyb-style `mailbox/YYYY/M/D/<hex>.eml` tree; reads gyb's `msg-db.sqlite` labels alongside if present | — |
| `gitrepo` | A git checkout: tracked files at a branch HEAD, plus its commit log | `branch` — defaults to `main` |

## Development

```sh
git clone https://github.com/polycode-public/corpus-loom-mcp
cd corpus-loom-mcp
python3.11 -m venv .venv
.venv/bin/pip install -e . --group dev
PYTHONPATH=src .venv/bin/pytest
```

## Licence

[MPL-2.0](LICENSE)
