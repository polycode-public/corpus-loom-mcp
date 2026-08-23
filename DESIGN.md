# PLAN: Hybrid lexical + semantic index over repos, Drive mirror, and mail

## User assertions (verbatim)

> "what about a mixed method RAG with sematic (embeddings?) and lexical (bm25) indexing, possibly a graph db, linking common: author, source, whatever you can think of and this would have a skill to re-index and a tool you can use directly and perhaps an mcp for claude desktop?"

> "We don't need to index the email attachments which are .doc / .docx / .xls / .xslx / gzip etc.. perhaps a useful rule is if we need to run conversion rather than a decoder even if with an internal library we can avoid indexing. This is because most of those will be copies of the product that we already have in git and some may have customer's account data that we don't need to use for anything."

> "for file on drive we do want to index .doc and .docx contents also pdfs anything that might be a "document" but not tabular data (spredsheets)."

## Shape of the system

Two pieces:

1. **A standalone open-source engine** (own repo; pure Python: CLI + FastMCP server, not Claude-Code-bound). Generic over configurable corpus roots with three source adapters: plain file trees, `.eml` mail trees, git repos. Everything below that is engine behaviour lives in that repo.
2. **This workspace's deployment**: a TOML config + entity seed/alias files in `~/projects/diy-accounting-limited/index/`, producing `index/corpus.db`. First and reference deployment.

Single SQLite file, no server infrastructure beyond the MCP process. Layers:

| Layer | Choice |
|---|---|
| Lexical | FTS5, BM25, `tokenize='porter unicode61'` — stemmed English recall; numbers/refs (invoice numbers, VAT refs) pass through stemming untouched |
| Semantic | sqlite-vec (`vec0`), Voyage `voyage-3.5` for prose/mail, `voyage-code-3` for source, both at 1024 dims, float32 |
| Fusion | Reciprocal Rank Fusion, k=60, over top-50 FTS + top-50 KNN per applicable model; optional `rerank-2` pass later behind a `--rerank` flag |
| Linking | `entities` / `doc_entities` / `entity_links` tables in the same DB; SQL joins (edges export trivially to a graph engine if multi-hop traversal is ever needed) |

## Corpus facts (verified on disk, 2026-08-23)

- **Drive** (`drive/DIY Accounting Limited/`, 5,345 files, 2.1 GiB): by extension, 2,006 pdf / 846 xlsx / 617 png / 414 csv / 331 docx / 308 txt / 254 doc / 253 xls dominate. Decoder-indexable set (txt, csv, md, html/htm, css, js, xml, sh, ini): ~800 files. Document-conversion set (2,006 pdf + 331 docx + 254 doc ≈ 2,600): content-indexed under the Drive documents ruling below. Spreadsheets (846 xlsx + 253 xls), images and other binaries: metadata-only via `MANIFEST.md` names/paths/sizes.
- **Mail** (38,524 messages; antony@ 1.4 GiB, support@ 16 GiB): gyb layout `mail/<mailbox>/YYYY/M/D/<hex>.eml` (date parts **not** zero-padded). Bodies sampled: mix of `multipart/alternative` (plain + HTML), HTML-only (`text/html` base64, no plain part — stdlib `get_body()` decodes cleanly), and bare `text/plain`. Charsets seen: utf-8, us-ascii, iso-8859-1 — all stdlib-decodable. The 16 GiB is mostly base64 attachment parts *inside* the .eml files, so the adapter must walk MIME parts selectively; indexed text is orders of magnitude smaller. gyb also maintains `mail/<mailbox>/msg-db.sqlite` with a `labels` table — read Gmail labels from it (keyed by filename stem) as document metadata.
- **Repos**: submit (909 tracked files, mostly js/md/java), spreadsheets (2,249 tracked files, of which 1,511 under `packages/` are nightly-generated xlsx/pdf/docx), www, root, and diy-accounting-archive (8,607 tracked files: 3,862 xlsx + 3,313 xls + 656 ods + 266 pdf; ~270 text files; 98 commits, last 2026-05).
- 3 `winmail.dat` (TNEF) attachments exist — attachments are metadata-only anyway.

## Content rule: decoder yes, converter no — except Drive documents

Base rule: content is indexed when readable with a plain decoder (charset, MIME structure, transfer-encoding, HTML markup); formats requiring *conversion* are metadata-only (findable by name/path/date/sender). Rationale: most such files are copies of the product already in git, and some carry customer account data we don't need to use for anything.

**Drive documents refinement** (operator, 2026-08-23): on the Drive source, prose *documents* are content-indexed even though conversion is required — .pdf, .doc, .docx (plus .rtf/.odt if encountered) — letters, minutes, contracts are precisely what retrieval is for. **Tabular data (spreadsheets: .xls/.xlsx/.ods) stays metadata-only regardless** — that's where the product copies and account data live. **Mail attachments stay metadata-only without exception.**

Rulings this plan makes under the rule:

- **HTML → text is decoding**, not conversion: charset decode + quoted-printable/base64 transfer decode + tag strip (stdlib `HTMLParser`; drop `script`/`style`, unescape entities). HTML-only mail bodies and .html files are therefore content-indexed. Without this ruling a large share of post-2015 mail would be invisible.
- **CSV is text** — content-indexed as-is, no parsing required. The tabular exclusion targets spreadsheet formats, not decoder-readable exports of the company's own records.
- **Conversion is per-source opt-in config** (`convert = ["pdf", "doc", "docx"]` on the drive source only; default empty, so the engine's base behaviour remains pure decoder). Tooling: `pdftotext` (poppler) for PDF; `textutil` (macOS built-in) or python-docx for .doc/.docx. A file whose conversion fails or yields no text degrades to metadata-only, counted and logged — never a hard error.
- **Indexed content**: mail text/plain and text/html body parts; Drive decoder set (txt, csv, md, html, htm, css, js, xml, sh, ini) plus the Drive document-conversion set above; repo files that are git-tracked, decoder-indexable by the same extension test plus code extensions (configurable per source; binary-sniff guard: skip any file whose first 8 KiB contains NUL).
- **Metadata-only**: everything else, including all mail attachments (filenames recorded from MIME part names), all spreadsheet files anywhere, images, archives, and `credentials.kdbx`.

### Per-repo decisions

- **diy-accounting-archive: commit log only.** Index its 98 commits (authors/dates/messages — cheap, preserves the pre-migration timeline for entity queries) but no file content: 94% of its tracked files are conversion-required product binaries, and its small text remainder is superseded by the live spreadsheets repo. Configured, not hard-coded.
- **spreadsheets `packages/`, `packages-archive/`, `packages-generated/`: excluded** entirely (nightly-generated binaries; matches the workspace search-hygiene rule). Path-exclude globs in workspace config.
- Standard excludes for all repos: `node_modules/`, `target/`, `.git/` internals.

## SQLite schema (DDL)

```sql
PRAGMA journal_mode = WAL;

CREATE TABLE documents (
  doc_id          INTEGER PRIMARY KEY,
  source          TEXT NOT NULL,          -- config source name: 'mail', 'drive', 'repo:submit', ...
  path            TEXT NOT NULL,          -- relative to that source's root; commits use 'commit/<sha>'
  content_hash    TEXT NOT NULL,          -- sha256 hex of raw bytes (git blob sha for repo files; commit sha for commits)
  stat_sig        TEXT,                   -- 'mtime:size' fast-path; NULL for git objects
  content_indexed INTEGER NOT NULL,       -- 1 = body chunked below; 0 = metadata-only
  title           TEXT,                   -- mail Subject / file basename / commit summary line
  doc_date        TEXT,                   -- ISO-8601 UTC (Date header / file mtime / author date)
  bytes           INTEGER,
  meta            TEXT,                   -- JSON: mail {message_id,from,to,cc,attachments[],labels[]}, commit {author,files[]}, ...
  UNIQUE (source, path)
);

CREATE TABLE chunks (
  chunk_id  INTEGER PRIMARY KEY,          -- join key for chunks_fts.rowid and chunks_vec.chunk_id
  doc_id    INTEGER NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
  seq       INTEGER NOT NULL,
  text      TEXT NOT NULL,
  embedded  INTEGER NOT NULL DEFAULT 0,   -- 0 = queued for embedding
  UNIQUE (doc_id, seq)
);

-- External-content FTS over chunks; triggers keep it in sync through cascade deletes.
CREATE VIRTUAL TABLE chunks_fts USING fts5(
  text, content='chunks', content_rowid='chunk_id',
  tokenize='porter unicode61'
);
CREATE TRIGGER chunks_ai AFTER INSERT ON chunks BEGIN
  INSERT INTO chunks_fts(rowid, text) VALUES (new.chunk_id, new.text);
END;
CREATE TRIGGER chunks_ad AFTER DELETE ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.chunk_id, old.text);
END;

-- sqlite-vec; one table per embedding model keeps KNN spaces separate.
CREATE VIRTUAL TABLE chunks_vec_prose USING vec0(chunk_id INTEGER PRIMARY KEY, embedding float[1024]);
CREATE VIRTUAL TABLE chunks_vec_code  USING vec0(chunk_id INTEGER PRIMARY KEY, embedding float[1024]);
-- vec rows are deleted explicitly by the indexer when chunks are removed (no triggers on virtual tables).

CREATE TABLE entities (
  entity_id INTEGER PRIMARY KEY,
  kind      TEXT NOT NULL,                -- 'email' | 'person' | 'org' | 'category'
  key       TEXT NOT NULL,                -- normalised (below)
  display   TEXT,
  UNIQUE (kind, key)
);

CREATE TABLE doc_entities (               -- document ↔ entity
  doc_id    INTEGER NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
  entity_id INTEGER NOT NULL REFERENCES entities(entity_id),
  rel       TEXT NOT NULL,                -- 'from'|'to'|'cc'|'author'|'committer'|'category'|'mentions'
  UNIQUE (doc_id, entity_id, rel)
);

CREATE TABLE entity_links (               -- entity ↔ entity
  a_id INTEGER NOT NULL REFERENCES entities(entity_id),
  b_id INTEGER NOT NULL REFERENCES entities(entity_id),
  rel  TEXT NOT NULL,                     -- 'alias_of' (email→person) | 'member_of' (person→org)
  UNIQUE (a_id, b_id, rel)
);

CREATE INDEX idx_documents_date ON documents(doc_date);
CREATE INDEX idx_doc_entities_e ON doc_entities(entity_id, rel);
CREATE TABLE index_meta (key TEXT PRIMARY KEY, value TEXT);  -- schema_version, last_run, model names/dims
```

Join spine: `documents.doc_id → chunks.doc_id → chunks.chunk_id = chunks_fts.rowid = chunks_vec_*.chunk_id`; entity queries pivot on `doc_entities`.

## Chunking policy

Token ≈ 4 chars (approximation used for all limits). Every chunk is prefixed with a one-line context header (included in FTS and embedding text): mail `«Subject — From, YYYY-MM-DD»`, files `«source/path»`, commits `«repo <shortsha> author YYYY-MM-DD»`.

| Content | Target | Overlap | Split preference | Model |
|---|---|---|---|---|
| Mail body (after HTML→text, strip quoted history: `>`-prefixed lines and `On … wrote:` tails; keep top-post text) | 1,000 tok | 100 tok | blank lines | voyage-3.5 |
| Prose/text/CSV, incl. converted Drive documents (md split at headings first; CSV chunks each re-prefixed with the header row) | 1,000 tok | 100 tok | blank lines / headings | voyage-3.5 |
| Source code | 1,500 tok | 0 | unindented (top-level) lines, else blank lines | voyage-code-3 |
| Commit message | whole message, one chunk | — | — | voyage-3.5 |

Rationale: ~1,000 tokens is Voyage's sweet spot for retrieval granularity and keeps one chunk ≈ one topic for RRF; overlap 100 protects boundary sentences in prose; code gets larger chunks and no overlap because definition boundaries (not sentence flow) carry meaning and overlap doubles cost for symbol-dense text. Most mail bodies fit a single chunk.

## Entity extraction and normalisation

**From mail headers** (`From`, `To`, `Cc`, RFC-2047-decoded): each mailbox address → `email` entity; `rel` = from/to/cc. **From git log** (`%an <%ae>`, author + committer): `email` entity, `rel` = author/committer. **From Drive paths**: first path segment → `category` entity (finance, minutes, personnel, product, support, technology, marketing, facilities), `rel` = category. **Mentions**: exact case-insensitive match of seed-list org/person names against document titles and Drive file/folder names (deterministic, no NER), `rel` = mentions.

Normalisation:

- `email` key: lowercase the whole address; strip a `+tag` from the local part for the key (raw form kept in `display`).
- `person` / `org` key: lowercase, collapse whitespace, strip punctuation (slug).
- Display names from mail headers attach to the `email` entity's `display` (most frequent form wins); they never auto-create or auto-merge `person` entities — sampling shows why: `terrycartwright@hotmail.com` sends as "DIY Accounting Customer Service", so display-name merging would misidentify.
- **Cross-source identity is by exact email key** (git author email = mail address = same entity, automatic) **plus a curated alias file** in workspace config mapping addresses → one `person` (`alias_of`) and persons → `org` (`member_of`). The indexer bootstraps a review file (top-200 correspondents by message count, plus all git author emails) that the operator prunes into `aliases.toml`; seed org names (customers, banks — NatWest, HMRC, Companies House already evident in Drive filenames) live in `seeds.toml`.

## Incremental indexing and deletions

1. Each adapter emits `(source, path, stat_sig | git-sha)` for every current document. Fast path: unchanged `stat_sig` (or unchanged git blob sha) → skip without hashing. Changed/new → sha256 the bytes.
2. Diff against `documents`: **new** → insert, chunk, extract entities, mark chunks `embedded=0`; **changed hash** → delete old chunks (cascade fires FTS triggers; indexer deletes matching `chunks_vec_*` rows first), reinsert; **in DB but gone from disk** → delete document row (mirrors propagate Drive/Gmail deletions; the index must follow). Orphaned entities are left in place — harmless, and stable IDs keep aliases valid.
3. Commits are immutable and append-only: key `commit/<sha>`, insert-only; commits no longer reachable from the configured branch are deleted on reindex.
4. Embedding is a separate drain step: select `embedded=0`, batch ≤128 chunks and ≤100k tokens per Voyage call, retry with backoff, write vector + set flag per chunk transactionally. A killed run resumes exactly where it stopped; unchanged content never re-embeds (content-hash keyed).
5. **Hook**: engine command `corpus update` (implies drain unless `--no-embed`). Workspace wiring: append `corpus update --config ~/projects/diy-accounting-limited/index/corpus.toml` to the existing `reindex` skill, and note in `drive/pull.sh` / `mail/pull.sh` docs that syncs should be followed by it (the pull scripts themselves stay index-agnostic; the skill is the orchestrator).
6. **Forced re-chunk on chunking-logic changes**: `indexer.INDEX_LAYOUT_VERSION`, stamped into `index_meta.layout_version` at the end of every `update()`, is the generic escape hatch for a chunking-logic change (a new synthetic chunk, a header-format change, a different split policy) that isn't visible to the stat_sig/git_sha fast path. When the stored value differs from `INDEX_LAYOUT_VERSION` on a non-empty `documents` table, that run bypasses the fast path entirely — every discovered document is reloaded and re-chunked regardless of its signature — then settles back to normal incremental behaviour once the stamp is current.

## Interfaces

### CLI (`corpus`)

```
corpus update  [--config PATH] [--source NAME]... [--no-embed]     # incremental index + embed drain
corpus embed   [--config PATH] [--dry-run]                          # drain only; --dry-run prints chunk/token/cost estimate
corpus search  QUERY [--source NAME]... [--since DATE] [--until DATE]
               [--mode hybrid|lexical|semantic] [--limit N] [--rerank] [--json]
corpus doc     SOURCE PATH [--json]                                 # full decoded content or metadata-only record
corpus entity  KEY [--kind email|person|org|category] [--json]      # entity card: links + recent documents
corpus stats   [--json]                                             # per-source doc/chunk/embedded counts, db size
```

`--config` defaults to `$CORPUS_INDEX_CONFIG`, then `./corpus.toml`. `VOYAGE_API_KEY` read from environment / `.env` next to the config.

### MCP server (FastMCP, stdio; `corpus-mcp --config PATH`)

```
search(query: str, sources: list[str] = [], since: str = "", until: str = "",
       mode: str = "hybrid", limit: int = 10) ->
  {hits: [{source, path, title, date, score, excerpt, chunk_seq, content_indexed}]}

get_document(source: str, path: str, max_chars: int = 20000) ->
  {source, path, title, date, meta, content_indexed, content | null, truncated: bool}

related_entities(key: str, kind: str = "", limit: int = 20) ->
  {entity: {kind, key, display}, links: [{rel, kind, key, display}],
   documents: [{source, path, title, date, rel}]}
```

Registered once in Claude Desktop and Claude Code config, pointing at the workspace TOML. Strictly read-only over the mirrors; the only file it writes is the DB (and only `update`/`embed` write that).

## Standalone repo layout

```
corpus-loom-mcp/                       # github.com/polycode-public/corpus-loom-mcp, MPL-2.0
├── pyproject.toml                     # deps: fastmcp, sqlite-vec, voyageai, tomli; entry points corpus, corpus-mcp
├── README.md  LICENSE
├── src/corpusindex/
│   ├── config.py                      # TOML schema: sources[{name,type,root,include/exclude globs,branch}], db path, models, seeds/aliases paths
│   ├── db.py                          # DDL above, schema_version migration, sqlite-vec loading
│   ├── adapters/
│   │   ├── base.py                    # SourceAdapter protocol: discover() -> iter[DocProbe]; load(probe) -> Doc
│   │   ├── filetree.py                # plain file tree (Drive mirror deployment)
│   │   ├── eml_tree.py                # .eml trees; gyb msg-db.sqlite labels if present
│   │   └── gitrepo.py                 # git ls-files @HEAD + git log via subprocess
│   ├── extract/
│   │   ├── decode.py                  # decoder-yes/converter-no gate: extension table, NUL sniff, charset fallbacks (utf-8 → declared → latin-1)
│   │   ├── html_text.py               # stdlib HTMLParser tag strip
│   │   ├── convert.py                 # per-source opt-in document conversion: pdftotext, textutil/python-docx; failure ⇒ metadata-only
│   │   ├── mailbody.py                # part selection, quoted-history strip
│   │   └── chunk.py                   # policy table above
│   ├── entities.py                    # extraction, normalisation, seeds/aliases application
│   ├── indexer.py                     # incremental diff walk, deletions, transactions
│   ├── embed.py                       # Voyage client, batching, resume, cost accounting
│   ├── search.py                      # FTS + KNN + RRF (+ rerank-2 optional)
│   ├── cli.py
│   └── mcp_server.py
└── tests/                             # fixture mini-corpus: eml (plain/html-only/iso-8859-1), file tree, tiny git repo
```

Workspace-specific (stays in this workspace, never in the engine repo): `index/corpus.toml` (sources: drive, mail, repo:submit, repo:spreadsheets, repo:www, repo:root, plus archive commits-only), `index/seeds.toml`, `index/aliases.toml`, `index/corpus.db`. `index/` gets a `.gitignore`-equivalent note; the DB and `.env` key never enter any git repository — enforced by the workspace's existing rule that `drive/`, `mail/`, `index/` are outside all repos.

## Milestones

**M0 — engine scaffold + lexical index (no API cost).** Repo skeleton, config, schema, three adapters, decode/chunk, `corpus update --no-embed`, FTS-only `corpus search --mode lexical`, entities. Verify against this workspace: (a) `corpus stats` document counts reconcile with `MANIFEST.md` (5,345 drive docs), `INDEX.tsv` (38,524 mail docs), and `git ls-files` minus excludes per repo; (b) a known invoice-number query returns the right message; (c) "everything involving NatWest in 2023" (`corpus entity natwest` + date filter) returns mail, Drive docs, and any commits; (d) rerunning `update` immediately is a no-op in <30 s; (e) deleting a mirror file and rerunning removes its rows.

**M1 — embeddings + hybrid.** `corpus embed --dry-run` first: report chunk/token counts and projected cost (expectation: several dollars at voyage-3.5 pricing now the ~2,600 converted Drive documents are included; gate actual spend on the dry-run number and open question 1). Then drain, wire RRF as default `--mode hybrid`. Verify: a paraphrase query with no keyword overlap (e.g. "customer wants money back for the wrong product" → the "Purchased wrong package" support thread) retrieves the right document; kill-and-resume mid-drain double-embeds nothing; a touched-but-unchanged file re-embeds nothing.

**M2 — MCP.** `corpus-mcp` exposing the three tools; register in Claude Desktop and Claude Code. Verify: a Desktop session answers a cross-source question ("what did we tell customers when VAT went to 20%, and what changed in the product?") through the MCP alone, no filesystem scanning.

**M3 — release hygiene.** Fixture-corpus tests green in CI, README quickstart (init a config against any maildir/file-tree/git corpus), first tag. Verify: a fresh clone indexes the fixture corpus and passes the M0 checks scripted.

## Embedding privacy (decided, 2026-08-23)

- Converted Drive documents under `finance/` and `personnel/` are never sent to the Voyage API — `embed = false` by path glob on the drive source. They remain fully lexically searchable and entity-linked.
- Everything else, support@ mail bodies included, is embedded. M1's `embed --dry-run` still gates actual spend on the projected cost number.

No open questions remain; the plan is in execution.
