# Graphify Tabular Sidecar DB Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Graphify-owned tabular sidecar DB for large `.tab`, TSV-like `.txt`, and `.tsv` files so domain graphs can stay small while preserving complete row-level retrieval.

**Architecture:** Phase 0.5 builds on Phase 0 manifest-domain build. Manifest files choose an effective tabular policy (`graph`, `sidecar`, `auto`); effective `sidecar` files are ingested into a shared SQLite DB under the active Graphify output and write the old `hybrid`-equivalent graph anchors/refs/`sidecar_ref`, while effective `graph` files stay on the existing extractor path. A sidecar-active file must not also run through `extract_tab`; sidecar projection is the only graph producer for that file's table/column/anchor/path-ref skeleton. Graph-aware `sidecar search/resolve` validates graph metadata against the sidecar DB before returning rows. `sidecar query` adds a read-only Agent SQL/debug surface over domain-scoped views for eval analysis and relationship discovery; it does not replace `search/resolve` as the stable business interface.

**Tech Stack:** Python 3.10+, stdlib `sqlite3`, existing Graphify manifest/build/report/watch code, existing `graphify.tabular` and `.tab` extraction helpers, `pytest`.

---

**Source Spec:** `docs/superpowers/specs/2026-05-26-tabular-sidecar-db-design.md`

**Phase Gate:** Phase 0 manifest/output-dir support is now implemented on `hanhai-v8`. It exposes a manifest-mode file list, repo root, exact output directory, and manifest-mode state. Phase 0 does **not** expose `domain_id` or tabular file-level metadata; Phase 0.5 must parse those from the caller manifest itself.

**Stop Condition:** After all tasks pass, stop and report evidence. Do not add JX3 wrapper-specific relationship rules in Graphify.

## Current Phase 0 Integration Reality

- Manifest CLI dispatch is in `graphify/__main__.py` through `_maybe_run_manifest_code_build(...)`.
- The shared manifest build runner is `graphify/code_build_runner.py::build_code_graph(...)`.
- `graphify/manifest.py::load_domain_manifest(...)` intentionally accepts only `repo_root` plus `files[].path` and ignores unknown caller fields. Phase 0.5 cannot rely on `DomainManifest` for `domain_id`, `tabular_policy`, selectors, or sidecar config hashes.
- Sidecar build integration must extend `code_build_runner` with optional tabular parameters. Do not create a separate wrapper or `domain_build.py` path that bypasses the current manifest runner.
- `--output-dir` is the exact domain graph output. The default sidecar DB belongs under the active Graphify output (`GRAPHIFY_OUT` or `<repo_root>/graphify-out`), not under the domain `--output-dir`.
- `_parse_manifest_cli_args(...)` currently rejects unknown manifest options. Phase 0.5 must add `--sidecar-db` parsing there before any tests expecting a custom sidecar DB path can pass.

## File Structure

- Create `graphify/tabular_manifest.py`: parse and normalize manifest tabular fields, resolve `auto` to effective policy, compute canonical config hashes.
- Extend `graphify/tabular.py`: shared decoding, TSV parser, duplicate header selectors, raw line canonicalization, `line_hash`, and path-like ref extraction.
- Create `graphify/tabular_sidecar.py`: SQLite schema, migration/version check, update/prune transaction, readonly search/resolve/query, repo identity and DB identity validation.
- Create `graphify/tabular_graph.py`: graph metadata and anchors/refs generated from sidecar-ingested files.
- Modify `graphify/code_build_runner.py`: call sidecar update before graph publish and merge sidecar graph metadata into the manifest build output.
- Modify `graphify/__main__.py`: parse sidecar CLI commands and wire manifest build flags to the sidecar integration.
- Modify `graphify/report.py`: render sidecar current snapshot and last run delta.
- Modify `graphify/watch.py`: keep ordinary watch as rebuild-signal only for tabular sidecar files without manifest context.
- Add `tests/test_tabular_manifest.py`, `tests/test_tabular_parser.py`, `tests/test_tabular_sidecar_store.py`, `tests/test_tabular_sidecar_cli.py`, `tests/test_tabular_sidecar_build.py`, and fixtures under `tests/fixtures/sidecar/`.

## Task 1: Failing Manifest Policy And CLI Tests

**Files:**
- Create: `tests/test_tabular_manifest.py`
- Create: `tests/test_tabular_sidecar_cli.py`
- Create fixtures under: `tests/fixtures/sidecar/generic/`

- [ ] **Step 1: Add policy parsing tests**

```python
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
```

- [ ] **Step 2: Add CLI smoke tests for new subcommands**

```python
# tests/test_tabular_sidecar_cli.py
from __future__ import annotations

import json
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
```

- [ ] **Step 3: Run tests and confirm expected failure**

Run:

```bash
uv run pytest tests/test_tabular_manifest.py tests/test_tabular_sidecar_cli.py -q
```

Expected: failures mention missing `graphify.tabular_manifest` and missing `graphify sidecar` command.

## Task 2: Tabular Manifest Normalization

**Files:**
- Create: `graphify/tabular_manifest.py`
- Test: `tests/test_tabular_manifest.py`

Implementation dependency: selector normalization and canonical `config_hash` require parsed header metadata from the tabular parser. Implement the Task 3 lightweight `read_tabular_profile()` helper before completing selector validation; the initial dataclasses and raw manifest parsing can still be scaffolded first. Do not call full `parse_tabular_file()` only to normalize selectors, because large JX3 tables would be parsed again during sidecar ingestion.

Current Phase 0 loader note: `graphify.manifest.load_domain_manifest()` deliberately ignores unknown manifest fields. `load_tabular_domain_manifest()` must read the raw caller manifest JSON itself, or Phase 0 must be explicitly extended to expose raw manifest entries. The Phase 0 `DomainManifest` output is useful for code-file materialization, but it is not a source of truth for sidecar policy or `domain_id`.

- [ ] **Step 1: Implement policy models and canonical hashes**

```python
# graphify/tabular_manifest.py
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from graphify.tabular import TabularProfile

TabularPolicy = Literal["graph", "sidecar", "auto"]
EffectivePolicy = Literal["graph", "sidecar"]


@dataclass(frozen=True)
class ColumnSelector:
    name: str
    normalized_name: str
    column_index: int | None = None


@dataclass(frozen=True)
class TabularFileConfig:
    path: Path
    source_file: str
    file_key: str
    declared_policy: TabularPolicy
    effective_policy: EffectivePolicy
    primary_key: ColumnSelector | None
    indexed_columns: tuple[ColumnSelector, ...]
    anchor_columns: tuple[ColumnSelector, ...]
    profile: TabularProfile
    auto_reason: str | None
    config_hash: str


@dataclass(frozen=True)
class TabularDomainManifest:
    manifest_path: Path
    repo_root: Path
    repo_key: str
    domain_id: str
    files: tuple[TabularFileConfig, ...]
    sidecar_active_files: tuple[TabularFileConfig, ...]
    sidecar_domain_config_hash: str | None


def canonical_json_hash(payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def compute_sidecar_domain_config_hash(
    domain_id: str | None,
    files: Sequence[TabularFileConfig] | Sequence[Mapping[str, str]],
) -> str | None:
    if not files:
        return None
    normalized = []
    for item in files:
        if isinstance(item, Mapping):
            source_file = str(item["source_file"])
            config_hash = str(item["config_hash"])
        else:
            source_file = item.source_file
            config_hash = item.config_hash
        normalized.append({"source_file": source_file, "config_hash": config_hash})
    normalized.sort(key=lambda row: row["source_file"])
    return canonical_json_hash({
        "domain_id": domain_id,
        "files": normalized,
    })


def _selector(raw: str | dict | None, *, field_name: str) -> ColumnSelector | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        return ColumnSelector(name=raw, normalized_name="")
    if isinstance(raw, dict) and isinstance(raw.get("name"), str):
        idx = raw.get("column_index")
        if idx is not None and (not isinstance(idx, int) or idx < 1):
            raise ValueError(f"{field_name}.column_index must be a 1-based positive integer")
        return ColumnSelector(name=raw["name"], normalized_name="", column_index=idx)
    raise ValueError(f"{field_name} must be a string or {{name, column_index}} object")


def _selectors(raw: object, *, field_name: str) -> tuple[ColumnSelector, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError(f"{field_name} must be a list")
    out = []
    for item in raw:
        sel = _selector(item, field_name=field_name)
        if sel is not None:
            out.append(sel)
    return tuple(out)


def _source_file(path: Path, repo_root: Path) -> str:
    return path.resolve().relative_to(repo_root).as_posix()


def _tabular_profile(path: Path) -> TabularProfile:
    from graphify.tabular import read_tabular_profile

    return read_tabular_profile(path)


def _auto_reason(profile: TabularProfile) -> str | None:
    reasons = []
    if profile.row_count > 2_000:
        reasons.append(f"row_count>{2_000}")
    if profile.column_count > 80:
        reasons.append("column_count>80")
    if profile.file_size > 1 * 1024 * 1024:
        reasons.append("file_size>1MB")
    return ",".join(reasons) if reasons else None


def _effective_policy(declared: TabularPolicy, *, profile: TabularProfile) -> EffectivePolicy:
    if declared != "auto":
        return declared
    return "sidecar" if _auto_reason(profile) else "graph"


def _selector_payload(selector: ColumnSelector | None) -> dict | None:
    if selector is None:
        return None
    return {
        "name": selector.name,
        "normalized_name": selector.normalized_name,
        "column_index": selector.column_index,
    }


def _selector_payloads(selectors: tuple[ColumnSelector, ...]) -> list[dict]:
    return sorted(
        (_selector_payload(s) for s in selectors),
        key=lambda item: (item["normalized_name"], item["column_index"] or 0, item["name"]),
    )


def _normalise_selector(
    selector: ColumnSelector | None,
    columns,
    *,
    source_file: str,
    field_name: str,
) -> ColumnSelector | None:
    if selector is None:
        return None
    if selector.column_index is not None:
        found = next((col for col in columns if col.column_index == selector.column_index), None)
        if found is None or found.name != selector.name:
            raise ValueError(f"{source_file}: {field_name} {selector.name!r} column_index does not match a header")
        return ColumnSelector(name=found.name, normalized_name=found.key, column_index=found.column_index)
    matches = [col for col in columns if col.name == selector.name]
    if not matches:
        raise ValueError(f"{source_file}: {field_name} {selector.name!r} does not exist")
    if len(matches) > 1:
        raise ValueError(f"{source_file}: selector {selector.name!r} is ambiguous; provide 1-based column_index")
    found = matches[0]
    return ColumnSelector(name=found.name, normalized_name=found.key, column_index=found.column_index)


def _normalise_selectors(
    selectors: tuple[ColumnSelector, ...],
    columns,
    *,
    source_file: str,
    field_name: str,
) -> tuple[ColumnSelector, ...]:
    normalized = [
        sel
        for sel in (
            _normalise_selector(selector, columns, source_file=source_file, field_name=field_name)
            for selector in selectors
        )
        if sel is not None
    ]
    return tuple(sorted(normalized, key=lambda sel: (sel.normalized_name, sel.column_index or 0, sel.name)))


def repo_key_for_root(repo_root: Path) -> str:
    return canonical_json_hash({"repo_root": str(repo_root.resolve())})


def _file_key(repo_key: str, source_file: str) -> str:
    return canonical_json_hash({"repo_key": repo_key, "source_file": source_file})[:24]


def _default_domain_id(repo_root: Path, output_dir: Path | None, explicit: object) -> str:
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    if output_dir is None:
        raise ValueError("manifest requires domain_id when output_dir is unavailable")
    try:
        rel = output_dir.resolve().relative_to(repo_root).as_posix()
    except ValueError as exc:
        raise ValueError("manifest output directory is outside repo_root; provide explicit domain_id") from exc
    return rel


def load_tabular_domain_manifest(
    manifest_path: Path,
    *,
    cwd: Path | None = None,
    output_dir: Path | None = None,
) -> TabularDomainManifest:
    cwd = (cwd or Path.cwd()).resolve()
    manifest_path = manifest_path.resolve()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    repo_raw = payload.get("repo_root")
    if not repo_raw:
        raise ValueError("manifest requires repo_root")
    repo_root = Path(repo_raw)
    if not repo_root.is_absolute():
        repo_root = (cwd / repo_root).resolve()
    else:
        repo_root = repo_root.resolve()
    repo_key = repo_key_for_root(repo_root)
    domain_id = _default_domain_id(repo_root, output_dir, payload.get("domain_id"))

    files: list[TabularFileConfig] = []
    for entry in payload.get("files", []):
        raw_path = entry.get("path")
        if not isinstance(raw_path, str):
            raise ValueError("files[].path must be a string")
        path = (repo_root / raw_path).resolve() if not Path(raw_path).is_absolute() else Path(raw_path).resolve()
        try:
            source_file = _source_file(path, repo_root)
        except ValueError as exc:
            raise ValueError(f"manifest file escapes repo_root: {raw_path}") from exc
        _validate_manifest_file_allowed(path, repo_root)
        suffix = path.suffix.lower()
        if suffix not in {".tab", ".tsv", ".txt"}:
            raise ValueError(f"{source_file}: tabular sidecar only supports .tab, .tsv, and TSV-like .txt")
        if suffix == ".txt":
            from graphify.tabular import looks_like_tabular_text
            if not looks_like_tabular_text(path):
                raise ValueError(f"{source_file}: .txt file is not TSV-like enough for tabular sidecar")
        declared = entry.get("tabular_policy", "auto")
        if declared not in {"graph", "sidecar", "auto"}:
            raise ValueError(f"unsupported tabular_policy: {declared}")
        profile = _tabular_profile(path)
        effective = _effective_policy(declared, profile=profile)
        auto_reason = _auto_reason(profile) if declared == "auto" else None
        primary_key = _selector(entry.get("primary_key"), field_name="primary_key")
        indexed_columns = _selectors(entry.get("indexed_columns"), field_name="indexed_columns")
        anchor_columns = _selectors(entry.get("anchor_columns"), field_name="anchor_columns")
        primary_key = _normalise_selector(primary_key, profile.columns, source_file=source_file, field_name="primary_key")
        indexed_columns = _normalise_selectors(indexed_columns, profile.columns, source_file=source_file, field_name="indexed_columns")
        anchor_columns = _normalise_selectors(anchor_columns, profile.columns, source_file=source_file, field_name="anchor_columns")
        config_payload = {
            "schema_version": 1,
            "parser_version": 1,
            "declared_policy": declared,
            "effective_policy": effective,
            "auto_reason": auto_reason,
            "primary_key": _selector_payload(primary_key),
            "indexed_columns": _selector_payloads(indexed_columns),
            "anchor_columns": _selector_payloads(anchor_columns),
        }
        file_key = _file_key(repo_key, source_file)
        files.append(TabularFileConfig(
            path=path,
            source_file=source_file,
            file_key=file_key,
            declared_policy=declared,
            effective_policy=effective,
            primary_key=primary_key,
            indexed_columns=indexed_columns,
            anchor_columns=anchor_columns,
            profile=profile,
            auto_reason=auto_reason,
            config_hash=canonical_json_hash(config_payload),
        ))

    sidecar_active = tuple(f for f in files if f.effective_policy == "sidecar")
    domain_hash = compute_sidecar_domain_config_hash(domain_id, sidecar_active)
    return TabularDomainManifest(
        manifest_path=manifest_path,
        repo_root=repo_root,
        repo_key=repo_key,
        domain_id=domain_id,
        files=tuple(files),
        sidecar_active_files=sidecar_active,
        sidecar_domain_config_hash=domain_hash,
    )
```

- [ ] **Step 2: Add file-policy and duplicate-header validation**

Use the same ignore/sensitive-file helpers as Phase 0 manifest mode:

```python
def _validate_manifest_file_allowed(path: Path, repo_root: Path) -> None:
    from graphify.detect import _is_ignored, _is_noise_dir, _is_sensitive, _load_graphifyignore

    patterns = _load_graphifyignore(repo_root)
    rel = path.resolve().relative_to(repo_root).as_posix()
    if any(_is_noise_dir(part) for part in path.resolve().relative_to(repo_root).parts[:-1]):
        raise ValueError(f"{rel}: filtered by Graphify file policy")
    if _is_ignored(path, repo_root, patterns) or _is_sensitive(path):
        raise ValueError(f"{rel}: filtered by Graphify file policy")
    if not path.exists():
        raise ValueError(f"{rel}: file not found")
```

Use `graphify.tabular.parse_tabular_file` from Task 3 once available. If a selector references a header name that appears more than once and has no `column_index`, raise:

```python
raise ValueError(f"{source_file}: selector {selector.name!r} is ambiguous; provide 1-based column_index")
```

- [ ] **Step 3: Run manifest tests**

Run:

```bash
uv run pytest tests/test_tabular_manifest.py -q
```

Expected: pass, including simple `auto` large/small table thresholds, auto reason recording, explicit large `graph` not being overridden, duplicate-header selector validation, selector-order-insensitive `config_hash`, and identical `sidecar_domain_config_hash` when sidecar-active manifest entries are reordered.

## Task 3: Shared Tabular Parser And Stable Row Identity

**Files:**
- Modify: `graphify/tabular.py`
- Modify: `graphify/extract.py`
- Test: `tests/test_tabular_parser.py`
- Existing tests: `tests/test_tab.py`

- [ ] **Step 1: Add parser dataclasses and no-replacement decode**

```python
# graphify/tabular.py
from dataclasses import dataclass
import csv
import hashlib
from io import StringIO
from pathlib import Path


@dataclass(frozen=True)
class ParsedColumn:
    name: str
    key: str
    column_index: int


@dataclass(frozen=True)
class ParsedRow:
    row_no: int
    raw_line: str
    line_hash: str
    values: tuple[str, ...]
    row_json: dict[str, str]
    row_values_json: list[dict[str, object]]


@dataclass(frozen=True)
class ParsedTabularFile:
    source_sha256: str
    encoding: str
    columns: tuple[ParsedColumn, ...]
    rows: tuple[ParsedRow, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class TabularProfile:
    row_count: int
    column_count: int
    file_size: int
    columns: tuple[ParsedColumn, ...]


def decode_structured_text_strict(raw: bytes) -> tuple[str, str]:
    if raw.startswith(b"\xff\xfe"):
        text = raw.decode("utf-16le")
        return text.lstrip("\ufeff"), "utf-16le"
    sample = raw[:4096]
    if sample[1::2].count(0) > max(8, len(sample) // 4):
        text = raw.decode("utf-16le")
        if "\x00" not in text:
            return text.lstrip("\ufeff"), "utf-16le"
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("structured-text", raw, 0, min(len(raw), 1), "unsupported encoding")


def _line_hash(source_file: str, row_no: int, raw_line: str) -> str:
    payload = f"{source_file}\0{row_no}\0{raw_line}".encode("utf-8", errors="surrogatepass")
    return hashlib.sha256(payload).hexdigest()


def tabular_source_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
```

- [ ] **Step 2: Implement lightweight profile parsing**

`read_tabular_profile()` shares the same strict decoder and header normalization as `parse_tabular_file()`, but it does not materialize `ParsedRow` objects. Manifest normalization uses this helper to resolve `primary_key`/`indexed_columns`/`anchor_columns` without paying the full ingestion cost twice for large JX3 tables. MVP can still scan the full file once to count rows and columns; it must not allocate full row objects during profiling. A future streaming profile helper can reduce memory further without changing the manifest contract.

```python
def _parse_columns(raw_headers: list[str]) -> tuple[ParsedColumn, ...]:
    seen: dict[str, int] = {}
    columns: list[ParsedColumn] = []
    for idx, raw_name in enumerate(raw_headers, start=1):
        name = raw_name.strip().strip("\r") or f"column_{idx}"
        count = seen.get(name, 0) + 1
        seen[name] = count
        key = name if count == 1 else f"{name}_{count}"
        columns.append(ParsedColumn(name=name, key=key, column_index=idx))
    return tuple(columns)


def read_tabular_profile(path: Path) -> TabularProfile:
    raw = path.read_bytes()
    text, _encoding = decode_structured_text_strict(raw)
    parsed = csv.reader(StringIO(text), delimiter="\t")
    header = next(parsed, [])
    row_count = sum(1 for _ in parsed)
    columns = _parse_columns(header)
    return TabularProfile(
        row_count=row_count,
        column_count=len(columns),
        file_size=len(raw),
        columns=columns,
    )
```

- [ ] **Step 3: Implement `parse_tabular_file`**

```python
def parse_tabular_file(path: Path, *, source_file: str | None = None) -> ParsedTabularFile:
    raw = path.read_bytes()
    source_sha256 = hashlib.sha256(raw).hexdigest()
    text, encoding = decode_structured_text_strict(raw)
    source_file = source_file or path.as_posix()
    physical_lines = text.splitlines()
    reader = csv.reader(StringIO(text), delimiter="\t")
    parsed = list(reader)
    if not parsed:
        return ParsedTabularFile(source_sha256, encoding, (), (), ())

    columns = _parse_columns(parsed[0])

    rows: list[ParsedRow] = []
    width = len(columns)
    for row_no, values in enumerate(parsed[1:], start=2):
        padded = tuple((values + [""] * max(0, width - len(values)))[:width])
        raw_without_eol = physical_lines[row_no - 1] if row_no - 1 < len(physical_lines) else "\t".join(values)
        raw_line = raw_without_eol.rstrip("\r") + "\n"
        row_json = {col.key: padded[col.column_index - 1] for col in columns}
        row_values_json = [
            {
                "name": col.name,
                "normalized_name": col.key,
                "key": col.key,
                "column_index": col.column_index,
                "value": padded[col.column_index - 1],
            }
            for col in columns
        ]
        rows.append(ParsedRow(
            row_no=row_no,
            raw_line=raw_line,
            line_hash=_line_hash(source_file, row_no, raw_line),
            values=padded,
            row_json=row_json,
            row_values_json=row_values_json,
        ))
    return ParsedTabularFile(source_sha256, encoding, columns, tuple(rows), ())
```

- [ ] **Step 4: Export generic path-like value detection**

Move the existing `_normalise_tab_path_value`, `_is_tab_url_value`, and `_looks_like_tab_path` logic from `graphify/extract.py` into `graphify/tabular.py` as public helpers:

```python
TABULAR_PATH_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".lua", ".luau", ".lh", ".toc",
    ".tab", ".ini", ".txt", ".md", ".xml", ".yaml", ".yml",
}


def normalise_tabular_path_value(value: str) -> str:
    return value.strip().strip('"\'').replace("\\", "/")


def normalise_tabular_value(value: str) -> str:
    return value.strip()


def looks_like_tabular_path_value(value: str) -> bool:
    cleaned = normalise_tabular_path_value(value)
    if not cleaned or len(cleaned) > 512:
        return False
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", cleaned):
        return False
    suffix = Path(cleaned).suffix.lower()
    if suffix in TABULAR_PATH_EXTENSIONS:
        return True
    if "/" in cleaned:
        parts = [p for p in cleaned.split("/") if p and p not in (".", "..")]
        return len(parts) >= 2 and any("." in p for p in parts[-1:])
    return False
```

Then update `graphify/extract.py` to import and use these helpers so the graph extractor and sidecar store share the same generic path-ref semantics.

- [ ] **Step 5: Register `.tsv` as tabular structured input**

Update `graphify/detect.py` and `graphify/extract.py` so `.tsv` is classified on the code/tabular structured path and dispatched to `extract_tab`:

```python
# graphify/detect.py
# Add ".tsv" to the existing CODE_EXTENSIONS literal set.
```

```python
# graphify/extract.py
_DISPATCH[".tsv"] = extract_tab
```

Keep TSV-like `.txt` conservative detection unchanged.

- [ ] **Step 6: Keep existing extractor behavior compatible**

Change `extract_tab` to call `decode_structured_text(path.read_bytes(), replace=True)` only for legacy graph extraction, while sidecar ingestion calls `parse_tabular_file` and fails on decode errors. Keep existing `tests/test_tab.py` expectations unchanged.

- [ ] **Step 7: Run parser and existing tab tests**

Run:

```bash
uv run pytest tests/test_tabular_parser.py tests/test_tab.py -q
```

Expected: pass, including GB18030 and UTF-8 BOM cases.

## Task 4: SQLite Sidecar Store

**Files:**
- Create: `graphify/tabular_sidecar.py`
- Test: `tests/test_tabular_sidecar_store.py`

- [ ] **Step 1: Implement schema and foreign-key self-check**

```python
# graphify/tabular_sidecar.py
from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from graphify.tabular import TabularProfile, parse_tabular_file, tabular_source_sha256
from graphify.tabular_manifest import (
    ColumnSelector,
    TabularDomainManifest,
    TabularFileConfig,
    canonical_json_hash,
    compute_sidecar_domain_config_hash,
)

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class SidecarUpdateStats:
    files_upserted: int
    rows_upserted: int
    indexes_upserted: int
    refs_upserted: int
    files_pruned: int
    rows_pruned: int


@dataclass(frozen=True)
class SidecarFileMeta:
    file_id: int
    source_file: str
    file_key: str
    source_sha256: str
    union_config_hash: str
    domain_file_config_hash: str
    config_json: dict


@dataclass(frozen=True)
class SidecarProjectionRow:
    file_meta: SidecarFileMeta
    table_id: int
    row_id: int
    row_no: int
    line_hash: str
    row_json: dict
    row_values_json: list[dict]
    path_refs: tuple[dict, ...]


@dataclass(frozen=True)
class SidecarTableProjection:
    table_id: int
    kind: str
    delimiter: str
    header_row: int
    row_count: int
    column_count: int
    warnings: tuple[str, ...]
    columns: tuple[dict, ...]


def deterministic_sidecar_db_id(repo_key: str) -> str:
    return canonical_json_hash({
        "repo_key": repo_key,
        "sidecar_kind": "tabular",
        "schema_version": SCHEMA_VERSION,
    })


def connect_sidecar(db_path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        uri = f"file:{db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
    else:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    if conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise RuntimeError("SQLite foreign_keys must be ON")
    if not readonly:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
    return conn


def sidecar_lock_path(db_path: Path) -> Path:
    return db_path.parent / ".tabular-sidecar.lock"


class SidecarWriteLock:
    def __init__(self, db_path: Path):
        self.path = sidecar_lock_path(db_path)
        self._fh = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a+", encoding="utf-8")
        try:
            import fcntl
        except ImportError:
            # Match existing Graphify lock behavior on platforms without fcntl:
            # keep the lock file for diagnostics and rely on SQLite's writer lock.
            return self
        fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._fh is not None:
            try:
                import fcntl
            except ImportError:
                pass
            else:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            self._fh.close()
        return False


def read_sidecar_meta(db_path: Path) -> dict[str, str]:
    conn = connect_sidecar(db_path, readonly=True)
    try:
        return {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM meta")}
    finally:
        conn.close()


def _get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return None if row is None else str(row["value"])


def _set_meta(conn: sqlite3.Connection, key: str, value: object) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)",
        (key, str(value)),
    )


def _normalize_column_name(name: str) -> str:
    return name.strip()


def _normalize_search_value(value: str) -> str:
    from graphify.tabular import normalise_tabular_value

    return normalise_tabular_value(value)


def read_sidecar_domain_config_hash(db_path: Path, domain_id: str) -> str | None:
    conn = connect_sidecar(db_path, readonly=True)
    try:
        rows = conn.execute("""
            SELECT sf.source_file, df.config_hash
            FROM domain_files df
            JOIN source_files sf ON sf.file_id = df.file_id
            WHERE df.domain_id = ?
            ORDER BY sf.source_file
        """, (domain_id,)).fetchall()
    finally:
        conn.close()
    return compute_sidecar_domain_config_hash(domain_id, [
        {"source_file": row["source_file"], "config_hash": row["config_hash"]}
        for row in rows
    ])


def read_sidecar_file_meta(db_path: Path, domain_id: str, file_key: str) -> SidecarFileMeta | None:
    conn = connect_sidecar(db_path, readonly=True)
    try:
        row = conn.execute("""
            SELECT
              sf.file_id, sf.source_file, sf.file_key, sf.sha256, sf.union_config_hash,
              df.config_hash, df.config_json
            FROM source_files sf
            JOIN domain_files df ON df.file_id = sf.file_id
            WHERE df.domain_id = ? AND sf.file_key = ?
        """, (domain_id, file_key)).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return SidecarFileMeta(
        file_id=int(row["file_id"]),
        source_file=str(row["source_file"]),
        file_key=str(row["file_key"]),
        source_sha256=str(row["sha256"]),
        union_config_hash=str(row["union_config_hash"]),
        domain_file_config_hash=str(row["config_hash"]),
        config_json=json.loads(row["config_json"]),
    )


def read_sidecar_table_projection(
    db_path: Path,
    *,
    domain_id: str,
    file_key: str,
) -> tuple[SidecarTableProjection, ...]:
    conn = connect_sidecar(db_path, readonly=True)
    try:
        tables = conn.execute("""
            SELECT tt.table_id, tt.kind, tt.delimiter, tt.header_row,
                   tt.row_count, tt.column_count, tt.warnings_json
            FROM source_files sf
            JOIN domain_files df ON df.file_id = sf.file_id
            JOIN tabular_tables tt ON tt.file_id = sf.file_id
            WHERE df.domain_id = ? AND sf.file_key = ?
            ORDER BY tt.table_id
        """, (domain_id, file_key)).fetchall()
        result: list[SidecarTableProjection] = []
        for table in tables:
            columns = conn.execute("""
                SELECT column_index, name, normalized_name
                FROM tabular_columns
                WHERE table_id = ?
                ORDER BY column_index
            """, (table["table_id"],)).fetchall()
            result.append(SidecarTableProjection(
                table_id=int(table["table_id"]),
                kind=str(table["kind"]),
                delimiter=str(table["delimiter"]),
                header_row=int(table["header_row"]),
                row_count=int(table["row_count"]),
                column_count=int(table["column_count"]),
                warnings=tuple(json.loads(table["warnings_json"])),
                columns=tuple(dict(col) for col in columns),
            ))
        return tuple(result)
    finally:
        conn.close()


def iter_sidecar_graph_projection(
    db_path: Path,
    *,
    domain_id: str,
    file_key: str,
) -> Iterator[SidecarProjectionRow]:
    # Use a two-pass row/ref load instead of a LEFT JOIN so wide row_json and
    # row_values_json blobs are not duplicated once per path ref. If JX3 smoke
    # shows this row fetch is still too large, keep this iterator contract and
    # convert the row query to a streaming/windowed loop.
    conn = connect_sidecar(db_path, readonly=True)
    try:
        rows = conn.execute("""
            SELECT
              sf.file_id, sf.source_file, sf.file_key, sf.sha256, sf.union_config_hash,
              df.config_hash, df.config_json,
              r.table_id, r.row_id, r.row_no, r.line_hash, r.row_json, r.row_values_json
            FROM source_files sf
            JOIN domain_files df ON df.file_id = sf.file_id
            JOIN rows r ON r.file_id = sf.file_id
            WHERE df.domain_id = ? AND sf.file_key = ?
            ORDER BY r.row_no
        """, (domain_id, file_key)).fetchall()
        refs_by_row_id: dict[int, list[dict]] = {int(row["row_id"]): [] for row in rows}
        row_ids = list(refs_by_row_id)
        for offset in range(0, len(row_ids), 1000):
            chunk = row_ids[offset:offset + 1000]
            placeholders = ",".join("?" for _ in chunk)
            ref_rows = conn.execute(f"""
                SELECT row_id, target_ref, value
                FROM tabular_refs
                WHERE row_id IN ({placeholders})
                ORDER BY row_id, target_ref, value
            """, chunk).fetchall()
            for ref in ref_rows:
                refs_by_row_id[int(ref["row_id"])].append({
                    "target_ref": ref["target_ref"],
                    "value": ref["value"],
                })
        for row in rows:
            meta = SidecarFileMeta(
                file_id=int(row["file_id"]),
                source_file=str(row["source_file"]),
                file_key=str(row["file_key"]),
                source_sha256=str(row["sha256"]),
                union_config_hash=str(row["union_config_hash"]),
                domain_file_config_hash=str(row["config_hash"]),
                config_json=json.loads(row["config_json"]),
            )
            yield SidecarProjectionRow(
                file_meta=meta,
                table_id=int(row["table_id"]),
                row_id=int(row["row_id"]),
                row_no=int(row["row_no"]),
                line_hash=str(row["line_hash"]),
                row_json=json.loads(row["row_json"]),
                row_values_json=json.loads(row["row_values_json"]),
                path_refs=tuple(refs_by_row_id[int(row["row_id"])]),
            )
    finally:
        conn.close()


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
    CREATE TABLE IF NOT EXISTS meta (
      key TEXT PRIMARY KEY,
      value TEXT NOT NULL
    );
    """)
    existing_schema = _get_meta(conn, "schema_version")
    if existing_schema is not None and existing_schema != str(SCHEMA_VERSION):
        raise RuntimeError(
            f"incompatible tabular sidecar schema_version={existing_schema}; "
            f"expected {SCHEMA_VERSION}; rebuild sidecar DB"
        )
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS source_files (
      file_id INTEGER PRIMARY KEY,
      source_file TEXT NOT NULL UNIQUE,
      file_key TEXT NOT NULL UNIQUE,
      sha256 TEXT NOT NULL,
      parser_version INTEGER NOT NULL,
      union_config_hash TEXT NOT NULL,
      encoding TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS domain_files (
      domain_id TEXT NOT NULL,
      file_id INTEGER NOT NULL REFERENCES source_files(file_id) ON DELETE CASCADE,
      declared_policy TEXT NOT NULL,
      effective_policy TEXT NOT NULL,
      config_json TEXT NOT NULL,
      config_hash TEXT NOT NULL,
      PRIMARY KEY(domain_id, file_id)
    );
    CREATE TABLE IF NOT EXISTS tabular_tables (
      table_id INTEGER PRIMARY KEY,
      file_id INTEGER NOT NULL REFERENCES source_files(file_id) ON DELETE CASCADE,
      kind TEXT NOT NULL,
      delimiter TEXT NOT NULL,
      header_row INTEGER NOT NULL,
      row_count INTEGER NOT NULL,
      column_count INTEGER NOT NULL,
      warnings_json TEXT NOT NULL DEFAULT '[]'
    );
    CREATE TABLE IF NOT EXISTS tabular_columns (
      column_id INTEGER PRIMARY KEY,
      table_id INTEGER NOT NULL REFERENCES tabular_tables(table_id) ON DELETE CASCADE,
      column_index INTEGER NOT NULL,
      name TEXT NOT NULL,
      normalized_name TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS rows (
      row_id INTEGER PRIMARY KEY,
      file_id INTEGER NOT NULL REFERENCES source_files(file_id) ON DELETE CASCADE,
      table_id INTEGER REFERENCES tabular_tables(table_id) ON DELETE CASCADE,
      row_no INTEGER NOT NULL,
      line_hash TEXT NOT NULL,
      raw_line TEXT NOT NULL,
      row_json TEXT NOT NULL,
      row_values_json TEXT NOT NULL,
      UNIQUE(file_id, row_no)
    );
    CREATE TABLE IF NOT EXISTS indexed_values (
      file_id INTEGER NOT NULL REFERENCES source_files(file_id) ON DELETE CASCADE,
      row_id INTEGER NOT NULL REFERENCES rows(row_id) ON DELETE CASCADE,
      domain_id TEXT NOT NULL,
      column_name TEXT NOT NULL,
      column_index INTEGER NOT NULL,
      normalized_column_name TEXT NOT NULL,
      value TEXT NOT NULL,
      value_norm TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS tabular_refs (
      file_id INTEGER NOT NULL REFERENCES source_files(file_id) ON DELETE CASCADE,
      row_id INTEGER NOT NULL REFERENCES rows(row_id) ON DELETE CASCADE,
      target_ref TEXT NOT NULL,
      value TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_domain_files_file ON domain_files(file_id);
    CREATE INDEX IF NOT EXISTS idx_tables_file ON tabular_tables(file_id);
    CREATE INDEX IF NOT EXISTS idx_columns_table ON tabular_columns(table_id);
    CREATE INDEX IF NOT EXISTS idx_rows_file ON rows(file_id);
    CREATE INDEX IF NOT EXISTS idx_indexed_lookup ON indexed_values(domain_id, normalized_column_name, value_norm);
    CREATE INDEX IF NOT EXISTS idx_indexed_column_lookup ON indexed_values(domain_id, normalized_column_name, column_index, value_norm);
    CREATE INDEX IF NOT EXISTS idx_indexed_row ON indexed_values(row_id);
    CREATE INDEX IF NOT EXISTS idx_refs_row ON tabular_refs(row_id);
    """)
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)", (str(SCHEMA_VERSION),))
```

- [ ] **Step 2: Implement sidecar meta lifecycle**

```python
def ensure_sidecar_identity(conn: sqlite3.Connection, *, repo_root: Path, repo_key: str) -> None:
    expected_db_id = deterministic_sidecar_db_id(repo_key)
    existing_repo_key = _get_meta(conn, "repo_key")
    existing_db_id = _get_meta(conn, "sidecar_db_id")
    if existing_repo_key not in (None, repo_key):
        raise RuntimeError("sidecar DB repo_key does not match current repo")
    if existing_db_id not in (None, expected_db_id):
        raise RuntimeError("sidecar DB identity does not match current repo/schema")

    _set_meta(conn, "schema_version", SCHEMA_VERSION)
    _set_meta(conn, "created_by", "graphify")
    _set_meta(conn, "repo_root", str(repo_root.resolve()))
    _set_meta(conn, "repo_key", repo_key)
    _set_meta(conn, "sidecar_db_id", expected_db_id)
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES('sidecar_instance_id', ?)",
        (str(uuid.uuid4()),),
    )
    conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES('sidecar_generation', '0')")


def bump_sidecar_generation(conn: sqlite3.Connection) -> int:
    current = int(_get_meta(conn, "sidecar_generation") or "0")
    updated = current + 1
    _set_meta(conn, "sidecar_generation", updated)
    return updated
```

- [ ] **Step 3: Implement full-file rebuild and domain membership replacement**

Use the spec's affected-set algorithm. Do not delete/rebuild a shared file solely because the current domain touched it; first compute all active domain configs for that file after the current domain membership has been replaced.

```python
def _active_config_hashes(conn: sqlite3.Connection, file_id: int) -> list[str]:
    return [
        str(row["config_hash"])
        for row in conn.execute(
            "SELECT config_hash FROM domain_files WHERE file_id = ? ORDER BY domain_id",
            (file_id,),
        )
    ]


def _union_config_hash(source_file: str, config_hashes: list[str]) -> str:
    return canonical_json_hash({
        "source_file": source_file,
        "active_config_hashes": config_hashes,
    })


def _file_id(conn: sqlite3.Connection, source_file: str) -> int | None:
    row = conn.execute(
        "SELECT file_id FROM source_files WHERE source_file = ?",
        (source_file,),
    ).fetchone()
    return None if row is None else int(row["file_id"])


def _upsert_source_stub(conn: sqlite3.Connection, file_cfg: TabularFileConfig) -> int:
    conn.execute("""
        INSERT INTO source_files(source_file, file_key, sha256, parser_version, union_config_hash, encoding)
        VALUES(?, ?, '', 1, '', '')
        ON CONFLICT(source_file) DO UPDATE SET file_key=excluded.file_key
    """, (file_cfg.source_file, file_cfg.file_key))
    found = _file_id(conn, file_cfg.source_file)
    assert found is not None
    return found


def _replace_domain_membership(
    conn: sqlite3.Connection,
    manifest: TabularDomainManifest,
) -> set[int]:
    old_ids = {
        int(row["file_id"])
        for row in conn.execute(
            "SELECT file_id FROM domain_files WHERE domain_id = ?",
            (manifest.domain_id,),
        )
    }
    conn.execute("DELETE FROM domain_files WHERE domain_id = ?", (manifest.domain_id,))
    new_ids: set[int] = set()
    for file_cfg in manifest.sidecar_active_files:
        file_id = _upsert_source_stub(conn, file_cfg)
        new_ids.add(file_id)
        conn.execute("""
            INSERT INTO domain_files(domain_id, file_id, declared_policy, effective_policy, config_json, config_hash)
            VALUES(?, ?, ?, ?, ?, ?)
        """, (
            manifest.domain_id,
            file_id,
            file_cfg.declared_policy,
            file_cfg.effective_policy,
            json.dumps(_config_json(file_cfg), sort_keys=True, ensure_ascii=False),
            file_cfg.config_hash,
        ))
    return old_ids | new_ids


def _rebuild_file_rows_and_refs(
    conn: sqlite3.Connection,
    file_id: int,
    file_cfg: TabularFileConfig,
    union_hash: str,
) -> tuple[int, int]:
    parsed = parse_tabular_file(file_cfg.path, source_file=file_cfg.source_file)
    conn.execute("DELETE FROM tabular_tables WHERE file_id = ?", (file_id,))
    conn.execute("""
        INSERT INTO tabular_tables(file_id, kind, delimiter, header_row, row_count, column_count, warnings_json)
        VALUES(?, 'tsv', '\t', 1, ?, ?, ?)
    """, (file_id, len(parsed.rows), len(parsed.columns), json.dumps(list(parsed.warnings), ensure_ascii=False)))
    table_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    for col in parsed.columns:
        conn.execute("""
            INSERT INTO tabular_columns(table_id, column_index, name, normalized_name)
            VALUES(?, ?, ?, ?)
        """, (table_id, col.column_index, col.name, col.key))
    row_count = ref_count = 0
    for row in parsed.rows:
        conn.execute("""
            INSERT INTO rows(file_id, table_id, row_no, line_hash, raw_line, row_json, row_values_json)
            VALUES(?, ?, ?, ?, ?, ?, ?)
        """, (
            file_id,
            table_id,
            row.row_no,
            row.line_hash,
            row.raw_line,
            json.dumps(row.row_json, ensure_ascii=False),
            json.dumps(row.row_values_json, ensure_ascii=False),
        ))
        row_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        ref_count += _insert_path_refs(conn, file_id, row_id, row)
        row_count += 1
    conn.execute("""
        UPDATE source_files
        SET sha256 = ?, parser_version = 1, union_config_hash = ?, encoding = ?
        WHERE file_id = ?
    """, (parsed.source_sha256, union_hash, parsed.encoding, file_id))
    return row_count, ref_count


def _rebuild_indexes_for_active_domains(conn: sqlite3.Connection, file_id: int) -> int:
    conn.execute("DELETE FROM indexed_values WHERE file_id = ?", (file_id,))
    rows = conn.execute("SELECT row_id, row_json, row_values_json FROM rows WHERE file_id = ?", (file_id,)).fetchall()
    domain_cfgs = conn.execute("""
        SELECT domain_id, config_json
        FROM domain_files
        WHERE file_id = ?
        ORDER BY domain_id
    """, (file_id,)).fetchall()
    inserted = 0
    for domain_cfg in domain_cfgs:
        file_cfg = tabular_file_config_from_json(json.loads(domain_cfg["config_json"]))
        for row_data in rows:
            row = row_from_sql_row(row_data)
            inserted += _insert_indexes(conn, file_id, int(row_data["row_id"]), str(domain_cfg["domain_id"]), file_cfg, row)
    return inserted


def update_sidecar(db_path: Path, manifest: TabularDomainManifest) -> SidecarUpdateStats:
    with SidecarWriteLock(db_path):
        conn = connect_sidecar(db_path)
        try:
            ensure_schema(conn)
            rows_upserted = indexes_upserted = refs_upserted = files_pruned = rows_pruned = 0
            with conn:
                ensure_sidecar_identity(conn, repo_root=manifest.repo_root, repo_key=manifest.repo_key)
                affected_ids = _replace_domain_membership(conn, manifest)
                cfg_by_file = {cfg.source_file: cfg for cfg in manifest.sidecar_active_files}
                for file_id in affected_ids:
                    active_hashes = _active_config_hashes(conn, file_id)
                    if not active_hashes:
                        rows_pruned += int(conn.execute(
                            "SELECT COUNT(*) FROM rows WHERE file_id = ?",
                            (file_id,),
                        ).fetchone()[0])
                        conn.execute("DELETE FROM source_files WHERE file_id = ?", (file_id,))
                        files_pruned += 1
                        continue
                    sf = conn.execute(
                        "SELECT source_file, sha256, union_config_hash FROM source_files WHERE file_id = ?",
                        (file_id,),
                    ).fetchone()
                    source_file = str(sf["source_file"])
                    union_hash = _union_config_hash(source_file, active_hashes)
                    file_cfg = cfg_by_file.get(source_file)
                    current_sha = file_cfg and tabular_source_sha256(file_cfg.path)
                    if file_cfg and current_sha != sf["sha256"]:
                        row_count, ref_count = _rebuild_file_rows_and_refs(conn, file_id, file_cfg, union_hash)
                        rows_upserted += row_count
                        refs_upserted += ref_count
                    elif union_hash != sf["union_config_hash"]:
                        conn.execute(
                            "UPDATE source_files SET union_config_hash = ? WHERE file_id = ?",
                            (union_hash, file_id),
                        )
                    if file_cfg or union_hash != sf["union_config_hash"]:
                        indexes_upserted += _rebuild_indexes_for_active_domains(conn, file_id)
                bump_sidecar_generation(conn)
            return SidecarUpdateStats(
                files_upserted=len(manifest.sidecar_active_files),
                rows_upserted=rows_upserted,
                indexes_upserted=indexes_upserted,
                refs_upserted=refs_upserted,
                files_pruned=files_pruned,
                rows_pruned=rows_pruned,
            )
        finally:
            conn.close()
```

Add `tabular_file_config_from_json()` and `row_from_sql_row()` as local conversion helpers:

```python
from types import SimpleNamespace


def selector_from_json(payload: dict | None) -> ColumnSelector | None:
    if payload is None:
        return None
    return ColumnSelector(
        name=payload["name"],
        normalized_name=payload["normalized_name"],
        column_index=payload.get("column_index"),
    )


def tabular_file_config_from_json(payload: dict) -> TabularFileConfig:
    return TabularFileConfig(
        path=Path(payload["source_file"]),
        source_file=payload["source_file"],
        file_key=payload["file_key"],
        declared_policy=payload["declared_policy"],
        effective_policy=payload["effective_policy"],
        primary_key=selector_from_json(payload.get("primary_key")),
        indexed_columns=tuple(selector_from_json(v) for v in payload.get("indexed_columns", [])),
        anchor_columns=tuple(selector_from_json(v) for v in payload.get("anchor_columns", [])),
        # This object is used only to replay selector config while rebuilding derived indexes.
        # Runtime profile belongs in table/source metadata and reports, not in domain config_json.
        profile=TabularProfile(
            row_count=0,
            column_count=0,
            file_size=0,
            columns=(),
        ),
        auto_reason=payload.get("auto_reason"),
        config_hash=payload["config_hash"],
    )


def row_from_sql_row(row_data: sqlite3.Row):
    return SimpleNamespace(
        row_json=json.loads(row_data["row_json"]),
        row_values_json=json.loads(row_data["row_values_json"]),
    )
```

The implementation must preserve the semantics above: current-domain membership replacement first, all-active-domain union second, row rebuild only on source hash change, index rebuild on union config change.

Add the helper functions used by `update_sidecar`:

```python
def _selector_json(selector) -> dict | None:
    if selector is None:
        return None
    return {
        "name": selector.name,
        "normalized_name": selector.normalized_name,
        "column_index": selector.column_index,
    }


def _config_json(file_cfg: TabularFileConfig) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "parser_version": 1,
        "source_file": file_cfg.source_file,
        "file_key": file_cfg.file_key,
        "config_hash": file_cfg.config_hash,
        "declared_policy": file_cfg.declared_policy,
        "effective_policy": file_cfg.effective_policy,
        "auto_reason": file_cfg.auto_reason,
        "primary_key": _selector_json(file_cfg.primary_key),
        "indexed_columns": [_selector_json(s) for s in file_cfg.indexed_columns],
        "anchor_columns": [_selector_json(s) for s in file_cfg.anchor_columns],
    }


def _value_for_selector(row, selector) -> tuple[int, str] | None:
    for item in row.row_values_json:
        if item["normalized_name"] != selector.normalized_name:
            continue
        if selector.column_index is not None and item["column_index"] != selector.column_index:
            continue
        value = str(item["value"])
        return int(item["column_index"]), value
    return None


def _insert_indexes(
    conn: sqlite3.Connection,
    file_id: int,
    row_id: int,
    domain_id: str,
    file_cfg: TabularFileConfig,
    row,
) -> int:
    selectors = []
    if file_cfg.primary_key is not None:
        selectors.append(file_cfg.primary_key)
    selectors.extend(file_cfg.indexed_columns)
    inserted = 0
    for selector in selectors:
        found = _value_for_selector(row, selector)
        if found is None:
            continue
        column_index, value = found
        if value == "":
            continue
        normalized_column_name = _normalize_column_name(selector.normalized_name)
        value_norm = _normalize_search_value(value)
        conn.execute("""
            INSERT INTO indexed_values(
                file_id, row_id, domain_id,
                column_name, column_index, normalized_column_name,
                value, value_norm
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
        """, (file_id, row_id, domain_id, selector.name, column_index, normalized_column_name, value, value_norm))
        inserted += 1
    return inserted


def _insert_path_refs(conn: sqlite3.Connection, file_id: int, row_id: int, row) -> int:
    from graphify.tabular import looks_like_tabular_path_value, normalise_tabular_path_value

    inserted = 0
    for item in row.row_values_json:
        value = str(item["value"])
        if not looks_like_tabular_path_value(value):
            continue
        conn.execute("""
            INSERT INTO tabular_refs(file_id, row_id, target_ref, value)
            VALUES(?, ?, ?, ?)
        """, (file_id, row_id, normalise_tabular_path_value(value), value))
        inserted += 1
    return inserted
```

- [ ] **Step 3: Implement readonly `resolve_ref` and `search_rows`**

`sidecar_ref` format:

```text
sidecar://tabular/<repo_key>/<file_key>/<row_no>/<line_hash>
```

Add readonly implementations:

```python
def make_sidecar_ref(repo_key: str, file_key: str, row_no: int, line_hash: str) -> str:
    return f"sidecar://tabular/{repo_key}/{file_key}/{row_no}/{line_hash}"


def _resolve_domain_indexed_column(
    conn: sqlite3.Connection,
    domain_id: str,
    column: str,
    column_index: int | None,
) -> str:
    rows = conn.execute(
        "SELECT config_json FROM domain_files WHERE domain_id = ?",
        (domain_id,),
    ).fetchall()
    if not rows:
        raise LookupError(f"domain not found or has no sidecar-active files: {domain_id}")
    for row in rows:
        cfg = json.loads(row["config_json"])
        for item in [cfg.get("primary_key"), *cfg.get("indexed_columns", [])]:
            if not item:
                continue
            if column_index is not None:
                if item.get("name") == column and item.get("column_index") == column_index:
                    return str(item["normalized_name"])
            elif item.get("normalized_name") == column or item.get("name") == column:
                return str(item["normalized_name"])
    raise ValueError(f"not indexed for domain: {column}")


def _indexed_values_for_row(conn: sqlite3.Connection, row_id: int, domain_id: str | None) -> list[dict]:
    if domain_id:
        rows = conn.execute("""
            SELECT column_name, normalized_column_name, column_index, value, value_norm
            FROM indexed_values
            WHERE row_id = ? AND domain_id = ?
            ORDER BY column_index, column_name, value
        """, (row_id, domain_id)).fetchall()
    else:
        rows = conn.execute("""
            SELECT domain_id, column_name, normalized_column_name, column_index, value, value_norm
            FROM indexed_values
            WHERE row_id = ?
            ORDER BY domain_id, column_index, column_name, value
        """, (row_id,)).fetchall()
    return [dict(row) for row in rows]


def _refs_for_row(conn: sqlite3.Connection, row_id: int) -> list[dict]:
    rows = conn.execute("""
        SELECT target_ref, value
        FROM tabular_refs
        WHERE row_id = ?
        ORDER BY target_ref, value
    """, (row_id,)).fetchall()
    return [dict(row) for row in rows]


def _row_payload(row: sqlite3.Row, *, indexed_values: list[dict], refs: list[dict]) -> dict:
    return {
        "source_file": row["source_file"],
        "file_key": row["file_key"],
        "row_no": row["row_no"],
        "line_hash": row["line_hash"],
        "raw_line": row["raw_line"],
        "row_json": json.loads(row["row_json"]),
        "row_values_json": json.loads(row["row_values_json"]),
        "source_sha256": row["sha256"],
        "domain_file_config_hash": row["domain_file_config_hash"],
        "union_config_hash": row["union_config_hash"],
        "indexed_values": indexed_values,
        "refs": refs,
        "sidecar_ref": make_sidecar_ref(row["repo_key"], row["file_key"], row["row_no"], row["line_hash"]),
    }


def resolve_ref(db_path: Path, sidecar_ref: str, *, domain_id: str | None = None, graph_meta: dict | None = None) -> dict:
    from urllib.parse import urlparse

    parsed = urlparse(sidecar_ref)
    parts = [p for p in parsed.path.split("/") if p]
    if parsed.scheme != "sidecar" or parsed.netloc != "tabular" or len(parts) != 4:
        raise ValueError("invalid sidecar_ref")
    repo_key, file_key, row_no_raw, line_hash = parts
    row_no = int(row_no_raw)
    conn = connect_sidecar(db_path, readonly=True)
    try:
        sql = """
            SELECT DISTINCT
              (SELECT value FROM meta WHERE key = 'repo_key') AS repo_key,
              {domain_config_select} AS domain_file_config_hash,
              sf.source_file, sf.file_key, sf.sha256, sf.union_config_hash,
              r.row_id, r.row_no, r.line_hash, r.raw_line, r.row_json, r.row_values_json
            FROM rows r
            JOIN source_files sf ON sf.file_id = r.file_id
            {domain_join}
            WHERE sf.file_key = ? AND r.row_no = ?
        """
        params: list[object] = [file_key, row_no]
        domain_join = ""
        domain_config_select = "NULL"
        if domain_id:
            domain_join = "JOIN domain_files df ON df.file_id = sf.file_id AND df.domain_id = ?"
            domain_config_select = "df.config_hash"
            params = [domain_id, file_key, row_no]
        found = conn.execute(
            sql.format(domain_join=domain_join, domain_config_select=domain_config_select),
            params,
        ).fetchone()
        if found is None or found["repo_key"] != repo_key or found["line_hash"] != line_hash:
            raise LookupError("sidecar_ref not found, row content changed, or domain scope does not include row")
        return _row_payload(
            found,
            indexed_values=_indexed_values_for_row(conn, int(found["row_id"]), domain_id),
            refs=_refs_for_row(conn, int(found["row_id"])),
        )
    finally:
        conn.close()


def search_rows(
    db_path: Path,
    *,
    domain_id: str | None,
    column: str,
    value: str,
    file_key: str | None = None,
    source_file: str | None = None,
    column_index: int | None = None,
    graph_meta: dict | None = None,
) -> list[dict]:
    conn = connect_sidecar(db_path, readonly=True)
    try:
        normalized_column = _normalize_column_name(column)
        value_norm = _normalize_search_value(value)
        filters = ["iv.normalized_column_name = ?", "iv.value_norm = ?"]
        params: list[object] = [normalized_column, value_norm]
        if domain_id:
            normalized_column = _resolve_domain_indexed_column(conn, domain_id, column, column_index)
            filters = ["iv.domain_id = ?", "iv.normalized_column_name = ?", "iv.value_norm = ?"]
            params = [domain_id, normalized_column, value_norm]
        if column_index is not None:
            filters.append("iv.column_index = ?")
            params.append(column_index)
        if file_key is not None:
            filters.append("sf.file_key = ?")
            params.append(file_key)
        if source_file is not None:
            filters.append("sf.source_file = ?")
            params.append(source_file)
        rows = conn.execute("""
            SELECT DISTINCT
              (SELECT value FROM meta WHERE key = 'repo_key') AS repo_key,
              {domain_config_select} AS domain_file_config_hash,
              sf.source_file, sf.file_key, sf.sha256, sf.union_config_hash,
              r.row_id, r.row_no, r.line_hash, r.raw_line, r.row_json, r.row_values_json
            FROM indexed_values iv
            JOIN rows r ON r.row_id = iv.row_id
            JOIN source_files sf ON sf.file_id = iv.file_id
            {domain_join}
            WHERE {where_clause}
            ORDER BY sf.source_file, r.row_no
        """.format(
            domain_config_select="df.config_hash" if domain_id else "NULL",
            domain_join="JOIN domain_files df ON df.file_id = sf.file_id AND df.domain_id = iv.domain_id" if domain_id else "",
            where_clause=" AND ".join(filters),
        ), params).fetchall()
        return [
            _row_payload(
                row,
                indexed_values=_indexed_values_for_row(conn, int(row["row_id"]), domain_id),
                refs=_refs_for_row(conn, int(row["row_id"])),
            )
            for row in rows
        ]
    finally:
        conn.close()


ALLOWED_QUERY_VIEWS = {
    "current_domain_sources",
    "current_domain_rows",
    "current_domain_indexed_values",
    "current_domain_refs",
}

ALLOWED_QUERY_FUNCTIONS = {
    "abs", "avg", "coalesce", "count", "hex", "ifnull", "instr", "json_extract",
    "length", "lower", "max", "min", "nullif", "round", "substr", "sum", "trim",
    "typeof", "upper",
}


def _has_sql_statement_separator(statement: str) -> bool:
    quote: str | None = None
    i = 0
    while i < len(statement):
        ch = statement[i]
        nxt = statement[i + 1] if i + 1 < len(statement) else ""
        if quote:
            if ch == quote:
                if nxt == quote:
                    i += 2
                    continue
                quote = None
            i += 1
            continue
        if ch in {"'", '"', "`"}:
            quote = ch
            i += 1
            continue
        if ch == "-" and nxt == "-":
            newline = statement.find("\n", i + 2)
            i = len(statement) if newline == -1 else newline + 1
            continue
        if ch == "/" and nxt == "*":
            end = statement.find("*/", i + 2)
            i = len(statement) if end == -1 else end + 2
            continue
        if ch == ";":
            return True
        i += 1
    return False


def _normalize_readonly_sql(sql: str) -> str:
    """Normalize one read-only statement.

    This intentionally checks only the top-level first keyword. Do not scan the
    full SQL text for forbidden words because string literals and aliases can
    contain values like "insert_into_buffer" or "update_time". Multi-statement
    detection only looks for semicolons outside strings/comments; sqlite3 plus
    the query authorizer remain the write/PRAGMA/ATTACH/function safety boundary.
    """
    statement = sql.strip()
    if statement.endswith(";"):
        statement = statement[:-1].strip()
    if not statement:
        raise ValueError("sidecar query requires SQL")
    if _has_sql_statement_separator(statement):
        raise ValueError("sidecar query accepts exactly one SQL statement")
    first_match = re.match(r"\s*(?:--[^\n]*\n\s*|/\*.*?\*/\s*)*([A-Za-z_]+)", statement, re.S)
    first = first_match.group(1).lower() if first_match else ""
    if first not in {"select", "with"}:
        raise ValueError("sidecar query only allows SELECT or WITH")
    return statement


def _install_query_views(conn: sqlite3.Connection, *, domain_id: str | None) -> None:
    conn.executescript("""
        DROP VIEW IF EXISTS current_domain_sources;
        DROP VIEW IF EXISTS current_domain_rows;
        DROP VIEW IF EXISTS current_domain_indexed_values;
        DROP VIEW IF EXISTS current_domain_refs;
        DROP TABLE IF EXISTS temp._sidecar_query_scope;
        CREATE TEMP TABLE _sidecar_query_scope(domain_id TEXT);
    """)
    conn.execute("INSERT INTO _sidecar_query_scope(domain_id) VALUES (?)", (domain_id,))
    if domain_id:
        conn.executescript("""
            CREATE TEMP VIEW current_domain_sources AS
            SELECT df.domain_id, sf.source_file, sf.file_key, sf.sha256, sf.encoding,
                   df.config_hash AS domain_file_config_hash,
                   sf.union_config_hash
            FROM source_files sf
            JOIN domain_files df ON df.file_id = sf.file_id
            WHERE df.domain_id = (SELECT domain_id FROM _sidecar_query_scope);

            CREATE TEMP VIEW current_domain_rows AS
            SELECT sf.source_file, sf.file_key, r.row_no, r.line_hash, r.raw_line,
                   r.row_json, r.row_values_json,
                   'sidecar://tabular/' || (SELECT value FROM meta WHERE key = 'repo_key') || '/' ||
                   sf.file_key || '/' || r.row_no || '/' || r.line_hash AS sidecar_ref
            FROM rows r
            JOIN source_files sf ON sf.file_id = r.file_id
            JOIN domain_files df ON df.file_id = sf.file_id
            WHERE df.domain_id = (SELECT domain_id FROM _sidecar_query_scope);

            CREATE TEMP VIEW current_domain_indexed_values AS
            SELECT iv.domain_id, sf.source_file, sf.file_key, r.row_no,
                   iv.column_name, iv.normalized_column_name, iv.column_index,
                   iv.value, iv.value_norm
            FROM indexed_values iv
            JOIN rows r ON r.row_id = iv.row_id
            JOIN source_files sf ON sf.file_id = iv.file_id
            JOIN domain_files df ON df.file_id = sf.file_id AND df.domain_id = iv.domain_id
            WHERE iv.domain_id = (SELECT domain_id FROM _sidecar_query_scope);

            CREATE TEMP VIEW current_domain_refs AS
            SELECT sf.source_file, sf.file_key, r.row_no, tr.target_ref, tr.value
            FROM tabular_refs tr
            JOIN rows r ON r.row_id = tr.row_id
            JOIN source_files sf ON sf.file_id = tr.file_id
            JOIN domain_files df ON df.file_id = sf.file_id
            WHERE df.domain_id = (SELECT domain_id FROM _sidecar_query_scope);
        """)
    else:
        conn.executescript("""
            CREATE TEMP VIEW current_domain_sources AS
            SELECT df.domain_id, sf.source_file, sf.file_key, sf.sha256, sf.encoding,
                   df.config_hash AS domain_file_config_hash,
                   sf.union_config_hash
            FROM source_files sf
            JOIN domain_files df ON df.file_id = sf.file_id;

            CREATE TEMP VIEW current_domain_rows AS
            SELECT sf.source_file, sf.file_key, r.row_no, r.line_hash, r.raw_line,
                   r.row_json, r.row_values_json,
                   'sidecar://tabular/' || (SELECT value FROM meta WHERE key = 'repo_key') || '/' ||
                   sf.file_key || '/' || r.row_no || '/' || r.line_hash AS sidecar_ref
            FROM rows r
            JOIN source_files sf ON sf.file_id = r.file_id;

            CREATE TEMP VIEW current_domain_indexed_values AS
            SELECT iv.domain_id, sf.source_file, sf.file_key, r.row_no,
                   iv.column_name, iv.normalized_column_name, iv.column_index,
                   iv.value, iv.value_norm
            FROM indexed_values iv
            JOIN rows r ON r.row_id = iv.row_id
            JOIN source_files sf ON sf.file_id = iv.file_id;

            CREATE TEMP VIEW current_domain_refs AS
            SELECT sf.source_file, sf.file_key, r.row_no, tr.target_ref, tr.value
            FROM tabular_refs tr
            JOIN rows r ON r.row_id = tr.row_id
            JOIN source_files sf ON sf.file_id = tr.file_id;
        """)


def _validate_query_scope_sources(
    conn: sqlite3.Connection,
    graph_meta: dict,
    *,
    require_union_hash: bool,
) -> None:
    index = graph_meta.get("_source_metadata_index", {})
    rows = conn.execute("""
        SELECT DISTINCT source_file, file_key, sha256, domain_file_config_hash, union_config_hash
        FROM current_domain_sources
    """).fetchall()
    for row in rows:
        source_meta = index.get(f"file_key:{row['file_key']}") or index.get(f"source_file:{row['source_file']}")
        if source_meta is None:
            raise LookupError(f"graph missing sidecar source metadata for {row['source_file']}")
        if source_meta.get("source_sha256") != row["sha256"]:
            raise LookupError(f"source sha mismatch for {row['source_file']}")
        if row["domain_file_config_hash"] is not None and source_meta.get("domain_file_config_hash") != row["domain_file_config_hash"]:
            raise LookupError(f"domain file config hash mismatch for {row['source_file']}")
        if require_union_hash and source_meta.get("union_config_hash") != row["union_config_hash"]:
            raise LookupError(f"union config hash mismatch for {row['source_file']}")


def _install_query_authorizer(conn: sqlite3.Connection) -> None:
    def _authorizer(action: int, arg1: str | None, arg2: str | None, db_name: str | None, source: str | None) -> int:
        if action == sqlite3.SQLITE_SELECT:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ:
            # Direct reads from physical tables have no view source. Reads expanded through
            # current_domain_* views carry the view name as source and are allowed.
            if source in ALLOWED_QUERY_VIEWS or (db_name == "temp" and arg1 in ALLOWED_QUERY_VIEWS):
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_FUNCTION:
            function_name = (arg2 or arg1 or "").lower()
            return sqlite3.SQLITE_OK if function_name in ALLOWED_QUERY_FUNCTIONS else sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_DENY

    conn.set_authorizer(_authorizer)


def execute_readonly_query(
    db_path: Path,
    *,
    sql: str,
    domain_id: str | None,
    graph_meta: dict | None = None,
    global_debug: bool = False,
    limit: int = 1000,
    max_limit: int = 10000,
    timeout_ms: int = 5000,
) -> dict:
    if domain_id is None and not global_debug:
        raise ValueError("sidecar query requires --domain unless --global-debug is explicit")
    clean_sql = _normalize_readonly_sql(sql)
    row_limit = max(1, min(limit, max_limit))
    conn = connect_sidecar(db_path, readonly=True)
    try:
        meta = read_sidecar_meta(db_path)
        _install_query_views(conn, domain_id=domain_id)
        if graph_meta:
            _validate_query_scope_sources(conn, graph_meta, require_union_hash=global_debug)
        deadline = time.monotonic() + (max(1, timeout_ms) / 1000)

        def _interrupt_when_expired() -> int:
            return 1 if time.monotonic() > deadline else 0

        conn.set_progress_handler(_interrupt_when_expired, 10000)
        conn.execute("PRAGMA query_only = ON")
        _install_query_authorizer(conn)
        try:
            cursor = conn.execute(f"SELECT * FROM ({clean_sql}) LIMIT ?", (row_limit + 1,))
        except sqlite3.Error as exc:
            raise ValueError(f"sidecar query failed: {exc}") from exc
        fetched = cursor.fetchall()
        truncated = len(fetched) > row_limit
        rows = [dict(row) for row in fetched[:row_limit]]
        columns = [desc[0] for desc in cursor.description or []]
        return {
            "sidecar_db": str(db_path),
            "schema_version": meta.get("schema_version"),
            "repo_key": meta.get("repo_key"),
            "sidecar_db_id": meta.get("sidecar_db_id"),
            "sidecar_instance_id": meta.get("sidecar_instance_id"),
            "sidecar_generation": meta.get("sidecar_generation"),
            "domain_id": domain_id,
            "scope": "global-debug" if domain_id is None else "domain",
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "truncated": truncated,
            "limit": row_limit,
            "timeout_ms": timeout_ms,
        }
    finally:
        conn.set_authorizer(None)
        conn.set_progress_handler(None, 0)
        conn.close()
```

- [ ] **Step 4: Run store tests**

Run:

```bash
uv run pytest tests/test_tabular_sidecar_store.py -q
```

Expected: pass for insert, domain-scoped search, `--file`/`--column-index` filtering, normalized value lookup, domain-scoped resolve, read-only query safety checks, domain-scoped query views, not-indexed-for-domain errors, update, prune, and FK cascade.

## Task 5: Build Integration And Graph Metadata

**Files:**
- Create: `graphify/tabular_graph.py`
- Modify: `graphify/code_build_runner.py`
- Modify: `graphify/__main__.py`
- Test: `tests/test_tabular_sidecar_build.py`

Integration boundary:

- `graphify/__main__.py` remains the CLI parser/dispatcher. It should pass the manifest path, exact `--output-dir`, optional `--sidecar-db`, and resolved active Graphify output into the manifest build runner.
- Extend `_parse_manifest_cli_args(...)` to accept both `--sidecar-db PATH` and `--sidecar-db=PATH`. The unsupported-option error text must include `--sidecar-db` in the accepted manifest option list after this change.
- `graphify/code_build_runner.py::build_code_graph(...)` is the current Phase 0 runner. Extend it with optional `tabular_manifest`, `active_graphify_output`, and `sidecar_db_path` parameters. Do not introduce a second wrapper that duplicates build/report/state/publish logic; sidecar projection must be injected into the extraction result before `build_from_json(...)`, so extending the existing runner is the lower-risk integration point.
- Do not infer sidecar location from the domain `--output-dir`. The domain output may later be used as `GRAPHIFY_OUT` by query/explain commands, but build-time sidecar location must come from active Graphify output or explicit `--sidecar-db`.
- Split manifest files before calling `extract(...)`: effective `sidecar` files must be removed from the extractor input and later merged back only through sidecar projection. Do not rely on post-filtering `extract_tab` output unless the implementation also proves all duplicate file/column/row/ref nodes and edges are removed; the preferred MVP path is pre-extract split.

```python
tabular_manifest = load_tabular_domain_manifest(manifest_path, cwd=..., output_dir=output_dir)
sidecar_paths = {file_cfg.path.resolve() for file_cfg in tabular_manifest.sidecar_active_files}
extractor_files = [path for path in domain.source_paths if path.resolve() not in sidecar_paths]

result = extract(extractor_files, **extract_kwargs)
merge_sidecar_projection(result, tabular_manifest, sidecar_db_path, active_graphify_output)
```

Precise manifest build sequence:

1. `__main__._maybe_run_manifest_code_build(...)` parses `--manifest`, `--output-dir`, `--sidecar-db`, and `--max-workers`.
2. `__main__` loads the existing Phase 0 `DomainManifest` and separately loads `TabularDomainManifest` from the raw caller manifest JSON.
3. `__main__` resolves `active_graphify_output` from `GRAPHIFY_OUT` or `<repo_root>/graphify-out`, resolves optional `sidecar_db_path`, and calls `build_code_graph(..., tabular_manifest=..., active_graphify_output=..., sidecar_db_path=...)`.
4. `build_code_graph(...)` splits `code_files` into `extractor_files` and `sidecar_files` from `tabular_manifest.sidecar_active_files`.
5. `build_code_graph(...)` calls `extract(extractor_files, **extract_kwargs)`. If `extractor_files` is empty, initialize an empty extraction result dict instead of treating this as an error.
6. If sidecar files exist, call `update_sidecar(...)`, read sidecar metadata, and merge file/table/column/anchor/path-ref skeleton nodes and edges into the extraction `result` dict.
7. Run existing post-extract graph cleanup on the merged `result`: `_redirect_tab_reference_edges(...)`, Lua include/reference edges, and `_relativize_source_files(...)`.
8. Call `build_from_json(result, root=repo_root)`.
9. Attach `graph.graph["tabular_sidecar"]` metadata.
10. Perform the empty-graph check after sidecar projection merge and `build_from_json(...)`. A sidecar-only manifest should pass as long as projection emitted at least one source/table/column/anchor/path node.
11. Continue unchanged with cluster/score/report/export/state staging and publish.

This ordering is required for pure tabular domains. If `extractor_files` is empty and projection is merged only into `G` after `build_from_json(...)`, the existing `manifest build produced an empty graph` guard will fire before sidecar nodes exist.

CLI parser change sketch:

```python
# graphify/__main__.py::_parse_manifest_cli_args
parsed = {
    "manifest": None,
    "output_dir": None,
    "sidecar_db": None,
    ...
}

elif arg == "--sidecar-db":
    parsed["sidecar_db"] = _required_value("--sidecar-db", i, "path")
    i += 2
elif arg.startswith("--sidecar-db="):
    value = arg.split("=", 1)[1]
    if not value:
        raise ValueError("--sidecar-db requires a path")
    parsed["sidecar_db"] = value
    i += 1
```

`_maybe_run_manifest_code_build(...)` must resolve relative `--sidecar-db` against the command cwd and pass it to the build integration; it must not leave the option in `unknown_options`.

- [ ] **Step 1: Add graph metadata helpers**

```python
# graphify/tabular_graph.py
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
```

- [ ] **Step 2: Add source-file metadata nodes and graph projection reader**

After `update_sidecar()` commits, read current source metadata from the sidecar DB for each effective `sidecar` file via `read_sidecar_file_meta()` and add it to the graph file node or a dedicated source metadata node:

```python
sidecar_file_meta = read_sidecar_file_meta(
    sidecar_db_path,
    domain_id=tabular_manifest.domain_id,
    file_key=file_cfg.file_key,
)
if sidecar_file_meta is None:
    raise RuntimeError(f"missing sidecar metadata for {file_cfg.source_file}")

{
    "source_file": file_cfg.source_file,
    "file_key": file_cfg.file_key,
    "sidecar_db_id": sidecar_db_id,
    "source_sha256": sidecar_file_meta.source_sha256,
    "domain_file_config_hash": sidecar_file_meta.domain_file_config_hash,
    "union_config_hash": sidecar_file_meta.union_config_hash,
}
```

Then read table and column projection metadata via `read_sidecar_table_projection()` and emit table/column nodes even when a table has zero data rows:

```python
for table in read_sidecar_table_projection(
    sidecar_db_path,
    domain_id=tabular_manifest.domain_id,
    file_key=file_cfg.file_key,
):
    table_node_id = f"tabular_table:{file_cfg.file_key}:{table.table_id}"
    emit_table_node(
        table_node_id,
        metadata={
            "source_file": file_cfg.source_file,
            "file_key": file_cfg.file_key,
            "table_id": table.table_id,
            "row_count": table.row_count,
            "column_count": table.column_count,
            "warnings": list(table.warnings),
            "sidecar_db_id": sidecar_db_id,
            "source_sha256": sidecar_file_meta.source_sha256,
            "domain_file_config_hash": sidecar_file_meta.domain_file_config_hash,
            "union_config_hash": sidecar_file_meta.union_config_hash,
        },
    )
    emit_contains_edge(source_node_id, table_node_id)
    for column in table.columns:
        column_node_id = f"tabular_column:{file_cfg.file_key}:{table.table_id}:{column['column_index']}"
        emit_column_node(column_node_id, metadata=column)
        emit_contains_edge(table_node_id, column_node_id)
```

For effective `sidecar`, do not emit full row or cell nodes. Emit the old `hybrid`-equivalent anchor/path/ref skeleton nodes with `sidecar_ref` and `sidecar_ref_key` by iterating `iter_sidecar_graph_projection(db_path, domain_id=..., file_key=...)`.

Add explicit graph projection helpers in `graphify/tabular_graph.py`:

```python
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
    values = []
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
```

Projection contract:

- Source file node `contains` table node; this is emitted from `read_sidecar_table_projection()`, not inferred from row projection.
- Table node `contains` column nodes; zero-row tables still emit table and column metadata.
- If `anchor_columns` is non-empty, each row with at least one non-empty anchor value emits one anchor node.
- Anchor node label is `anchor_label(...)`; anchor node metadata includes `source_file`, `file_key`, `row_no`, `line_hash`, `sidecar_ref`, `sidecar_ref_key`, `domain_file_config_hash`, and `source_sha256`.
- Table node `contains` anchor node.
- Each row path-like ref emits a lightweight path-ref skeleton node with `target_ref`, original `value`, `sidecar_ref`, and `sidecar_ref_key`; table node `contains` it, and the path-ref node `references` the target file node or a tab-owned stub using the same path resolution semantics as the existing `.tab` extractor.
- If an anchor and path-ref skeleton come from the same row, add an `references` edge from the anchor node to the path-ref skeleton node.
- Never emit full row nodes or cell nodes; JX3-specific joins stay outside Graphify core.

Projection loop sketch:

```python
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
    label = anchor_label(projection.row_values_json, file_cfg.anchor_columns)
    if label:
        emit_anchor_node(
            anchor_node_id(projection.file_meta.file_key, projection.row_no, projection.line_hash),
            label=label,
            metadata={
                "source_file": projection.file_meta.source_file,
                "file_key": projection.file_meta.file_key,
                "row_no": projection.row_no,
                "line_hash": projection.line_hash,
                "sidecar_ref": sidecar_ref,
                "sidecar_ref_key": ref_key,
                "domain_file_config_hash": projection.file_meta.domain_file_config_hash,
                "source_sha256": projection.file_meta.source_sha256,
            },
        )
    for path_ref in projection.path_refs:
        emit_path_ref_skeleton(
            path_ref_node_id(
                projection.file_meta.file_key,
                projection.row_no,
                projection.line_hash,
                path_ref["target_ref"],
            ),
            target_ref=path_ref["target_ref"],
            value=path_ref["value"],
            sidecar_ref=sidecar_ref,
            sidecar_ref_key=ref_key,
        )
```

- [ ] **Step 3: Commit sidecar before graph publish**

In `graphify/code_build_runner.py`:

```python
sidecar_stats = update_sidecar(sidecar_db_path, tabular_manifest)
sidecar_meta = read_sidecar_meta(sidecar_db_path)
graph_meta = graph_sidecar_metadata(
    manifest=tabular_manifest,
    repo_key=tabular_manifest.repo_key,
    repo_root=tabular_manifest.repo_root,
    graphify_output=active_graphify_output,
    db_path=sidecar_db_path,
    sidecar_meta=sidecar_meta,
)
G.graph["tabular_sidecar"] = graph_meta
```

Only after the sidecar transaction succeeds should the runner write `graph.json` and `GRAPH_REPORT.md`.

Default `sidecar_db_path` to the shared active Graphify output, not the domain `--output-dir`:

```python
sidecar_db_path = active_graphify_output / "sidecar" / "tabular.sqlite"
```

If the manifest build CLI receives `--sidecar-db /path/to/tabular.sqlite`, use that absolute path or resolve a relative path against the command cwd. Record the resolved path in report and graph metadata.

`active_graphify_output` must be resolved once from the build environment (`GRAPHIFY_OUT` or `<repo_root>/graphify-out`) and passed into graph metadata. Graph-aware `sidecar search/resolve/query` must use `sidecar_db_hint` from the graph as the DB path source of truth; it must not recompute DB location from the current process `GRAPHIFY_OUT`, because users can run query/explain later with `GRAPHIFY_OUT` pointing at the domain `--output-dir`.

- [ ] **Step 4: Run build integration tests**

Run:

```bash
uv run pytest tests/test_tabular_sidecar_build.py -q
```

Expected: pass for effective `graph` exclusion, effective `sidecar` files being skipped by `extract_tab`, sidecar-only manifest not failing the empty-graph guard, sidecar DB creation, sidecar source/table/column metadata including zero-row table/column projection, anchor nodes with `sidecar_ref` and `sidecar_ref_key`, path-ref skeleton nodes using shared path normalization, graph-level metadata, `graph.json` export preserving `data["graph"]["tabular_sidecar"]`, `--sidecar-db` override metadata, exact output directory placement, and a regression where graph-aware sidecar lookup still uses graph metadata when the current process `GRAPHIFY_OUT` points at the domain `--output-dir`.

## Task 6: Graph-Aware Search And Resolve CLI

**Files:**
- Create: `graphify/sidecar_cli.py`
- Modify: `graphify/__main__.py`
- Test: `tests/test_tabular_sidecar_cli.py`

- [ ] **Step 1: Implement CLI command dispatcher**

```python
# graphify/sidecar_cli.py
from __future__ import annotations

import argparse
import json
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
        raise SystemExit("error: raw sidecar mode requires --sidecar-db; graph-aware mode requires --graph")
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="graphify sidecar")
    sub = parser.add_subparsers(dest="cmd", required=True)
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
    _validate_db_identity(db_path, graph_meta, domain_id=args.domain)
    if args.cmd == "resolve":
        row = resolve_ref(db_path, args.sidecar_ref, domain_id=args.domain, graph_meta=graph_meta)
        _validate_graph_rows([row], graph_meta, require_union_hash=args.global_debug)
        print(json.dumps({"row": row}, ensure_ascii=False))
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
    print(json.dumps({"rows": rows}, ensure_ascii=False))
    return 0
```

Wire in `graphify/__main__.py`:

```python
elif cmd == "sidecar":
    from graphify.sidecar_cli import main as _sidecar_main
    sys.exit(_sidecar_main(sys.argv[2:]))
```

- [ ] **Step 2: Implement graph-aware validation**

Graph-aware mode must:

- Read graph-level `tabular_sidecar` metadata.
- Resolve DB path from graph `sidecar_db_hint` before considering any environment-derived default. The current process `GRAPHIFY_OUT` is not a reliable lookup source in graph-aware mode.
- Validate repo identity and `sidecar_db_id`; domain-scoped mode also validates `domain_id` and `sidecar_domain_config_hash`, while `--global-debug` validates `union_config_hash` for returned rows.
- For search results, use `file_key` first and `source_file` fallback to find source-file metadata in the graph.
- Validate `source_sha256` and `domain_file_config_hash`.
- For `sidecar query`, install scoped temp views first, validate every `current_domain_sources` row against graph source metadata before executing user SQL, then run the read-only statement against those views. In `--global-debug`, this validation must cover every source visible through the global debug views; if graph metadata is narrower than that scope, fail and tell the caller to use raw sidecar debug or provide graph metadata that covers the requested scope.
- Fail if source-file metadata is missing unless caller used raw sidecar mode with `--sidecar-db` and no `--graph`.

Also require `search_rows()` and `resolve_ref()` to include `domain_file_config_hash` in each returned row payload by joining `domain_files` for the requested `domain_id`; without that field graph-aware validation cannot prove per-file config freshness.

- [ ] **Step 3: Run CLI tests**

Run:

```bash
uv run pytest tests/test_tabular_sidecar_cli.py -q
```

Expected: pass for raw mode, graph-aware mode, stale hash failure, union hash failure in `--global-debug`, schema version mismatch failure, `--sidecar-db` override still validated against graph metadata, graph-aware lookup ignoring current `GRAPHIFY_OUT` when graph metadata supplies `sidecar_db_hint`, missing source metadata failure, `--file`/`--column-index` filtering, normalized value lookup, `not indexed for domain` failure, `sidecar query --domain` against temp views, `sidecar query --global-debug` source/union validation, direct physical table read rejection, SQL function allowlist rejection, SQL safety rejection, multi-statement SQL rejection, query timeout, query truncation metadata, query envelope metadata fields, and no false positive when a string literal or alias contains words such as `insert`, `update`, or `delete`, or a string literal contains `;`.

## Task 7: Report, Watch, And Diagnostics

**Files:**
- Modify: `graphify/report.py`
- Modify: `graphify/watch.py`
- Test: `tests/test_tabular_sidecar_build.py`
- Test: `tests/test_watch.py`

- [ ] **Step 1: Add report section from sidecar stats**

Extend `report.generate(..., sidecar_stats: dict | None = None)` and render:

```markdown
## Tabular Sidecar
- DB path: `<path>`
- schema version: `1`
- repo key: `<repo_key>`
- sidecar DB ID: `<sidecar_db_id>`
- sidecar instance ID: `<sidecar_instance_id>`
- generation: `<generation>`
- current snapshot: files `<n>`, tables `<n>`, rows `<n>`, indexed values `<n>`, refs `<n>`
- last run delta: files rebuilt `<n>`, rows rebuilt `<n>`, rows pruned `<n>`, warnings `<n>`
- auto decisions: graph `<n>`, sidecar `<n>`, reasons `<row_count>2000|column_count>80|file_size>1MB>`
- explicit graph warnings: `<n>` files exceeded auto thresholds but stayed on graph policy
- rejected files: `<n>`
- lock waits/fails: `<wait>/<fail>`
- SQLite busy timeouts: `<n>`
```

- [ ] **Step 2: Preserve ordinary watch behavior**

`watch.py` should continue treating `.tab` and TSV-like `.txt` as code-like for normal graph rebuilds, but it must not write sidecar DB without manifest context. When Phase 0 manifest mode records a domain output, tabular file changes should mark that domain as needing manifest rebuild rather than updating sidecar directly.

- [ ] **Step 3: Run report/watch tests**

Run:

```bash
uv run pytest tests/test_tabular_sidecar_build.py tests/test_watch.py -q
```

Expected: report contains sidecar current/delta fields, and existing watch tests still pass.

## Task 8: Update, Prune, And Shared Domain Isolation

**Files:**
- Modify: `graphify/tabular_sidecar.py`
- Modify: `graphify/code_build_runner.py`
- Test: `tests/test_tabular_sidecar_store.py`
- Test: `tests/test_tabular_sidecar_build.py`

- [ ] **Step 1: Replace current domain membership as a set**

During manifest update, delete current `domain_files` membership for `domain_id`, then insert only current effective `sidecar` files. Files removed from manifest or changed to effective `graph` must disappear from that domain sidecar scope.

- [ ] **Step 2: Rebuild derived indexes on union change**

When active domain configs for a shared file change, recompute `union_config_hash`, rebuild `indexed_values`, and keep `rows` if `sha256` is unchanged.

- [ ] **Step 3: Prune unreferenced files**

After membership replacement:

```sql
DELETE FROM source_files
WHERE file_id NOT IN (SELECT DISTINCT file_id FROM domain_files);
```

Because child FK indexes exist, cascade deletes must not require scanning every child table.

- [ ] **Step 4: Run isolation tests**

Run:

```bash
uv run pytest tests/test_tabular_sidecar_store.py tests/test_tabular_sidecar_build.py -q
```

Expected: pass for shared DB, two domains sharing one file, domain removal, union index shrink, and effective `graph` exclusion.

## Task 9: Compatibility And JX3 Smoke

**Files:**
- Add fixtures: `tests/fixtures/sidecar/jx3-mini/skills.tab`
- Add fixtures: `tests/fixtures/sidecar/jx3-mini/Buff.tab`
- Add fixtures: `tests/fixtures/sidecar/jx3-mini/recipeSkill.tab`
- Add fixtures: `tests/fixtures/sidecar/jx3-mini/CoolDownList.tab`
- Add fixtures: `tests/fixtures/sidecar/jx3-mini/skill.txt`
- Add fixtures: `tests/fixtures/sidecar/jx3-mini/buff.txt`
- Test: `tests/test_tabular_sidecar_build.py`

- [ ] **Step 1: Add generic and JX3 mini evals**

The generic fixture must prove Graphify value without JX3 names:

- graph-only cap loses high row data.
- sidecar search finds high row data.
- sidecar anchor resolves to full row through `sidecar_ref`.
- path-like refs are generic.
- update/prune leaves no stale rows.

The JX3 mini fixture must prove high-row `SkillID` and `BuffID` lookup works without adding JX3-specific relationship rules to Graphify.

- [ ] **Step 2: Record JX3 capacity smoke**

When a real JX3 checkout is available, run a lightweight profiling script before and after sidecar implementation and record the result in the PR or report. The baseline should include:

- `.tab`/`.tsv`/TSV-like `.txt` file count and total bytes.
- total row count and top 10 largest files by rows.
- files over the `auto` thresholds: `>1MB`, `>2000` rows, `>80` columns.
- resulting sidecar DB size, full rebuild time, and single changed-file rebuild time.

The latest local snapshot used while drafting this plan found roughly 84431 candidate files, 491MB source bytes, 4352443 rows, and 1103 files over at least one `auto` threshold. Treat this as a scale signal only; do not bake these absolute numbers into tests.

- [ ] **Step 3: Run full focused test suite**

Run:

```bash
uv run pytest \
  tests/test_tabular_manifest.py \
  tests/test_tabular_parser.py \
  tests/test_tabular_sidecar_store.py \
  tests/test_tabular_sidecar_cli.py \
  tests/test_tabular_sidecar_build.py \
  tests/test_tab.py \
  tests/test_watch.py \
  -q
```

Expected: pass.

- [ ] **Step 4: Run full compatibility suite**

Run:

```bash
uv run pytest tests -q
```

Expected: pass. If the environment lacks optional dependencies for unrelated tests, record exact skipped or failed tests and rerun the focused suite above.

## Self-Review Checklist

- [ ] Every effective `sidecar` file enters `domain_files`; effective `graph` files do not.
- [ ] Effective `sidecar` files are removed from the ordinary `extract()` input so `extract_tab` cannot emit duplicate row/column/path nodes for the same file.
- [ ] Sidecar projection is merged into the extraction `result` before `build_from_json(...)`, so sidecar-only manifests cannot trip the empty-graph guard.
- [ ] No full cell table exists in schema.
- [ ] `row_json` and `row_values_json` preserve duplicate headers and original order.
- [ ] `sidecar_ref` does not depend on SQLite autoincrement IDs.
- [ ] Raw sidecar mode and graph-aware mode have separate validation paths.
- [ ] Graph-aware search can validate cross-file results by `file_key` first and `source_file` fallback.
- [ ] `sidecar_domain_config_hash` is domain-scoped and excludes effective `graph` files.
- [ ] `sidecar_domain_config_hash` uses the same sorted canonical helper in manifest loading and DB validation.
- [ ] `sidecar search --domain` returns `not indexed for domain` instead of a silent miss for undeclared columns.
- [ ] `sidecar resolve --domain` returns only domain-visible indexed values plus generic path-like refs.
- [ ] `sidecar query --domain` uses read-only SQLite, exposes only domain-scoped temp views as the supported SQL surface, rejects unsafe SQL and multi-statement SQL, avoids false positives from SQL keywords or semicolons inside literals/aliases, and enforces default/max row limits plus timeout.
- [ ] `union_config_hash` is diagnostic for domain-scoped reads unless the query asks for global debug or union-derived facts.
- [ ] Ordinary directory watch does not write sidecar without manifest context.
- [ ] Existing `.tab` graph extraction remains compatible when sidecar is not enabled.
