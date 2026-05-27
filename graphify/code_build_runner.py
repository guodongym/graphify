"""Shared code-only graph build runner."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from graphify.tabular_manifest import TabularDomainManifest


@dataclass(frozen=True)
class CodeBuildResult:
    output_dir: Path
    graph_json: Path
    report: Path
    node_count: int
    edge_count: int
    community_count: int


def _relativize_source_files(payload: dict, root: Path) -> None:
    for bucket in ("nodes", "edges", "hyperedges"):
        for item in payload.get(bucket, []):
            source = item.get("source_file")
            if not source:
                continue
            source_path = Path(source)
            if not source_path.is_absolute():
                continue
            try:
                item["source_file"] = source_path.resolve().relative_to(root).as_posix()
            except ValueError:
                continue


def _word_count(paths: list[Path]) -> int:
    total = 0
    for path in paths:
        try:
            total += len(path.read_text(encoding="utf-8", errors="ignore").split())
        except OSError:
            pass
    return total


def _load_labels(labels_file: Path, communities: dict[int, list[str]]) -> dict[int, str]:
    labels: dict[int, str] = {}
    if labels_file.exists():
        try:
            raw = json.loads(labels_file.read_text(encoding="utf-8"))
            labels = {int(k): v for k, v in raw.items() if int(k) in communities}
        except Exception:
            labels = {}
    for cid in communities:
        labels.setdefault(cid, f"Community {cid}")
    return labels


def _stage_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return Path(tmp_name)


def _temp_path_for(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    os.close(fd)
    return Path(tmp_name)


def _empty_relative_files_by_type(files_by_type: dict[str, list[str]]) -> dict[str, list[str]]:
    return {key: [] for key in sorted(files_by_type)}


def _normalize_state_file(value: Any, repo_root: Path) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    path = Path(value)
    candidate = path if path.is_absolute() else repo_root / path
    try:
        normalized = candidate.resolve().relative_to(repo_root).as_posix()
    except ValueError:
        return None
    if normalized in ("", "."):
        return None
    return normalized


def _normalize_state_files(values: Any, repo_root: Path) -> list[str]:
    if not isinstance(values, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        file_path = _normalize_state_file(value, repo_root)
        if file_path is None or file_path in seen:
            continue
        normalized.append(file_path)
        seen.add(file_path)
    return normalized


def _normalize_state_files_by_type(
    values: Any,
    repo_root: Path,
    *,
    fallback_shape: dict[str, list[str]],
) -> dict[str, list[str]]:
    if not isinstance(values, dict):
        return _empty_relative_files_by_type(fallback_shape)
    normalized = _empty_relative_files_by_type(fallback_shape)
    for key, file_values in values.items():
        if not isinstance(key, str):
            continue
        normalized[key] = _normalize_state_files(file_values, repo_root)
    return {key: normalized[key] for key in sorted(normalized)}


def _load_previous_manifest_state(
    state_path: Path,
    *,
    repo_root: Path,
    fallback_shape: dict[str, list[str]],
) -> tuple[list[str], dict[str, list[str]]]:
    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError, OSError):
        return [], _empty_relative_files_by_type(fallback_shape)
    if not isinstance(raw, dict):
        return [], _empty_relative_files_by_type(fallback_shape)
    previous_files = _normalize_state_files(raw.get("files"), repo_root)
    previous_files_by_type = _normalize_state_files_by_type(
        raw.get("files_by_type"),
        repo_root,
        fallback_shape=fallback_shape,
    )
    return previous_files, previous_files_by_type


def _manifest_state_text(
    *,
    mode: str,
    relative_source_paths: list[str],
    relative_files_by_type: dict[str, list[str]],
    previous_files: list[str],
    previous_files_by_type: dict[str, list[str]],
) -> str:
    current_files = list(relative_source_paths)
    current_files_by_type = {
        key: list(value) for key, value in sorted(relative_files_by_type.items())
    }
    state = {
        "schema_version": 1,
        "mode": mode,
        # Keep both legacy-friendly and explicit names for downstream callers
        # that may already read either shape.
        "files": current_files,
        "files_by_type": current_files_by_type,
        "current_files": current_files,
        "current_files_by_type": current_files_by_type,
        "previous_files": list(previous_files),
        "previous_files_by_type": {
            key: list(value) for key, value in sorted(previous_files_by_type.items())
        },
    }
    return json.dumps(state, indent=2, ensure_ascii=False) + "\n"


def build_code_graph(
    *,
    code_files: list[Path],
    repo_root: Path,
    output_dir: Path,
    mode: str,
    files_by_type: dict[str, list[str]],
    relative_source_paths: list[str],
    relative_files_by_type: dict[str, list[str]],
    max_workers: int | None = None,
    tabular_manifest: TabularDomainManifest | None = None,
    active_graphify_output: Path | None = None,
    sidecar_db_path: Path | None = None,
) -> CodeBuildResult:
    """Build a clustered code graph from an explicit file set.

    This runner does not scan ``repo_root``. Callers must pass the exact
    materialized files they want included in the graph.
    """

    repo_root = repo_root.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    from graphify.analyze import god_nodes, surprising_connections, suggest_questions
    from graphify.build import build_from_json
    from graphify.cluster import cluster, score_all
    from graphify.export import _git_head, backup_if_protected, to_html, to_json
    from graphify.extract import (
        _add_lua_include_edges,
        _add_lua_ini_reference_edges,
        _redirect_tab_reference_edges,
        extract,
    )
    from graphify.report import generate

    # --- Pre-extract split: remove sidecar files from extractor input ---
    has_sidecar_projection = (
        tabular_manifest is not None
        and tabular_manifest.sidecar_active_files
    )
    if has_sidecar_projection:
        sidecar_paths = {
            file_cfg.path.resolve() for file_cfg in tabular_manifest.sidecar_active_files
        }
        extractor_files = [p for p in code_files if p.resolve() not in sidecar_paths]
    else:
        extractor_files = code_files

    # --- Extract (may be empty for sidecar-only domains) ---
    extract_kwargs: dict = {"cache_root": repo_root}
    if max_workers is not None:
        extract_kwargs["max_workers"] = max_workers

    if extractor_files:
        result = extract(extractor_files, **extract_kwargs)
    else:
        result = {"nodes": [], "edges": [], "hyperedges": []}

    # --- Sidecar update + projection merge ---
    sidecar_update_stats = None
    sidecar_meta = None
    effective_sidecar_db = None
    effective_graphify_output = (
        active_graphify_output if active_graphify_output is not None
        else (repo_root / "graphify-out")
    )
    if tabular_manifest is not None:
        effective_sidecar_db = (
            sidecar_db_path if sidecar_db_path is not None
            else effective_graphify_output / "sidecar" / "tabular.sqlite"
        )
        should_update_sidecar = bool(tabular_manifest.sidecar_active_files)
        if not should_update_sidecar and effective_sidecar_db.exists():
            from graphify.tabular_sidecar import read_sidecar_domain_config_hash

            should_update_sidecar = read_sidecar_domain_config_hash(
                effective_sidecar_db,
                tabular_manifest.domain_id,
            ) is not None
        if should_update_sidecar:
            from graphify.tabular_sidecar import read_sidecar_meta, update_sidecar

            effective_sidecar_db.parent.mkdir(parents=True, exist_ok=True)
            sidecar_update_stats = update_sidecar(effective_sidecar_db, tabular_manifest)
            sidecar_meta = read_sidecar_meta(effective_sidecar_db)
            if has_sidecar_projection:
                from graphify.tabular_graph import merge_sidecar_projection

                merge_sidecar_projection(
                    result,
                    tabular_manifest,
                    effective_sidecar_db,
                    effective_graphify_output,
                )

    # --- Post-extract cleanup on merged result ---
    redirected_tab_stub_ids = _redirect_tab_reference_edges(
        result["nodes"],
        result["edges"],
        root=repo_root,
        remove_unresolved=True,
    )
    if redirected_tab_stub_ids:
        result["nodes"] = [
            node for node in result["nodes"] if node.get("id") not in redirected_tab_stub_ids
        ]

    _add_lua_include_edges(code_files, result["nodes"], result["edges"], repo_root)
    _add_lua_ini_reference_edges(code_files, result["nodes"], result["edges"], repo_root)
    _relativize_source_files(result, repo_root)

    graph = build_from_json(result, root=repo_root)

    # --- Attach sidecar metadata ---
    if has_sidecar_projection and sidecar_meta is not None and effective_sidecar_db is not None:
        from graphify.tabular_graph import graph_sidecar_metadata

        graph.graph["tabular_sidecar"] = graph_sidecar_metadata(
            manifest=tabular_manifest,
            repo_key=tabular_manifest.repo_key,
            repo_root=tabular_manifest.repo_root,
            graphify_output=effective_graphify_output,
            db_path=effective_sidecar_db,
            sidecar_meta=sidecar_meta,
        )

    # --- Empty-graph check AFTER sidecar merge ---
    if graph.number_of_nodes() == 0:
        raise RuntimeError("manifest build produced an empty graph")

    communities = cluster(graph)
    cohesion = score_all(graph, communities)
    gods = god_nodes(graph)
    surprises = surprising_connections(graph, communities)

    labels_file = output_dir / ".graphify_labels.json"
    labels = _load_labels(labels_file, communities)
    questions = suggest_questions(graph, communities, labels)
    tokens = {
        "input": result.get("input_tokens", 0),
        "output": result.get("output_tokens", 0),
    }
    detection = {
        "files": files_by_type,
        "total_files": len(code_files),
        "total_words": _word_count(code_files),
    }
    commit = _git_head()

    # Build sidecar stats if available
    sidecar_stats_dict = None
    if sidecar_update_stats is not None and sidecar_meta is not None and effective_sidecar_db is not None:
        from graphify.tabular_sidecar import connect_sidecar

        sidecar_stats_dict = {
            "db_path": str(effective_sidecar_db),
            "schema_version": sidecar_meta.get("schema_version", "1"),
            "repo_key": sidecar_meta.get("repo_key", ""),
            "sidecar_db_id": sidecar_meta.get("sidecar_db_id", ""),
            "sidecar_instance_id": sidecar_meta.get("sidecar_instance_id", ""),
            "generation": sidecar_meta.get("sidecar_generation", "0"),
        }

        # Get current snapshot counts
        conn = connect_sidecar(effective_sidecar_db, readonly=True)
        try:
            files_count_row = conn.execute("SELECT COUNT(*) as cnt FROM source_files").fetchone()
            sidecar_stats_dict["files_count"] = int(files_count_row["cnt"]) if files_count_row else 0

            tables_count_row = conn.execute("SELECT COUNT(*) as cnt FROM tabular_tables").fetchone()
            sidecar_stats_dict["tables_count"] = int(tables_count_row["cnt"]) if tables_count_row else 0

            rows_count_row = conn.execute("SELECT COUNT(*) as cnt FROM rows").fetchone()
            sidecar_stats_dict["rows_count"] = int(rows_count_row["cnt"]) if rows_count_row else 0

            indexed_values_count_row = conn.execute("SELECT COUNT(*) as cnt FROM indexed_values").fetchone()
            sidecar_stats_dict["indexed_values_count"] = int(indexed_values_count_row["cnt"]) if indexed_values_count_row else 0

            refs_count_row = conn.execute("SELECT COUNT(*) as cnt FROM tabular_refs").fetchone()
            sidecar_stats_dict["refs_count"] = int(refs_count_row["cnt"]) if refs_count_row else 0
        finally:
            conn.close()

        # Add update stats
        sidecar_stats_dict["files_rebuilt"] = sidecar_update_stats.files_upserted
        sidecar_stats_dict["rows_rebuilt"] = sidecar_update_stats.rows_upserted
        sidecar_stats_dict["rows_pruned"] = sidecar_update_stats.rows_pruned

    report_text = generate(
        graph,
        communities,
        cohesion,
        labels,
        gods,
        surprises,
        detection,
        tokens,
        repo_root.name or str(repo_root),
        suggested_questions=questions,
        built_at_commit=commit,
        sidecar_stats=sidecar_stats_dict,
    )

    graph_json = output_dir / "graph.json"
    report_path = output_dir / "GRAPH_REPORT.md"
    analysis = {
        "communities": {str(k): v for k, v in communities.items()},
        "cohesion": {str(k): v for k, v in cohesion.items()},
        "gods": gods,
        "surprises": surprises,
        "tokens": tokens,
    }
    state_path = output_dir / ".graphify_state" / "update-state.json"
    current_relative_source_paths = _normalize_state_files(relative_source_paths, repo_root)
    current_relative_files_by_type = _normalize_state_files_by_type(
        relative_files_by_type,
        repo_root,
        fallback_shape=relative_files_by_type,
    )
    previous_files, previous_files_by_type = _load_previous_manifest_state(
        state_path,
        repo_root=repo_root,
        fallback_shape=current_relative_files_by_type,
    )
    graph_tmp = _temp_path_for(graph_json)
    staged: list[tuple[Path, Path]] = []
    try:
        if not to_json(graph, communities, str(graph_tmp), force=True, built_at_commit=commit):
            raise RuntimeError(f"failed to stage graph.json: {graph_json}")
        staged.extend(
            [
                (_stage_text(report_path, report_text), report_path),
                (
                    _stage_text(
                        labels_file,
                        json.dumps(
                            {str(k): v for k, v in sorted(labels.items())},
                            ensure_ascii=False,
                            indent=2,
                        )
                        + "\n",
                    ),
                    labels_file,
                ),
                (
                    _stage_text(
                        output_dir / ".graphify_analysis.json",
                        json.dumps(analysis, indent=2, ensure_ascii=False) + "\n",
                    ),
                    output_dir / ".graphify_analysis.json",
                ),
                (
                    _stage_text(
                        state_path,
                        _manifest_state_text(
                            mode=mode,
                            relative_source_paths=current_relative_source_paths,
                            relative_files_by_type=current_relative_files_by_type,
                            previous_files=previous_files,
                            previous_files_by_type=previous_files_by_type,
                        ),
                    ),
                    state_path,
                ),
            ]
        )

        backup_if_protected(output_dir)
        for tmp_path, final_path in staged:
            tmp_path.replace(final_path)
        graph_tmp.replace(graph_json)
    finally:
        graph_tmp.unlink(missing_ok=True)
        for tmp_path, _final_path in staged:
            tmp_path.unlink(missing_ok=True)

    try:
        to_html(graph, communities, str(output_dir / "graph.html"), community_labels=labels or None)
    except ValueError as exc:
        print(f"[graphify {mode}] skipped graph.html: {exc}")
        stale = output_dir / "graph.html"
        if stale.exists():
            stale.unlink()

    return CodeBuildResult(
        output_dir=output_dir,
        graph_json=graph_json,
        report=report_path,
        node_count=graph.number_of_nodes(),
        edge_count=graph.number_of_edges(),
        community_count=len(communities),
    )
