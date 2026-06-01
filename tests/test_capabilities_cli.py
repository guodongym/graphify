# tests/test_capabilities_cli.py
from __future__ import annotations

import json
import subprocess
import sys


def run_graphify(*args: str, cwd):
    return subprocess.run(
        [sys.executable, "-m", "graphify", *args],
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def test_capabilities_json_reports_tabular_sidecar_modes(tmp_path):
    result = run_graphify("capabilities", "--json", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == 1
    ts = payload["tabular_sidecar"]
    assert ts["schema_version"] == 1
    assert ts["supports_build_trace"] is True
    assert isinstance(ts["parallel_safe"], bool)
    # On platforms with fcntl, staging-merge is available
    if ts["parallel_safe"]:
        assert ts["write_modes"] == ["shared-serial", "staging-merge"]
        assert ts["default_write_mode"] == "staging-merge"
        assert ts["supports_domain_staging_merge"] is True
    else:
        assert ts["write_modes"] == ["shared-serial"]
        assert ts["default_write_mode"] == "shared-serial"
        assert ts["supports_domain_staging_merge"] is False


def test_capabilities_degrades_without_file_locking(tmp_path):
    """When fcntl is unavailable, capabilities must not advertise staging-merge."""
    from unittest.mock import patch
    from graphify.capabilities import capabilities_payload

    with patch("graphify.capabilities._has_file_locking", return_value=False):
        payload = capabilities_payload()

    ts = payload["tabular_sidecar"]
    assert ts["write_modes"] == ["shared-serial"]
    assert ts["default_write_mode"] == "shared-serial"
    assert ts["supports_domain_staging_merge"] is False
    assert ts["parallel_safe"] is False


def test_capabilities_rejects_non_json_output_for_now(tmp_path):
    result = run_graphify("capabilities", cwd=tmp_path)

    assert result.returncode == 2
    assert "Usage: graphify capabilities --json" in result.stderr
