# tests/test_tabular_sidecar_staging.py
from __future__ import annotations

import json

from graphify.tabular_manifest import load_tabular_domain_manifest
from graphify.tabular_sidecar import connect_sidecar, read_sidecar_meta, search_rows
from graphify.tabular_sidecar_staging import write_staging_sidecar, merge_domain_staging, cleanup_staging_attempt


def write_manifest(tmp_path, repo, domain_id: str, files: list[dict]):
    manifest = tmp_path / f"{domain_id}.json"
    manifest.write_text(json.dumps({
        "repo_root": str(repo),
        "domain_id": domain_id,
        "files": files,
    }), encoding="utf-8")
    return load_tabular_domain_manifest(manifest, cwd=tmp_path)


def test_write_staging_sidecar_writes_domain_rows_and_meta(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "skills.tab").write_text("ID\tName\n1\tAlpha\n", encoding="utf-8")
    manifest = write_manifest(
        tmp_path,
        repo,
        "skill-core",
        [{"path": "skills.tab", "tabular_policy": "sidecar", "primary_key": "ID"}],
    )
    staging_db = tmp_path / "staging.sqlite"

    stats = write_staging_sidecar(
        staging_db,
        manifest,
        run_id="run-1",
        attempt_id="attempt-1",
    )

    assert stats.files_upserted == 1
    assert stats.rows_upserted == 1
    meta = read_sidecar_meta(staging_db)
    assert meta["staging_schema_version"] == "1"
    assert meta["sidecar_kind"] == "tabular"
    assert meta["domain_id"] == "skill-core"
    assert meta["run_id"] == "run-1"
    assert meta["attempt_id"] == "attempt-1"
    conn = connect_sidecar(staging_db, readonly=True)
    try:
        assert conn.execute("SELECT COUNT(*) FROM domain_files").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM rows").fetchone()[0] == 1
    finally:
        conn.close()


def test_write_staging_sidecar_supports_empty_membership(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    manifest = write_manifest(tmp_path, repo, "graph-only", [{"path": "main.py"}])
    staging_db = tmp_path / "empty.sqlite"

    stats = write_staging_sidecar(
        staging_db,
        manifest,
        run_id="run-2",
        attempt_id="attempt-2",
    )

    assert stats.files_upserted == 0
    meta = read_sidecar_meta(staging_db)
    assert meta["domain_id"] == "graph-only"
    conn = connect_sidecar(staging_db, readonly=True)
    try:
        assert conn.execute("SELECT COUNT(*) FROM domain_files").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM source_files").fetchone()[0] == 0
    finally:
        conn.close()


def test_merge_domain_staging_inserts_domain_rows(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "skills.tab").write_text("ID\tName\n1\tAlpha\n", encoding="utf-8")
    manifest = write_manifest(
        tmp_path,
        repo,
        "skill-core",
        [{"path": "skills.tab", "tabular_policy": "sidecar", "primary_key": "ID", "indexed_columns": ["Name"]}],
    )
    staging_db = tmp_path / "staging.sqlite"
    canonical_db = tmp_path / "canonical.sqlite"
    write_staging_sidecar(staging_db, manifest, run_id="run-1", attempt_id="attempt-1")

    stats = merge_domain_staging("skill-core", staging_db, canonical_db)

    assert stats.files_merged == 1
    assert stats.rows_merged == 1
    rows = search_rows(canonical_db, domain_id="skill-core", column="ID", value="1")
    assert rows[0]["row_json"]["Name"] == "Alpha"


def test_merge_domain_staging_is_idempotent(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "skills.tab").write_text("ID\tName\n1\tAlpha\n", encoding="utf-8")
    manifest = write_manifest(
        tmp_path,
        repo,
        "skill-core",
        [{"path": "skills.tab", "tabular_policy": "sidecar", "primary_key": "ID"}],
    )
    staging_db = tmp_path / "staging.sqlite"
    canonical_db = tmp_path / "canonical.sqlite"
    write_staging_sidecar(staging_db, manifest, run_id="run-1", attempt_id="attempt-1")
    first = merge_domain_staging("skill-core", staging_db, canonical_db)
    second = merge_domain_staging("skill-core", staging_db, canonical_db)

    assert first.generation_changed is True
    assert second.generation_changed is False
    conn = connect_sidecar(canonical_db, readonly=True)
    try:
        assert conn.execute("SELECT COUNT(*) FROM source_files").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM rows").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM domain_files").fetchone()[0] == 1
    finally:
        conn.close()


def test_merge_domain_staging_preserves_shared_file_indexes(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "shared.tab").write_text("ID\tName\tValue\n1\tAlpha\t100\n", encoding="utf-8")
    manifest_a = write_manifest(
        tmp_path,
        repo,
        "domain-a",
        [{"path": "shared.tab", "tabular_policy": "sidecar", "primary_key": "ID", "indexed_columns": ["Name"]}],
    )
    manifest_b = write_manifest(
        tmp_path,
        repo,
        "domain-b",
        [{"path": "shared.tab", "tabular_policy": "sidecar", "primary_key": "ID", "indexed_columns": ["Value"]}],
    )
    canonical_db = tmp_path / "canonical.sqlite"
    staging_a = tmp_path / "a.sqlite"
    staging_b = tmp_path / "b.sqlite"
    write_staging_sidecar(staging_a, manifest_a, run_id="run", attempt_id="a")
    write_staging_sidecar(staging_b, manifest_b, run_id="run", attempt_id="b")

    merge_domain_staging("domain-a", staging_a, canonical_db)
    merge_domain_staging("domain-b", staging_b, canonical_db)

    assert search_rows(canonical_db, domain_id="domain-a", column="Name", value="Alpha")
    assert search_rows(canonical_db, domain_id="domain-b", column="Value", value="100")
    conn = connect_sidecar(canonical_db, readonly=True)
    try:
        assert conn.execute("SELECT COUNT(*) FROM source_files").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM domain_files").fetchone()[0] == 2
        # union_config_hash must reflect both domains' config_hashes
        row = conn.execute("SELECT union_config_hash FROM source_files").fetchone()
        assert row["union_config_hash"] != "", "union_config_hash should be computed after merge"
    finally:
        conn.close()


def test_merge_empty_membership_removes_domain_and_prunes_orphans(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "skills.tab").write_text("ID\tName\n1\tAlpha\n", encoding="utf-8")
    manifest = write_manifest(
        tmp_path,
        repo,
        "skill-core",
        [{"path": "skills.tab", "tabular_policy": "sidecar", "primary_key": "ID"}],
    )
    empty_manifest = write_manifest(tmp_path, repo, "skill-core", [])
    canonical_db = tmp_path / "canonical.sqlite"
    staging_full = tmp_path / "full.sqlite"
    staging_empty = tmp_path / "empty.sqlite"
    write_staging_sidecar(staging_full, manifest, run_id="run", attempt_id="full")
    write_staging_sidecar(staging_empty, empty_manifest, run_id="run", attempt_id="empty")
    merge_domain_staging("skill-core", staging_full, canonical_db)

    stats = merge_domain_staging("skill-core", staging_empty, canonical_db)

    assert stats.files_pruned == 1
    conn = connect_sidecar(canonical_db, readonly=True)
    try:
        assert conn.execute("SELECT COUNT(*) FROM source_files").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM domain_files").fetchone()[0] == 0
    finally:
        conn.close()


def test_merge_rejects_staging_with_older_parser_major_version(tmp_path):
    """Staging parser_version major < canonical parser_version major → merge fails."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "skills.tab").write_text("ID\tName\n1\tAlpha\n", encoding="utf-8")
    manifest = write_manifest(
        tmp_path,
        repo,
        "skill-core",
        [{"path": "skills.tab", "tabular_policy": "sidecar", "primary_key": "ID"}],
    )
    canonical_db = tmp_path / "canonical.sqlite"
    staging_v2 = tmp_path / "v2.sqlite"
    staging_v1 = tmp_path / "v1.sqlite"

    # Build staging with current parser, merge to establish canonical baseline
    write_staging_sidecar(staging_v2, manifest, run_id="run", attempt_id="v2")
    merge_domain_staging("skill-core", staging_v2, canonical_db)

    # Bump canonical parser_version major to simulate a newer Graphify
    conn = connect_sidecar(canonical_db)
    try:
        with conn:
            conn.execute(
                "UPDATE source_files SET parser_version = '2.0.0' WHERE source_file LIKE '%skills.tab'"
            )
    finally:
        conn.close()

    # Build staging with "old" parser (major=1), try to merge
    write_staging_sidecar(staging_v1, manifest, run_id="run", attempt_id="v1")
    conn_v1 = connect_sidecar(staging_v1)
    try:
        with conn_v1:
            conn_v1.execute(
                "UPDATE source_files SET parser_version = '1.0.0' WHERE source_file LIKE '%skills.tab'"
            )
    finally:
        conn_v1.close()

    import pytest

    with pytest.raises(RuntimeError, match="parser_version"):
        merge_domain_staging("skill-core", staging_v1, canonical_db)


def test_cleanup_staging_attempt_removes_only_requested_attempt(tmp_path):
    keep = tmp_path / "staging" / "run" / "keep.sqlite"
    remove = tmp_path / "staging" / "run" / "remove.sqlite"
    keep.parent.mkdir(parents=True)
    keep.write_text("keep", encoding="utf-8")
    remove.write_text("remove", encoding="utf-8")

    cleanup_staging_attempt(remove)

    assert keep.exists()
    assert not remove.exists()
