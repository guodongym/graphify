"""Graph metadata helpers and projection node/edge generation for tabular sidecar."""
from __future__ import annotations

from pathlib import Path

from graphify.tabular_manifest import ColumnSelector, TabularDomainManifest, canonical_json_hash
from graphify.tabular_sidecar import (
    deterministic_sidecar_db_id,
    iter_sidecar_graph_projection,
    make_sidecar_ref,
    read_sidecar_file_meta,
    read_sidecar_table_projection,
)


def sidecar_db_hint(*, repo_root: Path, graphify_output: Path, db_path: Path) -> dict:
    """Return a location hint dict for the sidecar DB relative to repo or absolute."""
    resolved = db_path.resolve()
    try:
        return {"kind": "repo_relative", "path": resolved.relative_to(repo_root.resolve()).as_posix()}
    except ValueError:
        pass
    try:
        return {
            "kind": "absolute",
            "path": str(resolved),
            "graphify_output_relative": resolved.relative_to(graphify_output.resolve()).as_posix(),
        }
    except ValueError:
        return {"kind": "absolute", "path": str(resolved)}


def graph_sidecar_metadata(
    *,
    manifest: TabularDomainManifest,
    repo_key: str,
    repo_root: Path,
    graphify_output: Path,
    db_path: Path,
    sidecar_meta: dict,
) -> dict:
    """Build the full metadata dict for ``graph.graph["tabular_sidecar"]``."""
    return {
        "domain_id": manifest.domain_id,
        "sidecar_domain_config_hash": manifest.sidecar_domain_config_hash,
        "sidecar_db_hint": sidecar_db_hint(repo_root=repo_root, graphify_output=graphify_output, db_path=db_path),
        "sidecar_db_id": deterministic_sidecar_db_id(repo_key),
        "sidecar_schema_version": 1,
        "sidecar_instance_id": sidecar_meta["sidecar_instance_id"],
        "sidecar_generation": sidecar_meta["sidecar_generation"],
        "repo_key": repo_key,
        "repo_root": str(repo_root),
        "graphify_output": str(graphify_output.resolve()),
    }


def sidecar_ref_key(repo_key: str, file_key: str, row_no: int, line_hash: str) -> str:
    return canonical_json_hash({
        "repo_key": repo_key,
        "file_key": file_key,
        "row_no": row_no,
        "line_hash": line_hash,
    })


def row_skeleton_key(file_key: str, row_no: int, line_hash: str) -> str:
    return canonical_json_hash({
        "file_key": file_key,
        "row_no": row_no,
        "line_hash": line_hash,
    })[:24]


def anchor_label(row_values: list[dict], selectors: tuple[ColumnSelector, ...]) -> str | None:
    values: list[str] = []
    for selector in selectors:
        for item in row_values:
            if item["normalized_name"] == selector.normalized_name and item["column_index"] == selector.column_index:
                value = str(item["value"]).strip()
                if value:
                    values.append(value)
                break
    return " | ".join(values) if values else None


def anchor_node_id(file_key: str, row_no: int, line_hash: str) -> str:
    return f"tabular_anchor:{file_key}:{row_skeleton_key(file_key, row_no, line_hash)}"


def path_ref_node_id(file_key: str, row_no: int, line_hash: str, target_ref: str) -> str:
    return "tabular_ref:" + canonical_json_hash({
        "file_key": file_key,
        "row_no": row_no,
        "line_hash": line_hash,
        "target_ref": target_ref,
    })[:24]


def path_ref_target_stub_id(file_key: str, target_ref: str) -> str:
    return "tabular_ref_target:" + canonical_json_hash({
        "file_key": file_key,
        "target_ref": target_ref,
    })[:24]


def _source_path(source_file: str, repo_root: Path) -> Path:
    path = Path(source_file)
    if path.is_absolute():
        return path.resolve()
    return (repo_root / path).resolve()


def _code_file_node_index(nodes: list[dict], repo_root: Path) -> dict[str, str]:
    index: dict[str, str] = {}
    for node in nodes:
        if node.get("file_type") != "code":
            continue
        source_file = node.get("source_file")
        if not source_file:
            continue
        source_path = _source_path(str(source_file), repo_root)
        if node.get("label") != source_path.name:
            continue
        index[str(source_path)] = str(node["id"])
    return index


def _path_ref_candidates(source_file: str, target_ref: str, repo_root: Path) -> list[Path]:
    source_path = _source_path(source_file, repo_root)
    candidates = [(source_path.parent / target_ref).resolve()]
    root_candidate = (repo_root / target_ref).resolve()
    if root_candidate not in candidates:
        candidates.append(root_candidate)
    return candidates


def _resolve_path_ref_target(
    file_node_index: dict[str, str],
    *,
    source_file: str,
    target_ref: str,
    repo_root: Path,
) -> str | None:
    try:
        candidates = _path_ref_candidates(source_file, target_ref, repo_root)
    except OSError:
        return None
    for candidate in candidates:
        target_node_id = file_node_index.get(str(candidate))
        if target_node_id:
            return target_node_id
    return None


def _repo_relative(path: Path, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _existing_path_ref_target(source_file: str, target_ref: str, repo_root: Path) -> Path | None:
    resolved_root = repo_root.resolve()
    try:
        candidates = _path_ref_candidates(source_file, target_ref, repo_root)
    except OSError:
        return None
    for candidate in candidates:
        try:
            if not candidate.exists() or not candidate.is_file():
                continue
        except OSError:
            continue
        try:
            candidate.relative_to(resolved_root)
        except ValueError:
            continue
        else:
            return candidate
    return None


def _edge(
    source: str,
    target: str,
    relation: str,
    *,
    source_file: str,
    source_location: str | None = None,
    context: str | None = None,
    **extra: object,
) -> dict:
    edge = {
        "source": source,
        "target": target,
        "relation": relation,
        "confidence": "EXTRACTED",
        "confidence_score": 1.0,
        "source_file": source_file,
        "source_location": source_location,
        "weight": 1.0,
    }
    if context is not None:
        edge["context"] = context
    edge.update(extra)
    return edge


def merge_sidecar_projection(
    result: dict,
    tabular_manifest: TabularDomainManifest,
    sidecar_db_path: Path,
    active_graphify_output: Path,
) -> None:
    """Merge sidecar projection nodes and edges into the extraction *result* dict.

    Emits source-file, table, column, anchor, and path-ref skeleton nodes with
    ``contains`` and ``references`` edges.  Mutates *result* in place.
    """
    nodes: list[dict] = result.setdefault("nodes", [])
    edges: list[dict] = result.setdefault("edges", [])

    sidecar_db_id = deterministic_sidecar_db_id(tabular_manifest.repo_key)
    file_node_index = _code_file_node_index(nodes, tabular_manifest.repo_root)
    emitted_target_stubs: set[str] = set()

    for file_cfg in tabular_manifest.sidecar_active_files:
        file_meta = read_sidecar_file_meta(
            sidecar_db_path,
            domain_id=tabular_manifest.domain_id,
            file_key=file_cfg.file_key,
        )
        if file_meta is None:
            raise RuntimeError(f"missing sidecar metadata for {file_cfg.source_file}")

        # Source file node
        source_node_id = f"tabular_source:{file_cfg.file_key}"
        nodes.append({
            "id": source_node_id,
            "type": "tabular_source",
            "label": file_cfg.source_file,
            "source_file": file_cfg.source_file,
            "file_key": file_cfg.file_key,
            "sidecar_db_id": sidecar_db_id,
            "source_sha256": file_meta.source_sha256,
            "domain_file_config_hash": file_meta.domain_file_config_hash,
            "union_config_hash": file_meta.union_config_hash,
        })

        # Table and column nodes from table projection
        for table in read_sidecar_table_projection(
            sidecar_db_path,
            domain_id=tabular_manifest.domain_id,
            file_key=file_cfg.file_key,
        ):
            table_node_id = f"tabular_table:{file_cfg.file_key}:{table.table_id}"
            nodes.append({
                "id": table_node_id,
                "type": "tabular_table",
                "label": f"{file_cfg.source_file}:table_{table.table_id}",
                "source_file": file_cfg.source_file,
                "file_key": file_cfg.file_key,
                "table_id": table.table_id,
                "row_count": table.row_count,
                "column_count": table.column_count,
                "warnings": list(table.warnings),
                "sidecar_db_id": sidecar_db_id,
                "source_sha256": file_meta.source_sha256,
                "domain_file_config_hash": file_meta.domain_file_config_hash,
                "union_config_hash": file_meta.union_config_hash,
            })
            edges.append(_edge(
                source_node_id,
                table_node_id,
                "contains",
                source_file=file_cfg.source_file,
                source_location="L1",
                context="tabular_sidecar",
            ))
            for column in table.columns:
                column_node_id = f"tabular_column:{file_cfg.file_key}:{table.table_id}:{column['column_index']}"
                nodes.append({
                    "id": column_node_id,
                    "type": "tabular_column",
                    "label": column.get("name", f"col_{column['column_index']}"),
                    "source_file": file_cfg.source_file,
                    **column,
                })
                edges.append(_edge(
                    table_node_id,
                    column_node_id,
                    "contains",
                    source_file=file_cfg.source_file,
                    source_location="L1",
                    context="tabular_column",
                ))

        # Anchor and path-ref skeleton nodes from row projection
        for projection in iter_sidecar_graph_projection(
            sidecar_db_path,
            domain_id=tabular_manifest.domain_id,
            file_key=file_cfg.file_key,
        ):
            ref_key = sidecar_ref_key(
                tabular_manifest.repo_key,
                projection.file_meta.file_key,
                projection.row_no,
                projection.line_hash,
            )
            sidecar_ref = make_sidecar_ref(
                tabular_manifest.repo_key,
                projection.file_meta.file_key,
                projection.row_no,
                projection.line_hash,
            )

            table_nid = f"tabular_table:{file_cfg.file_key}:{projection.table_id}"

            label = anchor_label(projection.row_values_json, file_cfg.anchor_columns)
            a_node_id = None
            if label:
                a_node_id = anchor_node_id(
                    projection.file_meta.file_key,
                    projection.row_no,
                    projection.line_hash,
                )
                nodes.append({
                    "id": a_node_id,
                    "type": "tabular_anchor",
                    "label": label,
                    "source_file": projection.file_meta.source_file,
                    "file_key": projection.file_meta.file_key,
                    "row_no": projection.row_no,
                    "line_hash": projection.line_hash,
                    "sidecar_ref": sidecar_ref,
                    "sidecar_ref_key": ref_key,
                    "domain_file_config_hash": projection.file_meta.domain_file_config_hash,
                    "source_sha256": projection.file_meta.source_sha256,
                })
                edges.append(_edge(
                    table_nid,
                    a_node_id,
                    "contains",
                    source_file=projection.file_meta.source_file,
                    source_location=f"L{projection.row_no}",
                    context="tabular_anchor",
                ))

            for path_ref in projection.path_refs:
                pr_node_id = path_ref_node_id(
                    projection.file_meta.file_key,
                    projection.row_no,
                    projection.line_hash,
                    path_ref["target_ref"],
                )
                nodes.append({
                    "id": pr_node_id,
                    "type": "tabular_ref",
                    "label": path_ref["target_ref"],
                    "source_file": projection.file_meta.source_file,
                    "target_ref": path_ref["target_ref"],
                    "value": path_ref["value"],
                    "sidecar_ref": sidecar_ref,
                    "sidecar_ref_key": ref_key,
                })
                edges.append(_edge(
                    table_nid,
                    pr_node_id,
                    "contains",
                    source_file=projection.file_meta.source_file,
                    source_location=f"L{projection.row_no}",
                    context="tabular_path_ref",
                ))
                if a_node_id:
                    edges.append(_edge(
                        a_node_id,
                        pr_node_id,
                        "references",
                        source_file=projection.file_meta.source_file,
                        source_location=f"L{projection.row_no}",
                        context="tabular_path_ref",
                    ))
                target_node_id = _resolve_path_ref_target(
                    file_node_index,
                    source_file=projection.file_meta.source_file,
                    target_ref=path_ref["target_ref"],
                    repo_root=tabular_manifest.repo_root,
                )
                if target_node_id is None:
                    existing_target = _existing_path_ref_target(
                        projection.file_meta.source_file,
                        path_ref["target_ref"],
                        tabular_manifest.repo_root,
                    )
                    if existing_target is not None:
                        target_node_id = path_ref_target_stub_id(
                            projection.file_meta.file_key,
                            path_ref["target_ref"],
                        )
                        if target_node_id not in emitted_target_stubs:
                            emitted_target_stubs.add(target_node_id)
                            nodes.append({
                                "id": target_node_id,
                                "type": "tabular_ref_target",
                                "label": existing_target.name,
                                "file_type": "code",
                                "source_file": _repo_relative(existing_target, tabular_manifest.repo_root),
                                "source_location": None,
                                "target_ref": path_ref["target_ref"],
                                "sidecar_db_id": sidecar_db_id,
                            })
                if target_node_id:
                    edges.append(_edge(
                        pr_node_id,
                        target_node_id,
                        "references",
                        source_file=projection.file_meta.source_file,
                        source_location=f"L{projection.row_no}",
                        context="path",
                    ))
