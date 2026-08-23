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
| W2 adapters | the adapters agent | agent/adapters | scratchpad/wt-adapters | running |
| W2 entities | the entities agent | agent/entities | scratchpad/wt-entities | running |
| W2 indexer | the indexer agent | agent/indexer | scratchpad/wt-indexer | running |

W2 seam (both sides build to this): entities exposes
`store_doc_entities(conn, doc_id, doc, seeds, aliases)`; indexer calls it.

## Merged, awaiting verification

(none — W1 verified: 99 tests green on main at c86c4b0)
