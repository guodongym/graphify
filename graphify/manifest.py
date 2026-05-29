"""Manifest helpers.

This module preserves the native ``graphify-out/manifest.json`` incremental
state facade while also providing a read-only loader for caller-owned domain
file manifests.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# re-export manifest helpers from detect for backwards compatibility
from graphify.detect import FileType, classify_file, detect_incremental, load_manifest, save_manifest
from graphify.extract import _get_extractor


class DomainManifestError(ValueError):
    """Raised when a caller-owned domain manifest is invalid."""


@dataclass(frozen=True)
class DomainManifest:
    manifest_path: Path
    repo_root: Path
    source_paths: list[Path]
    relative_source_paths: list[str]
    files_by_type: dict[str, list[str]]
    relative_files_by_type: dict[str, list[str]]
    manifest_skipped_files: list[dict[str, str]]


_NATIVE_MANIFEST_RELATIVE_PATH = Path("graphify-out") / "manifest.json"


def _empty_files_by_type() -> dict[str, list[str]]:
    return {
        FileType.CODE.value: [],
        FileType.DOCUMENT.value: [],
        FileType.PAPER.value: [],
        FileType.IMAGE.value: [],
        FileType.VIDEO.value: [],
    }


def _resolve_against_cwd(path: str | Path, cwd: Path) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = cwd / candidate
    return candidate.resolve()


def _repo_relative(path: Path, repo_root: Path) -> str:
    try:
        return path.relative_to(repo_root).as_posix()
    except ValueError as exc:
        raise DomainManifestError(
            f"manifest file path escapes repo_root: {path}"
        ) from exc


def _read_manifest_object(manifest_path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DomainManifestError(f"manifest not found: {manifest_path}") from exc
    except json.JSONDecodeError as exc:
        raise DomainManifestError(f"manifest is not valid JSON: {manifest_path}") from exc
    except OSError as exc:
        raise DomainManifestError(f"could not read manifest: {manifest_path}") from exc
    if not isinstance(raw, dict):
        raise DomainManifestError("manifest must be a JSON object")
    return raw


def _has_path_suffix(path: Path, suffix: Path) -> bool:
    suffix_parts = suffix.parts
    return len(path.parts) >= len(suffix_parts) and path.parts[-len(suffix_parts):] == suffix_parts


def _is_native_manifest_path(
    path: Path,
    *,
    command_cwd: Path | None = None,
    repo_root: Path | None = None,
) -> bool:
    if _has_path_suffix(path, _NATIVE_MANIFEST_RELATIVE_PATH):
        return True
    graphify_out = os.environ.get("GRAPHIFY_OUT")
    if not graphify_out:
        return False
    graphify_out_path = Path(graphify_out)
    native_manifest = graphify_out_path / "manifest.json"
    if graphify_out_path.is_absolute():
        try:
            return path.resolve() == native_manifest.resolve()
        except OSError:
            return path == native_manifest
    candidates = []
    if command_cwd is not None:
        candidates.append(command_cwd / native_manifest)
    if repo_root is not None:
        candidates.append(repo_root / native_manifest)
    for candidate in candidates:
        try:
            if path.resolve() == candidate.resolve():
                return True
        except OSError:
            if path == candidate:
                return True
    return False


def _raise_native_manifest_error() -> None:
    raise DomainManifestError(
        "refusing to use native graphify-out/manifest.json as a caller "
        "domain manifest; use a caller-owned domain-files.json path instead"
    )


def load_domain_manifest(
    manifest_path: str | Path,
    *,
    cwd: str | Path | None = None,
    strict: bool = False,
) -> DomainManifest:
    """Load a caller-owned Phase 0 domain file manifest.

    The accepted schema is intentionally small: ``repo_root`` plus
    ``files[].path``. Unknown caller-owned fields are ignored. Accepted files
    are returned as absolute paths for materialization and repo-root-relative
    POSIX paths for Graphify identity fields and manifest-mode state. By
    default, unsupported files are skipped so manifest mode behaves like native
    directory scanning. Set ``strict`` to preserve fail-fast validation.
    """

    command_cwd = Path.cwd().resolve() if cwd is None else Path(cwd).resolve()
    resolved_manifest_path = _resolve_against_cwd(manifest_path, command_cwd)
    if _is_native_manifest_path(resolved_manifest_path, command_cwd=command_cwd):
        _raise_native_manifest_error()

    raw_manifest = _read_manifest_object(resolved_manifest_path)

    repo_root_value = raw_manifest.get("repo_root")
    if not isinstance(repo_root_value, str) or not repo_root_value:
        raise DomainManifestError("manifest repo_root must be a non-empty string")
    repo_root = _resolve_against_cwd(repo_root_value, command_cwd)

    native_manifest_path = (repo_root / _NATIVE_MANIFEST_RELATIVE_PATH).resolve()
    if resolved_manifest_path == native_manifest_path or _is_native_manifest_path(
        resolved_manifest_path,
        repo_root=repo_root,
    ):
        _raise_native_manifest_error()

    files = raw_manifest.get("files")
    if not isinstance(files, list):
        raise DomainManifestError("manifest files must be a list")

    source_paths: list[Path] = []
    relative_source_paths: list[str] = []
    files_by_type = _empty_files_by_type()
    relative_files_by_type = _empty_files_by_type()
    manifest_skipped_files: list[dict[str, str]] = []
    seen_relative_paths: set[str] = set()

    for index, entry in enumerate(files):
        if not isinstance(entry, dict):
            raise DomainManifestError(f"manifest files[{index}] must be an object")
        path_value = entry.get("path")
        if not isinstance(path_value, str) or not path_value:
            raise DomainManifestError(
                f"manifest files[{index}].path must be a non-empty string"
            )

        candidate = Path(path_value)
        if not candidate.is_absolute():
            candidate = repo_root / candidate
        resolved_path = candidate.resolve()
        if resolved_path == resolved_manifest_path:
            raise DomainManifestError(
                f"manifest file list must not include the manifest itself: {path_value}"
            )
        relative_path = _repo_relative(resolved_path, repo_root)
        if relative_path in seen_relative_paths:
            continue
        seen_relative_paths.add(relative_path)

        if not resolved_path.is_file():
            raise DomainManifestError(f"manifest file is missing: {relative_path}")

        file_type = classify_file(resolved_path)
        if file_type is None:
            if not strict:
                manifest_skipped_files.append(
                    {"file": relative_path, "reason": "unsupported file type"}
                )
                continue
            raise DomainManifestError(
                f"manifest file is unsupported in Phase 0: {relative_path}"
            )
        if file_type != FileType.CODE:
            if not strict:
                manifest_skipped_files.append(
                    {"file": relative_path, "reason": file_type.value}
                )
                continue
            raise DomainManifestError(
                f"manifest file is classified as {file_type.value}, but Phase 0 "
                f"manifest builds only accept AST/code files: {relative_path}"
            )
        if _get_extractor(resolved_path) is None:
            if not strict:
                manifest_skipped_files.append(
                    {"file": relative_path, "reason": "no AST extractor"}
                )
                continue
            raise DomainManifestError(
                f"manifest file has no AST extractor in Phase 0: {relative_path}"
            )

        source_paths.append(resolved_path)
        relative_source_paths.append(relative_path)
        files_by_type[FileType.CODE.value].append(str(resolved_path))
        relative_files_by_type[FileType.CODE.value].append(relative_path)

    return DomainManifest(
        manifest_path=resolved_manifest_path,
        repo_root=repo_root,
        source_paths=source_paths,
        relative_source_paths=relative_source_paths,
        files_by_type=files_by_type,
        relative_files_by_type=relative_files_by_type,
        manifest_skipped_files=manifest_skipped_files,
    )


__all__ = [
    "DomainManifest",
    "DomainManifestError",
    "detect_incremental",
    "load_domain_manifest",
    "load_manifest",
    "save_manifest",
]
