# tests/test_tabular_manifest.py
from __future__ import annotations

import json

import pytest

from graphify.tabular_manifest import load_tabular_domain_manifest


def write_manifest(tmp_path, payload: dict):
    path = tmp_path / "domain-files.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def test_effective_sidecar_file_enters_sidecar_scope(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    table = repo / "skills.tab"
    table.write_text("SkillID\tName\n100\tKick\n", encoding="utf-8")
    manifest = write_manifest(tmp_path, {
        "repo_root": str(repo),
        "domain_id": "skill-core",
        "files": [{
            "path": "skills.tab",
            "tabular_policy": "sidecar",
            "primary_key": "SkillID",
            "indexed_columns": ["Name"],
            "anchor_columns": [],
        }],
    })

    loaded = load_tabular_domain_manifest(manifest, cwd=tmp_path)

    assert loaded.domain_id == "skill-core"
    assert [f.source_file for f in loaded.sidecar_active_files] == ["skills.tab"]
    assert loaded.files[0].declared_policy == "sidecar"
    assert loaded.files[0].effective_policy == "sidecar"
    assert loaded.files[0].config_hash


def test_effective_graph_file_is_excluded_from_sidecar_scope(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    table = repo / "small.tab"
    table.write_text("ID\tName\n1\tA\n", encoding="utf-8")
    manifest = write_manifest(tmp_path, {
        "repo_root": str(repo),
        "domain_id": "tiny",
        "files": [{"path": "small.tab", "tabular_policy": "graph"}],
    })

    loaded = load_tabular_domain_manifest(manifest, cwd=tmp_path)

    assert loaded.files[0].effective_policy == "graph"
    assert loaded.sidecar_active_files == []
    assert loaded.sidecar_domain_config_hash is None


def test_auto_uses_simple_large_table_thresholds(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    large = repo / "large.tab"
    large.write_text("ID\tName\n" + "".join(f"{i}\tSkill{i}\n" for i in range(2001)), encoding="utf-8")
    wide = repo / "wide.tab"
    wide.write_text("\t".join(f"C{i}" for i in range(81)) + "\n", encoding="utf-8")
    small = repo / "small.tab"
    small.write_text("ID\tName\n1\tA\n", encoding="utf-8")
    manifest = write_manifest(tmp_path, {
        "repo_root": str(repo),
        "domain_id": "auto",
        "files": [
            {"path": "large.tab", "tabular_policy": "auto"},
            {"path": "wide.tab", "tabular_policy": "auto"},
            {"path": "small.tab"},
        ],
    })

    loaded = load_tabular_domain_manifest(manifest, cwd=tmp_path)

    policies = {f.source_file: f.effective_policy for f in loaded.files}
    assert policies == {"large.tab": "sidecar", "wide.tab": "sidecar", "small.tab": "graph"}
    reasons = {f.source_file: f.auto_reason for f in loaded.files}
    assert "row_count>2000" in reasons["large.tab"]
    assert "column_count>80" in reasons["wide.tab"]
    assert reasons["small.tab"] is None


def test_explicit_graph_is_not_overridden_by_auto_threshold(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    large = repo / "large.tab"
    large.write_text("ID\tName\n" + "".join(f"{i}\tSkill{i}\n" for i in range(2001)), encoding="utf-8")
    manifest = write_manifest(tmp_path, {
        "repo_root": str(repo),
        "domain_id": "explicit-graph",
        "files": [{"path": "large.tab", "tabular_policy": "graph"}],
    })

    loaded = load_tabular_domain_manifest(manifest, cwd=tmp_path)

    assert loaded.files[0].declared_policy == "graph"
    assert loaded.files[0].effective_policy == "graph"
    assert loaded.files[0].auto_reason is None
    assert loaded.sidecar_active_files == []


def test_selector_order_does_not_change_config_hash(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    table = repo / "skills.tab"
    table.write_text("SkillID\tName\tScript\n100\tKick\tscripts/kick.lua\n", encoding="utf-8")
    base = {
        "repo_root": str(repo),
        "domain_id": "skill-core",
        "files": [{
            "path": "skills.tab",
            "tabular_policy": "sidecar",
            "primary_key": "SkillID",
            "indexed_columns": ["Name", "Script"],
            "anchor_columns": ["SkillID", "Name"],
        }],
    }
    manifest_a = write_manifest(tmp_path, base)
    loaded_a = load_tabular_domain_manifest(manifest_a, cwd=tmp_path)
    base["files"][0]["indexed_columns"] = ["Script", "Name"]
    base["files"][0]["anchor_columns"] = ["Name", "SkillID"]
    manifest_b = write_manifest(tmp_path, base)
    loaded_b = load_tabular_domain_manifest(manifest_b, cwd=tmp_path)

    assert loaded_a.files[0].config_hash == loaded_b.files[0].config_hash
    assert loaded_a.sidecar_domain_config_hash == loaded_b.sidecar_domain_config_hash


def test_duplicate_header_selector_requires_column_index(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    table = repo / "dup.tab"
    table.write_text("ID\tName\tName\n1\tA\tB\n", encoding="utf-8")
    manifest = write_manifest(tmp_path, {
        "repo_root": str(repo),
        "domain_id": "dup",
        "files": [{
            "path": "dup.tab",
            "tabular_policy": "sidecar",
            "indexed_columns": ["Name"],
        }],
    })

    with pytest.raises(ValueError, match="column_index"):
        load_tabular_domain_manifest(manifest, cwd=tmp_path)


def test_manifest_explicit_filtered_file_fails_closed(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".graphifyignore").write_text("secret.tab\n", encoding="utf-8")
    (repo / "secret.tab").write_text("ID\tName\n1\tHidden\n", encoding="utf-8")
    manifest = write_manifest(tmp_path, {
        "repo_root": str(repo),
        "domain_id": "secure",
        "files": [{"path": "secret.tab", "tabular_policy": "sidecar"}],
    })

    with pytest.raises(ValueError, match="filtered by Graphify file policy"):
        load_tabular_domain_manifest(manifest, cwd=tmp_path)


def test_tsv_file_can_use_sidecar_policy(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "skills.tsv").write_text("SkillID\tName\n1\tKick\n", encoding="utf-8")
    manifest = write_manifest(tmp_path, {
        "repo_root": str(repo),
        "domain_id": "skill-core",
        "files": [{"path": "skills.tsv", "tabular_policy": "sidecar", "primary_key": "SkillID"}],
    })

    loaded = load_tabular_domain_manifest(manifest, cwd=tmp_path)

    assert loaded.sidecar_active_files[0].source_file == "skills.tsv"


def test_domain_id_can_fallback_to_repo_relative_output_dir(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "skills.tab").write_text("SkillID\tName\n1\tKick\n", encoding="utf-8")
    manifest = write_manifest(tmp_path, {
        "repo_root": str(repo),
        "files": [{"path": "skills.tab", "tabular_policy": "sidecar", "primary_key": "SkillID"}],
    })

    loaded = load_tabular_domain_manifest(
        manifest,
        cwd=tmp_path,
        output_dir=repo / "graphify-out" / "domains" / "skill-core",
    )

    assert loaded.domain_id == "graphify-out/domains/skill-core"


def test_output_dir_outside_repo_requires_explicit_domain_id(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "skills.tab").write_text("SkillID\tName\n1\tKick\n", encoding="utf-8")
    manifest = write_manifest(tmp_path, {
        "repo_root": str(repo),
        "files": [{"path": "skills.tab", "tabular_policy": "sidecar", "primary_key": "SkillID"}],
    })

    with pytest.raises(ValueError, match="provide explicit domain_id"):
        load_tabular_domain_manifest(
            manifest,
            cwd=tmp_path,
            output_dir=tmp_path / "external-domain-output",
        )
