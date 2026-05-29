from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace

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
            if not chunk:
                continue
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


def _active_file_config_for_rebuild(
    conn: sqlite3.Connection,
    file_id: int,
    repo_root: Path,
) -> TabularFileConfig | None:
    row = conn.execute(
        "SELECT config_json FROM domain_files WHERE file_id = ? ORDER BY domain_id LIMIT 1",
        (file_id,),
    ).fetchone()
    if row is None:
        return None
    file_cfg = tabular_file_config_from_json(json.loads(row["config_json"]))
    if not file_cfg.path.is_absolute():
        file_cfg = replace(file_cfg, path=repo_root / file_cfg.path)
    return file_cfg


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
                    if file_cfg is None:
                        file_cfg = _active_file_config_for_rebuild(conn, file_id, manifest.repo_root)
                    current_sha = file_cfg and tabular_source_sha256(file_cfg.path)
                    rebuilt_rows = False
                    if file_cfg and current_sha != sf["sha256"]:
                        row_count, ref_count = _rebuild_file_rows_and_refs(conn, file_id, file_cfg, union_hash)
                        rows_upserted += row_count
                        refs_upserted += ref_count
                        rebuilt_rows = True
                    elif union_hash != sf["union_config_hash"]:
                        conn.execute(
                            "UPDATE source_files SET union_config_hash = ? WHERE file_id = ?",
                            (union_hash, file_id),
                        )
                    if rebuilt_rows or union_hash != sf["union_config_hash"]:
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
    from graphify.tabular import (
        looks_like_tabular_path_column_name,
        looks_like_tabular_path_value,
        normalise_tabular_path_value,
    )

    inserted = 0
    for item in row.row_values_json:
        if not looks_like_tabular_path_column_name(
            str(item["name"]),
            str(item["normalized_name"]),
        ):
            continue
        value = str(item["value"])
        if not looks_like_tabular_path_value(value):
            continue
        conn.execute("""
            INSERT INTO tabular_refs(file_id, row_id, target_ref, value)
            VALUES(?, ?, ?, ?)
        """, (file_id, row_id, normalise_tabular_path_value(value), value))
        inserted += 1
    return inserted


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

PHYSICAL_QUERY_TABLES = {
    "domain_files",
    "indexed_values",
    "meta",
    "rows",
    "source_files",
    "tabular_columns",
    "tabular_refs",
    "tabular_tables",
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


def _sql_tokens(statement: str) -> list[str]:
    tokens: list[str] = []
    i = 0
    while i < len(statement):
        ch = statement[i]
        nxt = statement[i + 1] if i + 1 < len(statement) else ""
        if ch == "'":
            i += 1
            while i < len(statement):
                if statement[i] == "'":
                    if i + 1 < len(statement) and statement[i + 1] == "'":
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue
        if ch in {'"', "`"}:
            quote = ch
            start = i + 1
            i = start
            value: list[str] = []
            while i < len(statement):
                if statement[i] == quote:
                    if i + 1 < len(statement) and statement[i + 1] == quote:
                        value.append(quote)
                        i += 2
                        continue
                    i += 1
                    break
                value.append(statement[i])
                i += 1
            identifier = "".join(value).strip()
            if identifier:
                tokens.append(identifier.lower())
            continue
        if ch == "[":
            i += 1
            value = []
            while i < len(statement) and statement[i] != "]":
                value.append(statement[i])
                i += 1
            if i < len(statement) and statement[i] == "]":
                i += 1
            identifier = "".join(value).strip()
            if identifier:
                tokens.append(identifier.lower())
            continue
        if ch == "-" and nxt == "-":
            newline = statement.find("\n", i + 2)
            i = len(statement) if newline == -1 else newline + 1
            continue
        if ch == "/" and nxt == "*":
            end = statement.find("*/", i + 2)
            i = len(statement) if end == -1 else end + 2
            continue
        if ch.isalpha() or ch == "_":
            start = i
            i += 1
            while i < len(statement) and (statement[i].isalnum() or statement[i] == "_"):
                i += 1
            tokens.append(statement[start:i].lower())
            continue
        if ch in {",", "(", ")", "."}:
            tokens.append(ch)
            i += 1
            continue
        i += 1
    return tokens


def _physical_table_refs(statement: str) -> set[str]:
    refs: set[str] = set()
    tokens = _sql_tokens(statement)
    expect_table = False
    in_from_clause = False
    stop_keywords = {
        "where", "group", "order", "having", "limit", "offset",
        "union", "except", "intersect", "returning",
    }
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in {"from", "join"}:
            expect_table = True
            in_from_clause = True
            i += 1
            continue
        if token in stop_keywords:
            expect_table = False
            in_from_clause = False
            i += 1
            continue
        if token == "," and in_from_clause:
            expect_table = True
            i += 1
            continue
        if expect_table:
            if token not in {"(", "select", "with"}:
                table_name = token
                if i + 2 < len(tokens) and tokens[i + 1] == ".":
                    table_name = tokens[i + 2]
                    i += 2
                if table_name in PHYSICAL_QUERY_TABLES:
                    refs.add(table_name)
                expect_table = False
            i += 1
            continue
        i += 1
    return refs


def _normalize_readonly_sql(sql: str) -> str:
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
    physical_tables = _physical_table_refs(statement)
    if physical_tables:
        names = ", ".join(sorted(physical_tables))
        raise ValueError(f"sidecar query cannot read physical table(s): {names}")
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
    allowed_view_expanded = False

    def _authorizer(action: int, arg1: str | None, arg2: str | None, db_name: str | None, source: str | None) -> int:
        nonlocal allowed_view_expanded
        if action == sqlite3.SQLITE_SELECT:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ:
            if source in ALLOWED_QUERY_VIEWS:
                allowed_view_expanded = True
                return sqlite3.SQLITE_OK
            if db_name == "temp" and arg1 in ALLOWED_QUERY_VIEWS:
                return sqlite3.SQLITE_OK
            # Direct physical-table refs are rejected before installation.
            # This branch only permits SQLite metadata reads emitted while
            # expanding the trusted TEMP views above.
            if allowed_view_expanded and source is None and arg1 in PHYSICAL_QUERY_TABLES and arg2 in ("", None):
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
    if limit > max_limit:
        raise ValueError(f"sidecar query limit exceeds maximum {max_limit}")
    row_limit = max(1, limit)
    conn = connect_sidecar(db_path, readonly=True)
    try:
        meta = read_sidecar_meta(db_path)
        if domain_id is not None:
            domain_exists = conn.execute(
                "SELECT 1 FROM domain_files WHERE domain_id = ? LIMIT 1",
                (domain_id,),
            ).fetchone()
            if domain_exists is None:
                raise ValueError(f"domain scope not found: {domain_id}")
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
            cursor = conn.execute(clean_sql)
        except sqlite3.Error as exc:
            raise ValueError(f"sidecar query failed: {exc}") from exc
        fetched = cursor.fetchmany(row_limit + 1)
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
