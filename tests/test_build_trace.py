from __future__ import annotations

import json

from graphify.build_trace import BuildTrace


def test_build_trace_writes_success_json(tmp_path):
    trace = BuildTrace(domain_id="skill-core", sidecar_mode="staging-merge", process_workers=4)
    trace.record_stage_ms("tabular_parse_ms", 12)
    trace.record_cache(ast_hits=2, ast_misses=1, tabular_content_unchanged=3, tabular_content_changed=4)
    trace.record_sidecar(files_staged=1, rows_staged=2, files_merged=1, rows_merged=2)

    output = trace.write_success(tmp_path)

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert output == tmp_path / ".graphify_state" / "build-trace.json"
    assert payload["domain_id"] == "skill-core"
    assert payload["sidecar_mode"] == "staging-merge"
    assert payload["process_workers"] == 4
    assert payload["stages"]["tabular_parse_ms"] == 12
    assert payload["cache"]["ast_hits"] == 2
    assert payload["sidecar"]["rows_merged"] == 2


def test_build_trace_writes_failed_json(tmp_path):
    trace = BuildTrace(domain_id="skill-core", sidecar_mode="staging-merge", process_workers=None)
    trace.record_stage_ms("sidecar_merge_wait_ms", 5)

    output = trace.write_failed(tmp_path, error_class="RuntimeError", error_message="boom")

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert output == tmp_path / ".graphify_state" / "build-trace.failed.json"
    assert payload["error"]["class"] == "RuntimeError"
    assert payload["error"]["message"] == "boom"
    assert payload["stages"]["sidecar_merge_wait_ms"] == 5
