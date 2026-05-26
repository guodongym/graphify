"""Regression tests for manifest-driven domain build/update CLI mode."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest


PYTHON = sys.executable
CLI_TIMEOUT_SECONDS = 20

_API_KEY_ENV_VARS = {
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "MOONSHOT_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "OLLAMA_API_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_PROFILE",
}

_PASSTHROUGH_ENV_VARS = {
    "HOME",
    "PATH",
    "PYTHONPATH",
    "SYSTEMROOT",
    "TMPDIR",
    "VIRTUAL_ENV",
}


def _env_without_api_keys(**overrides: str) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key in _PASSTHROUGH_ENV_VARS
    }
    env.pop("GRAPHIFY_OUT", None)
    env.pop("OLLAMA_BASE_URL", None)
    env.pop("AWS_REGION", None)
    env.pop("AWS_DEFAULT_REGION", None)
    for key in _API_KEY_ENV_VARS:
        env.pop(key, None)
    env["GRAPHIFY_API_TIMEOUT"] = "5"
    env["GRAPHIFY_MAX_WORKERS"] = "1"
    env["GRAPHIFY_NO_TIPS"] = "1"
    env.update(overrides)
    return env


def _run(
    args: list[str],
    cwd: Path,
    *,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [PYTHON, "-m", "graphify", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            env=env or _env_without_api_keys(),
            timeout=CLI_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail(
            f"graphify {' '.join(args)} timed out after "
            f"{CLI_TIMEOUT_SECONDS}s\nstdout:\n{exc.stdout or ''}\n"
            f"stderr:\n{exc.stderr or ''}"
        )


def _write_repo(repo: Path) -> dict[str, Path]:
    src = repo / "src"
    src.mkdir(parents=True)
    files = {
        "alpha": src / "alpha.py",
        "beta": src / "beta.py",
        "outside": src / "outside.py",
        "document": repo / "README.md",
        "paper": repo / "paper.pdf",
        "image": repo / "diagram.png",
        "unsupported": repo / "payload.bin",
    }
    files["alpha"].write_text(
        "class AlphaService:\n"
        "    def run(self):\n"
        "        return 'alpha'\n",
        encoding="utf-8",
    )
    files["beta"].write_text(
        "from src.alpha import AlphaService\n\n"
        "class BetaService:\n"
        "    def call(self):\n"
        "        return AlphaService().run()\n",
        encoding="utf-8",
    )
    files["outside"].write_text(
        "class OutsideManifestScope:\n"
        "    pass\n",
        encoding="utf-8",
    )
    files["document"].write_text("# Domain notes\n", encoding="utf-8")
    files["paper"].write_bytes(b"%PDF-1.4\n% test paper\n")
    files["image"].write_bytes(b"\x89PNG\r\n\x1a\n")
    files["unsupported"].write_bytes(b"\x00\x01unsupported")
    return files


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
                "files": [{"path": str(file_path)} for file_path in files],
                "caller_owned_extra": {"ignored": True},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def _domain_paths(repo: Path, name: str = "skill-core") -> tuple[Path, Path]:
    out_dir = repo / "graphify-out" / "domains" / name
    manifest = out_dir / "domain-files.json"
    return out_dir, manifest


def _read_graph(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _source_files(graph: dict[str, Any]) -> set[str]:
    sources: set[str] = set()
    for item in graph.get("nodes", []) + graph.get("links", []) + graph.get("edges", []):
        source = item.get("source_file")
        if source:
            sources.add(source)
    return sources


def _labels(graph: dict[str, Any]) -> set[str]:
    return {str(node.get("label", "")) for node in graph.get("nodes", [])}


def _all_json_strings(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, list):
        found: set[str] = set()
        for item in value:
            found.update(_all_json_strings(item))
        return found
    if isinstance(value, dict):
        found: set[str] = set()
        for item in value.values():
            found.update(_all_json_strings(item))
        return found
    return set()


def _assert_direct_domain_outputs(out_dir: Path) -> None:
    assert (out_dir / "graph.json").is_file()
    assert (out_dir / "GRAPH_REPORT.md").is_file()
    assert not (out_dir / "graphify-out" / "graph.json").exists()
    assert not (out_dir / "graphify-out" / "GRAPH_REPORT.md").exists()


def _assert_no_build_outputs(out_dir: Path) -> None:
    assert not (out_dir / "graph.json").exists()
    assert not (out_dir / "GRAPH_REPORT.md").exists()
    assert not (out_dir / ".graphify_state" / "update-state.json").exists()
    assert not (out_dir / ".graphify_analysis.json").exists()
    assert not (out_dir / ".graphify_labels.json").exists()


def _ast_cache_entries(cache_root: Path) -> list[Path]:
    return sorted((cache_root / "cache" / "ast").glob("*.json"))


def test_extract_manifest_writes_direct_outputs_limits_scan_and_query_reads_graph(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    files = _write_repo(repo)
    out_dir, manifest = _domain_paths(repo)
    _write_manifest(manifest, files=["src/alpha.py"])

    result = _run(
        ["extract", "--manifest", str(manifest), "--output-dir", str(out_dir)],
        cwd=repo,
    )

    assert result.returncode == 0, result.stderr
    _assert_direct_domain_outputs(out_dir)
    graph = _read_graph(out_dir / "graph.json")
    assert "src/alpha.py" in _source_files(graph)
    assert "src/outside.py" not in _source_files(graph)
    assert "OutsideManifestScope" not in _labels(graph)
    assert files["outside"].exists(), "outside file exists but must not be scanned"
    assert (repo / "graphify-out" / "cache").is_dir()
    assert not (out_dir / "cache").exists()

    query = _run(
        ["query", "AlphaService", "--graph", str(out_dir / "graph.json")],
        cwd=repo,
    )
    assert query.returncode == 0, query.stderr
    assert "AlphaService" in query.stdout


def test_extract_manifest_does_not_create_or_mutate_native_manifest(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write_repo(repo)
    out_dir, manifest = _domain_paths(repo)
    _write_manifest(manifest, files=["src/alpha.py"])
    native_manifest = repo / "graphify-out" / "manifest.json"
    native_manifest.write_text(
        json.dumps({"src/outside.py": {"ast_hash": "native-state"}}),
        encoding="utf-8",
    )
    original_native_manifest = native_manifest.read_text(encoding="utf-8")

    result = _run(
        ["extract", "--manifest", str(manifest), "--output-dir", str(out_dir)],
        cwd=repo,
    )

    assert result.returncode == 0, result.stderr
    _assert_direct_domain_outputs(out_dir)
    assert native_manifest.read_text(encoding="utf-8") == original_native_manifest
    graph = _read_graph(out_dir / "graph.json")
    assert "src/outside.py" not in _source_files(graph)
    assert "OutsideManifestScope" not in _labels(graph)


def test_extract_manifest_does_not_create_native_manifest_when_absent(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write_repo(repo)
    out_dir = tmp_path / "domain-out"
    manifest = repo / "domain-files.json"
    _write_manifest(manifest, files=["src/alpha.py"])
    native_manifest = repo / "graphify-out" / "manifest.json"

    result = _run(
        ["extract", "--manifest", str(manifest), "--output-dir", str(out_dir)],
        cwd=repo,
    )

    assert result.returncode == 0, result.stderr
    _assert_direct_domain_outputs(out_dir)
    assert not native_manifest.exists()


def test_extract_manifest_does_not_mutate_caller_manifest(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write_repo(repo)
    out_dir, manifest = _domain_paths(repo)
    _write_manifest(manifest, files=["src/alpha.py"])
    original_manifest = manifest.read_text(encoding="utf-8")

    result = _run(
        ["extract", "--manifest", str(manifest), "--output-dir", str(out_dir)],
        cwd=repo,
    )

    assert result.returncode == 0, result.stderr
    _assert_direct_domain_outputs(out_dir)
    assert manifest.read_text(encoding="utf-8") == original_manifest


def test_update_manifest_writes_direct_outputs_and_uses_manifest_only(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write_repo(repo)
    out_dir, manifest = _domain_paths(repo)
    _write_manifest(manifest, files=["src/alpha.py"])

    result = _run(
        ["update", "--manifest", str(manifest), "--output-dir", str(out_dir)],
        cwd=repo,
    )

    assert result.returncode == 0, result.stderr
    _assert_direct_domain_outputs(out_dir)
    graph = _read_graph(out_dir / "graph.json")
    assert "src/alpha.py" in _source_files(graph)
    assert "src/outside.py" not in _source_files(graph)
    assert "OutsideManifestScope" not in _labels(graph)
    assert (repo / "graphify-out" / "cache").is_dir()
    assert not (out_dir / "cache").exists()


def test_query_commands_read_manifest_update_output_dir_via_graphify_out(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _write_repo(repo)
    out_dir, manifest = _domain_paths(repo)
    _write_manifest(manifest, files=["src/alpha.py", "src/beta.py"])

    result = _run(
        ["update", "--manifest", str(manifest), "--output-dir", str(out_dir)],
        cwd=repo,
    )

    assert result.returncode == 0, result.stderr
    _assert_direct_domain_outputs(out_dir)

    query_env = _env_without_api_keys(GRAPHIFY_OUT=str(out_dir))
    query = _run(["query", "BetaService"], cwd=repo, env=query_env)
    explain = _run(["explain", "AlphaService"], cwd=repo, env=query_env)

    assert query.returncode == 0, query.stderr
    assert "BetaService" in query.stdout
    assert "src/beta.py" in query.stdout
    assert explain.returncode == 0, explain.stderr
    assert "Node: AlphaService" in explain.stdout
    assert "src/alpha.py" in explain.stdout


def test_update_manifest_does_not_create_or_mutate_native_manifest(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write_repo(repo)
    out_dir, manifest = _domain_paths(repo)
    _write_manifest(manifest, files=["src/alpha.py"])
    native_manifest = repo / "graphify-out" / "manifest.json"
    native_manifest.parent.mkdir(parents=True, exist_ok=True)
    native_manifest.write_text(
        json.dumps({"src/outside.py": {"ast_hash": "stale-native-state"}}),
        encoding="utf-8",
    )
    original_native_manifest = native_manifest.read_text(encoding="utf-8")

    result = _run(
        ["update", "--manifest", str(manifest), "--output-dir", str(out_dir)],
        cwd=repo,
    )

    assert result.returncode == 0, result.stderr
    _assert_direct_domain_outputs(out_dir)
    assert native_manifest.read_text(encoding="utf-8") == original_native_manifest
    graph = _read_graph(out_dir / "graph.json")
    assert "src/outside.py" not in _source_files(graph)
    assert "OutsideManifestScope" not in _labels(graph)


def test_manifest_mode_respects_graphify_out_for_cache_not_domain_outputs(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write_repo(repo)
    out_dir, manifest = _domain_paths(repo)
    _write_manifest(manifest, files=["src/alpha.py"])

    result = _run(
        ["extract", "--manifest", str(manifest), "--output-dir", str(out_dir)],
        cwd=repo,
        env=_env_without_api_keys(GRAPHIFY_OUT="graphify-out-custom"),
    )

    assert result.returncode == 0, result.stderr
    _assert_direct_domain_outputs(out_dir)
    assert (repo / "graphify-out-custom" / "cache").is_dir()
    assert not (repo / "graphify-out" / "cache").exists()
    assert not (out_dir / "graphify-out-custom").exists()


def test_manifest_mode_rejects_cache_root_option(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write_repo(repo)
    out_dir, manifest = _domain_paths(repo)
    _write_manifest(manifest, files=["src/alpha.py"])

    for cache_root_args in (
        ["--cache-root", str(tmp_path / "caller-cache")],
        [f"--cache-root={tmp_path / 'caller-cache'}"],
    ):
        result = _run(
            [
                "extract",
                "--manifest",
                str(manifest),
                "--output-dir",
                str(out_dir),
                *cache_root_args,
            ],
            cwd=repo,
        )

        assert result.returncode != 0
        assert "--cache-root" in result.stderr
        assert "not supported" in result.stderr or "unknown" in result.stderr
        assert not (out_dir / "graph.json").exists()
        assert not (tmp_path / "caller-cache").exists()


def test_manifest_mode_rejects_no_cluster_clearly(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write_repo(repo)
    out_dir, manifest = _domain_paths(repo)
    _write_manifest(manifest, files=["src/alpha.py"])

    result = _run(
        [
            "extract",
            "--manifest",
            str(manifest),
            "--output-dir",
            str(out_dir),
            "--no-cluster",
        ],
        cwd=repo,
    )

    assert result.returncode != 0
    assert "--no-cluster" in result.stderr
    assert "--manifest" in result.stderr
    assert "not supported" in result.stderr
    assert "GRAPH_REPORT.md" in result.stderr
    _assert_no_build_outputs(out_dir)


def test_absolute_graphify_out_controls_manifest_ast_cache_root(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write_repo(repo)
    out_dir, manifest = _domain_paths(repo)
    _write_manifest(manifest, files=["src/alpha.py"])
    shared_out = tmp_path / "shared-graphify-out"

    result = _run(
        ["extract", "--manifest", str(manifest), "--output-dir", str(out_dir)],
        cwd=repo,
        env=_env_without_api_keys(GRAPHIFY_OUT=str(shared_out)),
    )

    assert result.returncode == 0, result.stderr
    _assert_direct_domain_outputs(out_dir)
    assert len(_ast_cache_entries(shared_out)) == 1
    assert not (repo / "graphify-out" / "cache").exists()
    assert not (out_dir / "cache").exists()
    assert not (out_dir / "graphify-out" / "cache").exists()


def test_manifest_domains_with_different_output_dirs_share_ast_cache(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write_repo(repo)
    first_out, first_manifest = _domain_paths(repo, "first")
    second_out, second_manifest = _domain_paths(repo, "second")
    _write_manifest(first_manifest, files=["src/alpha.py"])
    _write_manifest(second_manifest, files=["src/alpha.py"])

    first = _run(
        ["extract", "--manifest", str(first_manifest), "--output-dir", str(first_out)],
        cwd=repo,
    )
    assert first.returncode == 0, first.stderr
    second = _run(
        ["extract", "--manifest", str(second_manifest), "--output-dir", str(second_out)],
        cwd=repo,
    )

    assert second.returncode == 0, second.stderr
    assert len(_ast_cache_entries(repo / "graphify-out")) == 1
    assert not (first_out / "cache").exists()
    assert not (second_out / "cache").exists()


def test_update_manifest_uses_active_graphify_out_ast_cache_root(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write_repo(repo)
    out_dir, manifest = _domain_paths(repo)
    _write_manifest(manifest, files=["src/alpha.py"])
    shared_out = tmp_path / "shared-update-cache"

    result = _run(
        ["update", "--manifest", str(manifest), "--output-dir", str(out_dir)],
        cwd=repo,
        env=_env_without_api_keys(GRAPHIFY_OUT=str(shared_out)),
    )

    assert result.returncode == 0, result.stderr
    _assert_direct_domain_outputs(out_dir)
    assert len(_ast_cache_entries(shared_out)) == 1
    assert not (repo / "graphify-out" / "cache").exists()
    assert not (out_dir / "cache").exists()


def test_relative_repo_root_and_output_dir_resolve_from_command_cwd(tmp_path: Path) -> None:
    repo = tmp_path / "jx3"
    _write_repo(repo)
    out_dir, manifest = _domain_paths(repo)
    _write_manifest(manifest, repo_root=".", files=["src/alpha.py"])

    result = _run(
        [
            "extract",
            "--manifest",
            "graphify-out/domains/skill-core/domain-files.json",
            "--output-dir",
            "graphify-out/domains/skill-core",
        ],
        cwd=repo,
    )

    assert result.returncode == 0, result.stderr
    _assert_direct_domain_outputs(out_dir)
    assert "src/alpha.py" in _source_files(_read_graph(out_dir / "graph.json"))


def test_absolute_output_dir_is_used_unchanged(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write_repo(repo)
    out_dir = tmp_path / "absolute-domain"
    manifest = repo / "domain-files.json"
    _write_manifest(manifest, repo_root=repo, files=["src/alpha.py"])

    result = _run(
        ["extract", "--manifest", str(manifest), "--output-dir", str(out_dir)],
        cwd=repo,
    )

    assert result.returncode == 0, result.stderr
    _assert_direct_domain_outputs(out_dir)
    assert not (repo / "absolute-domain").exists()


def test_native_manifest_path_and_positional_path_are_rejected(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write_repo(repo)
    native_manifest = repo / "graphify-out" / "manifest.json"
    _write_manifest(native_manifest, files=["src/alpha.py"])
    out_dir = repo / "graphify-out" / "domains" / "bad"
    domain_out_dir, domain_manifest = _domain_paths(repo)
    _write_manifest(domain_manifest, files=["src/alpha.py"])

    native = _run(
        ["extract", "--manifest", str(native_manifest), "--output-dir", str(out_dir)],
        cwd=repo,
    )
    assert native.returncode != 0
    assert "graphify-out/manifest.json" in native.stderr
    assert "domain-files.json" in native.stderr

    for command in ("extract", "update"):
        result = _run(
            [
                command,
                ".",
                "--manifest",
                str(domain_manifest),
                "--output-dir",
                str(domain_out_dir),
            ],
            cwd=repo,
        )
        assert result.returncode != 0
        assert "mutually exclusive" in result.stderr
        assert "--manifest" in result.stderr


def test_manifest_and_output_dir_must_be_supplied_together(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write_repo(repo)
    _, manifest = _domain_paths(repo)
    _write_manifest(manifest, files=["src/alpha.py"])

    for command in ("extract", "update"):
        missing_output = _run([command, "--manifest", str(manifest)], cwd=repo)
        assert missing_output.returncode != 0
        assert "--output-dir" in missing_output.stderr

        missing_manifest = _run(
            [command, ".", "--output-dir", "graphify-out/domains/skill-core"],
            cwd=repo,
        )
        assert missing_manifest.returncode != 0
        assert "--manifest" in missing_manifest.stderr
        if command == "extract":
            assert "--out" in missing_manifest.stderr


def test_manifest_option_requires_path_before_next_option(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write_repo(repo)

    result = _run(["extract", "--manifest", "--output-dir", "domain"], cwd=repo)

    assert result.returncode != 0
    assert "--manifest" in result.stderr
    assert "requires a path" in result.stderr


def test_manifest_mode_rejects_directory_out_option(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write_repo(repo)
    out_dir, manifest = _domain_paths(repo)
    _write_manifest(manifest, files=["src/alpha.py"])

    result = _run(
        [
            "extract",
            "--manifest",
            str(manifest),
            "--output-dir",
            str(out_dir),
            "--out",
            "legacy",
        ],
        cwd=repo,
    )

    assert result.returncode != 0
    assert "--out" in result.stderr
    assert "--output-dir" in result.stderr
    assert not (out_dir / "graph.json").exists()


def test_directory_extract_out_still_writes_nested_graphify_out(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    src = repo / "src"
    src.mkdir(parents=True)
    (src / "alpha.py").write_text(
        "class AlphaService:\n"
        "    def run(self):\n"
        "        return 'alpha'\n",
        encoding="utf-8",
    )
    out_root = tmp_path / "legacy-out"

    result = _run(
        ["extract", str(repo), "--backend", "ollama", "--out", str(out_root)],
        cwd=tmp_path,
        env=_env_without_api_keys(OLLAMA_BASE_URL="http://127.0.0.1:11434"),
    )

    assert result.returncode == 0, result.stderr
    assert (out_root / "graphify-out" / "graph.json").is_file()
    assert not (out_root / "graph.json").exists()


def test_code_like_manifest_extract_and_update_do_not_require_llm_api_keys(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write_repo(repo)
    out_dir, manifest = _domain_paths(repo)
    _write_manifest(manifest, files=["src/alpha.py"])

    extract = _run(
        ["extract", "--manifest", str(manifest), "--output-dir", str(out_dir)],
        cwd=repo,
        env=_env_without_api_keys(),
    )
    assert extract.returncode == 0, extract.stderr

    update = _run(
        ["update", "--manifest", str(manifest), "--output-dir", str(out_dir)],
        cwd=repo,
        env=_env_without_api_keys(),
    )
    assert update.returncode == 0, update.stderr


@pytest.mark.parametrize("command", ["extract", "update"])
def test_manifest_mode_accepts_max_workers_option(
    tmp_path: Path,
    command: str,
) -> None:
    repo = tmp_path / "repo"
    _write_repo(repo)
    out_dir, manifest = _domain_paths(repo)
    _write_manifest(manifest, files=["src/alpha.py"])

    result = _run(
        [
            command,
            "--manifest",
            str(manifest),
            "--output-dir",
            str(out_dir),
            "--max-workers",
            "1",
        ],
        cwd=repo,
        env=_env_without_api_keys(),
    )

    assert result.returncode == 0, result.stderr
    _assert_direct_domain_outputs(out_dir)


def test_manifest_mode_rejects_unknown_manifest_option(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write_repo(repo)
    out_dir, manifest = _domain_paths(repo)
    _write_manifest(manifest, files=["src/alpha.py"])

    for unknown_args in (["--unknown-flag"], ["-z"]):
        result = _run(
            [
                "extract",
                "--manifest",
                str(manifest),
                "--output-dir",
                str(out_dir),
                *unknown_args,
            ],
            cwd=repo,
        )

        assert result.returncode != 0
        assert "unknown manifest option" in result.stderr
        assert unknown_args[0] in result.stderr
        _assert_no_build_outputs(out_dir)


@pytest.mark.parametrize("command", ["extract", "update"])
@pytest.mark.parametrize(
    "unsupported_args",
    [
        ["--backend", "ollama"],
        ["--backend=ollama"],
        ["--model", "local-model"],
        ["--resolution", "1.5"],
        ["--exclude-hubs", "99"],
        ["--token-budget", "12000"],
    ],
)
def test_manifest_mode_rejects_ignored_native_options(
    tmp_path: Path,
    command: str,
    unsupported_args: list[str],
) -> None:
    repo = tmp_path / "repo"
    _write_repo(repo)
    out_dir, manifest = _domain_paths(repo)
    _write_manifest(manifest, files=["src/alpha.py"])

    result = _run(
        [
            command,
            "--manifest",
            str(manifest),
            "--output-dir",
            str(out_dir),
            *unsupported_args,
        ],
        cwd=repo,
    )

    assert result.returncode != 0
    assert unsupported_args[0].split("=", 1)[0] in result.stderr
    assert "not supported with --manifest" in result.stderr
    assert "--max-workers" in result.stderr
    _assert_no_build_outputs(out_dir)


def test_empty_manifest_fails_clearly_before_build(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write_repo(repo)
    out_dir, manifest = _domain_paths(repo)
    _write_manifest(manifest, files=[])

    result = _run(
        ["extract", "--manifest", str(manifest), "--output-dir", str(out_dir)],
        cwd=repo,
    )

    assert result.returncode != 0
    assert "manifest files list is empty" in result.stderr
    assert "produced an empty graph" not in result.stderr
    _assert_no_build_outputs(out_dir)


def test_duplicate_manifest_entries_are_deduplicated_in_state(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    files = _write_repo(repo)
    out_dir, manifest = _domain_paths(repo)
    _write_manifest(
        manifest,
        files=["src/alpha.py", "./src/alpha.py", files["alpha"].resolve()],
    )

    result = _run(
        ["extract", "--manifest", str(manifest), "--output-dir", str(out_dir)],
        cwd=repo,
    )

    assert result.returncode == 0, result.stderr
    _assert_direct_domain_outputs(out_dir)
    state = json.loads(
        (out_dir / ".graphify_state" / "update-state.json").read_text(encoding="utf-8")
    )
    assert state["files"] == ["src/alpha.py"]
    assert state["files_by_type"]["code"] == ["src/alpha.py"]
    assert state["current_files"] == ["src/alpha.py"]
    assert state["current_files_by_type"]["code"] == ["src/alpha.py"]


def test_unsupported_manifest_file_fails_before_writing_graph(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write_repo(repo)
    out_dir, manifest = _domain_paths(repo)
    _write_manifest(manifest, files=["payload.bin"])

    result = _run(
        ["extract", "--manifest", str(manifest), "--output-dir", str(out_dir)],
        cwd=repo,
    )

    assert result.returncode != 0
    assert "unsupported" in result.stderr.lower()
    assert "payload.bin" in result.stderr
    _assert_no_build_outputs(out_dir)


def test_malformed_manifest_fails_without_falling_back_to_directory_scan(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _write_repo(repo)
    out_dir = tmp_path / "domain-out"
    manifest = repo / "bad-domain-files.json"
    manifest.write_text("{not valid json", encoding="utf-8")

    result = _run(
        ["extract", "--manifest", str(manifest), "--output-dir", str(out_dir)],
        cwd=repo,
    )

    assert result.returncode != 0
    assert "manifest" in result.stderr.lower()
    assert "json" in result.stderr.lower()
    assert "OutsideManifestScope" not in result.stdout
    assert "OutsideManifestScope" not in result.stderr
    _assert_no_build_outputs(out_dir)
    assert not (repo / "graphify-out" / "graph.json").exists()
    assert not (repo / "graphify-out" / "manifest.json").exists()


def test_update_malformed_manifest_fails_without_falling_back_to_directory_scan(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _write_repo(repo)
    out_dir = tmp_path / "domain-out"
    manifest = repo / "bad-domain-files.json"
    manifest.write_text("{not valid json", encoding="utf-8")

    result = _run(
        ["update", "--manifest", str(manifest), "--output-dir", str(out_dir)],
        cwd=repo,
    )

    assert result.returncode != 0
    assert "manifest" in result.stderr.lower()
    assert "json" in result.stderr.lower()
    assert "OutsideManifestScope" not in result.stdout
    assert "OutsideManifestScope" not in result.stderr
    _assert_no_build_outputs(out_dir)
    assert not (repo / "graphify-out" / "graph.json").exists()
    assert not (repo / "graphify-out" / "manifest.json").exists()


def test_document_paper_and_image_manifest_files_are_rejected_in_phase_0(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    files = _write_repo(repo)

    for file_type, source_path in (
        ("document", files["document"]),
        ("paper", files["paper"]),
        ("image", files["image"]),
    ):
        out_dir, manifest = _domain_paths(repo, file_type)
        _write_manifest(manifest, files=[source_path.relative_to(repo)])

        result = _run(
            ["extract", "--manifest", str(manifest), "--output-dir", str(out_dir)],
            cwd=repo,
        )

        assert result.returncode != 0
        err = result.stderr.lower()
        assert source_path.name.lower() in err
        assert any(term in err for term in ("phase 0", "directory", "later", "semantic"))
        assert not (out_dir / "graph.json").exists()


def test_absolute_and_relative_manifest_paths_share_identity_state_and_cache(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    files = _write_repo(repo)

    rel_out, rel_manifest = _domain_paths(repo, "relative")
    _write_manifest(rel_manifest, files=["src/alpha.py"])
    rel = _run(
        ["extract", "--manifest", str(rel_manifest), "--output-dir", str(rel_out)],
        cwd=repo,
    )
    assert rel.returncode == 0, rel.stderr

    abs_out, abs_manifest = _domain_paths(repo, "absolute")
    _write_manifest(abs_manifest, files=[files["alpha"].resolve()])
    abs_result = _run(
        ["extract", "--manifest", str(abs_manifest), "--output-dir", str(abs_out)],
        cwd=repo,
    )
    assert abs_result.returncode == 0, abs_result.stderr

    assert _source_files(_read_graph(rel_out / "graph.json")) == _source_files(
        _read_graph(abs_out / "graph.json")
    )
    assert _source_files(_read_graph(rel_out / "graph.json")) == {"src/alpha.py"}

    rel_state = json.loads((rel_out / ".graphify_state" / "update-state.json").read_text())
    abs_state = json.loads((abs_out / ".graphify_state" / "update-state.json").read_text())
    assert "src/alpha.py" in _all_json_strings(rel_state)
    assert "src/alpha.py" in _all_json_strings(abs_state)
    assert str(files["alpha"].resolve()) not in _all_json_strings(abs_state)

    cache_entries = sorted((repo / "graphify-out" / "cache" / "ast").glob("*.json"))
    assert len(cache_entries) == 1


def test_extract_manifest_writes_update_state(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write_repo(repo)
    out_dir, manifest = _domain_paths(repo)
    _write_manifest(manifest, files=["src/alpha.py"])

    result = _run(
        ["extract", "--manifest", str(manifest), "--output-dir", str(out_dir)],
        cwd=repo,
    )

    assert result.returncode == 0, result.stderr
    state_path = out_dir / ".graphify_state" / "update-state.json"
    assert state_path.is_file()
    assert "src/alpha.py" in _all_json_strings(json.loads(state_path.read_text()))


def test_extract_manifest_state_uses_repo_relative_paths_only(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    files = _write_repo(repo)
    out_dir, manifest = _domain_paths(repo)
    _write_manifest(manifest, files=["./src/alpha.py", files["beta"].resolve()])

    result = _run(
        ["extract", "--manifest", str(manifest), "--output-dir", str(out_dir)],
        cwd=repo,
    )

    assert result.returncode == 0, result.stderr
    state = json.loads(
        (out_dir / ".graphify_state" / "update-state.json").read_text(encoding="utf-8")
    )
    assert state["files"] == ["src/alpha.py", "src/beta.py"]
    assert state["files_by_type"]["code"] == ["src/alpha.py", "src/beta.py"]
    assert state["current_files"] == ["src/alpha.py", "src/beta.py"]
    assert state["current_files_by_type"]["code"] == ["src/alpha.py", "src/beta.py"]
    assert state["previous_files"] == []
    assert state["previous_files_by_type"]["code"] == []
    all_strings = _all_json_strings(state)
    assert str(repo.resolve()) not in all_strings
    assert str(files["alpha"].resolve()) not in all_strings
    assert str(files["beta"].resolve()) not in all_strings
    assert "./src/alpha.py" not in all_strings


def test_update_manifest_missing_state_full_rebuilds_without_native_manifest(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write_repo(repo)
    out_dir, manifest = _domain_paths(repo)
    _write_manifest(manifest, files=["src/alpha.py"])
    native_manifest = repo / "graphify-out" / "manifest.json"
    native_manifest.parent.mkdir(parents=True, exist_ok=True)
    native_manifest.write_text(
        json.dumps({str(repo / "src" / "outside.py"): {"ast_hash": "stale"}}),
        encoding="utf-8",
    )
    original_native_manifest = native_manifest.read_text(encoding="utf-8")

    result = _run(
        ["update", "--manifest", str(manifest), "--output-dir", str(out_dir)],
        cwd=repo,
    )

    assert result.returncode == 0, result.stderr
    _assert_direct_domain_outputs(out_dir)
    assert (out_dir / ".graphify_state" / "update-state.json").is_file()
    graph = _read_graph(out_dir / "graph.json")
    assert "src/alpha.py" in _source_files(graph)
    assert "src/outside.py" not in _source_files(graph)
    assert "OutsideManifestScope" not in _labels(graph)
    assert native_manifest.read_text(encoding="utf-8") == original_native_manifest
    state = json.loads(
        (out_dir / ".graphify_state" / "update-state.json").read_text(encoding="utf-8")
    )
    assert state["files"] == ["src/alpha.py"]
    assert state["current_files"] == ["src/alpha.py"]
    assert state["previous_files"] == []
    all_strings = _all_json_strings(state)
    assert str(repo.resolve()) not in all_strings
    assert str((repo / "src" / "alpha.py").resolve()) not in all_strings


def test_update_manifest_normalizes_existing_absolute_state_paths(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    files = _write_repo(repo)
    out_dir, manifest = _domain_paths(repo)
    _write_manifest(manifest, files=["src/alpha.py"])
    state_path = out_dir / ".graphify_state" / "update-state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "files": [str(files["alpha"].resolve()), str(files["beta"].resolve())],
                "files_by_type": {
                    "code": [
                        str(files["alpha"].resolve()),
                        str(files["beta"].resolve()),
                    ]
                },
            }
        ),
        encoding="utf-8",
    )

    result = _run(
        ["update", "--manifest", str(manifest), "--output-dir", str(out_dir)],
        cwd=repo,
    )

    assert result.returncode == 0, result.stderr
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["current_files"] == ["src/alpha.py"]
    assert state["previous_files"] == ["src/alpha.py", "src/beta.py"]
    assert state["previous_files_by_type"]["code"] == ["src/alpha.py", "src/beta.py"]
    all_strings = _all_json_strings(state)
    assert str(repo.resolve()) not in all_strings
    assert str(files["alpha"].resolve()) not in all_strings
    assert str(files["beta"].resolve()) not in all_strings


def test_update_manifest_discards_non_utf8_existing_state(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write_repo(repo)
    out_dir, manifest = _domain_paths(repo)
    _write_manifest(manifest, files=["src/alpha.py"])
    state_path = out_dir / ".graphify_state" / "update-state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_bytes(b"\xff\xfe\x00not-json")

    result = _run(
        ["update", "--manifest", str(manifest), "--output-dir", str(out_dir)],
        cwd=repo,
    )

    assert result.returncode == 0, result.stderr
    _assert_direct_domain_outputs(out_dir)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["files"] == ["src/alpha.py"]
    assert state["current_files"] == ["src/alpha.py"]
    assert state["previous_files"] == []
    all_strings = _all_json_strings(state)
    assert str(repo.resolve()) not in all_strings
    assert str((repo / "src" / "alpha.py").resolve()) not in all_strings


def test_update_manifest_ignores_unlisted_file_added_between_runs(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write_repo(repo)
    out_dir, manifest = _domain_paths(repo)
    _write_manifest(manifest, files=["src/alpha.py"])

    initial = _run(
        ["update", "--manifest", str(manifest), "--output-dir", str(out_dir)],
        cwd=repo,
    )
    assert initial.returncode == 0, initial.stderr

    (repo / "src" / "rogue.py").write_text(
        "class RogueManifestScope:\n"
        "    pass\n",
        encoding="utf-8",
    )
    updated = _run(
        ["update", "--manifest", str(manifest), "--output-dir", str(out_dir)],
        cwd=repo,
    )

    assert updated.returncode == 0, updated.stderr
    graph = _read_graph(out_dir / "graph.json")
    assert "src/alpha.py" in _source_files(graph)
    assert "src/rogue.py" not in _source_files(graph)
    assert "RogueManifestScope" not in _labels(graph)


def test_update_manifest_rebuilds_changed_listed_file_with_manifest_scope(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    files = _write_repo(repo)
    out_dir, manifest = _domain_paths(repo)
    _write_manifest(manifest, files=["src/alpha.py"])

    initial = _run(
        ["update", "--manifest", str(manifest), "--output-dir", str(out_dir)],
        cwd=repo,
    )
    assert initial.returncode == 0, initial.stderr
    assert "AlphaService" in _labels(_read_graph(out_dir / "graph.json"))

    files["alpha"].write_text(
        "class AlphaServiceRenamed:\n"
        "    def run(self):\n"
        "        return 'alpha-v2'\n",
        encoding="utf-8",
    )
    updated = _run(
        ["update", "--manifest", str(manifest), "--output-dir", str(out_dir)],
        cwd=repo,
    )

    assert updated.returncode == 0, updated.stderr
    graph = _read_graph(out_dir / "graph.json")
    assert "src/alpha.py" in _source_files(graph)
    assert "src/outside.py" not in _source_files(graph)
    assert "AlphaServiceRenamed" in _labels(graph)
    assert "AlphaService" not in _labels(graph)
    assert "OutsideManifestScope" not in _labels(graph)


def test_update_manifest_prunes_removed_files(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write_repo(repo)
    out_dir, manifest = _domain_paths(repo)
    _write_manifest(manifest, files=["src/alpha.py", "src/beta.py"])

    initial = _run(
        ["extract", "--manifest", str(manifest), "--output-dir", str(out_dir)],
        cwd=repo,
    )
    assert initial.returncode == 0, initial.stderr
    initial_graph = _read_graph(out_dir / "graph.json")
    assert "src/beta.py" in _source_files(initial_graph)

    _write_manifest(manifest, files=["src/alpha.py"])
    updated = _run(
        ["update", "--manifest", str(manifest), "--output-dir", str(out_dir)],
        cwd=repo,
    )

    assert updated.returncode == 0, updated.stderr
    updated_graph = _read_graph(out_dir / "graph.json")
    assert "src/alpha.py" in _source_files(updated_graph)
    assert "src/beta.py" not in _source_files(updated_graph)
    assert "BetaService" not in _labels(updated_graph)
    state = json.loads(
        (out_dir / ".graphify_state" / "update-state.json").read_text(encoding="utf-8")
    )
    assert state["mode"] == "update"
    assert state["files"] == ["src/alpha.py"]
    assert state["files_by_type"]["code"] == ["src/alpha.py"]
    assert state["current_files"] == ["src/alpha.py"]
    assert state["current_files_by_type"]["code"] == ["src/alpha.py"]
    assert state["previous_files"] == ["src/alpha.py", "src/beta.py"]
    assert state["previous_files_by_type"]["code"] == ["src/alpha.py", "src/beta.py"]
    all_strings = _all_json_strings(state)
    assert str(repo.resolve()) not in all_strings
    assert str((repo / "src" / "alpha.py").resolve()) not in all_strings
    assert str((repo / "src" / "beta.py").resolve()) not in all_strings
