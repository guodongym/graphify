# tests/test_tabular_sidecar_cli.py
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys


def run_graphify(*args: str, cwd):
    return subprocess.run(
        [sys.executable, "-m", "graphify", *args],
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def test_sidecar_resolve_requires_ref_or_search_args(tmp_path):
    result = run_graphify("sidecar", "resolve", cwd=tmp_path)

    assert result.returncode == 2
    assert "Usage: graphify sidecar resolve" in result.stderr


def test_manifest_build_creates_sidecar_db_and_search_finds_row(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "skills.tab").write_text("SkillID\tName\n100\tKick\n200\tSlash\n", encoding="utf-8")
    manifest = repo / "domain-files.json"
    manifest.write_text(json.dumps({
        "repo_root": str(repo),
        "domain_id": "skill-core",
        "files": [{
            "path": "skills.tab",
            "tabular_policy": "sidecar",
            "primary_key": "SkillID",
            "indexed_columns": ["Name"],
        }],
    }), encoding="utf-8")
    out = repo / "graphify-out" / "domains" / "skill-core"

    build = run_graphify("extract", "--manifest", str(manifest), "--output-dir", str(out), cwd=repo)
    assert build.returncode == 0, build.stderr
    assert (repo / "graphify-out" / "sidecar" / "tabular.sqlite").exists()

    search = run_graphify(
        "sidecar", "search",
        "--graph", str(out / "graph.json"),
        "--domain", "skill-core",
        "--column", "SkillID",
        "--value", "200",
        cwd=repo,
    )
    assert search.returncode == 0, search.stderr
    payload = json.loads(search.stdout)
    assert payload["rows"][0]["row_json"]["SkillID"] == "200"
    assert payload["rows"][0]["row_json"]["Name"] == "Slash"


def test_raw_sidecar_search_uses_default_active_graphify_output(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "skills.tab").write_text("SkillID\tName\n100\tKick\n200\tSlash\n", encoding="utf-8")
    manifest = repo / "domain-files.json"
    manifest.write_text(json.dumps({
        "repo_root": str(repo),
        "domain_id": "skill-core",
        "files": [{
            "path": "skills.tab",
            "tabular_policy": "sidecar",
            "primary_key": "SkillID",
        }],
    }), encoding="utf-8")
    out = repo / "graphify-out" / "domains" / "skill-core"

    build = run_graphify("extract", "--manifest", str(manifest), "--output-dir", str(out), cwd=repo)
    assert build.returncode == 0, build.stderr

    search = run_graphify(
        "sidecar", "search",
        "--domain", "skill-core",
        "--column", "SkillID",
        "--value", "100",
        cwd=repo,
    )

    assert search.returncode == 0, search.stderr
    payload = json.loads(search.stdout)
    assert payload["db_path"] == str((repo / "graphify-out" / "sidecar" / "tabular.sqlite").resolve())
    assert payload["rows"][0]["row_json"]["Name"] == "Kick"


def test_manifest_build_sidecar_file_does_not_emit_extract_tab_rows(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "skills.tab").write_text("SkillID\tName\n100\tKick\n200\tSlash\n", encoding="utf-8")
    manifest = repo / "domain-files.json"
    manifest.write_text(json.dumps({
        "repo_root": str(repo),
        "domain_id": "skill-core",
        "files": [{
            "path": "skills.tab",
            "tabular_policy": "sidecar",
            "primary_key": "SkillID",
            "anchor_columns": ["SkillID"],
        }],
    }), encoding="utf-8")
    out = repo / "graphify-out" / "domains" / "skill-core"

    build = run_graphify("extract", "--manifest", str(manifest), "--output-dir", str(out), cwd=repo)

    assert build.returncode == 0, build.stderr
    graph = json.loads((out / "graph.json").read_text(encoding="utf-8"))
    nodes = graph["nodes"]
    assert any(node.get("sidecar_ref") for node in nodes)
    assert any(str(node.get("id", "")).startswith("tabular_anchor:") for node in nodes)
    assert not any(node.get("label") in {"row 2", "row 3"} for node in nodes)


def test_manifest_build_routes_lua_and_tabular_sidecar_independently(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "scripts").mkdir()
    (repo / "scripts" / "kick.lua").write_text("return 'kick'\n", encoding="utf-8")
    (repo / "skills.tab").write_text(
        "SkillID\tName\tScript\n100\tKick\tscripts/kick.lua\n",
        encoding="utf-8",
    )
    manifest = repo / "domain-files.json"
    manifest.write_text(json.dumps({
        "repo_root": str(repo),
        "domain_id": "mixed",
        "files": [
            {"path": "scripts/kick.lua"},
            {
                "path": "skills.tab",
                "tabular_policy": "sidecar",
                "primary_key": "SkillID",
                "anchor_columns": ["Name"],
            },
        ],
    }), encoding="utf-8")
    out = repo / "graphify-out" / "domains" / "mixed"

    build = run_graphify("extract", "--manifest", str(manifest), "--output-dir", str(out), cwd=repo)

    assert build.returncode == 0, build.stderr
    graph = json.loads((out / "graph.json").read_text(encoding="utf-8"))
    assert any(node.get("source_file") == "scripts/kick.lua" for node in graph["nodes"])
    assert any(str(node.get("id", "")).startswith("tabular_table:") for node in graph["nodes"])
    assert any(str(node.get("id", "")).startswith("tabular_anchor:") for node in graph["nodes"])
    assert not any(node.get("type") == "extract_tab" for node in graph["nodes"])


def test_manifest_build_prunes_sidecar_db_when_domain_becomes_graph_only(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "skills.tab").write_text("SkillID\tName\n100\tKick\n", encoding="utf-8")
    (repo / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    manifest = repo / "domain-files.json"
    manifest.write_text(json.dumps({
        "repo_root": str(repo),
        "domain_id": "skill-core",
        "files": [{
            "path": "skills.tab",
            "tabular_policy": "sidecar",
            "primary_key": "SkillID",
        }],
    }), encoding="utf-8")
    out = repo / "graphify-out" / "domains" / "skill-core"
    db_path = repo / "graphify-out" / "sidecar" / "tabular.sqlite"

    build = run_graphify("extract", "--manifest", str(manifest), "--output-dir", str(out), cwd=repo)
    assert build.returncode == 0, build.stderr

    manifest.write_text(json.dumps({
        "repo_root": str(repo),
        "domain_id": "skill-core",
        "files": [{"path": "main.py"}],
    }), encoding="utf-8")

    rebuild = run_graphify("extract", "--manifest", str(manifest), "--output-dir", str(out), cwd=repo)
    assert rebuild.returncode == 0, rebuild.stderr

    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM domain_files WHERE domain_id = ?",
            ("skill-core",),
        ).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM source_files").fetchone()[0] == 0
    finally:
        conn.close()

    graph = json.loads((out / "graph.json").read_text(encoding="utf-8"))
    assert "tabular_sidecar" not in graph.get("graph", {})


def test_manifest_build_sidecar_only_domain_does_not_fail_empty_graph_check(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "skills.tab").write_text("SkillID\tName\n100\tKick\n", encoding="utf-8")
    manifest = repo / "domain-files.json"
    manifest.write_text(json.dumps({
        "repo_root": str(repo),
        "domain_id": "tabular-only",
        "files": [{
            "path": "skills.tab",
            "tabular_policy": "sidecar",
            "primary_key": "SkillID",
        }],
    }), encoding="utf-8")
    out = repo / "graphify-out" / "domains" / "tabular-only"

    build = run_graphify("extract", "--manifest", str(manifest), "--output-dir", str(out), cwd=repo)

    assert build.returncode == 0, build.stderr
    graph = json.loads((out / "graph.json").read_text(encoding="utf-8"))
    assert graph["nodes"]
    assert any(str(node.get("id", "")).startswith("tabular_table:") for node in graph["nodes"])
    assert any(str(node.get("id", "")).startswith("tabular_column:") for node in graph["nodes"])
    assert not any(str(node.get("id", "")).startswith("tabular_anchor:") for node in graph["nodes"])
    assert graph["graph"]["tabular_sidecar"]["domain_id"] == "tabular-only"


def test_manifest_build_txt_sidecar_only_domain_does_not_require_code_files(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "skillevent.txt").write_text("SkillID\tName\n100\tKick\n", encoding="utf-8")
    manifest = repo / "domain-files.json"
    manifest.write_text(json.dumps({
        "repo_root": str(repo),
        "domain_id": "skill-text",
        "files": [{
            "path": "skillevent.txt",
            "tabular_policy": "sidecar",
            "primary_key": "SkillID",
        }],
    }), encoding="utf-8")
    out = repo / "graphify-out" / "domains" / "skill-text"

    build = run_graphify("extract", "--manifest", str(manifest), "--output-dir", str(out), cwd=repo)

    assert build.returncode == 0, build.stderr
    graph = json.loads((out / "graph.json").read_text(encoding="utf-8"))
    assert graph["graph"]["tabular_sidecar"]["domain_id"] == "skill-text"
    assert any(node.get("source_file") == "skillevent.txt" for node in graph["nodes"])
    assert any(str(node.get("id", "")).startswith("tabular_table:") for node in graph["nodes"])


def test_manifest_build_accepts_sidecar_db_override(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "skills.tab").write_text("SkillID\tName\n100\tKick\n", encoding="utf-8")
    manifest = repo / "domain-files.json"
    manifest.write_text(json.dumps({
        "repo_root": str(repo),
        "domain_id": "skill-core",
        "files": [{
            "path": "skills.tab",
            "tabular_policy": "sidecar",
            "primary_key": "SkillID",
        }],
    }), encoding="utf-8")
    out = repo / "graphify-out" / "domains" / "skill-core"
    sidecar_db = tmp_path / "custom-sidecar.sqlite"

    build = run_graphify(
        "extract",
        "--manifest", str(manifest),
        "--output-dir", str(out),
        "--sidecar-db", str(sidecar_db),
        cwd=repo,
    )

    assert build.returncode == 0, build.stderr
    assert sidecar_db.exists()
    graph = json.loads((out / "graph.json").read_text(encoding="utf-8"))
    meta = graph["graph"]["tabular_sidecar"]
    assert meta["sidecar_db_hint"]["kind"] == "absolute"
    assert meta["sidecar_db_hint"]["path"] == str(sidecar_db.resolve())
