from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from graphify.tabular_manifest import load_tabular_domain_manifest, repo_key_for_root
from graphify.tabular_sidecar import (
    SCHEMA_VERSION,
    _has_sql_statement_separator,
    _normalize_readonly_sql,
    connect_sidecar,
    ensure_schema,
    execute_readonly_query,
    make_sidecar_ref,
    read_sidecar_meta,
    resolve_ref,
    search_rows,
    update_sidecar,
)


def write_manifest(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "domain-files.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def build_manifest(
    tmp_path: Path,
    *,
    domain_id: str = "skill-core",
    files: list[dict],
) :
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    manifest = write_manifest(tmp_path, {
        "repo_root": str(repo),
        "domain_id": domain_id,
        "files": files,
    })
    return load_tabular_domain_manifest(manifest, cwd=tmp_path)


def test_schema_creation_and_fk_enforcement(tmp_path):
    db_path = tmp_path / "sidecar.db"
    conn = connect_sidecar(db_path)
    try:
        ensure_schema(conn)
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO domain_files(domain_id, file_id, declared_policy, effective_policy, config_json, config_hash) VALUES(?, ?, ?, ?, ?, ?)",
                ("skill-core", 999, "sidecar", "sidecar", "{}", "cfg"),
            )
    finally:
        conn.close()


def test_schema_indexes_file_id_for_merge_hot_paths(tmp_path):
    db_path = tmp_path / "sidecar.db"
    conn = connect_sidecar(db_path)
    try:
        ensure_schema(conn)
        indexed_indexes = {
            row["name"]
            for row in conn.execute("PRAGMA index_list('indexed_values')").fetchall()
        }
        refs_indexes = {
            row["name"]
            for row in conn.execute("PRAGMA index_list('tabular_refs')").fetchall()
        }

        assert "idx_indexed_file" in indexed_indexes
        assert "idx_refs_file" in refs_indexes

        indexed_plan = "\n".join(
            row["detail"]
            for row in conn.execute(
                "EXPLAIN QUERY PLAN SELECT COUNT(*) FROM indexed_values WHERE file_id = 1"
            ).fetchall()
        )
        refs_plan = "\n".join(
            row["detail"]
            for row in conn.execute(
                "EXPLAIN QUERY PLAN SELECT * FROM tabular_refs WHERE file_id = 1"
            ).fetchall()
        )

        assert "idx_indexed_file" in indexed_plan
        assert "idx_refs_file" in refs_plan
    finally:
        conn.close()


def test_update_sidecar_writes_rows_indexes_and_refs(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    table = repo / "skills.tab"
    table.write_text(
        "SkillID\tName\tScript\n"
        "100\tKick\tscripts/kick.lua\n"
        "101\tBlock\tscripts/block.lua\n",
        encoding="utf-8",
    )
    manifest = build_manifest(
        tmp_path,
        files=[{
            "path": "skills.tab",
            "tabular_policy": "sidecar",
            "primary_key": "SkillID",
            "indexed_columns": ["Name"],
        }],
    )
    db_path = tmp_path / "sidecar.db"

    stats = update_sidecar(db_path, manifest)

    assert stats.files_upserted == 1
    assert stats.rows_upserted == 2
    assert stats.indexes_upserted == 4
    assert stats.refs_upserted == 2
    conn = connect_sidecar(db_path, readonly=True)
    try:
        assert conn.execute("SELECT COUNT(*) FROM rows").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM indexed_values").fetchone()[0] == 4
        assert conn.execute("SELECT COUNT(*) FROM tabular_refs").fetchone()[0] == 2
    finally:
        conn.close()


def test_update_sidecar_does_not_extract_path_refs_from_desc_text(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    table = repo / "skillevent.txt"
    long_desc = "2021年8月16日—2021年8月30日：\\n1. 第一段说明。\\n2. 第二段说明。" * 6
    table.write_text(
        "SkillID\tDesc\tScript\n"
        f"100\t{long_desc}\tscripts/kick.lua\n",
        encoding="utf-8",
    )
    manifest = build_manifest(
        tmp_path,
        files=[{
            "path": "skillevent.txt",
            "tabular_policy": "sidecar",
            "primary_key": "SkillID",
        }],
    )
    db_path = tmp_path / "sidecar.db"

    stats = update_sidecar(db_path, manifest)

    assert stats.refs_upserted == 1
    conn = connect_sidecar(db_path, readonly=True)
    try:
        refs = conn.execute(
            "SELECT target_ref, value FROM tabular_refs ORDER BY target_ref"
        ).fetchall()
        assert [row["target_ref"] for row in refs] == ["scripts/kick.lua"]
        assert all("2021年8月16日" not in row["value"] for row in refs)
    finally:
        conn.close()


def test_search_rows_finds_row_by_indexed_value(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "skills.tab").write_text("SkillID\tName\n100\tKick\n101\tBlock\n", encoding="utf-8")
    manifest = build_manifest(
        tmp_path,
        files=[{
            "path": "skills.tab",
            "tabular_policy": "sidecar",
            "primary_key": "SkillID",
            "indexed_columns": ["Name"],
        }],
    )
    db_path = tmp_path / "sidecar.db"
    update_sidecar(db_path, manifest)

    rows = search_rows(db_path, domain_id=manifest.domain_id, column="Name", value=" Kick ")

    assert len(rows) == 1
    assert rows[0]["row_json"] == {"SkillID": "100", "Name": "Kick"}


def test_search_rows_filters_by_file_and_column_index(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "skills.tab").write_text("ID\tName\tName\n1\tAlpha\tDup\n", encoding="utf-8")
    (repo / "other.tab").write_text("ID\tName\tName\n2\tAlpha\tOther\n", encoding="utf-8")
    manifest = build_manifest(
        tmp_path,
        files=[
            {
                "path": "skills.tab",
                "tabular_policy": "sidecar",
                "primary_key": "ID",
                "indexed_columns": [{"name": "Name", "column_index": 2}],
            },
            {
                "path": "other.tab",
                "tabular_policy": "sidecar",
                "primary_key": "ID",
                "indexed_columns": [{"name": "Name", "column_index": 2}],
            },
        ],
    )
    db_path = tmp_path / "sidecar.db"
    update_sidecar(db_path, manifest)

    rows = search_rows(
        db_path,
        domain_id=manifest.domain_id,
        column="Name",
        value="Alpha",
        file_key=manifest.sidecar_active_files[0].file_key,
        column_index=2,
    )

    assert len(rows) == 1
    assert rows[0]["source_file"] == "skills.tab"
    assert rows[0]["row_json"]["ID"] == "1"


def test_search_rows_rejects_non_indexed_column(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "skills.tab").write_text("ID\tName\n1\tAlpha\n", encoding="utf-8")
    manifest = build_manifest(
        tmp_path,
        files=[{"path": "skills.tab", "tabular_policy": "sidecar", "primary_key": "ID"}],
    )
    db_path = tmp_path / "sidecar.db"
    update_sidecar(db_path, manifest)

    with pytest.raises(ValueError, match="not indexed"):
        search_rows(db_path, domain_id=manifest.domain_id, column="Name", value="Alpha")


def test_resolve_ref_resolves_valid_sidecar_ref(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "skills.tab").write_text("ID\tName\n1\tAlpha\n", encoding="utf-8")
    manifest = build_manifest(
        tmp_path,
        files=[{"path": "skills.tab", "tabular_policy": "sidecar", "primary_key": "ID"}],
    )
    db_path = tmp_path / "sidecar.db"
    update_sidecar(db_path, manifest)
    row = search_rows(db_path, domain_id=manifest.domain_id, column="ID", value="1")[0]

    resolved = resolve_ref(db_path, row["sidecar_ref"], domain_id=manifest.domain_id)

    assert resolved["row_json"] == {"ID": "1", "Name": "Alpha"}
    assert resolved["sidecar_ref"] == row["sidecar_ref"]


def test_resolve_ref_fails_on_invalid_or_stale_ref(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    table = repo / "skills.tab"
    table.write_text("ID\tName\n1\tAlpha\n", encoding="utf-8")
    manifest = build_manifest(
        tmp_path,
        files=[{"path": "skills.tab", "tabular_policy": "sidecar", "primary_key": "ID"}],
    )
    db_path = tmp_path / "sidecar.db"
    update_sidecar(db_path, manifest)
    row = search_rows(db_path, domain_id=manifest.domain_id, column="ID", value="1")[0]
    table.write_text("ID\tName\n1\tBeta\n", encoding="utf-8")
    update_sidecar(db_path, manifest)

    with pytest.raises(ValueError, match="invalid sidecar_ref"):
        resolve_ref(db_path, "not-a-ref", domain_id=manifest.domain_id)
    with pytest.raises(LookupError, match="sidecar_ref not found"):
        resolve_ref(db_path, row["sidecar_ref"], domain_id=manifest.domain_id)


def test_update_sidecar_detects_content_change_and_rebuilds_rows(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    table = repo / "skills.tab"
    table.write_text("ID\tName\n1\tAlpha\n", encoding="utf-8")
    manifest = build_manifest(
        tmp_path,
        files=[{"path": "skills.tab", "tabular_policy": "sidecar", "primary_key": "ID"}],
    )
    db_path = tmp_path / "sidecar.db"
    update_sidecar(db_path, manifest)
    before_meta = read_sidecar_meta(db_path)
    table.write_text("ID\tName\n1\tBeta\n", encoding="utf-8")

    stats = update_sidecar(db_path, manifest)
    rows = search_rows(db_path, domain_id=manifest.domain_id, column="ID", value="1")

    assert stats.rows_upserted == 1
    assert rows[0]["row_json"]["Name"] == "Beta"
    assert read_sidecar_meta(db_path)["sidecar_generation"] != before_meta["sidecar_generation"]


def test_update_sidecar_prunes_orphan_file_when_domain_removes_it(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "skills.tab").write_text("ID\tName\n1\tAlpha\n", encoding="utf-8")
    manifest = build_manifest(
        tmp_path,
        files=[{"path": "skills.tab", "tabular_policy": "sidecar", "primary_key": "ID"}],
    )
    db_path = tmp_path / "sidecar.db"
    update_sidecar(db_path, manifest)
    empty_manifest = build_manifest(tmp_path, files=[])

    stats = update_sidecar(db_path, empty_manifest)

    assert stats.files_pruned == 1
    assert stats.rows_pruned == 1
    conn = connect_sidecar(db_path, readonly=True)
    try:
        assert conn.execute("SELECT COUNT(*) FROM source_files").fetchone()[0] == 0
    finally:
        conn.close()


def test_has_sql_statement_separator_detects_only_top_level_semicolons():
    assert _has_sql_statement_separator("select 1; select 2") is True
    assert _has_sql_statement_separator("select ';' as semi") is False
    assert _has_sql_statement_separator("select 1 -- ;\n") is False
    assert _has_sql_statement_separator("select /* ; */ 1") is False
    assert _has_sql_statement_separator('select ";double" as x') is False
    assert _has_sql_statement_separator("select `; backtick`") is False
    assert _has_sql_statement_separator("select 'it''s fine'") is False
    assert _has_sql_statement_separator('select "a""b"') is False


def test_update_sidecar_noop_when_unchanged(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "skills.tab").write_text("ID\tName\n1\tAlpha\n", encoding="utf-8")
    manifest = build_manifest(
        tmp_path,
        files=[{"path": "skills.tab", "tabular_policy": "sidecar", "primary_key": "ID"}],
    )
    db_path = tmp_path / "sidecar.db"
    update_sidecar(db_path, manifest)

    stats = update_sidecar(db_path, manifest)

    assert stats.rows_upserted == 0
    assert stats.indexes_upserted == 0
    assert stats.refs_upserted == 0
    assert stats.files_pruned == 0


def test_normalize_readonly_sql_accepts_select_and_rejects_writes():
    assert _normalize_readonly_sql("  SELECT 1;  ") == "SELECT 1"
    assert _normalize_readonly_sql("WITH x AS (SELECT 1) SELECT * FROM x") == "WITH x AS (SELECT 1) SELECT * FROM x"
    with pytest.raises(ValueError, match="SELECT or WITH"):
        _normalize_readonly_sql("INSERT INTO rows VALUES (1)")
    with pytest.raises(ValueError, match="exactly one SQL statement"):
        _normalize_readonly_sql("SELECT 1; SELECT 2")


def test_execute_readonly_query_works_with_domain_scoped_views(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "skills.tab").write_text(
        "ID\tName\tScript\n1\tAlpha\tscripts/a.lua\n2\tBeta\tscripts/b.lua\n",
        encoding="utf-8",
    )
    manifest = build_manifest(
        tmp_path,
        files=[{
            "path": "skills.tab",
            "tabular_policy": "sidecar",
            "primary_key": "ID",
            "indexed_columns": ["Name"],
        }],
    )
    db_path = tmp_path / "sidecar.db"
    update_sidecar(db_path, manifest)

    result = execute_readonly_query(
        db_path,
        sql="SELECT source_file, row_no FROM current_domain_rows WHERE json_extract(row_json, '$.Name') = 'Alpha'",
        domain_id=manifest.domain_id,
    )

    assert result["row_count"] == 1
    assert result["rows"] == [{"source_file": "skills.tab", "row_no": 2}]


def test_execute_readonly_query_allows_common_readonly_projection_and_aggregation(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "skills.tab").write_text("ID\tName\n1\tAlpha\n2\tBeta\n", encoding="utf-8")
    manifest = build_manifest(
        tmp_path,
        files=[{"path": "skills.tab", "tabular_policy": "sidecar", "primary_key": "ID"}],
    )
    db_path = tmp_path / "sidecar.db"
    update_sidecar(db_path, manifest)

    projection = execute_readonly_query(
        db_path,
        sql="SELECT row_no FROM current_domain_rows ORDER BY row_no",
        domain_id=manifest.domain_id,
    )
    aggregate = execute_readonly_query(
        db_path,
        sql="SELECT count(*) AS n FROM current_domain_rows",
        domain_id=manifest.domain_id,
    )
    alias = execute_readonly_query(
        db_path,
        sql="SELECT row_no AS rows FROM current_domain_rows ORDER BY row_no",
        domain_id=manifest.domain_id,
    )

    assert projection["rows"] == [{"row_no": 2}, {"row_no": 3}]
    assert aggregate["rows"] == [{"n": 2}]
    assert alias["rows"] == [{"rows": 2}, {"rows": 3}]


def test_execute_readonly_query_rejects_missing_domain_scope(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "skills.tab").write_text("ID\tName\n1\tAlpha\n", encoding="utf-8")
    manifest = build_manifest(
        tmp_path,
        files=[{"path": "skills.tab", "tabular_policy": "sidecar", "primary_key": "ID"}],
    )
    db_path = tmp_path / "sidecar.db"
    update_sidecar(db_path, manifest)

    with pytest.raises(ValueError, match="domain scope not found"):
        execute_readonly_query(
            db_path,
            sql="SELECT source_file FROM current_domain_rows",
            domain_id="missing-domain",
        )


def test_execute_readonly_query_rejects_limit_above_max(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "skills.tab").write_text("ID\tName\n1\tAlpha\n", encoding="utf-8")
    manifest = build_manifest(
        tmp_path,
        files=[{"path": "skills.tab", "tabular_policy": "sidecar", "primary_key": "ID"}],
    )
    db_path = tmp_path / "sidecar.db"
    update_sidecar(db_path, manifest)

    with pytest.raises(ValueError, match="limit exceeds maximum"):
        execute_readonly_query(
            db_path,
            sql="SELECT source_file FROM current_domain_rows",
            domain_id=manifest.domain_id,
            limit=10001,
        )


def test_execute_readonly_query_rejects_direct_physical_table_reads(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "skills.tab").write_text("ID\tName\n1\tAlpha\n", encoding="utf-8")
    manifest = build_manifest(
        tmp_path,
        files=[{"path": "skills.tab", "tabular_policy": "sidecar", "primary_key": "ID"}],
    )
    db_path = tmp_path / "sidecar.db"
    update_sidecar(db_path, manifest)

    with pytest.raises(ValueError, match="physical table"):
        execute_readonly_query(db_path, sql="SELECT * FROM rows", domain_id=manifest.domain_id)

    with pytest.raises(ValueError, match="physical table"):
        execute_readonly_query(db_path, sql="SELECT count(*) FROM source_files", domain_id=manifest.domain_id)

    with pytest.raises(ValueError, match="physical table"):
        execute_readonly_query(
            db_path,
            sql="SELECT count(*) FROM current_domain_rows, source_files",
            domain_id=manifest.domain_id,
        )

    with pytest.raises(ValueError, match="physical table"):
        execute_readonly_query(
            db_path,
            sql="SELECT count(*) FROM main.source_files",
            domain_id=manifest.domain_id,
        )

    with pytest.raises(ValueError, match="physical table"):
        execute_readonly_query(
            db_path,
            sql="SELECT count(*) FROM current_domain_rows UNION ALL SELECT count(*) FROM main.source_files",
            domain_id=manifest.domain_id,
        )

    with pytest.raises(ValueError, match="physical table"):
        execute_readonly_query(
            db_path,
            sql="SELECT count(*) FROM current_domain_rows, main.source_files",
            domain_id=manifest.domain_id,
        )

    with pytest.raises(ValueError, match="physical table"):
        execute_readonly_query(
            db_path,
            sql='SELECT count(*) FROM current_domain_rows UNION ALL SELECT count(*) FROM "source_files"',
            domain_id=manifest.domain_id,
        )

    with pytest.raises(ValueError, match="physical table"):
        execute_readonly_query(
            db_path,
            sql='SELECT count(*) FROM current_domain_rows, main."source_files"',
            domain_id=manifest.domain_id,
        )


def test_schema_version_mismatch_raises_runtime_error(tmp_path):
    db_path = tmp_path / "sidecar.db"
    conn = connect_sidecar(db_path)
    try:
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION + 1),))
        with pytest.raises(RuntimeError, match="incompatible tabular sidecar schema_version"):
            ensure_schema(conn)
    finally:
        conn.close()


def test_sidecar_identity_repo_key_mismatch_raises_runtime_error(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "skills.tab").write_text("ID\tName\n1\tAlpha\n", encoding="utf-8")
    manifest = build_manifest(
        tmp_path,
        files=[{"path": "skills.tab", "tabular_policy": "sidecar", "primary_key": "ID"}],
    )
    db_path = tmp_path / "sidecar.db"
    update_sidecar(db_path, manifest)

    conn = connect_sidecar(db_path)
    try:
        conn.execute("UPDATE meta SET value = ? WHERE key = 'repo_key'", (repo_key_for_root(tmp_path / 'other-repo'),))
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(RuntimeError, match="repo_key"):
        update_sidecar(db_path, manifest)


def test_two_domains_sharing_one_file(tmp_path):
    """Test that two domains can share the same file with different configs."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "shared.tab").write_text("ID\tName\tValue\n1\tAlpha\t100\n2\tBeta\t200\n", encoding="utf-8")

    # First domain
    manifest1 = build_manifest(
        tmp_path,
        domain_id="domain-a",
        files=[{
            "path": "shared.tab",
            "tabular_policy": "sidecar",
            "primary_key": "ID",
            "indexed_columns": ["Name"],
        }],
    )
    db_path = tmp_path / "sidecar.db"
    update_sidecar(db_path, manifest1)

    # Second domain sharing the same file with different config
    manifest2 = build_manifest(
        tmp_path,
        domain_id="domain-b",
        files=[{
            "path": "shared.tab",
            "tabular_policy": "sidecar",
            "primary_key": "ID",
            "indexed_columns": ["Value"],
        }],
    )
    update_sidecar(db_path, manifest2)

    conn = connect_sidecar(db_path, readonly=True)
    try:
        # File should exist once
        assert conn.execute("SELECT COUNT(*) FROM source_files").fetchone()[0] == 1
        # Should have two domain_files entries
        assert conn.execute("SELECT COUNT(*) FROM domain_files").fetchone()[0] == 2
        # Rows should exist (shared)
        assert conn.execute("SELECT COUNT(*) FROM rows").fetchone()[0] == 2
        # Indexed values should include both Name (domain-a) and Value (domain-b)
        indexes = conn.execute("SELECT domain_id, column_name FROM indexed_values ORDER BY domain_id, column_name").fetchall()
        # domain-a: 2 rows * (ID + Name) = 4, domain-b: 2 rows * (ID + Value) = 4, total = 8
        assert len(indexes) == 8
        domain_a_indexes = [r for r in indexes if r["domain_id"] == "domain-a"]
        domain_b_indexes = [r for r in indexes if r["domain_id"] == "domain-b"]
        assert len(domain_a_indexes) == 4  # 2 rows * (ID + Name)
        assert len(domain_b_indexes) == 4  # 2 rows * (ID + Value)
        # Check union_config_hash reflects both domains
        union_hash = conn.execute("SELECT union_config_hash FROM source_files").fetchone()["union_config_hash"]
        assert union_hash != ""
    finally:
        conn.close()


def test_domain_removal_prunes_membership_but_keeps_shared_file(tmp_path):
    """Test that removing a domain from a shared file doesn't delete the file if other domains still use it."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "shared.tab").write_text("ID\tName\n1\tAlpha\n", encoding="utf-8")

    # Domain A and B both use the file
    manifest_a = build_manifest(
        tmp_path,
        domain_id="domain-a",
        files=[{"path": "shared.tab", "tabular_policy": "sidecar", "primary_key": "ID"}],
    )
    manifest_b = build_manifest(
        tmp_path,
        domain_id="domain-b",
        files=[{"path": "shared.tab", "tabular_policy": "sidecar", "primary_key": "ID"}],
    )
    db_path = tmp_path / "sidecar.db"
    update_sidecar(db_path, manifest_a)
    update_sidecar(db_path, manifest_b)

    # Remove the file from domain A
    manifest_a_empty = build_manifest(tmp_path, domain_id="domain-a", files=[])
    stats = update_sidecar(db_path, manifest_a_empty)

    # File should NOT be pruned because domain-b still uses it
    assert stats.files_pruned == 0
    assert stats.rows_pruned == 0

    conn = connect_sidecar(db_path, readonly=True)
    try:
        # File still exists
        assert conn.execute("SELECT COUNT(*) FROM source_files").fetchone()[0] == 1
        # Only domain-b membership remains
        assert conn.execute("SELECT COUNT(*) FROM domain_files").fetchone()[0] == 1
        assert conn.execute("SELECT domain_id FROM domain_files").fetchone()["domain_id"] == "domain-b"
        # Rows still exist
        assert conn.execute("SELECT COUNT(*) FROM rows").fetchone()[0] == 1
    finally:
        conn.close()

    # Now remove from domain B too
    manifest_b_empty = build_manifest(tmp_path, domain_id="domain-b", files=[])
    stats = update_sidecar(db_path, manifest_b_empty)

    # NOW the file should be pruned
    assert stats.files_pruned == 1
    assert stats.rows_pruned == 1

    conn = connect_sidecar(db_path, readonly=True)
    try:
        assert conn.execute("SELECT COUNT(*) FROM source_files").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM domain_files").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM rows").fetchone()[0] == 0
    finally:
        conn.close()


def test_domain_removal_refreshes_shared_file_rows_when_content_changed(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "shared.tab").write_text("ID\tName\n1\tAlpha\n", encoding="utf-8")

    manifest_a = build_manifest(
        tmp_path,
        domain_id="domain-a",
        files=[{"path": "shared.tab", "tabular_policy": "sidecar", "primary_key": "ID"}],
    )
    manifest_b = build_manifest(
        tmp_path,
        domain_id="domain-b",
        files=[{"path": "shared.tab", "tabular_policy": "sidecar", "primary_key": "ID"}],
    )
    db_path = tmp_path / "sidecar.db"
    update_sidecar(db_path, manifest_a)
    update_sidecar(db_path, manifest_b)

    (repo / "shared.tab").write_text("ID\tName\n1\tBeta\n", encoding="utf-8")
    manifest_a_empty = build_manifest(tmp_path, domain_id="domain-a", files=[])

    stats = update_sidecar(db_path, manifest_a_empty)

    assert stats.files_pruned == 0
    assert stats.rows_upserted == 1
    assert search_rows(db_path, domain_id="domain-b", column="ID", value="1")[0]["row_json"]["Name"] == "Beta"


def test_union_index_shrink_on_config_change(tmp_path):
    """Test that changing domain config rebuilds indexes correctly."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "data.tab").write_text("ID\tName\tValue\n1\tAlpha\t100\n", encoding="utf-8")

    # Domain A indexes Name
    manifest_a = build_manifest(
        tmp_path,
        domain_id="domain-a",
        files=[{
            "path": "data.tab",
            "tabular_policy": "sidecar",
            "primary_key": "ID",
            "indexed_columns": ["Name"],
        }],
    )
    db_path = tmp_path / "sidecar.db"
    update_sidecar(db_path, manifest_a)

    conn = connect_sidecar(db_path, readonly=True)
    try:
        # Should have ID and Name indexed
        indexes = conn.execute("SELECT column_name FROM indexed_values ORDER BY column_name").fetchall()
        assert len(indexes) == 2  # ID, Name
        assert set(r["column_name"] for r in indexes) == {"ID", "Name"}
    finally:
        conn.close()

    # Change config to index Value instead of Name
    manifest_a_changed = build_manifest(
        tmp_path,
        domain_id="domain-a",
        files=[{
            "path": "data.tab",
            "tabular_policy": "sidecar",
            "primary_key": "ID",
            "indexed_columns": ["Value"],
        }],
    )
    stats = update_sidecar(db_path, manifest_a_changed)

    # Indexes should be rebuilt
    assert stats.indexes_upserted == 2  # ID, Value

    conn = connect_sidecar(db_path, readonly=True)
    try:
        # Should now have ID and Value indexed, NOT Name
        indexes = conn.execute("SELECT column_name FROM indexed_values ORDER BY column_name").fetchall()
        assert len(indexes) == 2  # ID, Value
        assert set(r["column_name"] for r in indexes) == {"ID", "Value"}
    finally:
        conn.close()


def test_effective_graph_excludes_file_from_sidecar(tmp_path):
    """Test that files with effective_policy='graph' are excluded from sidecar."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "data.tab").write_text("ID\tName\n1\tAlpha\n", encoding="utf-8")

    # Create manifest with declared "auto" but effective "graph" (simulating manifest computation)
    # We need to create this by bypassing the normal manifest loading
    import json
    from graphify.tabular_manifest import TabularFileConfig, TabularDomainManifest, repo_key_for_root

    manifest_json = {
        "repo_root": str(repo),
        "domain_id": "test-domain",
        "files": [{
            "path": "data.tab",
            "tabular_policy": "auto",  # declared as auto
            "primary_key": "ID",
        }],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest_json), encoding="utf-8")

    # Manually construct a manifest with effective_policy='graph'
    from graphify.tabular import TabularProfile, ParsedColumn

    file_cfg = TabularFileConfig(
        path=repo / "data.tab",
        source_file="data.tab",
        file_key="data.tab",
        declared_policy="auto",
        effective_policy="graph",  # effective is graph
        auto_reason="test",
        config_hash="test-hash",
        primary_key=None,
        indexed_columns=(),
        anchor_columns=(),
        profile=TabularProfile(
            row_count=1,
            column_count=2,
            file_size=100,
            columns=(
                ParsedColumn(name="ID", key="id", column_index=0),
                ParsedColumn(name="Name", key="name", column_index=1),
            ),
        ),
    )

    manifest = TabularDomainManifest(
        repo_root=repo,
        repo_key=repo_key_for_root(repo),
        domain_id="test-domain",
        manifest_path=manifest_path,
        files=(file_cfg,),
        sidecar_active_files=(),  # Empty because effective_policy='graph'
        sidecar_domain_config_hash=None,
    )

    db_path = tmp_path / "sidecar.db"
    stats = update_sidecar(db_path, manifest)

    # No files should be upserted (effective=graph means not sidecar)
    assert stats.files_upserted == 0

    conn = connect_sidecar(db_path, readonly=True)
    try:
        assert conn.execute("SELECT COUNT(*) FROM source_files").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM domain_files").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM rows").fetchone()[0] == 0
    finally:
        conn.close()
