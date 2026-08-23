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
| W5 release | the release agent | agent/release | scratchpad/wt-release | running |

## Merged, awaiting verification

(none — W1–W4 verified: 234 tests green on main. Pipeline fixes applied at
merges: multi-recipient To/Cc via getaddresses (W2); default entities seam
loads Seeds/Aliases from config paths (W4, found by the cli agent).)

## After W5 release merges

Deployment in ~/projects/diy-accounting-limited/index/ (corpus.toml over the
five repos + drive + mail mirrors, archive commits-only, finance/personnel
embed_exclude), M0 verification against real corpora, embed dry-run, operator
gate, drain. Fixes found there feed back to this repo.
