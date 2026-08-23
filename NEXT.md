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
| W3 search | the search agent | agent/search | scratchpad/wt-search | running |
| W3 embed | the embed agent | agent/embed | scratchpad/wt-embed | running |

## Merged, awaiting verification

- W2 entities + indexer merged and verified (127 tests green on main).
  CHECK AT ADAPTERS MERGE: entities expects mail meta from/to/cc values as
  address string(s) ("Name <addr>" ok, parseaddr'd) or {email,name} mappings;
  commit meta as flat author_name/author_email strings. Confirm eml_tree /
  gitrepo conform, else shim in indexer.
