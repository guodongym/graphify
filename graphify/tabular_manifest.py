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


class _ComparableTuple(tuple):
    def __eq__(self, other: object) -> bool:
        if isinstance(other, Sequence) and not isinstance(other, (str, bytes, bytearray)):
            return tuple(self) == tuple(other)
        return super().__eq__(other)

    def __hash__(self) -> int:
        return super().__hash__()


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


def load_tabular_domain_manifest(
    manifest_path: Path,
    *,
    cwd: Path | None = None,
    output_dir: Path | None = None,
    strict: bool = False,
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
    raw_domain_id = payload.get("domain_id")

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
        suffix = path.suffix.lower()
        if suffix not in {".tab", ".tsv", ".txt"}:
            continue
        _validate_manifest_file_allowed(path, repo_root)
        if suffix == ".txt":
            from graphify.tabular import looks_like_tabular_text
            if not looks_like_tabular_text(path):
                if not strict:
                    continue
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
    # Only require domain_id when sidecar files exist; non-tabular manifests
    # may have output_dir outside repo_root with no explicit domain_id.
    if sidecar_active:
        domain_id = _default_domain_id(repo_root, output_dir, raw_domain_id)
    else:
        try:
            domain_id = _default_domain_id(repo_root, output_dir, raw_domain_id)
        except ValueError:
            domain_id = "__none__"
    domain_hash = compute_sidecar_domain_config_hash(domain_id, sidecar_active)
    return TabularDomainManifest(
        manifest_path=manifest_path,
        repo_root=repo_root,
        repo_key=repo_key,
        domain_id=domain_id,
        files=_ComparableTuple(files),
        sidecar_active_files=_ComparableTuple(sidecar_active),
        sidecar_domain_config_hash=domain_hash,
    )
