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
    assert payload["tabular_sidecar"]["schema_version"] == 1
    assert payload["tabular_sidecar"]["write_modes"] == ["shared-serial", "staging-merge"]
    assert payload["tabular_sidecar"]["default_write_mode"] == "staging-merge"
    assert payload["tabular_sidecar"]["supports_domain_staging_merge"] is True
    assert payload["tabular_sidecar"]["supports_build_trace"] is True


def test_capabilities_rejects_non_json_output_for_now(tmp_path):
    result = run_graphify("capabilities", cwd=tmp_path)

    assert result.returncode == 2
    assert "Usage: graphify capabilities --json" in result.stderr
