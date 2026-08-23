"""``corpus`` command-line entry point.

Subcommands wire the merged engine modules together per DESIGN.md's
"Interfaces / CLI" section: update (indexer + embed drain), embed
(drain or --dry-run plan), search (hybrid FTS + KNN via RRF), doc
(document record + reconstructed content), entity (entity card),
stats (per-source counts + db size), bootstrap-aliases (alias review
file). Every subcommand resolves its database through
corpusindex.config's --config > $CORPUS_INDEX_CONFIG > ./corpus.toml
order, and reports failure through the process exit code: 0 success,
1 runtime failure, 2 invalid/missing config.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from typing import Callable

from corpusindex import db, entities, indexer, search
from corpusindex import embed as embed_
from corpusindex import config as config_
from corpusindex.adapters.eml_tree import EmlTreeAdapter
from corpusindex.adapters.filetree import FiletreeAdapter
from corpusindex.adapters.gitrepo import GitRepoAdapter
from corpusindex.config import Config, ConfigError

ADAPTER_CLASSES: dict[str, Callable] = {
    "filetree": FiletreeAdapter,
    "eml_tree": EmlTreeAdapter,
    "gitrepo": GitRepoAdapter,
}

ALIAS_BOOTSTRAP_FILENAME = "aliases-bootstrap.toml"


def _add_config_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default=None, help="path to corpus.toml")


def _load_config(path: str | None) -> Config:
    return config_.load_config(path)


def _store_entities_for(cfg: Config) -> Callable:
    """indexer.update()'s default store_entities forwards config.seeds/aliases
    (paths) as-is to entities.store_doc_entities(), which expects the loaded
    Seeds/Aliases value objects; bridge that here rather than in indexer.py or
    entities.py, both out of this module's scope."""
    seeds = entities.load_seeds(cfg.seeds)
    aliases = entities.load_aliases(cfg.aliases)

    def _store(conn, doc_id, doc, _seeds_path, _aliases_path) -> None:
        entities.store_doc_entities(conn, doc_id, doc, seeds, aliases)

    return _store


# --- update -------------------------------------------------------------


def cmd_update(args: argparse.Namespace) -> int:
    cfg = _load_config(args.config)
    all_names = [s.name for s in cfg.sources]
    names = args.sources if args.sources else all_names
    unknown = [n for n in names if n not in all_names]
    if unknown:
        raise ValueError(f"unknown source(s): {', '.join(unknown)}")

    adapters = {}
    for name in names:
        source = cfg.source(name)
        adapters[name] = ADAPTER_CLASSES[source.type](source)

    conn = db.connect(cfg.db)
    try:
        stats = indexer.update(
            cfg, conn, adapters, sources=names, store_entities=_store_entities_for(cfg)
        )
        for name in names:
            s = stats.sources[name]
            print(
                f"{name}: added={s.added} changed={s.changed} deleted={s.deleted} "
                f"unchanged={s.unchanged} chunks={s.chunks}"
            )
        if not args.no_embed:
            try:
                estats = embed_.drain(cfg, conn)
                print(
                    f"embed: embedded={estats.embedded} batches={estats.batches} "
                    f"tokens={estats.tokens} retries={estats.retries}"
                )
            except Exception as e:
                print(f"embed: {e}", file=sys.stderr)
                return 1
        return 0
    finally:
        conn.close()


# --- embed ----------------------------------------------------------------


def _print_plan(plan: embed_.EmbedPlan) -> None:
    for source in sorted(plan.per_source):
        for kind in sorted(plan.per_source[source]):
            counts = plan.per_source[source][kind]
            print(
                f"{source} [{kind}]: chunks={counts.chunks} tokens={counts.tokens} "
                f"cost_usd={counts.cost_usd:.4f}"
            )
    for kind in ("prose", "code"):
        counts = plan.per_model[kind]
        print(
            f"total [{kind}]: chunks={counts.chunks} tokens={counts.tokens} "
            f"cost_usd={counts.cost_usd:.4f}"
        )
    print(f"total: chunks={plan.chunks} tokens={plan.tokens} cost_usd={plan.cost_usd:.4f}")


def cmd_embed(args: argparse.Namespace) -> int:
    cfg = _load_config(args.config)
    conn = db.connect(cfg.db)
    try:
        if args.dry_run:
            _print_plan(embed_.plan(cfg, conn))
        else:
            stats = embed_.drain(cfg, conn)
            print(
                f"embed: embedded={stats.embedded} batches={stats.batches} "
                f"tokens={stats.tokens} retries={stats.retries}"
            )
        return 0
    finally:
        conn.close()


# --- search -----------------------------------------------------------------


def _resolve_query_vectors(
    cfg: Config, conn, query: str, mode: str
) -> tuple[dict[str, list[float]] | None, str]:
    if not db.has_vec(conn):
        print("notice: sqlite-vec unavailable; degrading to lexical search", file=sys.stderr)
        return None, "lexical"
    try:
        vectors = {
            "prose": embed_.query_embed(query, "prose", cfg),
            "code": embed_.query_embed(query, "code", cfg),
        }
    except Exception as e:
        print(f"notice: {e}; degrading to lexical search", file=sys.stderr)
        return None, "lexical"
    return vectors, mode


def cmd_search(args: argparse.Namespace) -> int:
    cfg = _load_config(args.config)
    conn = db.connect(cfg.db)
    try:
        mode = args.mode
        query_vectors = None
        if mode in ("semantic", "hybrid"):
            query_vectors, mode = _resolve_query_vectors(cfg, conn, args.query, mode)
        if args.rerank:
            print(
                "notice: --rerank is not yet implemented; proceeding without reranking",
                file=sys.stderr,
            )
        hits = search.search(
            conn,
            args.query,
            query_vectors=query_vectors,
            mode=mode,
            sources=args.sources or None,
            since=args.since,
            until=args.until,
            limit=args.limit,
        )
        if args.json:
            print(json.dumps([dataclasses.asdict(h) for h in hits]))
        else:
            for i, h in enumerate(hits, start=1):
                print(f"{i}. [{h.score:.4f}] {h.source}/{h.path}  {h.date or '-'}  {h.title or ''}")
                print(f"   {h.excerpt}")
        return 0
    finally:
        conn.close()


# --- doc ----------------------------------------------------------------


def cmd_doc(args: argparse.Namespace) -> int:
    cfg = _load_config(args.config)
    conn = db.connect(cfg.db)
    try:
        row = conn.execute(
            "SELECT doc_id, content_hash, content_indexed, title, doc_date, bytes, meta"
            " FROM documents WHERE source = ? AND path = ?",
            (args.source, args.path),
        ).fetchone()
        if row is None:
            raise ValueError(f"document not found: {args.source}/{args.path}")
        doc_id, content_hash, content_indexed, title, doc_date, bytes_, meta_json = row
        meta = json.loads(meta_json) if meta_json else {}

        content = None
        if content_indexed:
            chunk_rows = conn.execute(
                "SELECT text FROM chunks WHERE doc_id = ? ORDER BY seq", (doc_id,)
            ).fetchall()
            content = "\n\n".join(r[0] for r in chunk_rows)

        result = {
            "source": args.source,
            "path": args.path,
            "title": title,
            "doc_date": doc_date,
            "bytes": bytes_,
            "content_hash": content_hash,
            "content_indexed": bool(content_indexed),
            "meta": meta,
            "content": content,
        }
        if args.json:
            print(json.dumps(result))
        else:
            print(f"{args.source}/{args.path}")
            print(f"title: {title or ''}")
            print(f"date: {doc_date or ''}")
            print(f"bytes: {bytes_}")
            print(f"content_hash: {content_hash}")
            print(f"content_indexed: {result['content_indexed']}")
            if meta:
                print(f"meta: {json.dumps(meta)}")
            if content is not None:
                print("---")
                print(content)
        return 0
    finally:
        conn.close()


# --- entity -----------------------------------------------------------------


def cmd_entity(args: argparse.Namespace) -> int:
    cfg = _load_config(args.config)
    conn = db.connect(cfg.db)
    try:
        if args.kind:
            row = conn.execute(
                "SELECT entity_id, kind, key, display FROM entities WHERE kind = ? AND key = ?",
                (args.kind, args.key),
            ).fetchone()
        else:
            rows = conn.execute(
                "SELECT entity_id, kind, key, display FROM entities WHERE key = ?", (args.key,)
            ).fetchall()
            if len(rows) > 1:
                kinds = ", ".join(sorted({r[1] for r in rows}))
                raise ValueError(
                    f"ambiguous entity key {args.key!r}: matches kinds [{kinds}]; use --kind"
                )
            row = rows[0] if rows else None
        if row is None:
            raise ValueError(f"entity not found: {args.key}")
        entity_id, kind, key, display = row

        outgoing = conn.execute(
            "SELECT el.rel, b.kind, b.key, b.display FROM entity_links el"
            " JOIN entities b ON b.entity_id = el.b_id WHERE el.a_id = ?",
            (entity_id,),
        ).fetchall()
        incoming = conn.execute(
            "SELECT el.rel, a.kind, a.key, a.display FROM entity_links el"
            " JOIN entities a ON a.entity_id = el.a_id WHERE el.b_id = ?",
            (entity_id,),
        ).fetchall()
        links = [
            {"direction": "out", "rel": rel, "kind": k, "key": ky, "display": disp}
            for rel, k, ky, disp in outgoing
        ] + [
            {"direction": "in", "rel": rel, "kind": k, "key": ky, "display": disp}
            for rel, k, ky, disp in incoming
        ]

        doc_rows = conn.execute(
            "SELECT d.source, d.path, d.title, d.doc_date, de.rel"
            " FROM doc_entities de JOIN documents d ON d.doc_id = de.doc_id"
            " WHERE de.entity_id = ? ORDER BY d.doc_date DESC LIMIT 20",
            (entity_id,),
        ).fetchall()
        documents = [
            {"source": s, "path": p, "title": t, "doc_date": dd, "rel": rel}
            for s, p, t, dd, rel in doc_rows
        ]

        result = {
            "entity": {"kind": kind, "key": key, "display": display},
            "links": links,
            "documents": documents,
        }
        if args.json:
            print(json.dumps(result))
        else:
            print(f"{kind}:{key} ({display or ''})")
            print("links:")
            for link in links:
                arrow = "->" if link["direction"] == "out" else "<-"
                print(f"  {arrow} {link['rel']} {link['kind']}:{link['key']} ({link['display'] or ''})")
            print("recent documents:")
            for doc in documents:
                print(f"  {doc['doc_date'] or ''} {doc['source']}/{doc['path']} [{doc['rel']}] {doc['title'] or ''}")
        return 0
    finally:
        conn.close()


# --- stats --------------------------------------------------------------


def cmd_stats(args: argparse.Namespace) -> int:
    cfg = _load_config(args.config)
    conn = db.connect(cfg.db)
    try:
        per_source: dict[str, dict[str, int]] = {}
        for source in cfg.sources:
            row = conn.execute(
                "SELECT COUNT(DISTINCT d.doc_id), COUNT(c.chunk_id),"
                " COALESCE(SUM(CASE WHEN c.embedded = 1 THEN 1 ELSE 0 END), 0)"
                " FROM documents d LEFT JOIN chunks c ON c.doc_id = d.doc_id"
                " WHERE d.source = ?",
                (source.name,),
            ).fetchone()
            docs, chunks, embedded = row
            per_source[source.name] = {
                "documents": docs,
                "chunks": chunks,
                "embedded": embedded,
            }
        db_bytes = cfg.db.stat().st_size if cfg.db.exists() else 0
        result = {"sources": per_source, "db_bytes": db_bytes}
        if args.json:
            print(json.dumps(result))
        else:
            for name, s in per_source.items():
                print(f"{name}: documents={s['documents']} chunks={s['chunks']} embedded={s['embedded']}")
            print(f"db size: {db_bytes} bytes")
        return 0
    finally:
        conn.close()


# --- bootstrap-aliases ----------------------------------------------------


def cmd_bootstrap_aliases(args: argparse.Namespace) -> int:
    cfg = _load_config(args.config)
    conn = db.connect(cfg.db)
    try:
        out_path = cfg.config_dir / ALIAS_BOOTSTRAP_FILENAME
        entities.generate_alias_bootstrap(conn, out_path, top_n=args.top)
        print(f"wrote {out_path}")
        return 0
    finally:
        conn.close()


# --- parser -----------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="corpus")
    sub = parser.add_subparsers(dest="command", required=True)

    p_update = sub.add_parser("update", help="incremental index + embed drain")
    _add_config_arg(p_update)
    p_update.add_argument("--source", dest="sources", action="append", default=None)
    p_update.add_argument("--no-embed", dest="no_embed", action="store_true")
    p_update.set_defaults(func=cmd_update)

    p_embed = sub.add_parser("embed", help="embed drain, or --dry-run for a plan")
    _add_config_arg(p_embed)
    p_embed.add_argument("--dry-run", dest="dry_run", action="store_true")
    p_embed.set_defaults(func=cmd_embed)

    p_search = sub.add_parser("search", help="hybrid lexical + semantic search")
    _add_config_arg(p_search)
    p_search.add_argument("query")
    p_search.add_argument("--source", dest="sources", action="append", default=None)
    p_search.add_argument("--since", default=None)
    p_search.add_argument("--until", default=None)
    p_search.add_argument("--mode", choices=("hybrid", "lexical", "semantic"), default="hybrid")
    p_search.add_argument("--limit", type=int, default=10)
    p_search.add_argument("--rerank", action="store_true")
    p_search.add_argument("--json", action="store_true")
    p_search.set_defaults(func=cmd_search)

    p_doc = sub.add_parser("doc", help="print a document record and its content")
    _add_config_arg(p_doc)
    p_doc.add_argument("source")
    p_doc.add_argument("path")
    p_doc.add_argument("--json", action="store_true")
    p_doc.set_defaults(func=cmd_doc)

    p_entity = sub.add_parser("entity", help="print an entity card")
    _add_config_arg(p_entity)
    p_entity.add_argument("key")
    p_entity.add_argument("--kind", choices=("email", "person", "org", "category"), default=None)
    p_entity.add_argument("--json", action="store_true")
    p_entity.set_defaults(func=cmd_entity)

    p_stats = sub.add_parser("stats", help="per-source counts and db size")
    _add_config_arg(p_stats)
    p_stats.add_argument("--json", action="store_true")
    p_stats.set_defaults(func=cmd_stats)

    p_bootstrap = sub.add_parser("bootstrap-aliases", help="write an alias review file")
    _add_config_arg(p_bootstrap)
    p_bootstrap.add_argument("--top", type=int, default=200)
    p_bootstrap.set_defaults(func=cmd_bootstrap_aliases)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
