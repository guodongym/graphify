from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from graphify.tabular_sidecar import (
    SCHEMA_VERSION,
    deterministic_sidecar_db_id,
    execute_readonly_query,
    read_sidecar_domain_config_hash,
    read_sidecar_meta,
    resolve_ref,
    search_rows,
)


def _load_graph_meta(graph_path: Path) -> dict:
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    meta = graph.get("graph", {}).get("tabular_sidecar") or graph.get("tabular_sidecar")
    if not isinstance(meta, dict):
        raise SystemExit("error: graph has no tabular_sidecar metadata")
    meta["_graph_path"] = str(graph_path)
    meta["_source_metadata_index"] = _source_metadata_index(graph)
    return meta


def _source_metadata_index(graph: dict) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for node in graph.get("nodes", []):
        if not isinstance(node, dict):
            continue
        if not node.get("sidecar_db_id") or not node.get("source_sha256"):
            continue
        if node.get("file_key"):
            index[f"file_key:{node['file_key']}"] = node
        if node.get("source_file"):
            index[f"source_file:{node['source_file']}"] = node
    return index


def _resolve_db_path(args, graph_meta: dict | None) -> Path:
    if args.sidecar_db:
        return Path(args.sidecar_db).resolve()
    if not graph_meta:
        active_output = (
            Path(os.environ["GRAPHIFY_OUT"])
            if os.environ.get("GRAPHIFY_OUT")
            else Path.cwd() / "graphify-out"
        )
        return (active_output / "sidecar" / "tabular.sqlite").resolve()
    hint = graph_meta.get("sidecar_db_hint")
    if not isinstance(hint, dict):
        raise SystemExit("error: graph sidecar_db_hint must be structured")
    if hint.get("kind") == "absolute":
        return Path(str(hint["path"])).resolve()
    if hint.get("kind") == "repo_relative":
        repo_root = Path(str(graph_meta["repo_root"]))
        return (repo_root / str(hint["path"])).resolve()
    if hint.get("kind") == "graphify_output_relative":
        graphify_output = graph_meta.get("graphify_output")
        if not graphify_output:
            raise SystemExit("error: graphify_output_relative sidecar hint requires graphify_output metadata")
        return (Path(str(graphify_output)) / str(hint["path"])).resolve()
    raise SystemExit(f"error: unsupported sidecar_db_hint kind: {hint.get('kind')}")


def _validate_db_identity(db_path: Path, graph_meta: dict | None, *, domain_id: str | None) -> dict:
    meta = read_sidecar_meta(db_path)
    if meta.get("schema_version") != str(SCHEMA_VERSION):
        raise SystemExit(
            f"error: incompatible sidecar schema_version={meta.get('schema_version')}; "
            f"expected {SCHEMA_VERSION}; rebuild sidecar DB"
        )
    if not graph_meta:
        return meta
    if meta.get("repo_key") != graph_meta.get("repo_key"):
        raise SystemExit("error: sidecar repo identity does not match graph")
    if meta.get("repo_root") != graph_meta.get("repo_root"):
        raise SystemExit("error: sidecar repo root does not match graph")
    expected_db_id = deterministic_sidecar_db_id(str(graph_meta["repo_key"]))
    if meta.get("sidecar_db_id") != expected_db_id or graph_meta.get("sidecar_db_id") != expected_db_id:
        raise SystemExit("error: sidecar DB identity does not match graph")
    if graph_meta.get("domain_id") and domain_id and graph_meta.get("domain_id") != domain_id:
        raise SystemExit("error: requested domain does not match graph metadata")
    if domain_id:
        current_domain_hash = read_sidecar_domain_config_hash(db_path, domain_id)
        if graph_meta.get("sidecar_domain_config_hash") != current_domain_hash:
            raise SystemExit("error: sidecar domain config hash does not match graph")
    return meta


def _validate_graph_rows(rows: list[dict], graph_meta: dict | None, *, require_union_hash: bool = False) -> None:
    if not graph_meta:
        return
    index = graph_meta["_source_metadata_index"]
    for row in rows:
        source_meta = index.get(f"file_key:{row['file_key']}") or index.get(f"source_file:{row['source_file']}")
        if source_meta is None:
            raise SystemExit(f"error: graph missing sidecar source metadata for {row['source_file']}")
        if source_meta.get("source_sha256") != row.get("source_sha256"):
            raise SystemExit(f"error: source sha mismatch for {row['source_file']}")
        if row.get("domain_file_config_hash") is not None and source_meta.get("domain_file_config_hash") != row.get("domain_file_config_hash"):
            raise SystemExit(f"error: domain file config hash mismatch for {row['source_file']}")
        if require_union_hash and source_meta.get("union_config_hash") != row.get("union_config_hash"):
            raise SystemExit(f"error: union config hash mismatch for {row['source_file']}")


def _response_metadata(db_path: Path, meta: dict) -> dict:
    return {
        "db_path": str(db_path),
        "sidecar_db": str(db_path),
        "schema_version": meta.get("schema_version"),
        "repo_key": meta.get("repo_key"),
        "sidecar_db_id": meta.get("sidecar_db_id"),
        "sidecar_instance_id": meta.get("sidecar_instance_id"),
        "sidecar_generation": meta.get("sidecar_generation"),
    }


class _SidecarArgumentParser(argparse.ArgumentParser):
    """Custom parser that capitalises 'Usage:' in error output to match test expectations."""

    def error(self, message):
        usage = self.format_usage()
        # Capitalise the 'u' in 'usage:' to produce 'Usage:'
        if usage.startswith("usage:"):
            usage = "Usage:" + usage[len("usage:"):]
        self.exit(2, f"{usage}{self.prog}: error: {message}\n")


def build_parser() -> argparse.ArgumentParser:
    parser = _SidecarArgumentParser(prog="graphify sidecar")
    sub = parser.add_subparsers(
        dest="cmd",
        required=True,
        parser_class=_SidecarArgumentParser,
    )
    resolve = sub.add_parser("resolve")
    resolve.add_argument("sidecar_ref")
    resolve.add_argument("--graph")
    resolve.add_argument("--sidecar-db", "--db", dest="sidecar_db")
    resolve.add_argument("--domain")
    resolve.add_argument("--global-debug", action="store_true")
    search = sub.add_parser("search")
    search.add_argument("--graph")
    search.add_argument("--sidecar-db", "--db", dest="sidecar_db")
    search.add_argument("--domain")
    search.add_argument("--file")
    search.add_argument("--file-key")
    search.add_argument("--column", required=True)
    search.add_argument("--column-index", type=int)
    search.add_argument("--value", required=True)
    search.add_argument("--global-debug", action="store_true")
    query = sub.add_parser("query")
    query.add_argument("--graph")
    query.add_argument("--sidecar-db", "--db", dest="sidecar_db")
    query.add_argument("--domain")
    query.add_argument("--sql")
    query.add_argument("--sql-file")
    query.add_argument("--limit", type=int, default=1000)
    query.add_argument("--timeout-ms", type=int, default=5000)
    query.add_argument("--global-debug", action="store_true")
    return parser


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    graph_meta = _load_graph_meta(Path(args.graph)) if args.graph else None
    if graph_meta and args.domain is None and not args.global_debug:
        args.domain = graph_meta.get("domain_id")
    if args.cmd == "search" and not args.domain and not args.global_debug:
        raise SystemExit("error: sidecar search requires --domain or graph metadata with domain_id; use --global-debug for union search")
    if args.cmd == "query" and not args.domain and not args.global_debug:
        raise SystemExit("error: sidecar query requires --domain or graph metadata with domain_id; use --global-debug for union SQL")
    db_path = _resolve_db_path(args, graph_meta)
    meta = _validate_db_identity(db_path, graph_meta, domain_id=args.domain)
    try:
        if args.cmd == "resolve":
            row = resolve_ref(db_path, args.sidecar_ref, domain_id=args.domain, graph_meta=graph_meta)
            _validate_graph_rows([row], graph_meta, require_union_hash=args.global_debug)
            print(json.dumps({**_response_metadata(db_path, meta), "row": row}, ensure_ascii=False))
            return 0
        if args.cmd == "query":
            if bool(args.sql) == bool(args.sql_file):
                raise SystemExit("error: sidecar query requires exactly one of --sql or --sql-file")
            sql = args.sql if args.sql else Path(args.sql_file).read_text(encoding="utf-8")
            result = execute_readonly_query(
                db_path,
                sql=sql,
                domain_id=args.domain,
                graph_meta=graph_meta,
                global_debug=args.global_debug,
                limit=args.limit,
                timeout_ms=args.timeout_ms,
            )
            print(json.dumps(result, ensure_ascii=False))
            return 0
        # search
        rows = search_rows(
            db_path,
            domain_id=args.domain,
            column=args.column,
            value=args.value,
            file_key=args.file_key,
            source_file=args.file,
            column_index=args.column_index,
            graph_meta=graph_meta,
        )
        _validate_graph_rows(rows, graph_meta, require_union_hash=args.global_debug)
        print(json.dumps({**_response_metadata(db_path, meta), "rows": rows}, ensure_ascii=False))
        return 0
    except SystemExit:
        raise
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
