"""Backward-compatibility checks for native directory-mode CLI behavior."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

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
    "PATH",
    "PYTHONPATH",
    "SYSTEMROOT",
    "TMPDIR",
    "VIRTUAL_ENV",
}


def _env(*, home: Path, **overrides: str) -> dict[str, str]:
    home.mkdir(parents=True, exist_ok=True)
    env = {
        key: value
        for key, value in os.environ.items()
        if key in _PASSTHROUGH_ENV_VARS
    }
    for key in _API_KEY_ENV_VARS:
        env.pop(key, None)
    env.pop("GRAPHIFY_OUT", None)
    env.pop("OLLAMA_BASE_URL", None)
    env.pop("AWS_REGION", None)
    env.pop("AWS_DEFAULT_REGION", None)
    env["HOME"] = str(home)
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
    home: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [PYTHON, "-m", "graphify", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            env=env or _env(home=home or cwd.parent / ".graphify-test-home"),
            timeout=CLI_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail(
            f"graphify {' '.join(args)} timed out after "
            f"{CLI_TIMEOUT_SECONDS}s\nstdout:\n{exc.stdout or ''}\n"
            f"stderr:\n{exc.stderr or ''}"
        )


def _write_code_repo(repo: Path) -> Path:
    src = repo / "src"
    src.mkdir(parents=True)
    source = src / "alpha.py"
    source.write_text(
        "class AlphaService:\n"
        "    def run(self):\n"
        "        return 'alpha'\n",
        encoding="utf-8",
    )
    return source


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _assert_native_manifest_shape(manifest: dict, source: Path) -> None:
    assert "schema_version" not in manifest
    assert "repo_root" not in manifest
    assert "files" not in manifest
    source_keys = [key for key in manifest if key.endswith(source.name)]
    assert source_keys
    assert "ast_hash" in manifest[source_keys[0]]


@pytest.mark.parametrize("command", ["extract", "update"])
def test_manifest_flags_are_documented_in_subcommand_help(
    tmp_path: Path,
    command: str,
) -> None:
    result = _run([command, "--help"], cwd=tmp_path, home=tmp_path / ".home")

    assert result.returncode == 0
    assert f"graphify {command} --manifest FILE --output-dir DIR" in result.stdout
    assert "--manifest FILE" in result.stdout
    assert "--output-dir DIR" in result.stdout
    assert "domain-files.json" in result.stdout
    assert "GRAPHIFY_OUT cache" in result.stdout
    assert "no --cache-root option" in result.stdout


def test_native_update_writes_default_graphify_out_layout_and_manifest(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    source = _write_code_repo(repo)

    result = _run(["update", ".", "--no-cluster"], cwd=repo, home=tmp_path / ".home")

    assert result.returncode == 0, result.stderr
    assert (repo / "graphify-out" / "graph.json").is_file()
    assert (repo / "graphify-out" / "manifest.json").is_file()
    assert not (repo / "graphify-out" / "graphify-out" / "graph.json").exists()
    _assert_native_manifest_shape(
        _read_json(repo / "graphify-out" / "manifest.json"),
        source,
    )


def test_native_update_honors_graphify_out_for_graph_and_manifest(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    source = _write_code_repo(repo)

    result = _run(
        ["update", ".", "--no-cluster"],
        cwd=repo,
        env=_env(home=tmp_path / ".home", GRAPHIFY_OUT="custom-graphify-out"),
    )

    assert result.returncode == 0, result.stderr
    custom_out = repo / "custom-graphify-out"
    assert (custom_out / "graph.json").is_file()
    assert (custom_out / "manifest.json").is_file()
    assert not (repo / "graphify-out").exists()
    _assert_native_manifest_shape(_read_json(custom_out / "manifest.json"), source)


def test_native_extract_writes_out_directory_nested_graphify_out_and_manifest(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    source = _write_code_repo(repo)
    out_root = tmp_path / "native-out"

    result = _run(
        ["extract", str(repo), "--backend", "ollama", "--out", str(out_root)],
        cwd=tmp_path,
        env=_env(
            home=tmp_path / ".home",
            OLLAMA_BASE_URL="http://127.0.0.1:11434",
        ),
    )

    assert result.returncode == 0, result.stderr
    native_out = out_root / "graphify-out"
    assert (native_out / "graph.json").is_file()
    assert (native_out / "manifest.json").is_file()
    assert not (out_root / "graph.json").exists()
    _assert_native_manifest_shape(_read_json(native_out / "manifest.json"), source)


def test_output_dir_is_rejected_outside_manifest_mode(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write_code_repo(repo)

    extract = _run(
        ["extract", str(repo), "--output-dir", "domain-out"],
        cwd=tmp_path,
        home=tmp_path / ".home",
    )
    update = _run(
        ["update", ".", "--output-dir", "domain-out"],
        cwd=repo,
        home=tmp_path / ".home",
    )

    assert extract.returncode != 0
    assert "--output-dir" in extract.stderr
    assert "--manifest" in extract.stderr
    assert update.returncode != 0
    assert "--output-dir" in update.stderr
    assert not (tmp_path / "domain-out").exists()
    assert not (repo / "domain-out").exists()


def test_native_manifest_state_is_not_domain_manifest_for_normal_update(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    source = _write_code_repo(repo)
    native_out = repo / "graphify-out"
    native_out.mkdir(parents=True)
    (native_out / "manifest.json").write_text(
        json.dumps({str(source): {"mtime": 1, "ast_hash": "stale"}}),
        encoding="utf-8",
    )

    result = _run(["update", ".", "--no-cluster"], cwd=repo, home=tmp_path / ".home")

    assert result.returncode == 0, result.stderr
    assert "domain-files.json" not in result.stderr
    assert (native_out / "graph.json").is_file()
    _assert_native_manifest_shape(_read_json(native_out / "manifest.json"), source)
