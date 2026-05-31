# Design: Generic `.tab` Structured Extraction

**Date:** 2026-05-20
**Status:** Draft for review
**Scope:** Add generic `.tab` support as local structural extraction, not schema-specific semantic extraction.

---

## Problem

Graphify currently does not index `.tab` files. `detect.classify_file()` returns `None` for `.tab`, so these files are skipped before extraction. The attached `sAIType.tab` shows why this matters: the file is not natural-language documentation, but a structured configuration table whose rows reference Lua scripts. If Graphify skips it, the generated graph misses the relationship between data-driven configuration entities and the source files that implement their behavior.

The first sample has columns such as `AIType` and `ScriptFile`, but project `.tab` files are not guaranteed to share that schema. The solution must support all `.tab` files generically instead of requiring specific column names.

---

## Goals

- Index every `.tab` file as part of the local code/structure extraction path.
- Avoid LLM/API use for `.tab` files.
- Preserve row-level entities, column structure, and file/path references.
- Support multiple `.tab` schemas without a schema gate.
- Decode common real-world encodings, especially UTF-8 and GB18030.
- Keep graph size bounded so large data tables do not dominate god-node rankings.
- Make `graphify update .` and watch-driven rebuilds treat `.tab` changes as code-only updates.

---

## Non-goals

- Do not build a full data-analysis engine for every cell.
- Do not require `AIType`, `ScriptFile`, or any other schema-specific column.
- Do not introduce a new `file_type` such as `table` or `config` in the first version.
- Do not add a user-facing profile/config format in the first version.
- Do not convert `.tab` to Markdown for semantic extraction.

---

## Current Code Path

Full extraction:

```
detect()
    -> graphify.__main__ separates code/doc/paper/image
    -> extract.extract(code_files)
    -> semantic extraction for docs/papers/images only
    -> build/cluster/analyze/export
```

AST-only update:

```
graphify update .
    -> watch._rebuild_code()
    -> detect()
    -> extract.extract(code_files)
    -> preserve old semantic graph pieces
    -> rebuild graph/report
```

Therefore `.tab` should enter `FileType.CODE`, not `FileType.DOCUMENT`. That gives `.tab` files the same local, cacheable, no-LLM update behavior as `.json`, `.sql`, and other structured code/config formats.

---

## Proposed Architecture

### Detection

Add `.tab` to `CODE_EXTENSIONS` in `graphify/detect.py`.

All `.tab` files should be classified as `FileType.CODE`. There is no schema validation at classification time. Files that hit extractor caps return a bounded partial extraction with non-fatal warning metadata. True read or parse failures return an `error` result and should not be cached.

### Dispatch

Add `extract_tab(path: Path) -> dict` in `graphify/extract.py` and register it in `_DISPATCH`:

```python
".tab": extract_tab
```

`Path.suffix.lower()` classification already handles uppercase `.TAB` in detection. Update `_get_extractor()` to use `path.suffix.lower()` before `_DISPATCH` lookup so any supported extension with uppercase spelling is extracted consistently. Add a `.TAB` regression test that reaches extraction, not only classification.

### Watch/update

`graphify/watch.py` builds `_WATCHED_EXTENSIONS` from `CODE_EXTENSIONS`, so adding `.tab` to `CODE_EXTENSIONS` is enough for watch coverage. Add a regression test to make this explicit.

---

## Extractor Design

### Parsing

Use Python's `csv.reader` with `delimiter="\t"`.

Encoding order:

1. `utf-8-sig`
2. `utf-8`
3. `gb18030`
4. final fallback with replacement, only if all strict decodes fail

The extractor should tolerate CRLF, trailing empty columns, missing cells, and rows with extra cells.

### Header Handling

The first parsed row is the header row.

Header normalization:

- Strip whitespace and `\r`.
- Empty header becomes `column_<index>`.
- Duplicate header becomes `<header>_<ordinal>`.
- Preserve the original label for display.

If the file is empty, return a file node only with no edges. The file itself is still part of the project, and returning a valid partial extraction keeps downstream build behavior simple.

### Row Identity

Choose a stable row key without relying on schema-specific names:

1. Prefer the first column when all non-empty row values are unique.
2. Otherwise choose the earliest column with high uniqueness and low emptiness.
3. Otherwise use the physical line number: `row <line_number>`.

If a row's selected identity column value is empty, that row falls back to its physical line number. Do not reuse an empty string or a duplicate placeholder as the row label or ID seed.

The line number used in `source_location` should be the actual file line, so the first record row starts at `L2`.

### Node Model

Use existing schema-compatible `file_type` values.

Nodes:

- File node: `file_type="code"`, label is the filename.
- Column nodes: `file_type="code"`, labels like `ScriptFile (column)`.
- Row nodes: `file_type="code"`, labels like `AIType 17673` when a useful row key exists, otherwise `row 15`.
- Value nodes: `file_type="concept"` because field values are extracted concepts, not source files. Add `context="table_value"` on value nodes so reports or later analysis can distinguish TSV-derived concepts from semantic/LLM concepts.
- Referenced file nodes: `file_type="code"` nodes for path references. If the target file exists, use the resolved target path as `source_file`. If the target file does not exist, emit a stub node whose `source_file` remains the `.tab` file path and whose label is the normalized target path. This keeps missing-resource stubs owned by the table that introduced them and lets update eviction work predictably.

Edges:

- `file --contains--> column`
- `file --contains--> row`
- `row --sets--> value`
- `row --references--> file/path target`

All edges emitted by this extractor use `confidence="EXTRACTED"` because they come directly from table cells. Use `context="table"`, `context="value"`, or `context="path"` where helpful.

### Path Reference Detection

Any cell can be considered a path/reference candidate. Do not hardcode `ScriptFile`.

A cell is a path reference when it matches one or more of:

- Contains `/` or `\`.
- Ends with a known code/config extension. Start from graphify's `CODE_EXTENSIONS` and add a small explicit resource/config set such as `.txt`, `.md`, `.xml`, `.yaml`, and `.yml`.
- Looks like a relative project resource path rather than a plain enum value.

Do not treat URL/URI values such as `http://...`, `https://...`, `ftp://...`, or any `<scheme>://...` string as local path references, even when the URL ends with a known extension.

Path normalization:

- Convert `\` to `/` for graph labels and source portability.
- Preserve Unicode path components.
- Resolve relative paths against the `.tab` file's parent first.
- If the parent-relative target does not exist, resolve against the nearest ancestor that looks like a project root, using deterministic markers such as `.git`, `pyproject.toml`, `package.json`, `go.mod`, `Cargo.toml`, or `graphify-out`. This covers common layouts like `data/config/*.tab` referencing `scripts/...` from the project root without depending on the current working directory or scan-root process state.
- If a target file exists, use the same node ID scheme as other file extractors where possible.
- If no candidate target exists, emit a stub node so the `references` edge is not dropped during graph build.

For the sample `sAIType.tab`, this means rows naturally reference paths such as `scripts/ai/StandardAI.lua` and `scripts/Map/成都/ai/围城演员AI.lua` without requiring a `ScriptFile` rule.

### Value Node Throttling

Do not make every cell a node. That would make high-cardinality IDs and numeric ranges dominate the graph.

Create value nodes only for columns that satisfy all conditions:

- Non-empty value count is meaningful.
- Unique value count is greater than 1.
- Unique value count is below a small threshold, such as `min(20, max(2, row_count * 0.2))`, so tiny tables can still expose binary/low-cardinality categories.
- Column is not selected as the row identity column.
- Values are short enough to be labels.

Skip constant columns. Skip high-cardinality columns. This prevents a field like `AlertRange=768` from becoming a misleading super-hub when every row has the same value.

### Size Limits

All `.tab` files are included, but extraction should be bounded.

First-version caps:

- Maximum bytes read for structural extraction: 5 MiB.
- Maximum indexed rows: 10,000.
- Maximum indexed columns: 200.
- Maximum value nodes per file: 500.
- Maximum path references per file: 10,000.

Keep these as private constants in `graphify/extract.py` for the first version. Do not add environment variables until real corpora show the defaults are too restrictive.

For the byte cap, read at most `MAX_BYTES + 1` bytes. If the file exceeds the cap, truncate to the last complete newline before decoding so multibyte encodings and CSV row parsing are not cut mid-character or mid-record. If no complete newline exists inside the capped byte range, return the file node only with a non-fatal warning rather than decoding a partial row.

When any cap actually causes rows, columns, values, or path references to be skipped, return the partial graph plus non-fatal metadata such as `warnings` and `truncated=true`, and print one concise warning to stderr for that file. Do not use the top-level `error` field for caps, because AST extraction only caches results that have no `error`. Use `error` only for true failures where the extracted graph should not be cached.

---

## Example Output Shape

For a row like:

```tsv
AIType	ScriptFile	IsPositive
17673	scripts\Map\成都\ai\动物表现\动物逃跑.lua	0
```

Expected graph shape:

```text
sAIType.tab
  contains -> AIType (column)
  contains -> ScriptFile (column)
  contains -> IsPositive (column)
  contains -> AIType 17673

AIType 17673
  references -> scripts/Map/成都/ai/动物表现/动物逃跑.lua
  sets -> IsPositive=0
```

If the referenced Lua file is also scanned, the reference should connect to the Lua file node rather than an isolated duplicate.

---

## Files To Change

- `graphify/detect.py`
  - Add `.tab` to `CODE_EXTENSIONS`.
  - Add or adjust tests for `.tab` and `.TAB` classification.

- `graphify/extract.py`
  - Add `extract_tab`.
  - Register `.tab` in `_DISPATCH`.
  - Normalize `_get_extractor()` suffix lookup to lower-case.

- `graphify/watch.py`
  - No direct production change should be required if `.tab` is in `CODE_EXTENSIONS`.
  - Add regression test coverage.

- `README.md` and translated README later
  - Update supported code extensions list to include `.tab`.
  - Translation updates can be deferred if this project normally updates translations separately.

- `tests/fixtures/`
  - Add at least one UTF-8 `.tab` fixture.
  - Add one GB18030 fixture with Chinese path content.

- `tests/test_detect.py`
  - Add `.tab` classification tests.

- `tests/test_tab.py` or `tests/test_languages.py`
  - Add extractor tests.

- `tests/test_watch.py`
  - Assert `.tab` is watched.

---

## Test Plan

Unit tests:

- `.tab` and `.TAB` classify as `FileType.CODE`.
- `.TAB` files are actually extracted, proving dispatch lower-cases suffix lookup.
- `extract_tab` emits file, column, and row nodes for a basic TSV.
- GB18030 fixture decodes Chinese paths correctly.
- UTF-8 BOM and CRLF files decode and parse correctly.
- Header-only, single-column, empty, and blank-line-only files remain valid extractions.
- Empty and duplicate headers normalize safely.
- Rows fall back to physical line numbers when no identity column is good enough.
- Ragged rows do not crash.
- Path-like cells emit `references` edges.
- URL/URI values are not treated as local file references.
- Project-root fallback resolves `data/config/*.tab` references to root-relative `scripts/...` files when an ancestor project marker exists.
- Constant columns do not create value hub nodes.
- High-cardinality columns do not create value nodes.
- Cap/truncation events return `warnings`/`truncated` metadata and do not set top-level `error`.
- Extractor output passes `validate_extraction()`.
- `.tab` appears in `_WATCHED_EXTENSIONS`.

Focused command:

```bash
pytest tests/test_detect.py tests/test_watch.py tests/test_tab.py -q
```

Broader regression:

```bash
pytest tests/test_pipeline.py tests/test_build.py tests/test_validate.py -q
```

---

## Risks And Mitigations

### Graph bloat

Risk: Large data tables can produce too many nodes and edges.

Mitigation: Row/column caps, value-node throttling, and skipping high-cardinality value nodes.

Residual risk: Even with caps, large `.tab` files can have high `contains` degree and may appear in god-node rankings. In the first version this is acceptable because table structure is real extracted structure, but reports should retain `context="table"` on edges so a future analyzer can discount table containment edges if they dominate architecture signals.

Validation: After implementation, run the extractor on at least one real `.tab` corpus and inspect `GRAPH_REPORT.md` god-node rankings. If `.tab` file nodes dominate the report, follow up with analyzer-side weighting rather than weakening extraction fidelity.

### False path references

Risk: A non-path string containing `/` may become a reference.

Mitigation: Exclude URL/URI schemes before path matching, then prefer candidates with known extensions or path-like shape. Even when a false positive occurs, it remains a local `references` edge with extracted confidence; it is less harmful than dropping real script/resource references.

### Duplicate referenced file nodes

Risk: A path reference node may not match the actual file node ID generated by another extractor.

Mitigation: Normalize separators, preserve Unicode, check both `.tab` parent and deterministic project-root candidates, and reuse the same `_make_id(str(path))` style for resolved existing paths. Emit stubs only when no file exists.

### Referenced file node attribute precedence

Risk: When a `.tab` reference node and a scanned source file node share the same ID, graph build may keep attributes from the later node in extraction order.

Mitigation: This matches existing graphify build semantics, where duplicate node IDs are merged by NetworkX and later attributes win. The `.tab` edge preserves the table row evidence through edge `source_file`, `source_location`, and `context="path"`, so losing the reference-node line number on the file node itself is acceptable in the first version.

### Encoding ambiguity

Risk: Some legacy files may decode under the wrong single-byte encoding.

Mitigation: Try strict UTF-8 variants first, then GB18030. Use replacement fallback only as last resort and keep extraction best-effort.

---

## Implementation Decisions

- Keep the size caps as extractor-local private constants for the first version.
- Use an extractor-local allowlist for path-reference extensions. Include common code/config/resource extensions and `.tab`, but do not import all document/image/video extensions from `detect.py`; that would broaden false positives.
- Use `file_type="concept"` for value nodes.
- Use non-fatal warning metadata for truncation/cap events so bounded partial extraction remains cacheable.
- Keep first-version path resolution local to the `.tab` file directory. Broader scan-root resolution requires an extractor interface change and is deferred.
