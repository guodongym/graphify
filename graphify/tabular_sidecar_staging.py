from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3

from graphify.tabular_manifest import TabularDomainManifest
from graphify.tabular_sidecar import (
    SidecarUpdateStats,
    SidecarWriteLock,
    bump_sidecar_generation,
    connect_sidecar,
    ensure_schema,
    ensure_sidecar_identity,
    read_sidecar_meta,
    set_sidecar_meta,
    tabular_file_config_from_json,
    update_sidecar,
)
from graphify.tabular_manifest import canonical_json_hash


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

                    union_hash = canonical_json_hash({
                        "source_file": source_file,
                        "active_config_hashes": active_hashes,
                    })
                    dst.execute(
                        "UPDATE source_files SET union_config_hash = ? WHERE file_id = ?",
                        (union_hash, canonical_file_id),
                    )

                    staged_file_id = next((sid for sid, cid in staging_to_canonical.items() if cid == canonical_file_id), None)
                    if staged_file_id is not None:
                        staged_sha = src.execute(
                            "SELECT sha256 FROM source_files WHERE file_id = ?",
                            (staged_file_id,),
                        ).fetchone()["sha256"]
                        if canonical_file_id in new_source_files or staged_sha != file_row["sha256"]:
                            _copy_staged_file_payload(src, dst, staged_file_id, canonical_file_id)
                            files_merged += 1
                            rows_merged += int(dst.execute(
                                "SELECT COUNT(*) FROM rows WHERE file_id = ?",
                                (canonical_file_id,),
                            ).fetchone()[0])
                    _rebuild_indexes_for_canonical_file(dst, canonical_file_id)
                    indexes_merged += int(dst.execute(
                        "SELECT COUNT(*) FROM indexed_values WHERE file_id = ?",
                        (canonical_file_id,),
                    ).fetchone()[0])
                    refs_merged += int(dst.execute(
                        "SELECT COUNT(*) FROM tabular_refs WHERE file_id = ?",
                        (canonical_file_id,),
                    ).fetchone()[0])

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
