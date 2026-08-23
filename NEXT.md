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
| W4 cli | the cli agent | agent/cli | scratchpad/wt-cli | running |
| W4 mcp | the mcp agent | agent/mcp | scratchpad/wt-mcp | running |

## Merged, awaiting verification

(none — W1–W3 verified: 197 tests green on main. Pipeline fix applied at W2
merge: multi-recipient To/Cc headers now expand via getaddresses.)
