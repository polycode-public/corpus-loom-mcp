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

(none — all waves complete)

## Done and verified

All five waves merged and verified: 237 tests green on main at v0.1.0.
First real deployment (~/projects/diy-accounting-limited/index/) verified:
48,696 docs / 88,350 chunks indexed in 20 min; no-op rerun 4.8 s; M0 checks
pass; 79,927 chunks embedded (~40M tokens); M1 paraphrase-query check passes;
MCP registered in Claude Code.

Production fixes fed back during deployment: multi-recipient To/Cc via
getaddresses; default entities seam loads Seeds/Aliases; code-chunk token
budgeting 2x + batch bisection on API size rejection (found live against
Voyage's 120K cap).

## Candidate next work (not started)

- `--rerank` implementation (voyage rerank-2) — CLI flag currently a no-op notice.
- PyPI / GitHub package publishing when wanted; git-install is the supported path.
- Desktop registration of the MCP is manual (claude_desktop_config.json).
