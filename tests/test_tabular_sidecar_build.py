"""Integration tests for tabular sidecar build pipeline."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from graphify.tabular_manifest import load_tabular_domain_manifest
from graphify.tabular_sidecar import connect_sidecar, update_sidecar, search_rows, resolve_ref


def _write_manifest(tmp_path: Path, repo: Path, files: list[dict], **extra: object) -> Path:
    payload: dict = {"repo_root": str(repo), "files": files, **extra}
    payload.setdefault("domain_id", "test-domain")
    manifest = tmp_path / "domain-files.json"
    manifest.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return manifest


def _build_graph(
    tmp_path: Path,
    repo: Path,
    manifest_path: Path,
    output_dir: Path,
    *,
    code_files: list[Path] | None = None,
    sidecar_db_path: Path | None = None,
):
    """Minimal in-process build_code_graph invocation."""
    from graphify.code_build_runner import build_code_graph
    from graphify.manifest import load_domain_manifest

    domain = load_domain_manifest(str(manifest_path), cwd=tmp_path)
    tabular = load_tabular_domain_manifest(manifest_path, cwd=tmp_path, output_dir=output_dir)
    active_graphify_output = repo / "graphify-out"

    return build_code_graph(
        code_files=code_files if code_files is not None else domain.source_paths,
        repo_root=repo,
        output_dir=output_dir,
        mode="extract",
        files_by_type=domain.files_by_type,
        relative_source_paths=domain.relative_source_paths,
        relative_files_by_type=domain.relative_files_by_type,
        max_workers=1,
        tabular_manifest=tabular,
        active_graphify_output=active_graphify_output,
        sidecar_db_path=sidecar_db_path,
    )


# ---------------------------------------------------------------------------
# Test 1: Sidecar file excluded from extract dispatch
# ---------------------------------------------------------------------------


def test_sidecar_file_excluded_from_extract_dispatch(tmp_path: Path) -> None:
    """Sidecar files must not produce extract_tab row nodes."""
    repo = tmp_path / "repo"
    repo.mkdir()
    tab = repo / "skills.tab"
    tab.write_text("SkillID\tName\tScript\n100\tKick\tscripts/kick.lua\n", encoding="utf-8")

    manifest_path = _write_manifest(tmp_path, repo, [
        {"path": "skills.tab", "tabular_policy": "sidecar", "primary_key": "SkillID"},
    ])
    output_dir = tmp_path / "out"

    result = _build_graph(tmp_path, repo, manifest_path, output_dir)
    graph_json = json.loads((output_dir / "graph.json").read_text(encoding="utf-8"))

    # No extract_tab nodes — sidecar files go through sidecar projection
    node_types = {n.get("type") for n in graph_json.get("nodes", [])}
    assert "extract_tab" not in node_types


# ---------------------------------------------------------------------------
# Test 2: Sidecar-only domain produces non-empty graph
# ---------------------------------------------------------------------------


def test_sidecar_only_domain_produces_nonempty_graph(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    tab = repo / "items.tab"
    tab.write_text("ItemID\tName\n1\tSword\n2\tShield\n", encoding="utf-8")

    manifest_path = _write_manifest(tmp_path, repo, [
        {
            "path": "items.tab",
            "tabular_policy": "sidecar",
            "primary_key": "ItemID",
            "anchor_columns": ["Name"],
        },
    ])
    output_dir = tmp_path / "out"

    result = _build_graph(tmp_path, repo, manifest_path, output_dir)
    assert result.node_count > 0
    assert result.edge_count > 0

    graph_json = json.loads((output_dir / "graph.json").read_text(encoding="utf-8"))
    node_types = {n.get("type") for n in graph_json.get("nodes", [])}
    assert "tabular_source" in node_types
    assert "tabular_table" in node_types
    assert "tabular_column" in node_types


# ---------------------------------------------------------------------------
# Test 3: Sidecar DB created at default path
# ---------------------------------------------------------------------------


def test_sidecar_db_at_default_path(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    tab = repo / "data.tab"
    tab.write_text("ID\tValue\n1\tA\n", encoding="utf-8")

    manifest_path = _write_manifest(tmp_path, repo, [
        {"path": "data.tab", "tabular_policy": "sidecar", "primary_key": "ID"},
    ])
    output_dir = tmp_path / "out"
    _build_graph(tmp_path, repo, manifest_path, output_dir)

    default_db = repo / "graphify-out" / "sidecar" / "tabular.sqlite"
    assert default_db.exists(), f"expected sidecar DB at {default_db}"


# ---------------------------------------------------------------------------
# Test 4: Graph metadata includes tabular_sidecar
# ---------------------------------------------------------------------------


def test_graph_metadata_includes_tabular_sidecar(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    tab = repo / "data.tab"
    tab.write_text("ID\tValue\n1\tA\n", encoding="utf-8")

    manifest_path = _write_manifest(tmp_path, repo, [
        {"path": "data.tab", "tabular_policy": "sidecar", "primary_key": "ID"},
    ])
    output_dir = tmp_path / "out"
    _build_graph(tmp_path, repo, manifest_path, output_dir)

    graph_json = json.loads((output_dir / "graph.json").read_text(encoding="utf-8"))
    meta = graph_json.get("graph", {}).get("tabular_sidecar")
    assert meta is not None, "missing tabular_sidecar in graph metadata"
    assert "domain_id" in meta
    assert "sidecar_db_hint" in meta
    assert "sidecar_db_id" in meta
    assert "sidecar_instance_id" in meta
    assert "sidecar_generation" in meta


# ---------------------------------------------------------------------------
# Test 5: --sidecar-db override puts DB at custom path with absolute hint
# ---------------------------------------------------------------------------


def test_sidecar_db_override_custom_path(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    tab = repo / "data.tab"
    tab.write_text("ID\tValue\n1\tA\n", encoding="utf-8")

    manifest_path = _write_manifest(tmp_path, repo, [
        {"path": "data.tab", "tabular_policy": "sidecar", "primary_key": "ID"},
    ])
    output_dir = tmp_path / "out"
    custom_db = tmp_path / "custom" / "my.sqlite"

    _build_graph(tmp_path, repo, manifest_path, output_dir, sidecar_db_path=custom_db)

    assert custom_db.exists()
    graph_json = json.loads((output_dir / "graph.json").read_text(encoding="utf-8"))
    hint = graph_json["graph"]["tabular_sidecar"]["sidecar_db_hint"]
    assert hint["kind"] == "absolute"
    assert str(custom_db.resolve()) in hint["path"]


# ---------------------------------------------------------------------------
# Test 6: Anchor nodes have sidecar_ref and sidecar_ref_key
# ---------------------------------------------------------------------------


def test_anchor_nodes_have_sidecar_ref(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    tab = repo / "skills.tab"
    tab.write_text("SkillID\tName\n100\tKick\n101\tBlock\n", encoding="utf-8")

    manifest_path = _write_manifest(tmp_path, repo, [
        {
            "path": "skills.tab",
            "tabular_policy": "sidecar",
            "primary_key": "SkillID",
            "anchor_columns": ["Name"],
        },
    ])
    output_dir = tmp_path / "out"
    _build_graph(tmp_path, repo, manifest_path, output_dir)

    graph_json = json.loads((output_dir / "graph.json").read_text(encoding="utf-8"))
    anchors = [n for n in graph_json["nodes"] if n.get("type") == "tabular_anchor"]
    assert len(anchors) >= 2, f"expected at least 2 anchor nodes, got {len(anchors)}"
    for anchor in anchors:
        assert "sidecar_ref" in anchor, f"anchor missing sidecar_ref: {anchor}"
        assert "sidecar_ref_key" in anchor, f"anchor missing sidecar_ref_key: {anchor}"
        assert anchor["sidecar_ref"].startswith("sidecar://tabular/")


# ---------------------------------------------------------------------------
# Test 7: Path-ref skeleton nodes reference targets
# ---------------------------------------------------------------------------


def test_path_ref_skeleton_nodes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    scripts = repo / "scripts"
    scripts.mkdir()
    (scripts / "kick.lua").write_text("return 'kick'\n", encoding="utf-8")
    tab = repo / "skills.tab"
    tab.write_text(
        "SkillID\tName\tScript\n100\tKick\tscripts/kick.lua\n",
        encoding="utf-8",
    )

    manifest_path = _write_manifest(tmp_path, repo, [
        {
            "path": "skills.tab",
            "tabular_policy": "sidecar",
            "primary_key": "SkillID",
            "anchor_columns": ["Name"],
        },
        {"path": "scripts/kick.lua"},
    ])
    output_dir = tmp_path / "out"
    _build_graph(tmp_path, repo, manifest_path, output_dir)

    graph_json = json.loads((output_dir / "graph.json").read_text(encoding="utf-8"))
    refs = [n for n in graph_json["nodes"] if n.get("type") == "tabular_ref"]
    assert len(refs) >= 1, f"expected at least 1 path-ref node, got {len(refs)}"
    ref = refs[0]
    assert "target_ref" in ref
    assert "sidecar_ref" in ref
    assert "sidecar_ref_key" in ref

    ref_edges = [
        edge for edge in graph_json.get("links", [])
        if edge.get("source") == ref["id"] or edge.get("target") == ref["id"]
    ]
    assert ref_edges, "expected path-ref node to participate in graph edges"
    for edge in ref_edges:
        assert "relation" in edge
        assert "confidence" in edge
        assert "confidence_score" in edge

    file_node_ids = {
        node["id"] for node in graph_json["nodes"]
        if node.get("source_file") == "scripts/kick.lua"
    }
    assert any(
        edge.get("relation") == "references"
        and edge.get("source") == ref["id"]
        and edge.get("target") in file_node_ids
        for edge in graph_json.get("links", [])
    ), "expected sidecar path-ref node to reference the target file node"


def test_anchor_to_path_ref_edge_uses_references_relation(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    scripts = repo / "scripts"
    scripts.mkdir()
    (scripts / "kick.lua").write_text("return 'kick'\n", encoding="utf-8")
    tab = repo / "skills.tab"
    tab.write_text(
        "SkillID\tName\tScript\n100\tKick\tscripts/kick.lua\n",
        encoding="utf-8",
    )

    manifest_path = _write_manifest(tmp_path, repo, [
        {
            "path": "skills.tab",
            "tabular_policy": "sidecar",
            "primary_key": "SkillID",
            "anchor_columns": ["Name"],
        },
        {"path": "scripts/kick.lua"},
    ])
    output_dir = tmp_path / "out"
    _build_graph(tmp_path, repo, manifest_path, output_dir)

    graph_json = json.loads((output_dir / "graph.json").read_text(encoding="utf-8"))
    anchors = [n for n in graph_json["nodes"] if n.get("type") == "tabular_anchor"]
    refs = [n for n in graph_json["nodes"] if n.get("type") == "tabular_ref"]
    assert anchors, "expected at least one anchor node"
    assert refs, "expected at least one path-ref node"

    same_row_pairs = [
        (anchor["id"], ref["id"])
        for anchor in anchors
        for ref in refs
        if anchor.get("sidecar_ref") == ref.get("sidecar_ref")
    ]
    assert same_row_pairs, "expected anchor and path-ref from the same source row"

    links = graph_json.get("links", [])
    assert any(
        edge.get("source") == anchor_id
        and edge.get("target") == ref_id
        and edge.get("relation") == "references"
        for anchor_id, ref_id in same_row_pairs
        for edge in links
    ), "expected same-row anchor to reference the path-ref skeleton"
    assert not any(
        edge.get("source") == anchor_id
        and edge.get("target") == ref_id
        and edge.get("relation") == "contains"
        for anchor_id, ref_id in same_row_pairs
        for edge in links
    ), "same-row anchor must not contain the path-ref skeleton"


def test_path_ref_skeleton_does_not_publish_outside_repo_target(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_target = outside / "kick.lua"
    outside_target.write_text("return 'outside'\n", encoding="utf-8")
    tab = repo / "skills.tab"
    tab.write_text(
        "SkillID\tName\tScript\n100\tKick\t../outside/kick.lua\n",
        encoding="utf-8",
    )

    manifest_path = _write_manifest(tmp_path, repo, [
        {
            "path": "skills.tab",
            "tabular_policy": "sidecar",
            "primary_key": "SkillID",
            "anchor_columns": ["Name"],
        },
    ])
    output_dir = tmp_path / "out"
    _build_graph(tmp_path, repo, manifest_path, output_dir)

    graph_json = json.loads((output_dir / "graph.json").read_text(encoding="utf-8"))
    assert not any(
        node.get("source_file") == str(outside_target.resolve())
        for node in graph_json["nodes"]
    )
    outside_node_ids = {
        node["id"] for node in graph_json["nodes"]
        if str(node.get("source_file", "")).endswith("outside/kick.lua")
    }
    assert not any(
        edge.get("relation") == "references" and edge.get("target") in outside_node_ids
        for edge in graph_json.get("links", [])
    )


# ---------------------------------------------------------------------------
# Test 8: GRAPH_REPORT.md includes tabular sidecar section
# ---------------------------------------------------------------------------


def test_graph_report_includes_tabular_sidecar_section(tmp_path: Path) -> None:
    """GRAPH_REPORT.md must contain '## Tabular Sidecar' section when sidecar is active."""
    repo = tmp_path / "repo"
    repo.mkdir()
    tab = repo / "data.tab"
    tab.write_text("ID\tValue\n1\tA\n2\tB\n", encoding="utf-8")

    manifest_path = _write_manifest(tmp_path, repo, [
        {"path": "data.tab", "tabular_policy": "sidecar", "primary_key": "ID"},
    ])
    output_dir = tmp_path / "out"
    _build_graph(tmp_path, repo, manifest_path, output_dir)

    report_path = output_dir / "GRAPH_REPORT.md"
    assert report_path.exists(), "GRAPH_REPORT.md should exist"

    report_text = report_path.read_text(encoding="utf-8")
    assert "## Tabular Sidecar" in report_text, "Report should contain '## Tabular Sidecar' section"
    assert "DB path:" in report_text, "Report should contain DB path"
    assert "schema version:" in report_text, "Report should contain schema version"
    assert "current snapshot:" in report_text, "Report should contain current snapshot info"


# ---------------------------------------------------------------------------
# Test 9: Generic eval - sidecar search finds high-row data
# ---------------------------------------------------------------------------


def test_sidecar_search_finds_high_row_data(tmp_path: Path) -> None:
    """Sidecar can find rows that would be lost in graph-only cap."""
    repo = tmp_path / "repo"
    repo.mkdir()

    # Create a large table with high row numbers
    lines = ["ID\tValue\tDescription\n"]
    for i in range(1, 101):
        lines.append(f"{i}\tValue{i}\tDesc{i}\n")

    tab = repo / "large.tab"
    tab.write_text("".join(lines), encoding="utf-8")

    manifest_path = _write_manifest(tmp_path, repo, [
        {
            "path": "large.tab",
            "tabular_policy": "sidecar",
            "primary_key": "ID",
            "anchor_columns": ["Value"],
        },
    ])
    output_dir = tmp_path / "out"
    _build_graph(tmp_path, repo, manifest_path, output_dir)

    # Search for high row ID (row 95)
    db_path = repo / "graphify-out" / "sidecar" / "tabular.sqlite"
    results = search_rows(
        db_path,
        domain_id="test-domain",
        column="ID",
        value="95",
    )
    assert len(results) == 1, f"expected 1 result for ID=95, got {len(results)}"
    assert results[0]["row_no"] == 96  # header is row 1, data starts at row 2

    # Verify row content (row_json is already parsed)
    row_json = results[0]["row_json"]
    assert row_json["ID"] == "95"
    assert row_json["Value"] == "Value95"
    assert row_json["Description"] == "Desc95"


# ---------------------------------------------------------------------------
# Test 10: Generic eval - anchor resolves through sidecar_ref
# ---------------------------------------------------------------------------


def test_anchor_resolves_through_sidecar_ref(tmp_path: Path) -> None:
    """Create a sidecar anchor, get its sidecar_ref, then resolve it."""
    repo = tmp_path / "repo"
    repo.mkdir()
    tab = repo / "items.tab"
    tab.write_text("ItemID\tName\tType\n1\tSword\tWeapon\n2\tShield\tArmor\n", encoding="utf-8")

    manifest_path = _write_manifest(tmp_path, repo, [
        {
            "path": "items.tab",
            "tabular_policy": "sidecar",
            "primary_key": "ItemID",
            "anchor_columns": ["Name"],
        },
    ])
    output_dir = tmp_path / "out"
    _build_graph(tmp_path, repo, manifest_path, output_dir)

    # Get anchor from graph
    graph_json = json.loads((output_dir / "graph.json").read_text(encoding="utf-8"))
    anchors = [n for n in graph_json["nodes"] if n.get("type") == "tabular_anchor"]
    assert len(anchors) >= 1

    sword_anchor = [a for a in anchors if a.get("label") == "Sword"][0]
    sidecar_ref = sword_anchor["sidecar_ref"]

    # Resolve the anchor via sidecar CLI
    db_path = repo / "graphify-out" / "sidecar" / "tabular.sqlite"
    resolved = resolve_ref(db_path, sidecar_ref, domain_id="test-domain")

    assert resolved["row_no"] == 2  # header is row 1
    row_json = resolved["row_json"]  # Already parsed
    assert row_json["Name"] == "Sword"
    assert row_json["Type"] == "Weapon"


# ---------------------------------------------------------------------------
# Test 11: Generic eval - update/prune leaves no stale rows
# ---------------------------------------------------------------------------


def test_update_prune_no_stale_rows(tmp_path: Path) -> None:
    """Build, modify a file, rebuild → old row gone, new row present."""
    repo = tmp_path / "repo"
    repo.mkdir()
    tab = repo / "data.tab"
    tab.write_text("ID\tValue\n1\tOldValue\n", encoding="utf-8")

    manifest_path = _write_manifest(tmp_path, repo, [
        {
            "path": "data.tab",
            "tabular_policy": "sidecar",
            "primary_key": "ID",
            "indexed_columns": ["Value"],  # Need to index Value column
        },
    ])
    output_dir = tmp_path / "out"
    _build_graph(tmp_path, repo, manifest_path, output_dir)

    db_path = repo / "graphify-out" / "sidecar" / "tabular.sqlite"

    # Verify old value
    results = search_rows(db_path, domain_id="test-domain", column="Value", value="OldValue")
    assert len(results) == 1

    # Modify file
    tab.write_text("ID\tValue\n1\tNewValue\n", encoding="utf-8")

    # Rebuild
    _build_graph(tmp_path, repo, manifest_path, output_dir)

    # Old value should be gone
    old_results = search_rows(db_path, domain_id="test-domain", column="Value", value="OldValue")
    assert len(old_results) == 0, "old row should be pruned"

    # New value should be present
    new_results = search_rows(db_path, domain_id="test-domain", column="Value", value="NewValue")
    assert len(new_results) == 1, "new row should be present"


def test_update_prunes_sidecar_domain_when_manifest_becomes_graph_only(tmp_path: Path) -> None:
    """A domain that drops all sidecar files must prune its old DB membership."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "data.tab").write_text("ID\tValue\n1\tStale\n", encoding="utf-8")
    (repo / "main.py").write_text("VALUE = 1\n", encoding="utf-8")

    manifest_path = _write_manifest(tmp_path, repo, [
        {
            "path": "data.tab",
            "tabular_policy": "sidecar",
            "primary_key": "ID",
        },
    ])
    output_dir = tmp_path / "out"
    _build_graph(tmp_path, repo, manifest_path, output_dir)

    db_path = repo / "graphify-out" / "sidecar" / "tabular.sqlite"
    conn = connect_sidecar(db_path, readonly=True)
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM domain_files WHERE domain_id = ?",
            ("test-domain",),
        ).fetchone()[0] == 1
    finally:
        conn.close()

    manifest_path.write_text(json.dumps({
        "repo_root": str(repo),
        "domain_id": "test-domain",
        "files": [{"path": "main.py"}],
    }), encoding="utf-8")

    _build_graph(tmp_path, repo, manifest_path, output_dir)

    conn = connect_sidecar(db_path, readonly=True)
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM domain_files WHERE domain_id = ?",
            ("test-domain",),
        ).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM source_files").fetchone()[0] == 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Test 12: JX3 mini - high SkillID lookup
# ---------------------------------------------------------------------------


def test_jx3_mini_high_skillid_lookup(tmp_path: Path) -> None:
    """Build JX3 mini fixtures, search for high SkillID."""
    import shutil

    repo = tmp_path / "repo"
    repo.mkdir()

    # Copy JX3 fixtures
    fixtures_src = Path(__file__).parent / "fixtures" / "sidecar" / "jx3-mini"
    for file in fixtures_src.glob("*.tab"):
        shutil.copy(file, repo / file.name)
    for file in fixtures_src.glob("*.txt"):
        shutil.copy(file, repo / file.name)

    manifest_path = _write_manifest(tmp_path, repo, [
        {
            "path": "skills.tab",
            "tabular_policy": "sidecar",
            "primary_key": "SkillID",
            "anchor_columns": ["Name"],
        },
        {
            "path": "Buff.tab",
            "tabular_policy": "sidecar",
            "primary_key": "BuffID",
            "anchor_columns": ["Name"],
        },
        {
            "path": "recipeSkill.tab",
            "tabular_policy": "sidecar",
            "primary_key": "RecipeID",
        },
        {
            "path": "CoolDownList.tab",
            "tabular_policy": "sidecar",
            "primary_key": "ID",
        },
        {
            "path": "skill_extended.tab",
            "tabular_policy": "sidecar",
            "primary_key": "SkillID",
            "anchor_columns": ["Name"],
        },
        {
            "path": "buff_extended.tab",
            "tabular_policy": "sidecar",
            "primary_key": "BuffID",
            "anchor_columns": ["Name"],
        },
    ])
    output_dir = tmp_path / "out"
    _build_graph(tmp_path, repo, manifest_path, output_dir)

    db_path = repo / "graphify-out" / "sidecar" / "tabular.sqlite"

    # Search for high SkillID from skills.tab
    results = search_rows(db_path, domain_id="test-domain", column="SkillID", value="50001")
    assert len(results) >= 1, f"expected at least 1 result for SkillID=50001, got {len(results)}"

    # Verify it's from skills.tab
    assert any("skills.tab" in r["source_file"] for r in results)

    # Search for high SkillID from skill_extended.tab
    results_txt = search_rows(db_path, domain_id="test-domain", column="SkillID", value="60000")
    assert len(results_txt) >= 1, f"expected at least 1 result for SkillID=60000, got {len(results_txt)}"
    assert any("skill_extended.tab" in r["source_file"] for r in results_txt)


# ---------------------------------------------------------------------------
# Test 13: JX3 mini - cross-table SkillID reference
# ---------------------------------------------------------------------------


def test_jx3_mini_cross_table_reference(tmp_path: Path) -> None:
    """recipeSkill.tab references SkillID; verify ref node exists in graph."""
    import shutil

    repo = tmp_path / "repo"
    repo.mkdir()

    # Copy JX3 fixtures
    fixtures_src = Path(__file__).parent / "fixtures" / "sidecar" / "jx3-mini"
    for file in fixtures_src.glob("*.tab"):
        shutil.copy(file, repo / file.name)

    manifest_path = _write_manifest(tmp_path, repo, [
        {
            "path": "skills.tab",
            "tabular_policy": "sidecar",
            "primary_key": "SkillID",
            "anchor_columns": ["Name"],
        },
        {
            "path": "recipeSkill.tab",
            "tabular_policy": "sidecar",
            "primary_key": "RecipeID",
            "indexed_columns": ["SkillID"],  # Index the SkillID column for cross-ref
        },
    ])
    output_dir = tmp_path / "out"
    _build_graph(tmp_path, repo, manifest_path, output_dir)

    # Verify ref nodes exist for SkillID references
    graph_json = json.loads((output_dir / "graph.json").read_text(encoding="utf-8"))
    ref_nodes = [n for n in graph_json["nodes"] if n.get("type") == "tabular_ref"]

    # Should have refs for SkillID values in recipeSkill.tab
    assert len(ref_nodes) > 0, "expected cross-table reference nodes"

    # Verify we can search for SkillID 50001 in recipeSkill
    db_path = repo / "graphify-out" / "sidecar" / "tabular.sqlite"
    results = search_rows(db_path, domain_id="test-domain", column="SkillID", value="50001", source_file="recipeSkill.tab")
    assert len(results) >= 1, f"expected recipe with SkillID=50001, got {len(results)}"
