# NEXT — in-flight build coordination

Live board for the multi-agent build of DESIGN.md. Rows are removed when the
item is merged to main AND verified; plans (DESIGN.md) are updated on removal
where the build taught us something.

## Wave map

- **W1 foundation**: pyproject, config, db schema, adapter protocol, test fixtures
- **W1 extract**: decode / html_text / mailbody / chunk / convert + unit tests
- **W2**: adapters (filetree, eml_tree, gitrepo), entities, indexer
- **W3**: search (FTS+RRF), embed (Voyage), cli
- **W4**: mcp_server, README user quickstart, CI workflow, e2e fixture run
- **W5**: deployment in ~/projects/diy-accounting-limited (index/corpus.toml,
  seeds/aliases, M0 verification against real corpora, fixes fed back here)

## In flight

| Workstream | Agent label | Branch | Worktree | Status |
|---|---|---|---|---|
| W1 foundation | the foundation agent | agent/foundation | scratchpad/wt-foundation | running |

## Merged, awaiting verification

- W1 extract (`agent/extract`, 5 commits, 57 tests green on main) — verify against
  foundation's fixture corpus once W1 foundation merges; then remove.
  Settled API: decode.classify/decode_text, html_to_text, mailbody.extract_body →
  MailBody(text, attachments), chunk_prose/csv/code/whole (header-prefixed),
  convert() never raises. classify() is intrinsic; per-source convert gating
  belongs to config/indexer.
