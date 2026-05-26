# Graphify Manifest Domain Build Implementation Plan

> Execute this plan in `/Users/zhaoguodong/Codes/open-source/graphify`. Preserve unrelated dirty changes, especially existing docs translation edits.

**Goal:** Add first-class manifest-driven domain build/update support to Graphify, with exact output directories, native shared cache reuse, source decoding parity for JX3-relevant files, and compatibility with `query/path/explain --graph`.

**Source Spec:** `docs/superpowers/specs/2026-05-26-manifest-domain-build-design.md`

**Stop Condition:** After this plan passes, stop and report evidence. Do not continue into the JX3 wrapper plan until the user confirms the Graphify version is stable.

## Task 1: Capability Probe And Failing CLI Tests

Files:

- Modify or add tests under the existing Graphify test layout.
- Do not modify JX3 files.

Steps:

- [ ] Add a failing test for `graphify extract --manifest <domain-files.json> --output-dir <dir>`.
- [ ] Add a failing test for `graphify update --manifest <domain-files.json> --output-dir <dir>`.
- [ ] Assert manifest mode writes `graph.json` directly under `<output-dir>/graph.json`, not `<output-dir>/graphify-out/graph.json`.
- [ ] Assert manifest mode writes `GRAPH_REPORT.md` directly under `<output-dir>/GRAPH_REPORT.md`, not `<output-dir>/graphify-out/GRAPH_REPORT.md`.
- [ ] Assert manifest mode does not scan files outside `files[].path`.
- [ ] Assert `graphify query "..." --graph <output-dir>/graph.json` can read the generated graph.
- [ ] Assert manifest mode reuses the native active `GRAPHIFY_OUT` cache root, normally `repo_root/graphify-out/cache`, not a cache under `<output-dir>`.
- [ ] Assert setting `GRAPHIFY_OUT=graphify-out-custom` changes the native cache location to `repo_root/graphify-out-custom/cache` while `--output-dir` still receives direct domain outputs.
- [ ] Assert relative `repo_root` is resolved from the command cwd, so the JX3 smoke command works when invoked at the JX3 repo root.
- [ ] Assert `--manifest <repo_root>/graphify-out/manifest.json` is rejected with a clear error.
- [ ] Assert `graphify extract <path> --manifest <domain-files.json> --output-dir <dir>` and `graphify update <path> --manifest <domain-files.json> --output-dir <dir>` fail because manifest mode and positional path are mutually exclusive.
- [ ] Assert relative `--output-dir` is resolved from the command cwd, while absolute `--output-dir` is used unchanged.
- [ ] Assert `--manifest` without `--output-dir` fails clearly for both `extract` and `update`.
- [ ] Assert `--output-dir` without `--manifest` fails clearly and points directory-mode `extract` users to `--out`.
- [ ] Assert code-like `extract --manifest` succeeds without any LLM backend API key.
- [ ] Assert code-like `update --manifest` succeeds without any LLM backend API key.
- [ ] Assert an unsupported manifest file fails before writing `graph.json`.
- [ ] Assert `document`, `paper`, and `image` manifest files are rejected in Phase 0 manifest mode with a clear message, even when existing directory `extract` would handle them through semantic extraction.
- [ ] Assert absolute and repo-relative `files[].path` forms produce the same repo-relative `source_file` values, state entries, and cache identity.
- [ ] Assert `extract --manifest` writes `<output-dir>/.graphify_state/update-state.json`.
- [ ] Assert `update --manifest` with missing state performs a manifest full rebuild and initializes state, without reading native `graphify-out/manifest.json`.
- [ ] Assert removing a previously listed file from the manifest and running `update --manifest` prunes that file's old nodes and edges.

Verification:

```bash
uv run pytest tests/test_manifest_domain_build.py -q
```

Expected before implementation: targeted new tests fail because the CLI does not recognize the new flags.

## Task 2: Manifest Loader

Files:

- Extend existing manifest facade if present, or add a focused helper under `graphify/`.
- Add focused unit tests.

Behavior:

- [ ] Load JSON manifest.
- [ ] Accept `repo_root` and `files[].path`.
- [ ] Ignore unknown caller-owned fields.
- [ ] Resolve relative `repo_root` against the CLI current working directory; accept absolute `repo_root` unchanged.
- [ ] Resolve `files[].path` relative to the resolved `repo_root`.
- [ ] Reject paths that escape `repo_root`.
- [ ] Report missing files explicitly.
- [ ] Classify each file with Graphify's native file classification and AST/code extractor lookup.
- [ ] Accept only files classified as `code` and handled by the AST/code extractor path in Phase 0.
- [ ] Reject `document`, `paper`, `image`, `video`, unknown, and no-extractor files before build/update starts; do not pass unsupported files through as empty extraction results.
- [ ] Normalize every accepted source path to a `repo_root` relative path for `source_file`, cache lookup identity, and manifest-mode state.
- [ ] Return an ordered materialized code-like file list plus a `files_by_type` shape compatible with existing build/report helpers.
- [ ] Treat the caller manifest as read-only; never overwrite or mutate it.
- [ ] Keep Graphify's native `graphify-out/manifest.json` semantics unchanged; do not use that path for caller-owned domain file lists.
- [ ] Reject `--manifest` when it resolves to the native `<repo_root>/graphify-out/manifest.json` state file.
- [ ] Use `domain-files.json` in tests and JX3 examples to avoid naming collision with native incremental state.

Implementation constraints:

- Do not add JX3-specific fields or routing logic.
- Do not infer files from parent directories when `--manifest` is supplied.
- Do not route manifest-mode document/paper/image files into the LLM semantic pipeline in Phase 0.

## Task 3: Shared Build Runner And Exact Output Directory

Files:

- `graphify/__main__.py`
- shared build/export/report call sites used by `extract` and manifest-mode `update`

Behavior:

- [ ] Extract the existing AST/code build/report orchestration into a small shared runner that accepts a materialized code-like file set, repo root, output directory, and mode.
- [ ] Add `--output-dir` to manifest-mode `extract`.
- [ ] Add `--output-dir` to manifest-mode `update`.
- [ ] Require `--output-dir` whenever `--manifest` is supplied for `extract` or `update`.
- [ ] Reject `--output-dir` without `--manifest`; keep directory mode on existing `--out` and `GRAPHIFY_OUT` behavior.
- [ ] Reject positional `<path>` when `--manifest` is supplied for `extract` or `update`.
- [ ] Resolve relative `--output-dir` against the CLI current working directory; accept absolute `--output-dir` unchanged.
- [ ] Ensure `--output-dir` is not affected by `GRAPHIFY_OUT`.
- [ ] In manifest mode, write outputs directly into `--output-dir`.
- [ ] Do not require LLM backend discovery or API keys for code-like manifest extract/update.
- [ ] Keep existing directory mode behavior: `graphify extract <path> --out DIR` still writes `<DIR>/graphify-out/`.
- [ ] Ensure `GRAPH_REPORT.md` is mandatory for manifest-mode extract/update and uses the same output directory as `graph.json`.
- [ ] Ensure reports and sidecars use the same output directory as `graph.json`.
- [ ] Keep `--output-dir` separate from `--out`; do not change `--out` semantics in directory mode.
- [ ] Update CLI usage/help text so manifest mode examples show `--manifest ... --output-dir ...` and directory mode examples keep `--out`.

Verification:

```bash
uv run pytest <targeted_manifest_cli_tests> -q
```

## Task 4: Native Shared Cache Reuse

Files:

- Manifest-mode build/update call sites that invoke AST cache helpers.
- `graphify/cache.py` only if verification proves the existing cache API cannot reuse `repo_root` cleanly.

Behavior:

- [ ] Do not add `--cache-root` to the Phase 0 CLI.
- [ ] In manifest mode, pass `repo_root` as the cache root input to the existing cache APIs.
- [ ] Cache entries remain Graphify-owned and live under the native shared cache layout, resolved through active `GRAPHIFY_OUT`: normally `repo_root/graphify-out/cache`, or `repo_root/<GRAPHIFY_OUT>/cache` for a relative `GRAPHIFY_OUT`.
- [ ] If `GRAPHIFY_OUT` is absolute, preserve the existing native cache behavior and use `<GRAPHIFY_OUT>/cache`.
- [ ] Prefer no cache API changes; do not introduce a manifest-specific cache directory unless an existing cache helper cannot be reused correctly.
- [ ] Existing non-manifest cache behavior remains compatible.
- [ ] Different domain `--output-dir` values do not create independent per-domain caches.

Acceptance:

- A manifest-mode extract/update writes or reads AST cache entries from the native shared cache resolved by active `GRAPHIFY_OUT`.
- No caller code needs to inspect, configure, or own cache internals.

## Task 5: Manifest-Driven Extract

Files:

- `graphify/__main__.py`
- extraction/build orchestration code

Behavior:

- [ ] `graphify extract --manifest ...` uses only manifest files.
- [ ] Full AST/code build output includes mandatory `graph.json` and mandatory `GRAPH_REPORT.md`.
- [ ] Code-like manifest extract does not require LLM backend API key.
- [ ] Existing extract options such as clustering/no-cluster keep sensible behavior where applicable.
- [ ] If manifest mode supports `--no-cluster`, it still writes `GRAPH_REPORT.md` with raw/no-cluster status; otherwise `--manifest --no-cluster` is rejected clearly.
- [ ] Unsupported files are reported clearly and fail before output is written.
- [ ] Full extract writes `<output-dir>/.graphify_state/update-state.json` after successful graph/report output.
- [ ] Graphify-owned manifest-mode state is read from and written only under `<output-dir>/.graphify_state/update-state.json`, never from the caller manifest or native `graphify-out/manifest.json`.
- [ ] Manifest-mode state stores previous manifest file identities as normalized `repo_root` relative paths, not absolute paths or cwd-relative spellings.

Do not:

- Fall back to directory scan if manifest parsing fails.
- Require JX3-specific manifest fields.
- Accept files that require the LLM semantic extraction pipeline in Phase 0 manifest mode.

## Task 6: Manifest-Driven Update

Files:

- `graphify/__main__.py`
- shared build/update runner introduced for manifest mode

Behavior:

- [ ] `graphify update --manifest ...` uses the same manifest file list as the source of truth.
- [ ] Manifest-mode update does not route through `watch._rebuild_code()` unless that path is refactored so file discovery is injectable and directory scanning is disabled.
- [ ] Update does not scan repository root or the manifest parent directory.
- [ ] Update writes to the exact `--output-dir`.
- [ ] Update writes mandatory `graph.json` and mandatory `GRAPH_REPORT.md` to the exact `--output-dir`.
- [ ] Code-like manifest update does not require LLM backend API key.
- [ ] Update reuses Graphify's native shared cache resolved by active `GRAPHIFY_OUT`.
- [ ] Update reads and writes manifest-mode incremental state only under `<output-dir>/.graphify_state/update-state.json`, not native `graphify-out/manifest.json`.
- [ ] If manifest-mode state is missing, update performs a manifest full rebuild and initializes state; it must not fall back to native `graphify-out/manifest.json`.
- [ ] Update state tracks the previous manifest file set as normalized `repo_root` relative paths and prunes nodes/edges for files removed from the current manifest, even when those files still exist in the repo.
- [ ] Existing directory-based update behavior remains compatible.

Acceptance:

- Touching one manifest-listed fixture changes only the relevant manifest-mode graph/update path.
- Adding a file outside the manifest does not affect manifest-mode update.
- Removing a file from the manifest removes that file's old graph contribution from the domain graph.

## Task 7: Source Decoding Parity

Files:

- shared text decoding helper, or a new focused helper under `graphify/`
- structured text extraction paths for `.tab`, `.ini`, TSV-like `.txt`
- Lua/LH extraction paths that decode labels, string literals, or Include references

Behavior:

- [ ] Centralize bytes-to-text decoding for the relevant extraction paths.
- [ ] Decode order: UTF-16LE sniff, UTF-8-SIG, UTF-8, GB18030, replacement fallback.
- [ ] UTF-16LE sniff uses BOM or strong NUL parity on a small sample.
- [ ] UTF-16LE decoded text must pass sanity checks before being accepted.
- [ ] Text with embedded NUL characters is not accepted as normal decoded source.
- [ ] Replacement fallback records a warning.

Fixtures:

- GB18030 `.tab` or `.txt` with Chinese labels.
- UTF-16LE `.tab` or `.txt`.
- A misleading NUL-byte sample that must not silently produce NUL-filled text.

Verification:

```bash
uv run pytest <targeted_decoding_tests> -q
```

## Task 8: Query Compatibility Smoke

Behavior:

- [ ] Build a manifest-mode graph.
- [ ] Run `graphify query "..." --graph <output-dir>/graph.json`.
- [ ] Run `graphify path` or `graphify explain` where the fixture graph has suitable nodes.

Acceptance:

- Existing query commands do not need new JX3-specific switches.

## Task 9: Backward Compatibility

Verification:

```bash
uv run pytest tests -q
```

Also run a targeted existing directory-mode smoke:

```bash
uv run graphify extract <small-fixture-or-temp-dir> --out /tmp/graphify-directory-mode-smoke --no-cluster
uv run graphify update <small-fixture-or-temp-dir> --no-cluster
```

Expected:

- Existing directory mode still works.
- If the environment has no LLM backend API key, record that native directory `extract` cannot complete before semantic backend discovery; do not weaken manifest-mode no-LLM acceptance on that basis.
- Manifest mode writes direct domain outputs.
- Manifest mode writes mandatory `GRAPH_REPORT.md`.
- Manifest mode reuses native shared cache behavior through active `GRAPHIFY_OUT`; no `--cache-root` option is required.

## Task 10: JX3 Phase 0 Smoke

Use a JX3-generated or hand-written minimal domain manifest from `/Users/zhaoguodong/Codes/jx3`:

```bash
graphify extract \
  --manifest graphify-out/domains/skill-core/domain-files.json \
  --output-dir graphify-out/domains/skill-core

graphify update \
  --manifest graphify-out/domains/skill-core/domain-files.json \
  --output-dir graphify-out/domains/skill-core

graphify query "测试 SkillID 关联" \
  --graph graphify-out/domains/skill-core/graph.json
```

Record:

- Graphify branch or commit SHA.
- Exact test commands and results.
- Output directory listing showing `graph.json` directly under `graphify-out/domains/skill-core/`.
- Output directory listing showing `GRAPH_REPORT.md` directly under `graphify-out/domains/skill-core/`.
- Confirmation that JX3 wrapper code did not generate or modify `graph.json`.

## Self-Review Checklist

- [ ] No JX3 business routing, school, skill, or eval-set logic was added to Graphify.
- [ ] Manifest mode consumes only `files[].path`.
- [ ] Manifest loader deduplicates resolved repo-relative file identities before extraction/state output.
- [ ] Empty manifest file lists fail clearly before graph build starts.
- [ ] Manifest self-reference, Unicode paths, and symlink boundaries are covered by tests.
- [ ] Unknown manifest-mode CLI options are rejected clearly.
- [ ] Caller-owned domain file lists use names like `domain-files.json`; native `graphify-out/manifest.json` remains untouched and keeps its existing directory-mode meaning.
- [ ] `--manifest <repo_root>/graphify-out/manifest.json` and active `GRAPHIFY_OUT/manifest.json` are rejected and never treated as caller-owned domain file lists.
- [ ] Manifest mode never falls back to directory scan after manifest failure.
- [ ] Relative `--output-dir` is resolved from CLI cwd; absolute `--output-dir` is used unchanged.
- [ ] Manifest mode requires `--output-dir`, while directory mode rejects `--output-dir` and keeps `--out`.
- [ ] `--output-dir` writes direct outputs and does not nest another `graphify-out/`.
- [ ] Manifest-mode `GRAPH_REPORT.md` is mandatory and written directly under `--output-dir`.
- [ ] No manifest-mode `--cache-root` was added; cache remains Graphify-owned and shared under active `GRAPHIFY_OUT` native cache layout.
- [ ] Manifest update prunes files removed from the caller manifest.
- [ ] Manifest state and graph `source_file` values use normalized `repo_root` relative paths, so absolute and relative manifest entries do not fork identity.
- [ ] Manifest state schema documents that `files` mirrors `current_files`, and `files_by_type` mirrors `current_files_by_type`.
- [ ] Callers are told not to run concurrent manifest writes against the same `--output-dir`.
- [ ] Unsupported manifest files fail clearly before outputs are written.
- [ ] Manifest-driven update has parity with manifest-driven extract.
- [ ] Source decoding handles GB18030 and UTF-16LE without silent UTF-8 replacement.
- [ ] Existing directory-based extract/update tests still pass.
- [ ] Stop after Phase 0 evidence is recorded; do not proceed into JX3 wrapper implementation.
