# Architecture

graphify is a Claude Code skill backed by a Python library. The skill orchestrates the library; the library can be used standalone.

## Pipeline

```
detect()  →  extract()  →  build_graph()  →  cluster()  →  analyze()  →  report()  →  export()
```

Each stage is a single function in its own module. They communicate through plain Python dicts and NetworkX graphs - no shared state, no side effects outside `graphify-out/`.

## Module responsibilities

| Module | Function | Input → Output |
|--------|----------|----------------|
| `detect.py` | `collect_files(root)` | directory → `[Path]` filtered list |
| `extract.py` | `extract(path)` | file path → `{nodes, edges}` dict |
| `build.py` | `build_graph(extractions)` | list of extraction dicts → `nx.Graph` |
| `cluster.py` | `cluster(G)` | graph → graph with `community` attr on each node |
| `analyze.py` | `analyze(G)` | graph → analysis dict (god nodes, surprises, questions) |
| `report.py` | `render_report(G, analysis)` | graph + analysis → GRAPH_REPORT.md string |
| `export.py` | `export(G, out_dir, ...)` | graph → Obsidian vault, graph.json, graph.html, graph.svg |
| `code_build_runner.py` | `build_code_graph(...)` | explicit manifest file set → domain graph outputs |
| `manifest.py` | `load_domain_manifest(...)` | caller-owned domain manifest → normalized file set |
| `tabular.py` | tabular parser/profile helpers | `.tab`/`.tsv` file → headers, rows, profile, path-like refs |
| `tabular_manifest.py` | `load_tabular_domain_manifest(...)` | domain manifest → effective graph/sidecar tabular policies |
| `tabular_sidecar.py` | sidecar store/query API | tabular manifest → shared SQLite DB; search/resolve/query rows |
| `tabular_graph.py` | `merge_sidecar_projection(...)` | sidecar DB rows → graph table/column/anchor/path-ref skeleton |
| `sidecar_cli.py` | `graphify sidecar ...` | CLI search/resolve/query over tabular sidecar DB |
| `callflow_html.py` | `write_callflow_html(...)` | graphify-out files → Mermaid architecture/call-flow HTML |
| `ingest.py` | `ingest(url, ...)` | URL → file saved to corpus dir |
| `cache.py` | `check_semantic_cache / save_semantic_cache` | files → (cached, uncached) split |
| `security.py` | validation helpers | URL / path / label → validated or raises |
| `validate.py` | `validate_extraction(data)` | extraction dict → raises on schema errors |
| `serve.py` | `start_server(graph_path)` | graph file path → MCP stdio server |
| `watch.py` | `watch(root, flag_path)` | directory → writes flag file on change |
| `benchmark.py` | `run_benchmark(graph_path)` | graph file → corpus vs subgraph token comparison |

## Extraction output schema

Every extractor returns:

```json
{
  "nodes": [
    {"id": "unique_string", "label": "human name", "source_file": "path", "source_location": "L42"}
  ],
  "edges": [
    {"source": "id_a", "target": "id_b", "relation": "calls|imports|uses|...", "confidence": "EXTRACTED|INFERRED|AMBIGUOUS"}
  ]
}
```

`validate.py` enforces this schema before `build_graph()` consumes it.

## Manifest domain builds and tabular sidecars

Directory-mode extraction scans a root and writes the native `graphify-out/`
layout. Manifest mode is caller-owned: `graphify extract|update --manifest
FILE --output-dir DIR` receives an explicit file list, builds a smaller domain
graph directly in `DIR`, and keeps manifest state in
`DIR/.graphify_state/update-state.json`. It still reuses the active native
`GRAPHIFY_OUT` cache; there is no manifest-specific cache root.

Tabular manifest entries can choose `tabular_policy: "graph"`, `"sidecar"`, or
`"auto"`. Effective `graph` files keep the existing extractor behavior.
Effective `sidecar` files are removed from the normal extractor input, written
into a shared SQLite DB at `GRAPHIFY_OUT/sidecar/tabular.sqlite` by default
(`--sidecar-db PATH` overrides this), and projected back into the graph as
source/table/column/anchor/path-ref skeleton nodes with `sidecar_ref` metadata.
Full rows are intentionally not stored as graph nodes; callers recover them via
`graphify sidecar search`, `graphify sidecar resolve`, or the constrained
read-only `graphify sidecar query` debug/eval surface.

## Confidence labels

| Label | Meaning |
|-------|---------|
| `EXTRACTED` | Relationship is explicitly stated in the source (e.g., an import statement, a direct call) |
| `INFERRED` | Relationship is a reasonable deduction (e.g., call-graph second pass, co-occurrence in context) |
| `AMBIGUOUS` | Relationship is uncertain; flagged for human review in GRAPH_REPORT.md |

## Adding a new language extractor

1. Add a `extract_<lang>(path: Path) -> dict` function in `extract.py` following the existing pattern (tree-sitter parse → walk nodes → collect `nodes` and `edges` → call-graph second pass for INFERRED `calls` edges).
2. Register the file suffix in `extract()` dispatch and `collect_files()`.
3. Add the suffix to `CODE_EXTENSIONS` in `detect.py` and `_WATCHED_EXTENSIONS` in `watch.py`.
4. Add the tree-sitter package to `pyproject.toml` dependencies.
5. Add a fixture file to `tests/fixtures/` and tests to `tests/test_languages.py`.

## Security

All external input passes through `graphify/security.py` before use:

- URLs → `validate_url()` (http/https only) + `_NoFileRedirectHandler` (blocks file:// redirects)
- Fetched content → `safe_fetch()` / `safe_fetch_text()` (size cap, timeout)
- Graph file paths → `validate_graph_path()` (must resolve inside `graphify-out/`)
- Node labels → `sanitize_label()` (strips control chars, caps 256 chars, HTML-escapes)

See `SECURITY.md` for the full threat model.

## Testing

One test file per module under `tests/`. Run with:

```bash
pytest tests/ -q
```

All tests are pure unit tests - no network calls, no file system side effects outside `tmp_path`.
