from __future__ import annotations

import json
from pathlib import Path

import pytest

from graphify.manifest import DomainManifestError, load_domain_manifest


def _write_manifest(
    path: Path,
    *,
    repo_root: str | Path = ".",
    files: list[str | Path],
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "repo_root": str(repo_root),
                "files": [
                    {"path": str(file_path), "caller_owned_extra": True}
                    for file_path in files
                ],
                "caller_owned_top_level": {"ignored": True},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def test_load_domain_manifest_materializes_ordered_code_files_relative_to_cwd(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "alpha.py").write_text("class Alpha: pass\n", encoding="utf-8")
    (repo / "src" / "config.tab").write_text("ID\tName\n1\tAlpha\n", encoding="utf-8")
    manifest = _write_manifest(
        repo / "graphify-out" / "domains" / "skill-core" / "domain-files.json",
        repo_root="repo",
        files=["src/alpha.py", "src/config.tab"],
    )
    original_manifest = manifest.read_text(encoding="utf-8")

    result = load_domain_manifest(manifest.relative_to(tmp_path), cwd=tmp_path)

    assert result.manifest_path == manifest.resolve()
    assert result.repo_root == repo.resolve()
    assert result.source_paths == [
        (repo / "src" / "alpha.py").resolve(),
        (repo / "src" / "config.tab").resolve(),
    ]
    assert result.relative_source_paths == ["src/alpha.py", "src/config.tab"]
    assert result.files_by_type == {
        "code": [
            str((repo / "src" / "alpha.py").resolve()),
            str((repo / "src" / "config.tab").resolve()),
        ],
        "document": [],
        "paper": [],
        "image": [],
        "video": [],
    }
    assert result.relative_files_by_type == {
        "code": ["src/alpha.py", "src/config.tab"],
        "document": [],
        "paper": [],
        "image": [],
        "video": [],
    }
    assert manifest.read_text(encoding="utf-8") == original_manifest


def test_load_domain_manifest_accepts_absolute_repo_root_and_file_paths(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    source = repo / "src" / "alpha.py"
    source.write_text("class Alpha: pass\n", encoding="utf-8")
    manifest = _write_manifest(
        repo / "domain-files.json",
        repo_root=repo.resolve(),
        files=[source.resolve()],
    )

    result = load_domain_manifest(manifest, cwd=tmp_path / "elsewhere")

    assert result.repo_root == repo.resolve()
    assert result.source_paths == [source.resolve()]
    assert result.relative_source_paths == ["src/alpha.py"]
    assert result.files_by_type["code"] == [str(source.resolve())]
    assert result.relative_files_by_type["code"] == ["src/alpha.py"]


def test_load_domain_manifest_deduplicates_resolved_repo_relative_paths(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    source = repo / "src" / "alpha.py"
    source.write_text("class Alpha: pass\n", encoding="utf-8")
    manifest = _write_manifest(
        repo / "domain-files.json",
        repo_root=repo,
        files=["src/alpha.py", "./src/alpha.py", source.resolve()],
    )

    result = load_domain_manifest(manifest, cwd=tmp_path)

    assert result.source_paths == [source.resolve()]
    assert result.relative_source_paths == ["src/alpha.py"]
    assert result.files_by_type["code"] == [str(source.resolve())]
    assert result.relative_files_by_type["code"] == ["src/alpha.py"]


def test_load_domain_manifest_allows_empty_files_list_for_sidecar_only_coordination(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    manifest = _write_manifest(repo / "domain-files.json", repo_root=repo, files=[])

    result = load_domain_manifest(manifest, cwd=tmp_path)

    assert result.source_paths == []
    assert result.relative_source_paths == []
    assert result.manifest_skipped_files == []


def test_load_domain_manifest_accepts_unicode_file_paths(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    source = repo / "src" / "模块.py"
    source.write_text("class 模块: pass\n", encoding="utf-8")
    manifest = _write_manifest(repo / "domain-files.json", repo_root=repo, files=["src/模块.py"])

    result = load_domain_manifest(manifest, cwd=tmp_path)

    assert result.source_paths == [source.resolve()]
    assert result.relative_source_paths == ["src/模块.py"]
    assert result.relative_files_by_type["code"] == ["src/模块.py"]


def test_load_domain_manifest_rejects_native_graphify_manifest(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    native_manifest = _write_manifest(
        repo / "graphify-out" / "manifest.json",
        repo_root=repo,
        files=[],
    )

    with pytest.raises(DomainManifestError, match="domain-files.json"):
        load_domain_manifest(native_manifest, cwd=tmp_path)


def test_load_domain_manifest_rejects_native_state_shape_before_schema_validation(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    native_manifest = repo / "graphify-out" / "manifest.json"
    native_manifest.parent.mkdir(parents=True)
    native_manifest.write_text(
        json.dumps({"src/a.py": {"mtime": 1, "ast_hash": "abc"}}),
        encoding="utf-8",
    )

    with pytest.raises(DomainManifestError, match="domain-files.json") as exc_info:
        load_domain_manifest(native_manifest.relative_to(tmp_path), cwd=tmp_path)

    assert "graphify-out/manifest.json" in str(exc_info.value)
    assert "repo_root" not in str(exc_info.value)


def test_load_domain_manifest_rejects_native_graphify_manifest_under_graphify_out_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    custom_out = repo / "custom-out"
    native_manifest = custom_out / "manifest.json"
    _write_manifest(native_manifest, repo_root=repo, files=[])
    monkeypatch.setenv("GRAPHIFY_OUT", "custom-out")

    with pytest.raises(DomainManifestError, match="domain-files.json") as exc_info:
        load_domain_manifest(native_manifest, cwd=repo)

    assert "manifest repo_root" not in str(exc_info.value)


def test_load_domain_manifest_allows_manifest_json_under_non_output_custom_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    source = repo / "src" / "alpha.py"
    source.write_text("class Alpha: pass\n", encoding="utf-8")
    manifest = _write_manifest(
        repo / "domains" / "custom-out" / "manifest.json",
        repo_root=repo,
        files=["src/alpha.py"],
    )
    monkeypatch.setenv("GRAPHIFY_OUT", "custom-out")

    result = load_domain_manifest(manifest, cwd=repo)

    assert result.source_paths == [source.resolve()]
    assert result.relative_source_paths == ["src/alpha.py"]


@pytest.mark.parametrize(
    ("file_name", "contents", "message"),
    [
        ("README.md", "# Notes\n", "document"),
        ("paper.pdf", "%PDF-1.4\n", "paper"),
        ("diagram.png", "\x89PNG\r\n", "image"),
        ("clip.mp4", "video", "video"),
        ("payload.bin", "binary", "unsupported"),
        ("script", "#!/usr/bin/env python3\nprint('hi')\n", "no AST extractor"),
    ],
)
def test_load_domain_manifest_skips_non_phase0_ast_files_by_default(
    tmp_path: Path,
    file_name: str,
    contents: str,
    message: str,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / file_name
    source.write_text(contents, encoding="utf-8")
    code = repo / "alpha.py"
    code.write_text("class Alpha: pass\n", encoding="utf-8")
    manifest = _write_manifest(repo / "domain-files.json", repo_root=repo, files=["alpha.py", file_name])

    result = load_domain_manifest(manifest, cwd=tmp_path)

    assert result.relative_source_paths == ["alpha.py"]
    assert result.relative_files_by_type["code"] == ["alpha.py"]
    expected_reason = "unsupported file type" if message == "unsupported" else message
    assert result.manifest_skipped_files == [{"file": file_name, "reason": expected_reason}]


@pytest.mark.parametrize(
    ("file_name", "contents", "message"),
    [
        ("README.md", "# Notes\n", "document"),
        ("paper.pdf", "%PDF-1.4\n", "paper"),
        ("diagram.png", "\x89PNG\r\n", "image"),
        ("clip.mp4", "video", "video"),
        ("payload.bin", "binary", "unsupported"),
        ("script", "#!/usr/bin/env python3\nprint('hi')\n", "no AST extractor"),
    ],
)
def test_load_domain_manifest_strict_rejects_non_phase0_ast_files(
    tmp_path: Path,
    file_name: str,
    contents: str,
    message: str,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / file_name
    source.write_text(contents, encoding="utf-8")
    manifest = _write_manifest(repo / "domain-files.json", repo_root=repo, files=[file_name])

    with pytest.raises(DomainManifestError, match=message):
        load_domain_manifest(manifest, cwd=tmp_path, strict=True)


def test_load_domain_manifest_rejects_missing_files(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    manifest = _write_manifest(
        repo / "domain-files.json",
        repo_root=repo,
        files=["src/missing.py"],
    )

    with pytest.raises(DomainManifestError, match="missing"):
        load_domain_manifest(manifest, cwd=tmp_path)


def test_load_domain_manifest_rejects_self_reference_manifest_file(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    manifest = _write_manifest(
        repo / "domain-files.json",
        repo_root=repo,
        files=["domain-files.json"],
    )

    with pytest.raises(DomainManifestError, match="manifest itself"):
        load_domain_manifest(manifest, cwd=tmp_path)


def test_load_domain_manifest_accepts_symlink_to_repo_file(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    source = repo / "src" / "alpha.py"
    source.write_text("class Alpha: pass\n", encoding="utf-8")
    link = repo / "src" / "link.py"
    link.symlink_to(source)
    manifest = _write_manifest(repo / "domain-files.json", repo_root=repo, files=["src/link.py"])

    result = load_domain_manifest(manifest, cwd=tmp_path)

    assert result.source_paths == [source.resolve()]
    assert result.relative_source_paths == ["src/alpha.py"]


def test_load_domain_manifest_rejects_symlink_escaping_repo_root(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    outside = tmp_path / "outside.py"
    outside.write_text("class Outside: pass\n", encoding="utf-8")
    link = repo / "src" / "outside.py"
    link.symlink_to(outside)
    manifest = _write_manifest(repo / "domain-files.json", repo_root=repo, files=["src/outside.py"])

    with pytest.raises(DomainManifestError, match="escapes repo_root"):
        load_domain_manifest(manifest, cwd=tmp_path)


def test_load_domain_manifest_rejects_paths_that_escape_repo_root(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("class Outside: pass\n", encoding="utf-8")
    manifest = _write_manifest(
        repo / "domain-files.json",
        repo_root=repo,
        files=["../outside.py"],
    )

    with pytest.raises(DomainManifestError, match="escapes repo_root"):
        load_domain_manifest(manifest, cwd=tmp_path)
