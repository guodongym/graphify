# Generic `.tab` Structured Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add generic `.tab` support as local structural extraction so every tab-delimited configuration file is indexed without LLM/API calls.

**Architecture:** `.tab` enters `FileType.CODE`, dispatches to a new `extract_tab()` extractor, and produces schema-agnostic file/column/row/value/path-reference graph nodes. The extractor remains bounded and cacheable: caps produce `warnings`/`truncated` metadata, while real failures use top-level `error`.

**Tech Stack:** Python 3.10+, standard-library `csv`, `Path`, existing `graphify.detect`, `graphify.extract`, `graphify.watch`, `graphify.validate`.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `graphify/detect.py` | Modify | Classify all `.tab` files as code by adding `.tab` to `CODE_EXTENSIONS`. |
| `graphify/extract.py` | Modify | Add `extract_tab()`, helper functions, caps, lower-case `_get_extractor()` dispatch. |
| `graphify/watch.py` | No production change expected | Watch coverage comes from `CODE_EXTENSIONS`; tests pin behavior. |
| `tests/test_detect.py` | Modify | Add `.tab` / `.TAB` classification tests. |
| `tests/test_watch.py` | Modify | Add `.tab` watched-extension test. |
| `tests/test_tab.py` | Create | Cover generic extraction, encoding, ragged rows, path references, value throttling, caps, validation. |
| `tests/fixtures/sample.tab` | Create | UTF-8 TSV fixture. |
| `tests/fixtures/sample_gb18030.tab` | Create | GB18030 TSV fixture with Chinese path content. |
| `README.md` | Modify | Add `.tab` to supported code extension list. |

---

## Task 1: Wire `.tab` Into Detection, Watch, And Dispatch

**Files:**
- Modify: `graphify/detect.py`
- Modify: `graphify/extract.py`
- Modify: `tests/test_detect.py`
- Modify: `tests/test_watch.py`
- Create: `tests/test_tab.py`

- [ ] **Step 1: Write failing detection and watch tests**

Append to `tests/test_detect.py`:

```python
def test_classify_tab_as_code():
    assert classify_file(Path("config.tab")) == FileType.CODE


def test_classify_uppercase_tab_as_code():
    assert classify_file(Path("CONFIG.TAB")) == FileType.CODE
```

Append to `tests/test_watch.py` inside the watched-extension section:

```python
def test_watched_extensions_includes_tab():
    assert ".tab" in _WATCHED_EXTENSIONS
```

- [ ] **Step 2: Write failing dispatch test**

Create `tests/test_tab.py`:

```python
"""Tests for generic .tab structured extraction."""
from __future__ import annotations

from pathlib import Path

from graphify.extract import extract, extract_tab
from graphify.validate import validate_extraction


def labels(result: dict) -> set[str]:
    return {n["label"] for n in result["nodes"]}


def relations(result: dict) -> set[str]:
    return {e["relation"] for e in result["edges"]}


def test_uppercase_tab_dispatches_to_extractor(tmp_path):
    path = tmp_path / "CONFIG.TAB"
    path.write_text("ID\tName\n1\tAlpha\n", encoding="utf-8")

    result = extract([path], cache_root=tmp_path, parallel=False)

    assert "CONFIG.TAB" in labels(result)
    assert "ID (column)" in labels(result)
    assert "ID 1" in labels(result)
```

- [ ] **Step 3: Run tests to verify failures**

Run:

```bash
pytest tests/test_detect.py::test_classify_tab_as_code tests/test_detect.py::test_classify_uppercase_tab_as_code tests/test_watch.py::test_watched_extensions_includes_tab tests/test_tab.py::test_uppercase_tab_dispatches_to_extractor -q
```

Expected:

- Detection tests fail because `.tab` is not in `CODE_EXTENSIONS`.
- Watch test fails because `.tab` is not watched.
- Dispatch test fails because `extract_tab` is not defined or dispatch returns no nodes.

- [ ] **Step 4: Add `.tab` to `CODE_EXTENSIONS`**

In `graphify/detect.py`, add `'.tab'` to the `CODE_EXTENSIONS` set.

Target shape:

```python
CODE_EXTENSIONS = {
    # existing entries...
    '.sh', '.bash', '.json', '.tab',
}
```

Keep the existing one-line style if the file is still using one-line sets; do not reformat unrelated entries.

- [ ] **Step 5: Add temporary minimal extractor and lower-case dispatch**

In `graphify/extract.py`, add this minimal `extract_tab()` before `_DISPATCH`:

```python
def extract_tab(path: Path) -> dict:
    """Extract structural nodes and edges from a generic tab-delimited file."""
    str_path = str(path)
    file_nid = _make_id(str(path))
    return {
        "nodes": [{
            "id": file_nid,
            "label": path.name,
            "file_type": "code",
            "source_file": str_path,
            "source_location": "L1",
        }],
        "edges": [],
    }
```

Register `.tab` in `_DISPATCH`:

```python
".tab": extract_tab,
```

Update `_get_extractor()`:

```python
def _get_extractor(path: Path) -> Any | None:
    """Return the correct extractor function for a file, or None if unsupported."""
    if path.name.lower().endswith(".blade.php"):
        return extract_blade
    return _DISPATCH.get(path.suffix.lower())
```

Do not remove the existing uppercase Fortran keys from `_DISPATCH` in this task. They become redundant for dispatch lookup after `suffix.lower()`, but `collect_files()` still uses `_DISPATCH.keys()` for case-sensitive `rglob("*{ext}")` file discovery.

- [ ] **Step 6: Run focused tests**

Run:

```bash
pytest tests/test_detect.py::test_classify_tab_as_code tests/test_detect.py::test_classify_uppercase_tab_as_code tests/test_watch.py::test_watched_extensions_includes_tab -q
```

Expected: `3 passed`.

Run:

```bash
pytest tests/test_tab.py::test_uppercase_tab_dispatches_to_extractor -q
```

Expected: still fails because minimal extractor does not emit columns/rows. That failure drives Task 2.

Do not commit unless the user explicitly requests commits for this branch.

---

## Task 2: Implement Generic TSV Parsing, Encoding, Headers, And Rows

**Files:**
- Modify: `graphify/extract.py`
- Modify: `tests/test_tab.py`
- Create: `tests/fixtures/sample.tab`
- Create: `tests/fixtures/sample_gb18030.tab`

- [ ] **Step 1: Add fixture files**

Create `tests/fixtures/sample.tab`:

```tsv
ID	Name	Mode
1	Alpha	active
2	Beta	inactive
```

Create `tests/fixtures/sample_gb18030.tab` by running this command:

```bash
python - <<'PY'
from pathlib import Path
body = "AIType\tScriptFile\tIsPositive\n17673\tscripts\\Map\\成都\\ai\\动物表现\\动物逃跑.lua\t0\n"
Path("tests/fixtures/sample_gb18030.tab").write_bytes(body.encode("gb18030"))
PY
```

- [ ] **Step 2: Add failing parser tests**

Append to `tests/test_tab.py`:

```python
FIXTURES = Path(__file__).parent / "fixtures"


def test_extract_tab_finds_file_columns_and_rows():
    result = extract_tab(FIXTURES / "sample.tab")

    assert "sample.tab" in labels(result)
    assert "ID (column)" in labels(result)
    assert "Name (column)" in labels(result)
    assert "Mode (column)" in labels(result)
    assert "ID 1" in labels(result)
    assert "ID 2" in labels(result)
    assert "contains" in relations(result)
    assert validate_extraction(result) == []


def test_extract_tab_decodes_gb18030():
    result = extract_tab(FIXTURES / "sample_gb18030.tab")

    assert "AIType 17673" in labels(result)
    assert "ScriptFile (column)" in labels(result)
    assert validate_extraction(result) == []


def test_extract_tab_decodes_utf8_bom(tmp_path):
    path = tmp_path / "bom.tab"
    path.write_bytes("ID\tName\n1\tAlpha\n".encode("utf-8-sig"))

    result = extract_tab(path)

    assert "ID 1" in labels(result)
    assert validate_extraction(result) == []


def test_extract_tab_handles_crlf(tmp_path):
    path = tmp_path / "crlf.tab"
    path.write_bytes(b"ID\tName\r\n1\tAlpha\r\n")

    result = extract_tab(path)

    assert "ID 1" in labels(result)
    assert validate_extraction(result) == []


def test_extract_tab_header_only_file(tmp_path):
    path = tmp_path / "header-only.tab"
    path.write_text("ID\tName\n", encoding="utf-8")

    result = extract_tab(path)

    assert "ID (column)" in labels(result)
    assert "Name (column)" in labels(result)
    assert "ID 1" not in labels(result)
    assert validate_extraction(result) == []


def test_extract_tab_single_column_file(tmp_path):
    path = tmp_path / "single-column.tab"
    path.write_text("Name\nAlpha\nBeta\n", encoding="utf-8")

    result = extract_tab(path)

    assert "Name (column)" in labels(result)
    assert "Name Alpha" in labels(result)
    assert "Name Beta" in labels(result)
    assert validate_extraction(result) == []


def test_extract_tab_falls_back_to_line_number_when_no_identity_column(tmp_path):
    path = tmp_path / "no-identity.tab"
    path.write_text("A\tB\nx\t1\nx\t1\n\t\n", encoding="utf-8")

    result = extract_tab(path)

    assert "row 2" in labels(result)
    assert "row 3" in labels(result)
    assert "row 4" in labels(result)
    assert validate_extraction(result) == []


def test_extract_tab_handles_ragged_rows(tmp_path):
    path = tmp_path / "ragged.tab"
    path.write_text("ID\tName\n1\tAlpha\tExtra\n2\n", encoding="utf-8")

    result = extract_tab(path)

    assert "ID 1" in labels(result)
    assert "ID 2" in labels(result)
    assert "extra_1 (column)" in labels(result)
    assert validate_extraction(result) == []


def test_extract_tab_falls_back_to_line_number_for_empty_identity(tmp_path):
    path = tmp_path / "empty-id.tab"
    path.write_text("ID\tName\n\tAlpha\n2\tBeta\n", encoding="utf-8")

    result = extract_tab(path)

    assert "row 2" in labels(result)
    assert "ID 2" in labels(result)
    assert validate_extraction(result) == []


def test_extract_tab_empty_file_returns_file_node_only(tmp_path):
    path = tmp_path / "empty.tab"
    path.write_text("", encoding="utf-8")

    result = extract_tab(path)

    assert labels(result) == {"empty.tab"}
    assert result["edges"] == []
    assert validate_extraction(result) == []


def test_extract_tab_blank_file_returns_file_node_only(tmp_path):
    path = tmp_path / "blank.tab"
    path.write_text("\n\n", encoding="utf-8")

    result = extract_tab(path)

    assert labels(result) == {"blank.tab"}
    assert result["edges"] == []
    assert validate_extraction(result) == []


def test_extract_tab_normalizes_empty_and_duplicate_headers(tmp_path):
    path = tmp_path / "headers.tab"
    path.write_text("\tName\tName\n1\tAlpha\tBeta\n", encoding="utf-8")

    result = extract_tab(path)

    assert "column_1 (column)" in labels(result)
    assert len([n for n in result["nodes"] if n["label"] == "Name (column)"]) == 2
    assert validate_extraction(result) == []
```

- [ ] **Step 3: Run parser tests to verify failure**

Run:

```bash
pytest tests/test_tab.py::test_extract_tab_finds_file_columns_and_rows tests/test_tab.py::test_extract_tab_decodes_gb18030 tests/test_tab.py::test_extract_tab_decodes_utf8_bom tests/test_tab.py::test_extract_tab_handles_crlf tests/test_tab.py::test_extract_tab_header_only_file tests/test_tab.py::test_extract_tab_single_column_file tests/test_tab.py::test_extract_tab_falls_back_to_line_number_when_no_identity_column tests/test_tab.py::test_extract_tab_handles_ragged_rows tests/test_tab.py::test_extract_tab_falls_back_to_line_number_for_empty_identity tests/test_tab.py::test_extract_tab_empty_file_returns_file_node_only tests/test_tab.py::test_extract_tab_blank_file_returns_file_node_only tests/test_tab.py::test_extract_tab_normalizes_empty_and_duplicate_headers -q
```

Expected: non-empty parser tests fail because the temporary extractor only emits a file node; empty/blank file-node-only tests may already pass.

- [ ] **Step 4: Implement parsing helpers**

In `graphify/extract.py`, add imports near the top:

```python
import csv
from io import StringIO
from graphify.detect import CODE_EXTENSIONS
```

Add helper constants and functions before `extract_tab()`:

```python
_TAB_MAX_BYTES = 5 * 1024 * 1024
_TAB_MAX_ROWS = 10_000
_TAB_MAX_COLUMNS = 200
_TAB_MAX_VALUE_NODES = 500
_TAB_MAX_PATH_REFS = 10_000


def _decode_tab_bytes(raw: bytes) -> tuple[str, str]:
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8-replace"


def _read_tab_text(path: Path) -> tuple[str, list[str], bool]:
    warnings: list[str] = []
    truncated = False
    with path.open("rb") as f:
        raw = f.read(_TAB_MAX_BYTES + 1)
    if len(raw) > _TAB_MAX_BYTES:
        truncated = True
        warnings.append(f"truncated to {_TAB_MAX_BYTES} bytes")
        raw = raw[:_TAB_MAX_BYTES]
        last_newline = max(raw.rfind(b"\n"), raw.rfind(b"\r"))
        if last_newline >= 0:
            raw = raw[:last_newline + 1]
        else:
            warnings.append("no complete tab-delimited record inside byte limit")
            raw = b""
    text, encoding = _decode_tab_bytes(raw)
    if encoding == "utf-8-replace":
        warnings.append("decoded with replacement after utf-8/gb18030 failed")
    return text, warnings, truncated


def _normalise_tab_headers(raw_headers: list[str]) -> list[tuple[str, str]]:
    seen: dict[str, int] = {}
    out: list[tuple[str, str]] = []
    for idx, raw in enumerate(raw_headers, start=1):
        label = raw.strip().strip("\r")
        if not label:
            label = f"column_{idx}"
        base = label
        count = seen.get(base, 0) + 1
        seen[base] = count
        key = base if count == 1 else f"{base}_{count}"
        out.append((key, label))
    return out


def _pad_tab_row(row: list[str], width: int) -> list[str]:
    if len(row) < width:
        return row + [""] * (width - len(row))
    return row


def _choose_tab_identity(headers: list[tuple[str, str]], rows: list[list[str]]) -> int | None:
    if not rows or not headers:
        return None
    best_idx: int | None = None
    best_score = -1.0
    for idx in range(len(headers)):
        values = [r[idx].strip() for r in rows if idx < len(r) and r[idx].strip()]
        if not values:
            continue
        unique = len(set(values))
        if idx == 0 and unique == len(values):
            return 0
        uniqueness = unique / max(len(values), 1)
        coverage = len(values) / max(len(rows), 1)
        score = uniqueness * 0.7 + coverage * 0.3
        if uniqueness >= 0.8 and coverage >= 0.8 and score > best_score:
            best_idx = idx
            best_score = score
    return best_idx
```

- [ ] **Step 5: Replace `extract_tab()` with full file/column/row extraction**

Replace the temporary `extract_tab()` with:

```python
def extract_tab(path: Path) -> dict:
    """Extract structural nodes and edges from a generic tab-delimited file."""
    try:
        text, warnings, truncated = _read_tab_text(path)
    except Exception as e:
        return {"nodes": [], "edges": [], "error": f"{type(e).__name__}: {e}"}

    str_path = str(path)
    stem = _file_stem(path)
    nodes: list[dict] = []
    edges: list[dict] = []
    seen_ids: set[str] = set()

    def add_node(
        nid: str,
        label: str,
        file_type: str,
        line: int,
        extra: dict | None = None,
    ) -> None:
        if nid not in seen_ids:
            seen_ids.add(nid)
            node = {
                "id": nid,
                "label": label,
                "file_type": file_type,
                "source_file": str_path,
                "source_location": f"L{line}",
            }
            if extra:
                node.update(extra)
            nodes.append(node)

    def add_edge(src: str, tgt: str, relation: str, line: int, context: str) -> None:
        edges.append({
            "source": src,
            "target": tgt,
            "relation": relation,
            "context": context,
            "confidence": "EXTRACTED",
            "source_file": str_path,
            "source_location": f"L{line}",
            "weight": 1.0,
        })

    file_nid = _make_id(str(path))
    add_node(file_nid, path.name, "code", 1)

    reader = csv.reader(StringIO(text), delimiter="\t")
    parsed = list(reader)
    if not parsed:
        result = {"nodes": nodes, "edges": edges, "input_tokens": 0, "output_tokens": 0}
        if warnings:
            result["warnings"] = warnings
        if truncated:
            result["truncated"] = True
        return result

    raw_headers = parsed[0][:_TAB_MAX_COLUMNS]
    headers = _normalise_tab_headers(raw_headers)
    rows = parsed[1:_TAB_MAX_ROWS + 1]
    max_width = min(_TAB_MAX_COLUMNS, max([len(raw_headers), *(len(r) for r in rows)] or [0]))
    if max_width == 0:
        result = {"nodes": nodes, "edges": edges, "input_tokens": 0, "output_tokens": 0}
        if warnings:
            result["warnings"] = warnings
        if truncated:
            result["truncated"] = True
        return result
    while len(headers) < max_width:
        idx = len(headers) + 1
        headers.append((f"extra_{idx - len(raw_headers)}", f"extra_{idx - len(raw_headers)}"))
    rows = [_pad_tab_row(r[:max_width], max_width) for r in rows]

    if len(parsed) - 1 > _TAB_MAX_ROWS:
        warnings.append(f"indexed first {_TAB_MAX_ROWS} rows")
        truncated = True
    if max((len(r) for r in parsed if r), default=0) > _TAB_MAX_COLUMNS:
        warnings.append(f"indexed first {_TAB_MAX_COLUMNS} columns")
        truncated = True

    column_ids: list[str] = []
    for idx, (key, label) in enumerate(headers, start=1):
        col_nid = _make_id(stem, "column", key)
        column_ids.append(col_nid)
        add_node(col_nid, f"{label} (column)", "code", 1)
        add_edge(file_nid, col_nid, "contains", 1, "table")

    identity_idx = _choose_tab_identity(headers, rows)
    for row_offset, row in enumerate(rows, start=2):
        row_label = f"row {row_offset}"
        if identity_idx is not None and identity_idx < len(row):
            value = row[identity_idx].strip()
            if value:
                identity_label = headers[identity_idx][1]
                row_label = f"{identity_label} {value}"
        row_nid = _make_id(stem, "row", str(row_offset), row_label)
        add_node(row_nid, row_label, "code", row_offset)
        add_edge(file_nid, row_nid, "contains", row_offset, "table")

    result = {"nodes": nodes, "edges": edges, "input_tokens": 0, "output_tokens": 0}
    if warnings:
        result["warnings"] = warnings
    if truncated:
        result["truncated"] = True
    return result
```

- [ ] **Step 6: Run parser tests**

Run:

```bash
pytest tests/test_tab.py::test_uppercase_tab_dispatches_to_extractor tests/test_tab.py::test_extract_tab_finds_file_columns_and_rows tests/test_tab.py::test_extract_tab_decodes_gb18030 tests/test_tab.py::test_extract_tab_decodes_utf8_bom tests/test_tab.py::test_extract_tab_handles_crlf tests/test_tab.py::test_extract_tab_header_only_file tests/test_tab.py::test_extract_tab_single_column_file tests/test_tab.py::test_extract_tab_falls_back_to_line_number_when_no_identity_column tests/test_tab.py::test_extract_tab_handles_ragged_rows tests/test_tab.py::test_extract_tab_falls_back_to_line_number_for_empty_identity tests/test_tab.py::test_extract_tab_empty_file_returns_file_node_only tests/test_tab.py::test_extract_tab_blank_file_returns_file_node_only tests/test_tab.py::test_extract_tab_normalizes_empty_and_duplicate_headers -q
```

Expected: all parser tests pass. The GB18030 test in this task verifies decoding and column/row extraction only; Chinese path label coverage is introduced in Task 3 with path-reference extraction.

Do not commit unless the user explicitly requests commits for this branch.

---

## Task 3: Add Path Reference Detection And Stub Nodes

**Files:**
- Modify: `graphify/extract.py`
- Modify: `tests/test_tab.py`

- [ ] **Step 1: Add failing path-reference tests**

Append to `tests/test_tab.py`:

```python
def edge_labels(result: dict, relation: str) -> set[tuple[str, str]]:
    by_id = {n["id"]: n["label"] for n in result["nodes"]}
    return {
        (by_id.get(e["source"], e["source"]), by_id.get(e["target"], e["target"]))
        for e in result["edges"]
        if e["relation"] == relation
    }


def test_path_like_cells_emit_references_to_existing_file(tmp_path):
    scripts = tmp_path / "scripts" / "ai"
    scripts.mkdir(parents=True)
    target = scripts / "StandardAI.lua"
    target.write_text("function tick() end\n", encoding="utf-8")
    tab = tmp_path / "ai.tab"
    tab.write_text("ID\tScript\n1\tscripts/ai/StandardAI.lua\n", encoding="utf-8")

    result = extract_tab(tab)

    assert ("ID 1", "StandardAI.lua") in edge_labels(result, "references")
    target_nodes = [n for n in result["nodes"] if n["label"] == "StandardAI.lua"]
    assert target_nodes
    assert target_nodes[0]["source_file"].endswith("scripts/ai/StandardAI.lua")
    assert validate_extraction(result) == []


def test_path_reference_connects_to_scanned_file_node(tmp_path):
    scripts = tmp_path / "scripts" / "ai"
    scripts.mkdir(parents=True)
    target = scripts / "StandardAI.lua"
    target.write_text("function tick() end\n", encoding="utf-8")
    tab = tmp_path / "ai.tab"
    tab.write_text("ID\tScript\n1\tscripts/ai/StandardAI.lua\n", encoding="utf-8")

    result = extract([tab, target], cache_root=tmp_path, parallel=False)

    target_file_ids = {
        n["id"]
        for n in result["nodes"]
        if n["label"] == "StandardAI.lua" and n["source_file"].endswith("scripts/ai/StandardAI.lua")
    }
    assert target_file_ids
    assert any(
        e["target"] in target_file_ids
        for e in result["edges"]
        if e["relation"] == "references"
    )
    assert validate_extraction(result) == []


def test_path_reference_resolves_against_project_root_fallback(tmp_path):
    (tmp_path / ".git").mkdir()
    scripts = tmp_path / "scripts" / "ai"
    scripts.mkdir(parents=True)
    target = scripts / "StandardAI.lua"
    target.write_text("function tick() end\n", encoding="utf-8")
    config_dir = tmp_path / "data" / "config"
    config_dir.mkdir(parents=True)
    tab = config_dir / "ai.tab"
    tab.write_text("ID\tScript\n1\tscripts/ai/StandardAI.lua\n", encoding="utf-8")

    result = extract_tab(tab)

    assert ("ID 1", "StandardAI.lua") in edge_labels(result, "references")
    target_nodes = [n for n in result["nodes"] if n["label"] == "StandardAI.lua"]
    assert target_nodes
    assert target_nodes[0]["source_file"].endswith("scripts/ai/StandardAI.lua")
    assert validate_extraction(result) == []


def test_path_like_cells_emit_stub_for_missing_file(tmp_path):
    tab = tmp_path / "ai.tab"
    tab.write_text("ID\tScript\n1\tscripts/ai/MissingAI.lua\n", encoding="utf-8")

    result = extract_tab(tab)

    assert ("ID 1", "scripts/ai/MissingAI.lua") in edge_labels(result, "references")
    stub_nodes = [n for n in result["nodes"] if n["label"] == "scripts/ai/MissingAI.lua"]
    assert stub_nodes
    assert stub_nodes[0]["source_file"] == str(tab)
    assert validate_extraction(result) == []


def test_url_values_are_not_local_path_references(tmp_path):
    tab = tmp_path / "urls.tab"
    tab.write_text("ID\tUrl\n1\thttps://cdn.example.com/config.json\n", encoding="utf-8")

    result = extract_tab(tab)

    assert "https://cdn.example.com/config.json" not in labels(result)
    assert "references" not in relations(result)
    assert validate_extraction(result) == []


def test_backslash_paths_are_normalized(tmp_path):
    tab = tmp_path / "ai.tab"
    tab.write_text("ID\tScript\n1\tscripts\\Map\\成都\\ai\\动物表现\\动物逃跑.lua\n", encoding="utf-8")

    result = extract_tab(tab)

    assert any("scripts/Map/成都/ai/动物表现/动物逃跑.lua" == label for label in labels(result))
    assert "references" in relations(result)
    assert validate_extraction(result) == []
```

- [ ] **Step 2: Run path tests to verify failure**

Run:

```bash
pytest tests/test_tab.py::test_path_like_cells_emit_references_to_existing_file tests/test_tab.py::test_path_reference_connects_to_scanned_file_node tests/test_tab.py::test_path_reference_resolves_against_project_root_fallback tests/test_tab.py::test_path_like_cells_emit_stub_for_missing_file tests/test_tab.py::test_url_values_are_not_local_path_references tests/test_tab.py::test_backslash_paths_are_normalized -q
```

Expected: fail because references are not emitted.

- [ ] **Step 3: Add path-reference helpers**

In `graphify/extract.py`, add helpers before `extract_tab()`:

```python
_TAB_PATH_EXTENSIONS = {
    *CODE_EXTENSIONS,
    ".txt", ".md", ".xml", ".yaml", ".yml",
}


_TAB_PROJECT_ROOT_MARKERS = {
    ".git", "pyproject.toml", "package.json", "go.mod", "Cargo.toml", "graphify-out",
}


def _normalise_tab_path_value(value: str) -> str:
    return value.strip().strip('"\'').replace("\\", "/")


def _is_tab_url_value(value: str) -> bool:
    return re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", value) is not None


def _looks_like_tab_path(value: str) -> bool:
    cleaned = _normalise_tab_path_value(value)
    if not cleaned or len(cleaned) > 512:
        return False
    if _is_tab_url_value(cleaned):
        return False
    suffix = Path(cleaned).suffix.lower()
    if suffix in _TAB_PATH_EXTENSIONS:
        return True
    if "/" in cleaned:
        parts = [p for p in cleaned.split("/") if p and p not in (".", "..")]
        return len(parts) >= 2 and any("." in p for p in parts[-1:])
    return False


def _find_tab_project_root(tab_path: Path) -> Path | None:
    for parent in tab_path.resolve().parents:
        if any((parent / marker).exists() for marker in _TAB_PROJECT_ROOT_MARKERS):
            return parent
    return None


def _tab_path_candidates(tab_path: Path, normalised: str) -> list[Path]:
    candidates = [(tab_path.parent / normalised).resolve()]
    project_root = _find_tab_project_root(tab_path)
    if project_root is not None:
        root_candidate = (project_root / normalised).resolve()
        if root_candidate not in candidates:
            candidates.append(root_candidate)
    return candidates


def _tab_reference_node(raw_value: str, tab_path: Path) -> tuple[str, str, str]:
    normalised = _normalise_tab_path_value(raw_value)
    for candidate in _tab_path_candidates(tab_path, normalised):
        if candidate.exists():
            return _make_id(str(candidate)), candidate.name, str(candidate)
    return _make_id(str(tab_path), "ref", normalised), normalised, str(tab_path)
```

- [ ] **Step 4: Add reference emission inside the row loop**

In `extract_tab()`, after creating each row node, walk the row cells:

```python
    path_ref_count = 0
    path_ref_skipped = False
    # place before row loop
```

Inside the row loop after `add_edge(file_nid, row_nid, "contains", row_offset, "table")`:

```python
        for cell in row:
            if not _looks_like_tab_path(cell):
                continue
            if path_ref_count >= _TAB_MAX_PATH_REFS:
                path_ref_skipped = True
                continue
            ref_nid, ref_label, ref_source = _tab_reference_node(cell, path)
            if ref_nid not in seen_ids:
                seen_ids.add(ref_nid)
                nodes.append({
                    "id": ref_nid,
                    "label": ref_label,
                    "file_type": "code",
                    "source_file": ref_source,
                    "source_location": f"L{row_offset}",
                })
            add_edge(row_nid, ref_nid, "references", row_offset, "path")
            path_ref_count += 1
```

After the row loop:

```python
    if path_ref_skipped:
        warnings.append(f"indexed first {_TAB_MAX_PATH_REFS} path references")
        truncated = True
```

- [ ] **Step 5: Run path and GB18030 tests**

Run:

```bash
pytest tests/test_tab.py::test_extract_tab_decodes_gb18030 tests/test_tab.py::test_path_like_cells_emit_references_to_existing_file tests/test_tab.py::test_path_reference_connects_to_scanned_file_node tests/test_tab.py::test_path_reference_resolves_against_project_root_fallback tests/test_tab.py::test_path_like_cells_emit_stub_for_missing_file tests/test_tab.py::test_url_values_are_not_local_path_references tests/test_tab.py::test_backslash_paths_are_normalized -q
```

Expected: `7 passed`.

Do not commit unless the user explicitly requests commits for this branch.

---

## Task 4: Add Value Node Throttling And `sets` Edges

**Files:**
- Modify: `graphify/extract.py`
- Modify: `tests/test_tab.py`

- [ ] **Step 1: Add failing value-node tests**

Append to `tests/test_tab.py`:

```python
def test_low_cardinality_non_constant_values_emit_sets_edges(tmp_path):
    path = tmp_path / "modes.tab"
    path.write_text("ID\tMode\n1\tactive\n2\tinactive\n3\tactive\n", encoding="utf-8")

    result = extract_tab(path)

    assert "Mode=active" in labels(result)
    assert "Mode=inactive" in labels(result)
    value_nodes = [n for n in result["nodes"] if n["label"] == "Mode=active"]
    assert value_nodes[0].get("context") == "table_value"
    assert "sets" in relations(result)
    assert validate_extraction(result) == []


def test_constant_columns_do_not_create_value_hubs(tmp_path):
    path = tmp_path / "constant.tab"
    path.write_text("ID\tAlertRange\n1\t768\n2\t768\n3\t768\n", encoding="utf-8")

    result = extract_tab(path)

    assert "AlertRange=768" not in labels(result)
    assert validate_extraction(result) == []


def test_high_cardinality_columns_do_not_create_value_nodes(tmp_path):
    path = tmp_path / "high-cardinality.tab"
    rows = ["ID\tName"] + [f"{i}\tName{i}" for i in range(1, 31)]
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")

    result = extract_tab(path)

    assert "Name=Name1" not in labels(result)
    assert validate_extraction(result) == []
```

- [ ] **Step 2: Run value tests to verify failure**

Run:

```bash
pytest tests/test_tab.py::test_low_cardinality_non_constant_values_emit_sets_edges tests/test_tab.py::test_constant_columns_do_not_create_value_hubs tests/test_tab.py::test_high_cardinality_columns_do_not_create_value_nodes -q
```

Expected: first test fails because no value nodes exist; the other two may pass incidentally.

- [ ] **Step 3: Add value-column helper**

In `graphify/extract.py`, add:

```python
def _tab_value_columns(headers: list[tuple[str, str]], rows: list[list[str]], identity_idx: int | None) -> set[int]:
    selected: set[int] = set()
    row_count = max(len(rows), 1)
    max_unique = min(20, max(2, int(row_count * 0.2)))
    for idx, _ in enumerate(headers):
        if idx == identity_idx:
            continue
        values = [r[idx].strip() for r in rows if idx < len(r) and r[idx].strip()]
        if not values:
            continue
        unique_values = set(values)
        if len(unique_values) <= 1:
            continue
        if len(unique_values) > max_unique:
            continue
        if any(len(v) > 80 for v in unique_values):
            continue
        selected.add(idx)
    return selected
```

- [ ] **Step 4: Emit value nodes and `sets` edges**

In `extract_tab()`, after `identity_idx = _choose_tab_identity(headers, rows)`, add:

```python
    value_columns = _tab_value_columns(headers, rows, identity_idx)
    value_node_count = 0
    value_node_skipped = False
```

Inside the row loop after path reference handling:

```python
        for idx in value_columns:
            if idx >= len(row):
                continue
            value = row[idx].strip()
            if not value:
                continue
            header_label = headers[idx][1]
            value_label = f"{header_label}={value}"
            value_nid = _make_id(stem, "value", header_label, value)
            if value_nid not in seen_ids:
                if value_node_count >= _TAB_MAX_VALUE_NODES:
                    value_node_skipped = True
                    continue
                add_node(value_nid, value_label, "concept", row_offset, {"context": "table_value"})
                value_node_count += 1
            add_edge(row_nid, value_nid, "sets", row_offset, "value")
```

After the row loop:

```python
    if value_node_skipped:
        warnings.append(f"indexed first {_TAB_MAX_VALUE_NODES} value nodes")
        truncated = True
```

- [ ] **Step 5: Run value tests**

Run:

```bash
pytest tests/test_tab.py::test_low_cardinality_non_constant_values_emit_sets_edges tests/test_tab.py::test_constant_columns_do_not_create_value_hubs tests/test_tab.py::test_high_cardinality_columns_do_not_create_value_nodes -q
```

Expected: `3 passed`.

Do not commit unless the user explicitly requests commits for this branch.

---

## Task 5: Add Cap/Warning Tests, README Update, And Full Verification

**Files:**
- Modify: `graphify/extract.py`
- Modify: `tests/test_tab.py`
- Modify: `README.md`

- [ ] **Step 1: Add failing cap contract test**

Append to `tests/test_tab.py`:

```python
def test_tab_row_cap_sets_warning_not_error(tmp_path, monkeypatch, capsys):
    import graphify.extract as gx

    monkeypatch.setattr(gx, "_TAB_MAX_ROWS", 2)
    path = tmp_path / "large.tab"
    path.write_text("ID\tName\n1\tA\n2\tB\n3\tC\n", encoding="utf-8")

    result = extract_tab(path)

    assert result.get("truncated") is True
    assert "warnings" in result
    assert "error" not in result
    captured = capsys.readouterr()
    assert "warning" in captured.err.lower()
    assert validate_extraction(result) == []


def test_tab_byte_cap_does_not_decode_partial_record(tmp_path, monkeypatch, capsys):
    import graphify.extract as gx

    monkeypatch.setattr(gx, "_TAB_MAX_BYTES", 4)
    path = tmp_path / "partial.tab"
    path.write_text("ID\tName\n1\tAlpha\n", encoding="utf-8")

    result = extract_tab(path)

    assert labels(result) == {"partial.tab"}
    assert result["edges"] == []
    assert result.get("truncated") is True
    assert "error" not in result
    captured = capsys.readouterr()
    assert "warning" in captured.err.lower()
    assert validate_extraction(result) == []
```

- [ ] **Step 2: Run cap test to verify failure**

Run:

```bash
pytest tests/test_tab.py::test_tab_row_cap_sets_warning_not_error tests/test_tab.py::test_tab_byte_cap_does_not_decode_partial_record -q
```

Expected: may fail because no stderr warning is printed yet and byte-cap truncation may still decode a partial record.

- [ ] **Step 3: Print concise warning on cap/truncation**

In `extract_tab()`, before returning `result`, add this block in every return path that may carry `warnings`: the `not parsed` return path, the `max_width == 0` return path, and the final return path. A small local helper is acceptable if it keeps the return paths consistent.

```python
    if warnings:
        print(f"  warning: {path} indexed with limits: {'; '.join(warnings)}", file=sys.stderr, flush=True)
```

Because `sys` is already imported at the top of `extract.py`, no new import is needed.

- [ ] **Step 4: Run complete tab test suite**

Run:

```bash
pytest tests/test_tab.py -q
```

Expected: all `tests/test_tab.py` tests pass.

- [ ] **Step 5: Update README supported extensions**

In `README.md`, find the "What files it handles" table. In the Code extensions row, add `.tab` near `.json`.

Target substring:

```markdown
.sh .bash .json .tab
```

Do not update translations in this first implementation unless the user asks; the spec explicitly allows deferring translated README updates.

- [ ] **Step 6: Run focused verification**

Run:

```bash
pytest tests/test_detect.py tests/test_watch.py tests/test_tab.py -q
```

Expected: pass.

- [ ] **Step 7: Run broader regression**

Run:

```bash
pytest tests/test_pipeline.py tests/test_build.py tests/test_validate.py -q
```

Expected: pass.

- [ ] **Step 8: Optional full test pass if time allows**

Run:

```bash
pytest tests/ -q
```

Expected: pass. If unrelated failures occur, record the exact failing tests and confirm whether they reproduce on the base branch before changing unrelated code.

- [ ] **Step 9: Update graphify graph after code changes**

After implementation code files are modified, run:

```bash
graphify update .
```

Expected: code graph updates without LLM/API usage. If `graphify-out/` is absent or the command fails because no prior graph exists, report that and do not treat it as a feature failure.

- [ ] **Step 10: Inspect god-node impact when a graph report exists**

If `graphify-out/GRAPH_REPORT.md` exists after update, inspect the god-node section and record whether `.tab` file nodes dominate the ranking. Do not change `analyze.py` in this implementation unless the report shows a concrete regression; if table containment edges dominate, file a follow-up to consider analyzer-side weighting for `context="table"` edges.

Do not commit unless the user explicitly requests commits for this branch.

---

## Self-Review Checklist

- [ ] `.tab` is classified as `FileType.CODE`.
- [ ] `.TAB` reaches `_get_extractor()` successfully because dispatch lower-cases suffixes.
- [ ] `extract_tab()` uses `csv.reader(..., delimiter="\t")`.
- [ ] UTF-8, UTF-8 BOM, and GB18030 decoding are supported.
- [ ] Empty files return a valid file-node-only extraction.
- [ ] Blank-line-only files return a valid file-node-only extraction.
- [ ] Ragged rows are tolerated.
- [ ] Empty and duplicate headers are normalized without invalid node IDs.
- [ ] Row identity is schema-agnostic and empty identity cells fall back to line number.
- [ ] Path-like cells emit `references` edges without hardcoding `ScriptFile`.
- [ ] URL/URI values are not treated as local path references.
- [ ] Root-relative script/resource paths from nested `.tab` directories resolve through deterministic project-root markers.
- [ ] Path references to files included in the same extraction connect to the scanned file node.
- [ ] Existing path references use target `source_file`; missing path stubs use the `.tab` file `source_file`.
- [ ] Value nodes are only emitted for low-cardinality, non-constant, non-identity columns.
- [ ] Value nodes include `context="table_value"` so TSV concepts can be filtered separately.
- [ ] Byte caps do not decode partial records.
- [ ] Caps emit `warnings`/`truncated` and no top-level `error` only when data was actually skipped.
- [ ] Large real `.tab` corpora are checked for god-node impact after implementation.
- [ ] Extractor output passes `validate_extraction()`.
- [ ] README includes `.tab`.
- [ ] Focused and broader test commands pass or have documented unrelated failures.
