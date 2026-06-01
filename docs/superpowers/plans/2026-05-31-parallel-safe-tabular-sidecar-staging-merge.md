# Parallel-Safe Tabular Sidecar Staging and Merge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Graphify tabular sidecar writes parallel-safe by staging each domain locally and merging into the canonical shared SQLite sidecar through one Graphify-owned writer.

**Architecture:** Keep Graphify manifest builds as single-domain invocations; wrappers such as JX3 own multi-domain process scheduling. Each domain build writes sidecar-active tabular data into an isolated staging DB, then Graphify merges that domain delta into the canonical sidecar, reads graph projection from the canonical DB, and only then writes successful domain output/state. Capability output and machine-readable build traces make wrapper behavior and bottlenecks observable without exposing sidecar internals.

**Tech Stack:** Python 3.10+, stdlib `sqlite3`, existing Graphify manifest build pipeline, `pytest`, subprocess-based CLI tests.

---

**Source Spec:** `docs/superpowers/specs/2026-05-31-parallel-safe-tabular-sidecar-staging-merge-design.md`

**Current Baseline:** `graphify/code_build_runner.py` calls `update_sidecar()` directly against the canonical sidecar DB, then calls `merge_sidecar_projection()` against that same DB. `graphify/tabular_sidecar.py` already owns schema, identity, update, search, resolve, and constrained query. `graphify/tabular_graph.py` already owns projection and graph metadata.

**Execution Boundary:** Do not add a Graphify multi-domain runner in this plan. Implement per-domain `staging-merge`; JX3 or other wrappers use `graphify capabilities --json` to decide whether parallel domain scheduling is safe.

**Stop Condition:** After Task 8, stop and report evidence. Do not implement JX3 wrapper scheduling in this repository.

## File Structure

- Create `graphify/capabilities.py`: return machine-readable Graphify capabilities, including tabular sidecar write modes.
- Create `graphify/build_trace.py`: collect stage timings and write success/failure trace JSON.
- Create `graphify/tabular_sidecar_paths.py`: resolve canonical DB path and staging attempt path with `--sidecar-db` support.
- Create `graphify/tabular_sidecar_staging.py`: write staging sidecar DBs and merge staging DBs into canonical sidecar DBs.
- Modify `graphify/tabular_sidecar.py`: expose small helpers needed by staging/merge instead of duplicating schema logic.
- Modify `graphify/tabular_graph.py`: add `sidecar_mode`, staging attempt, and merge generation metadata.
- Modify `graphify/code_build_runner.py`: use `staging-merge` for manifest sidecar builds, emit build traces, and read projection from canonical sidecar after merge.
- Modify `graphify/__main__.py`: route `graphify capabilities --json`.
- Modify `graphify/report.py`: include sidecar mode and merge generation in the Tabular Sidecar report section.
- Add tests:
  - `tests/test_capabilities_cli.py`
  - `tests/test_build_trace.py`
  - `tests/test_tabular_sidecar_paths.py`
  - `tests/test_tabular_sidecar_staging.py`
  - Extend `tests/test_tabular_sidecar_cli.py`
  - Extend `tests/test_tabular_sidecar_build.py`

## Task 1: Capabilities CLI

**Files:**
- Create: `graphify/capabilities.py`
- Modify: `graphify/__main__.py`
- Create: `tests/test_capabilities_cli.py`

- [ ] **Step 1: Write the failing CLI tests**

```python
# tests/test_capabilities_cli.py
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


def test_capabilities_json_reports_tabular_sidecar_modes(tmp_path):
    result = run_graphify("capabilities", "--json", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == 1
    assert payload["tabular_sidecar"]["schema_version"] == 1
    assert payload["tabular_sidecar"]["write_modes"] == ["shared-serial", "staging-merge"]
    assert payload["tabular_sidecar"]["default_write_mode"] == "shared-serial"
    assert payload["tabular_sidecar"]["supports_domain_staging_merge"] is True
    assert payload["tabular_sidecar"]["supports_build_trace"] is True


def test_capabilities_rejects_non_json_output_for_now(tmp_path):
    result = run_graphify("capabilities", cwd=tmp_path)

    assert result.returncode == 2
    assert "Usage: graphify capabilities --json" in result.stderr
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
uv run pytest tests/test_capabilities_cli.py -q
```

Expected: fail because `graphify capabilities` is not routed.

- [ ] **Step 3: Add capabilities payload**

```python
# graphify/capabilities.py
from __future__ import annotations

import json


def capabilities_payload() -> dict:
    return {
        "schema_version": 1,
        "tabular_sidecar": {
            "schema_version": 1,
            "write_modes": ["shared-serial", "staging-merge"],
            "default_write_mode": "shared-serial",
            "supports_domain_staging_merge": True,
            "supports_build_trace": True,
        },
    }


def capabilities_json() -> str:
    return json.dumps(capabilities_payload(), indent=2, sort_keys=True) + "\n"
```

In `graphify/__main__.py`, add a command branch near the existing top-level command dispatch for `sidecar`:

```python
    elif cmd == "capabilities":
        if sys.argv[2:] != ["--json"]:
            print("Usage: graphify capabilities --json", file=sys.stderr)
            sys.exit(2)
        from graphify.capabilities import capabilities_json

        print(capabilities_json(), end="")
        sys.exit(0)
```

Also update help text near the command list:

```python
    print("  capabilities --json  print machine-readable Graphify capabilities")
```

- [ ] **Step 4: Run tests and verify they pass**

Run:

```bash
uv run pytest tests/test_capabilities_cli.py -q
```

Expected: `2 passed`.

- [ ] **Step 5: Commit**

```bash
git add graphify/capabilities.py graphify/__main__.py tests/test_capabilities_cli.py
git commit -m "feat: expose graphify capabilities"
```

## Task 2: Sidecar Path Resolver

**Files:**
- Create: `graphify/tabular_sidecar_paths.py`
- Create: `tests/test_tabular_sidecar_paths.py`

- [ ] **Step 1: Write failing path resolver tests**

```python
# tests/test_tabular_sidecar_paths.py
from __future__ import annotations

from pathlib import Path

from graphify.tabular_sidecar_paths import plan_sidecar_paths, safe_domain_slug


def test_default_canonical_path_uses_active_graphify_output(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    active_output = repo / "graphify-out"

    paths = plan_sidecar_paths(
        repo_root=repo,
        active_graphify_output=active_output,
        sidecar_db_path=None,
        domain_id="skill-core",
        run_id="run-1",
        attempt_id="attempt-1",
    )

    assert paths.canonical_db == active_output / "sidecar" / "tabular.sqlite"
    assert paths.staging_db.parent == active_output / "sidecar" / "staging" / "run-1"
    assert paths.staging_db.name.startswith("skill-core-")
    assert paths.staging_db.name.endswith("-attempt-1.sqlite")


def test_sidecar_db_override_places_staging_next_to_override(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    canonical = tmp_path / "custom" / "tabular.sqlite"

    paths = plan_sidecar_paths(
        repo_root=repo,
        active_graphify_output=repo / "graphify-out",
        sidecar_db_path=canonical,
        domain_id="skill/core",
        run_id="run-2",
        attempt_id="attempt-2",
    )

    assert paths.canonical_db == canonical
    assert paths.staging_db.parent == canonical.parent / "staging" / "run-2"
    assert paths.staging_db.name.startswith("skill-core-")
    assert paths.staging_db.name.endswith("-attempt-2.sqlite")


def test_safe_domain_slug_is_stable_and_filename_safe():
    assert safe_domain_slug("client/settings/skill core") == "client-settings-skill-core"
    assert safe_domain_slug("...") == "domain"
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
uv run pytest tests/test_tabular_sidecar_paths.py -q
```

Expected: import failure for `graphify.tabular_sidecar_paths`.

- [ ] **Step 3: Implement resolver**

```python
# graphify/tabular_sidecar_paths.py
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re


@dataclass(frozen=True)
class SidecarPathPlan:
    canonical_db: Path
    staging_db: Path
    run_id: str
    attempt_id: str


def safe_domain_slug(domain_id: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", domain_id).strip(".-_")
    return slug or "domain"


def _domain_hash(domain_id: str) -> str:
    return sha256(domain_id.encode("utf-8")).hexdigest()[:12]


def plan_sidecar_paths(
    *,
    repo_root: Path,
    active_graphify_output: Path,
    sidecar_db_path: Path | None,
    domain_id: str,
    run_id: str,
    attempt_id: str,
) -> SidecarPathPlan:
    del repo_root  # reserved for future use; kept in signature for API stability
    canonical_db = (
        sidecar_db_path.resolve()
        if sidecar_db_path is not None
        else (active_graphify_output / "sidecar" / "tabular.sqlite").resolve()
    )
    name = f"{safe_domain_slug(domain_id)}-{_domain_hash(domain_id)}-{attempt_id}.sqlite"
    staging_db = canonical_db.parent / "staging" / run_id / name
    return SidecarPathPlan(
        canonical_db=canonical_db,
        staging_db=staging_db,
        run_id=run_id,
        attempt_id=attempt_id,
    )
```

- [ ] **Step 4: Run tests and verify they pass**

Run:

```bash
uv run pytest tests/test_tabular_sidecar_paths.py -q
```

Expected: `3 passed`.

- [ ] **Step 5: Commit**

```bash
git add graphify/tabular_sidecar_paths.py tests/test_tabular_sidecar_paths.py
git commit -m "feat: resolve sidecar staging paths"
```

## Task 3: Build Trace Writer

**Files:**
- Create: `graphify/build_trace.py`
- Create: `tests/test_build_trace.py`

- [ ] **Step 1: Write failing trace tests**

```python
# tests/test_build_trace.py
from __future__ import annotations

import json

from graphify.build_trace import BuildTrace


def test_build_trace_writes_success_json(tmp_path):
    trace = BuildTrace(domain_id="skill-core", sidecar_mode="staging-merge", process_workers=4)
    trace.record_stage_ms("tabular_parse_ms", 12)
    trace.record_cache(ast_hits=2, ast_misses=1, tabular_content_unchanged=3, tabular_content_changed=4)
    trace.record_sidecar(files_staged=1, rows_staged=2, files_merged=1, rows_merged=2)

    output = trace.write_success(tmp_path)

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert output == tmp_path / ".graphify_state" / "build-trace.json"
    assert payload["domain_id"] == "skill-core"
    assert payload["sidecar_mode"] == "staging-merge"
    assert payload["process_workers"] == 4
    assert payload["stages"]["tabular_parse_ms"] == 12
    assert payload["cache"]["ast_hits"] == 2
    assert payload["sidecar"]["rows_merged"] == 2


def test_build_trace_writes_failed_json(tmp_path):
    trace = BuildTrace(domain_id="skill-core", sidecar_mode="staging-merge", process_workers=None)
    trace.record_stage_ms("sidecar_merge_wait_ms", 5)

    output = trace.write_failed(tmp_path, error_class="RuntimeError", error_message="boom")

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert output == tmp_path / ".graphify_state" / "build-trace.failed.json"
    assert payload["error"]["class"] == "RuntimeError"
    assert payload["error"]["message"] == "boom"
    assert payload["stages"]["sidecar_merge_wait_ms"] == 5
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
uv run pytest tests/test_build_trace.py -q
```

Expected: import failure for `graphify.build_trace`.

- [ ] **Step 3: Implement `BuildTrace`**

```python
# graphify/build_trace.py
from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path


STAGE_KEYS = (
    "code_extract_ms",
    "tabular_parse_ms",
    "sidecar_stage_write_ms",
    "sidecar_merge_wait_ms",
    "sidecar_merge_write_ms",
    "sidecar_projection_ms",
    "graph_build_ms",
    "cluster_ms",
    "report_export_ms",
)


@dataclass
class BuildTrace:
    domain_id: str | None
    sidecar_mode: str
    process_workers: int | None
    stages: dict[str, int] = field(default_factory=lambda: {key: 0 for key in STAGE_KEYS})
    cache: dict[str, int] = field(default_factory=lambda: {
        "ast_hits": 0,
        "ast_misses": 0,
        "tabular_content_unchanged": 0,
        "tabular_content_changed": 0,
    })
    sidecar: dict[str, int] = field(default_factory=lambda: {
        "files_staged": 0,
        "files_merged": 0,
        "files_pruned": 0,
        "rows_staged": 0,
        "rows_merged": 0,
        "rows_pruned": 0,
        "indexed_values_merged": 0,
        "refs_merged": 0,
    })

    def record_stage_ms(self, key: str, value: int) -> None:
        if key not in self.stages:
            raise KeyError(f"unknown build trace stage: {key}")
        self.stages[key] = int(value)

    def record_cache(self, **values: int) -> None:
        for key, value in values.items():
            if key not in self.cache:
                raise KeyError(f"unknown build trace cache field: {key}")
            self.cache[key] = int(value)

    def record_sidecar(self, **values: int) -> None:
        for key, value in values.items():
            if key not in self.sidecar:
                raise KeyError(f"unknown build trace sidecar field: {key}")
            self.sidecar[key] = int(value)

    def payload(self) -> dict:
        return {
            "schema_version": 1,
            "domain_id": self.domain_id,
            "sidecar_mode": self.sidecar_mode,
            "process_workers": self.process_workers,
            "stages": self.stages,
            "cache": self.cache,
            "sidecar": self.sidecar,
        }

    def write_success(self, output_dir: Path) -> Path:
        return self._write(output_dir / ".graphify_state" / "build-trace.json", self.payload())

    def write_failed(self, output_dir: Path, *, error_class: str, error_message: str) -> Path:
        payload = self.payload()
        payload["error"] = {"class": error_class, "message": error_message}
        return self._write(output_dir / ".graphify_state" / "build-trace.failed.json", payload)

    @staticmethod
    def _write(path: Path, payload: dict) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return path
```

- [ ] **Step 4: Run tests and verify they pass**

Run:

```bash
uv run pytest tests/test_build_trace.py -q
```

Expected: `2 passed`.

- [ ] **Step 5: Commit**

```bash
git add graphify/build_trace.py tests/test_build_trace.py
git commit -m "feat: add manifest build trace writer"
```

## Task 4: Staging Sidecar Writer

**Files:**
- Create: `graphify/tabular_sidecar_staging.py`
- Modify: `graphify/tabular_sidecar.py`
- Create: `tests/test_tabular_sidecar_staging.py`

- [ ] **Step 1: Write failing staging writer tests**

```python
# tests/test_tabular_sidecar_staging.py
from __future__ import annotations

import json

from graphify.tabular_manifest import load_tabular_domain_manifest
from graphify.tabular_sidecar import connect_sidecar, read_sidecar_meta
from graphify.tabular_sidecar_staging import write_staging_sidecar


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
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
uv run pytest tests/test_tabular_sidecar_staging.py::test_write_staging_sidecar_writes_domain_rows_and_meta tests/test_tabular_sidecar_staging.py::test_write_staging_sidecar_supports_empty_membership -q
```

Expected: import failure for `write_staging_sidecar`.

- [ ] **Step 3: Expose meta setter and implement staging writer**

In `graphify/tabular_sidecar.py`, add a public wrapper around the existing private meta helper:

```python
def set_sidecar_meta(conn: sqlite3.Connection, key: str, value: object) -> None:
    _set_meta(conn, key, value)
```

Create `graphify/tabular_sidecar_staging.py`:

```python
from __future__ import annotations

from pathlib import Path

from graphify.tabular_manifest import TabularDomainManifest
from graphify.tabular_sidecar import (
    SidecarUpdateStats,
    connect_sidecar,
    set_sidecar_meta,
    update_sidecar,
)


def write_staging_sidecar(
    staging_db: Path,
    manifest: TabularDomainManifest,
    *,
    run_id: str,
    attempt_id: str,
) -> SidecarUpdateStats:
    staging_db.parent.mkdir(parents=True, exist_ok=True)
    # update_sidecar already calls ensure_schema + ensure_sidecar_identity internally,
    # so we only need to append staging-specific meta afterwards.
    stats = update_sidecar(staging_db, manifest)
    conn = connect_sidecar(staging_db)
    try:
        with conn:
            set_sidecar_meta(conn, "staging_schema_version", "1")
            set_sidecar_meta(conn, "sidecar_kind", "tabular")
            set_sidecar_meta(conn, "domain_id", manifest.domain_id)
            set_sidecar_meta(conn, "run_id", run_id)
            set_sidecar_meta(conn, "attempt_id", attempt_id)
    finally:
        conn.close()
    return stats
```

- [ ] **Step 4: Run tests and verify they pass**

Run:

```bash
uv run pytest tests/test_tabular_sidecar_staging.py::test_write_staging_sidecar_writes_domain_rows_and_meta tests/test_tabular_sidecar_staging.py::test_write_staging_sidecar_supports_empty_membership -q
```

Expected: `2 passed`.

- [ ] **Step 5: Commit**

```bash
git add graphify/tabular_sidecar.py graphify/tabular_sidecar_staging.py tests/test_tabular_sidecar_staging.py
git commit -m "feat: write domain staging sidecars"
```

## Task 5: Canonical Merge From Staging

**Files:**
- Modify: `graphify/tabular_sidecar_staging.py`
- Modify: `graphify/tabular_sidecar.py`
- Extend: `tests/test_tabular_sidecar_staging.py`

- [ ] **Step 1: Add merge tests**

Append these tests to `tests/test_tabular_sidecar_staging.py`:

```python
from graphify.tabular_sidecar import search_rows
from graphify.tabular_sidecar_staging import merge_domain_staging


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
```

- [ ] **Step 2: Run merge tests and verify they fail**

Run:

```bash
uv run pytest tests/test_tabular_sidecar_staging.py -q
```

Expected: fail because `merge_domain_staging` and `SidecarMergeStats` do not exist.

- [ ] **Step 3: Implement merge stats and merge function**

In `graphify/tabular_sidecar_staging.py`, add:

```python
from dataclasses import dataclass
import json
import sqlite3

from graphify.tabular_sidecar import (
    SidecarWriteLock,
    bump_sidecar_generation,
    ensure_schema,
    ensure_sidecar_identity,
    read_sidecar_meta,
    tabular_file_config_from_json,
)


@dataclass(frozen=True)
class SidecarMergeStats:
    files_merged: int = 0
    rows_merged: int = 0
    indexes_merged: int = 0
    refs_merged: int = 0
    files_pruned: int = 0
    rows_pruned: int = 0
    generation_before: int = 0
    generation_after: int = 0
    generation_changed: bool = False
```

Implement `merge_domain_staging()` with an explicit row ID remap when copying staged rows:

```python
def merge_domain_staging(domain_id: str, staging_db: Path, canonical_db: Path) -> SidecarMergeStats:
    staging_meta = read_sidecar_meta(staging_db)
    if staging_meta.get("domain_id") != domain_id:
        raise RuntimeError("staging sidecar domain_id does not match requested domain")
    canonical_meta = read_sidecar_meta(canonical_db) if canonical_db.exists() else {}
    if canonical_meta and canonical_meta.get("repo_key") and staging_meta.get("repo_key") != canonical_meta["repo_key"]:
        raise RuntimeError("staging/canonical repo_key mismatch")

    with SidecarWriteLock(canonical_db):
        src = connect_sidecar(staging_db, readonly=True)
        dst = connect_sidecar(canonical_db)
        try:
            ensure_schema(dst)
            repo_root = Path(staging_meta["repo_root"])
            repo_key = staging_meta["repo_key"]
            with dst:
                ensure_sidecar_identity(dst, repo_root=repo_root, repo_key=repo_key)
                before = int(read_sidecar_meta(canonical_db).get("sidecar_generation", "0"))
                files_merged = rows_merged = indexes_merged = refs_merged = files_pruned = rows_pruned = 0

                # Snapshot old canonical membership for idempotency fingerprint
                # Includes parser_version and encoding so metadata-only changes also bump generation
                old_membership = {
                    (str(row["source_file"]), str(row["config_hash"]), str(row["sha256"]),
                     str(row["parser_version"]), str(row["encoding"]))
                    for row in dst.execute("""
                        SELECT sf.source_file, df.config_hash, sf.sha256,
                               sf.parser_version, sf.encoding
                        FROM domain_files df
                        JOIN source_files sf ON sf.file_id = df.file_id
                        WHERE df.domain_id = ?
                        ORDER BY sf.source_file
                    """, (domain_id,))
                }
                staged_sources = src.execute("""
                    SELECT sf.file_id AS staging_file_id, sf.source_file, sf.file_key, sf.sha256,
                           sf.parser_version, sf.encoding, df.declared_policy, df.effective_policy,
                           df.config_json, df.config_hash
                    FROM source_files sf
                    JOIN domain_files df ON df.file_id = sf.file_id
                    WHERE df.domain_id = ?
                    ORDER BY sf.source_file
                """, (domain_id,)).fetchall()

                # Reject staging with older parser major version than canonical
                for staged_row in staged_sources:
                    canonical_file = dst.execute(
                        "SELECT parser_version FROM source_files WHERE source_file = ?",
                        (staged_row["source_file"],),
                    ).fetchone()
                    if canonical_file is not None:
                        canonical_major = int(str(canonical_file["parser_version"]).split(".")[0])
                        staging_major = int(str(staged_row["parser_version"]).split(".")[0])
                        if staging_major < canonical_major:
                            raise RuntimeError(
                                f"staging parser_version {staged_row['parser_version']} "
                                f"< canonical {canonical_file['parser_version']} for "
                                f"{staged_row['source_file']}; re-stage with current Graphify"
                            )

                affected_source_files = {
                    str(row["source_file"])
                    for row in dst.execute(
                        "SELECT sf.source_file FROM source_files sf JOIN domain_files df ON df.file_id = sf.file_id WHERE df.domain_id = ?",
                        (domain_id,),
                    )
                }
                affected_source_files.update(str(row["source_file"]) for row in staged_sources)

                dst.execute("DELETE FROM indexed_values WHERE domain_id = ?", (domain_id,))
                dst.execute("DELETE FROM domain_files WHERE domain_id = ?", (domain_id,))

                # Track which canonical source_files already existed before this merge
                preexisting_sources: set[str] = {
                    str(row[0])
                    for row in dst.execute("SELECT source_file FROM source_files").fetchall()
                }

                staging_to_canonical: dict[int, int] = {}
                new_source_files: set[int] = set()
                for row in staged_sources:
                    is_new_file = str(row["source_file"]) not in preexisting_sources
                    dst.execute("""
                        INSERT INTO source_files(source_file, file_key, sha256, parser_version, union_config_hash, encoding)
                        VALUES(?, ?, ?, ?, '', ?)
                        ON CONFLICT(source_file) DO UPDATE SET
                          file_key=excluded.file_key,
                          parser_version=excluded.parser_version,
                          encoding=excluded.encoding
                    """, (row["source_file"], row["file_key"], row["sha256"], row["parser_version"], row["encoding"]))
                    canonical_file_id = int(dst.execute(
                        "SELECT file_id FROM source_files WHERE source_file = ?",
                        (row["source_file"],),
                    ).fetchone()["file_id"])
                    staging_to_canonical[int(row["staging_file_id"])] = canonical_file_id
                    if is_new_file:
                        new_source_files.add(canonical_file_id)
                    dst.execute("""
                        INSERT INTO domain_files(domain_id, file_id, declared_policy, effective_policy, config_json, config_hash)
                        VALUES(?, ?, ?, ?, ?, ?)
                    """, (domain_id, canonical_file_id, row["declared_policy"], row["effective_policy"], row["config_json"], row["config_hash"]))

                for source_file in sorted(affected_source_files):
                    file_row = dst.execute(
                        "SELECT file_id, sha256 FROM source_files WHERE source_file = ?",
                        (source_file,),
                    ).fetchone()
                    if file_row is None:
                        continue
                    canonical_file_id = int(file_row["file_id"])
                    active_hashes = [
                        str(r["config_hash"])
                        for r in dst.execute(
                            "SELECT config_hash FROM domain_files WHERE file_id = ? ORDER BY domain_id",
                            (canonical_file_id,),
                        )
                    ]
                    if not active_hashes:
                        rows_pruned += int(dst.execute(
                            "SELECT COUNT(*) FROM rows WHERE file_id = ?",
                            (canonical_file_id,),
                        ).fetchone()[0])
                        dst.execute("DELETE FROM source_files WHERE file_id = ?", (canonical_file_id,))
                        files_pruned += 1
                        continue

                    staged_file_id = next((sid for sid, cid in staging_to_canonical.items() if cid == canonical_file_id), None)
                    if staged_file_id is not None:
                        staged_sha = src.execute(
                            "SELECT sha256 FROM source_files WHERE file_id = ?",
                            (staged_file_id,),
                        ).fetchone()["sha256"]
                        # Copy rows for new files or when content hash changed
                        if canonical_file_id in new_source_files or staged_sha != file_row["sha256"]:
                            _copy_staged_file_payload(src, dst, staged_file_id, canonical_file_id)
                            files_merged += 1
                            rows_merged += int(dst.execute(
                                "SELECT COUNT(*) FROM rows WHERE file_id = ?",
                                (canonical_file_id,),
                            ).fetchone()[0])
                    # TODO: skip rebuild if content and config unchanged (perf optimization)
                    _rebuild_indexes_for_canonical_file(dst, canonical_file_id)
                    indexes_merged += int(dst.execute(
                        "SELECT COUNT(*) FROM indexed_values WHERE file_id = ?",
                        (canonical_file_id,),
                    ).fetchone()[0])
                    refs_merged += int(dst.execute(
                        "SELECT COUNT(*) FROM tabular_refs WHERE file_id = ?",
                        (canonical_file_id,),
                    ).fetchone()[0])

                # Compute new membership fingerprint for idempotency check
                new_membership = {
                    (str(row["source_file"]), str(row["config_hash"]), str(row["sha256"]),
                     str(row["parser_version"]), str(row["encoding"]))
                    for row in dst.execute("""
                        SELECT sf.source_file, df.config_hash, sf.sha256,
                               sf.parser_version, sf.encoding
                        FROM domain_files df
                        JOIN source_files sf ON sf.file_id = df.file_id
                        WHERE df.domain_id = ?
                        ORDER BY sf.source_file
                    """, (domain_id,))
                }
                changed = old_membership != new_membership
                after = before
                if changed:
                    after = bump_sidecar_generation(dst)
            return SidecarMergeStats(
                files_merged=files_merged,
                rows_merged=rows_merged,
                indexes_merged=indexes_merged,
                refs_merged=refs_merged,
                files_pruned=files_pruned,
                rows_pruned=rows_pruned,
                generation_before=before,
                generation_after=after,
                generation_changed=after != before,
            )
        finally:
            src.close()
            dst.close()
```

Add the two helper functions referenced above in the same module. Use `row_json` and `row_values_json` from canonical rows to rebuild domain indexes rather than relying on staging row IDs:

```python
def _copy_staged_file_payload(src: sqlite3.Connection, dst: sqlite3.Connection, staging_file_id: int, canonical_file_id: int) -> None:
    dst.execute("DELETE FROM tabular_tables WHERE file_id = ?", (canonical_file_id,))
    table_map: dict[int, int] = {}
    for table in src.execute("SELECT * FROM tabular_tables WHERE file_id = ?", (staging_file_id,)):
        dst.execute("""
            INSERT INTO tabular_tables(file_id, kind, delimiter, header_row, row_count, column_count, warnings_json)
            VALUES(?, ?, ?, ?, ?, ?, ?)
        """, (canonical_file_id, table["kind"], table["delimiter"], table["header_row"], table["row_count"], table["column_count"], table["warnings_json"]))
        canonical_table_id = int(dst.execute("SELECT last_insert_rowid()").fetchone()[0])
        table_map[int(table["table_id"])] = canonical_table_id
        for col in src.execute("SELECT * FROM tabular_columns WHERE table_id = ? ORDER BY column_index", (table["table_id"],)):
            dst.execute("""
                INSERT INTO tabular_columns(table_id, column_index, name, normalized_name)
                VALUES(?, ?, ?, ?)
            """, (canonical_table_id, col["column_index"], col["name"], col["normalized_name"]))

    row_map: dict[int, int] = {}
    for row in src.execute("SELECT * FROM rows WHERE file_id = ? ORDER BY row_no", (staging_file_id,)):
        dst.execute("""
            INSERT INTO rows(file_id, table_id, row_no, line_hash, raw_line, row_json, row_values_json)
            VALUES(?, ?, ?, ?, ?, ?, ?)
        """, (canonical_file_id, table_map.get(int(row["table_id"])), row["row_no"], row["line_hash"], row["raw_line"], row["row_json"], row["row_values_json"]))
        row_map[int(row["row_id"])] = int(dst.execute("SELECT last_insert_rowid()").fetchone()[0])

    for ref in src.execute("SELECT * FROM tabular_refs WHERE file_id = ?", (staging_file_id,)):
        dst.execute("""
            INSERT INTO tabular_refs(file_id, row_id, target_ref, value)
            VALUES(?, ?, ?, ?)
        """, (canonical_file_id, row_map[int(ref["row_id"])], ref["target_ref"], ref["value"]))

    staged_source = src.execute("SELECT * FROM source_files WHERE file_id = ?", (staging_file_id,)).fetchone()
    dst.execute("""
        UPDATE source_files
        SET sha256 = ?, parser_version = ?, encoding = ?
        WHERE file_id = ?
    """, (staged_source["sha256"], staged_source["parser_version"], staged_source["encoding"], canonical_file_id))
```

Before adding `_rebuild_indexes_for_canonical_file()`, expose two public helpers in `graphify/tabular_sidecar.py` so staging code does not import private names:

```python
def sidecar_row_from_sql_row(row_data):
    return row_from_sql_row(row_data)


def insert_indexes_for_row(
    conn: sqlite3.Connection,
    file_id: int,
    row_id: int,
    domain_id: str,
    file_cfg: TabularFileConfig,
    row,
) -> int:
    return _insert_indexes(conn, file_id, row_id, domain_id, file_cfg, row)
```

Then add the canonical index rebuild helper in `graphify/tabular_sidecar_staging.py`:

```python
def _rebuild_indexes_for_canonical_file(dst: sqlite3.Connection, canonical_file_id: int) -> None:
    from graphify.tabular_sidecar import insert_indexes_for_row, sidecar_row_from_sql_row

    dst.execute("DELETE FROM indexed_values WHERE file_id = ?", (canonical_file_id,))
    rows = dst.execute(
        "SELECT row_id, row_json, row_values_json FROM rows WHERE file_id = ? ORDER BY row_id",
        (canonical_file_id,),
    ).fetchall()
    domain_cfgs = dst.execute("""
        SELECT domain_id, config_json
        FROM domain_files
        WHERE file_id = ?
        ORDER BY domain_id
    """, (canonical_file_id,)).fetchall()
    for domain_cfg in domain_cfgs:
        file_cfg = tabular_file_config_from_json(json.loads(domain_cfg["config_json"]))
        for row_data in rows:
            insert_indexes_for_row(
                dst,
                canonical_file_id,
                int(row_data["row_id"]),
                str(domain_cfg["domain_id"]),
                file_cfg,
                sidecar_row_from_sql_row(row_data),
            )
```

- [ ] **Step 4: Run merge tests and verify they pass**

Run:

```bash
uv run pytest tests/test_tabular_sidecar_staging.py -q
```

Expected: all staging tests pass.

- [ ] **Step 5: Run existing sidecar store tests**

Run:

```bash
uv run pytest tests/test_tabular_sidecar_store.py -q
```

Expected: existing shared-serial semantics still pass.

- [ ] **Step 6: Commit**

```bash
git add graphify/tabular_sidecar.py graphify/tabular_sidecar_staging.py tests/test_tabular_sidecar_staging.py
git commit -m "feat: merge staging sidecars into canonical store"
```

## Task 6: Manifest Build Integration and Metadata

**Files:**
- Modify: `graphify/code_build_runner.py`
- Modify: `graphify/tabular_graph.py`
- Modify: `graphify/report.py`
- Modify: `graphify/capabilities.py`
- Extend: `tests/test_tabular_sidecar_cli.py`
- Extend: `tests/test_tabular_sidecar_build.py`

- [ ] **Step 1: Add integration assertions for metadata and custom sidecar path**

Extend `tests/test_tabular_sidecar_cli.py::test_manifest_build_accepts_sidecar_db_override`:

```python
    assert meta["sidecar_mode"] == "staging-merge"
    assert meta["staging_run_id"]
    assert meta["staging_attempt_id"]
    assert int(meta["sidecar_merge_generation_after"]) >= int(meta["sidecar_merge_generation_before"])
    assert (sidecar_db.parent / "staging").exists()
```

Add a new CLI test:

```python
def test_manifest_build_writes_build_trace(tmp_path):
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

    build = run_graphify("extract", "--manifest", str(manifest), "--output-dir", str(out), cwd=repo)

    assert build.returncode == 0, build.stderr
    trace_path = out / ".graphify_state" / "build-trace.json"
    assert trace_path.exists()
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    assert trace["sidecar_mode"] == "staging-merge"
    assert trace["sidecar"]["files_staged"] == 1
    assert trace["sidecar"]["files_merged"] == 1
```

- [ ] **Step 2: Run integration tests and verify they fail**

Run:

```bash
uv run pytest tests/test_tabular_sidecar_cli.py::test_manifest_build_accepts_sidecar_db_override tests/test_tabular_sidecar_cli.py::test_manifest_build_writes_build_trace -q
```

Expected: fail because build runner still writes canonical sidecar directly and metadata lacks staging fields.

- [ ] **Step 3: Update graph metadata helper**

Change `graphify/tabular_graph.py::graph_sidecar_metadata(...)` signature to accept mode and merge metadata:

```python
def graph_sidecar_metadata(
    *,
    manifest: TabularDomainManifest,
    repo_key: str,
    repo_root: Path,
    graphify_output: Path,
    db_path: Path,
    sidecar_meta: dict,
    sidecar_mode: str = "shared-serial",
    staging_run_id: str | None = None,
    staging_attempt_id: str | None = None,
    merge_generation_before: int | None = None,
    merge_generation_after: int | None = None,
) -> dict:
    payload = {
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
        "sidecar_mode": sidecar_mode,
    }
    if staging_run_id is not None:
        payload["staging_run_id"] = staging_run_id
    if staging_attempt_id is not None:
        payload["staging_attempt_id"] = staging_attempt_id
    if merge_generation_before is not None:
        payload["sidecar_merge_generation_before"] = str(merge_generation_before)
    if merge_generation_after is not None:
        payload["sidecar_merge_generation_after"] = str(merge_generation_after)
    return payload
```

- [ ] **Step 4: Integrate staging mode in `build_code_graph()`**

In `graphify/code_build_runner.py`, import standard helpers locally near the sidecar block:

```python
from time import perf_counter
from uuid import uuid4
```

Replace the direct `update_sidecar(effective_sidecar_db, tabular_manifest)` path with:

```python
from graphify.build_trace import BuildTrace
from graphify.tabular_sidecar_paths import plan_sidecar_paths
from graphify.tabular_sidecar_staging import merge_domain_staging, write_staging_sidecar
from graphify.tabular_sidecar import read_sidecar_meta

run_id = uuid4().hex
attempt_id = uuid4().hex
trace = BuildTrace(
    domain_id=tabular_manifest.domain_id,
    sidecar_mode="staging-merge",
    process_workers=max_workers,
)
path_plan = plan_sidecar_paths(
    repo_root=repo_root,
    active_graphify_output=effective_graphify_output,
    sidecar_db_path=sidecar_db_path,
    domain_id=tabular_manifest.domain_id,
    run_id=run_id,
    attempt_id=attempt_id,
)
effective_sidecar_db = path_plan.canonical_db
stage_start = perf_counter()
sidecar_update_stats = write_staging_sidecar(
    path_plan.staging_db,
    tabular_manifest,
    run_id=run_id,
    attempt_id=attempt_id,
)
trace.record_stage_ms("sidecar_stage_write_ms", int((perf_counter() - stage_start) * 1000))
trace.record_sidecar(
    files_staged=sidecar_update_stats.files_upserted,
    rows_staged=sidecar_update_stats.rows_upserted,
)
merge_start = perf_counter()
merge_stats = merge_domain_staging(tabular_manifest.domain_id, path_plan.staging_db, effective_sidecar_db)
trace.record_stage_ms("sidecar_merge_write_ms", int((perf_counter() - merge_start) * 1000))
trace.record_sidecar(
    files_merged=merge_stats.files_merged,
    rows_merged=merge_stats.rows_merged,
    files_pruned=merge_stats.files_pruned,
    rows_pruned=merge_stats.rows_pruned,
    indexed_values_merged=merge_stats.indexes_merged,
    refs_merged=merge_stats.refs_merged,
)
sidecar_meta = read_sidecar_meta(effective_sidecar_db)
```

Keep the existing graph-only prune condition, but route it through empty membership staging when `read_sidecar_domain_config_hash()` shows the domain exists. For the first implementation, always create staging and merge when `tabular_manifest is not None`; this keeps empty membership prune correct and keeps behavior simple.

Pass the new metadata into `graph_sidecar_metadata()`:

```python
graph.graph["tabular_sidecar"] = graph_sidecar_metadata(
    manifest=tabular_manifest,
    repo_key=tabular_manifest.repo_key,
    repo_root=tabular_manifest.repo_root,
    graphify_output=effective_graphify_output,
    db_path=effective_sidecar_db,
    sidecar_meta=sidecar_meta,
    sidecar_mode="staging-merge",
    staging_run_id=run_id,
    staging_attempt_id=attempt_id,
    merge_generation_before=merge_stats.generation_before,
    merge_generation_after=merge_stats.generation_after,
)
```

After staged output replacement succeeds, write the trace:

```python
trace.write_success(output_dir)
```

If this function does not currently have a broad exception boundary, do not add one around the whole build in this task. Failed trace is added in Task 7.

- [ ] **Step 5: Update report sidecar stats**

In `graphify/code_build_runner.py`, include mode and merge generation in `sidecar_stats_dict`:

```python
sidecar_stats_dict["sidecar_mode"] = "staging-merge"
sidecar_stats_dict["merge_generation_before"] = merge_stats.generation_before
sidecar_stats_dict["merge_generation_after"] = merge_stats.generation_after
```

In `graphify/report.py`, render these keys under `## Tabular Sidecar`:

```python
        if "sidecar_mode" in sidecar_stats:
            lines.append(f"- sidecar mode: `{sidecar_stats['sidecar_mode']}`")
        if "merge_generation_before" in sidecar_stats and "merge_generation_after" in sidecar_stats:
            lines.append(
                f"- merge generation: `{sidecar_stats['merge_generation_before']}` -> `{sidecar_stats['merge_generation_after']}`"
            )
```

- [ ] **Step 6: Upgrade capabilities default_write_mode to staging-merge**

Now that the build runner uses staging-merge, update `graphify/capabilities.py` so wrappers can detect the new default:

```python
            "default_write_mode": "staging-merge",
```

And update the corresponding assertion in `tests/test_capabilities_cli.py`:

```python
    assert payload["tabular_sidecar"]["default_write_mode"] == "staging-merge"
```

Run:

```bash
uv run pytest tests/test_capabilities_cli.py -q
```

Expected: `2 passed`.

- [ ] **Step 7: Run targeted integration tests**

Run:

```bash
uv run pytest tests/test_tabular_sidecar_cli.py::test_manifest_build_accepts_sidecar_db_override tests/test_tabular_sidecar_cli.py::test_manifest_build_writes_build_trace tests/test_tabular_sidecar_cli.py::test_manifest_build_prunes_sidecar_db_when_domain_becomes_graph_only -q
```

Expected: all selected tests pass.

- [ ] **Step 8: Commit**

```bash
git add graphify/code_build_runner.py graphify/tabular_graph.py graphify/report.py graphify/capabilities.py tests/test_capabilities_cli.py tests/test_tabular_sidecar_cli.py tests/test_tabular_sidecar_build.py
git commit -m "feat: use staging merge for manifest sidecars"
```

## Task 7: Failure Trace and Cleanup Safety

**Files:**
- Modify: `graphify/code_build_runner.py`
- Modify: `graphify/tabular_sidecar_staging.py`
- Extend: `tests/test_tabular_sidecar_cli.py`
- Extend: `tests/test_tabular_sidecar_staging.py`

- [ ] **Step 1: Add failure trace test**

Add to `tests/test_tabular_sidecar_cli.py`:

```python
def test_manifest_build_writes_failed_trace_when_sidecar_merge_fails(tmp_path):
    from graphify.tabular_sidecar import connect_sidecar, ensure_schema, ensure_sidecar_identity

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
    wrong_repo_db = tmp_path / "wrong.sqlite"
    other_repo = tmp_path / "other"
    other_repo.mkdir()
    conn = connect_sidecar(wrong_repo_db)
    try:
        ensure_schema(conn)
        with conn:
            ensure_sidecar_identity(conn, repo_root=other_repo, repo_key="other-repo-key")
    finally:
        conn.close()

    build = run_graphify("extract", "--manifest", str(manifest), "--output-dir", str(out), "--sidecar-db", str(wrong_repo_db), cwd=repo)

    assert build.returncode != 0
    failed_trace = out / ".graphify_state" / "build-trace.failed.json"
    assert failed_trace.exists()
    payload = json.loads(failed_trace.read_text(encoding="utf-8"))
    assert payload["sidecar_mode"] == "staging-merge"
    assert payload["error"]["class"]
```

- [ ] **Step 2: Add cleanup helper tests**

Add to `tests/test_tabular_sidecar_staging.py`:

```python
from graphify.tabular_sidecar_staging import cleanup_staging_attempt


def test_cleanup_staging_attempt_removes_only_requested_attempt(tmp_path):
    keep = tmp_path / "staging" / "run" / "keep.sqlite"
    remove = tmp_path / "staging" / "run" / "remove.sqlite"
    keep.parent.mkdir(parents=True)
    keep.write_text("keep", encoding="utf-8")
    remove.write_text("remove", encoding="utf-8")

    cleanup_staging_attempt(remove)

    assert keep.exists()
    assert not remove.exists()
```

- [ ] **Step 3: Run tests and verify they fail**

Run:

```bash
uv run pytest tests/test_tabular_sidecar_staging.py::test_cleanup_staging_attempt_removes_only_requested_attempt -q
```

Expected: import failure for `cleanup_staging_attempt`.

- [ ] **Step 4: Implement cleanup helper**

In `graphify/tabular_sidecar_staging.py`:

```python
def cleanup_staging_attempt(staging_db: Path) -> None:
    for path in (
        staging_db,
        staging_db.with_suffix(staging_db.suffix + "-wal"),
        staging_db.with_suffix(staging_db.suffix + "-shm"),
    ):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
```

- [ ] **Step 5: Add failed trace around sidecar path in build runner**

In `graphify/code_build_runner.py`, keep the exception boundary narrow around sidecar staging/merge/projection so non-sidecar build behavior is not changed:

```python
try:
    # existing staging/merge/projection block
except Exception as exc:
    if trace is not None:
        trace.write_failed(output_dir, error_class=exc.__class__.__name__, error_message=str(exc))
    raise
finally:
    if path_plan is not None:
        from graphify.tabular_sidecar_staging import cleanup_staging_attempt

        cleanup_staging_attempt(path_plan.staging_db)
```

Initialize `trace = None` and `path_plan = None` before the sidecar block.

- [ ] **Step 6: Run targeted tests**

Run:

```bash
uv run pytest tests/test_tabular_sidecar_staging.py::test_cleanup_staging_attempt_removes_only_requested_attempt tests/test_tabular_sidecar_cli.py::test_manifest_build_writes_failed_trace_when_sidecar_merge_fails -q
```

Expected: selected tests pass.

- [ ] **Step 7: Commit**

```bash
git add graphify/code_build_runner.py graphify/tabular_sidecar_staging.py tests/test_tabular_sidecar_cli.py tests/test_tabular_sidecar_staging.py
git commit -m "feat: record failed sidecar build traces"
```

## Task 8: Parallel Equivalence and Final Verification

**Files:**
- Extend: `tests/test_tabular_sidecar_cli.py`
- Modify: `README.md`
- Modify: `docs/how-it-works.md`

- [ ] **Step 1: Add a subprocess parallel equivalence test**

Add to `tests/test_tabular_sidecar_cli.py`:

```python
from concurrent.futures import ThreadPoolExecutor


def test_parallel_domain_builds_share_canonical_sidecar(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "shared.tab").write_text("ID\tName\tValue\n1\tAlpha\t100\n", encoding="utf-8")
    domains = {
        "domain-a": {"indexed_columns": ["Name"]},
        "domain-b": {"indexed_columns": ["Value"]},
    }
    manifests = {}
    for domain_id, cfg in domains.items():
        manifest = repo / f"{domain_id}.json"
        manifest.write_text(json.dumps({
            "repo_root": str(repo),
            "domain_id": domain_id,
            "files": [{
                "path": "shared.tab",
                "tabular_policy": "sidecar",
                "primary_key": "ID",
                "indexed_columns": cfg["indexed_columns"],
            }],
        }), encoding="utf-8")
        manifests[domain_id] = manifest

    def build(domain_id: str):
        return run_graphify(
            "extract",
            "--manifest", str(manifests[domain_id]),
            "--output-dir", str(repo / "graphify-out" / "domains" / domain_id),
            cwd=repo,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(build, sorted(domains)))

    assert all(result.returncode == 0 for result in results), [result.stderr for result in results]
    search_a = run_graphify("sidecar", "search", "--domain", "domain-a", "--column", "Name", "--value", "Alpha", cwd=repo)
    search_b = run_graphify("sidecar", "search", "--domain", "domain-b", "--column", "Value", "--value", "100", cwd=repo)
    assert search_a.returncode == 0, search_a.stderr
    assert search_b.returncode == 0, search_b.stderr
    assert json.loads(search_a.stdout)["rows"][0]["row_json"]["ID"] == "1"
    assert json.loads(search_b.stdout)["rows"][0]["row_json"]["ID"] == "1"
```

- [ ] **Step 2: Run parallel test and verify it passes**

Run:

```bash
uv run pytest tests/test_tabular_sidecar_cli.py::test_parallel_domain_builds_share_canonical_sidecar -q
```

Expected: selected test passes.

- [ ] **Step 3: Update docs**

In `README.md`, update the manifest sidecar section to mention `staging-merge` and capabilities:

```markdown
Graphify writes sidecar-active manifest builds through `staging-merge`: each
domain build first writes a disposable staging DB, then Graphify merges it into
the canonical shared sidecar. Wrappers can check support with:

```bash
graphify capabilities --json
```
```

In `docs/how-it-works.md`, update the "Manifest domains and tabular sidecars" section with the same behavior and note that wrappers own multi-domain `jobs`.

- [ ] **Step 4: Run focused sidecar suites**

Run:

```bash
uv run pytest tests/test_capabilities_cli.py tests/test_build_trace.py tests/test_tabular_sidecar_paths.py tests/test_tabular_sidecar_staging.py tests/test_tabular_sidecar_store.py tests/test_tabular_sidecar_cli.py tests/test_tabular_sidecar_build.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Run lint and full tests**

Run:

```bash
uv run ruff check .
uv run pytest -q
```

Expected: ruff passes; full test suite passes.

- [ ] **Step 6: Update Graphify graph after code changes**

Run:

```bash
uv run graphify update .
```

Expected: graph update completes successfully. If `uv run graphify update .` is not the established command in this checkout, use the project-local command that `graphify-out/GRAPH_REPORT.md` recommends.

- [ ] **Step 7: Commit final docs and verification**

```bash
git add README.md docs/how-it-works.md graphify tests graphify-out
git commit -m "docs: describe sidecar staging merge"
```

## Self-Review Checklist

- Spec coverage:
  - Capability contract: Task 1 (initial `shared-serial` default), Task 6 (upgrade to `staging-merge` default).
  - `--sidecar-db` and staging path resolution: Task 2.
  - Build traces and failed traces: Tasks 3 and 7.
  - Staging writer and empty membership: Task 4.
  - Canonical merge, shared file semantics, idempotency, prune, repo_key validation, parser_version major check: Task 5.
  - Projection from canonical sidecar and graph metadata: Task 6.
  - Parallel equivalence and docs: Task 8.
- Placeholder scan: the plan intentionally contains no `TBD` or unresolved placeholders.
- Type consistency: `SidecarPathPlan`, `BuildTrace`, `SidecarMergeStats`, `write_staging_sidecar()`, `merge_domain_staging()`, and `cleanup_staging_attempt()` are introduced before later tasks depend on them.
- Scope check: JX3 wrapper scheduling is excluded; Graphify only exposes per-domain staging/merge plus capabilities.
