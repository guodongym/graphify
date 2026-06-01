from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path


STAGE_KEYS = (
    "code_extract_ms",
    "tabular_parse_ms",
    "sidecar_stage_write_ms",
    "sidecar_merge_wait_ms",
    "sidecar_merge_write_ms",
    "sidecar_projection_ms",
    "graph_build_ms",
    "cluster_ms",
    "report_export_ms",
)


@dataclass
class BuildTrace:
    domain_id: str | None
    sidecar_mode: str
    process_workers: int | None
    stages: dict[str, int] = field(default_factory=lambda: {key: 0 for key in STAGE_KEYS})
    cache: dict[str, int] = field(default_factory=lambda: {
        "ast_hits": 0,
        "ast_misses": 0,
        "tabular_content_unchanged": 0,
        "tabular_content_changed": 0,
    })
    sidecar: dict[str, int] = field(default_factory=lambda: {
        "files_staged": 0,
        "files_merged": 0,
        "files_pruned": 0,
        "rows_staged": 0,
        "rows_merged": 0,
        "rows_pruned": 0,
        "indexed_values_merged": 0,
        "refs_merged": 0,
    })

    def record_stage_ms(self, key: str, value: int) -> None:
        if key not in self.stages:
            raise KeyError(f"unknown build trace stage: {key}")
        self.stages[key] = int(value)

    def record_cache(self, **values: int) -> None:
        for key, value in values.items():
            if key not in self.cache:
                raise KeyError(f"unknown build trace cache field: {key}")
            self.cache[key] = int(value)

    def record_sidecar(self, **values: int) -> None:
        for key, value in values.items():
            if key not in self.sidecar:
                raise KeyError(f"unknown build trace sidecar field: {key}")
            self.sidecar[key] = int(value)

    def payload(self) -> dict:
        return {
            "schema_version": 1,
            "domain_id": self.domain_id,
            "sidecar_mode": self.sidecar_mode,
            "process_workers": self.process_workers,
            "stages": self.stages,
            "cache": self.cache,
            "sidecar": self.sidecar,
        }

    def write_success(self, output_dir: Path) -> Path:
        return self._write(output_dir / ".graphify_state" / "build-trace.json", self.payload())

    def write_failed(self, output_dir: Path, *, error_class: str, error_message: str) -> Path:
        payload = self.payload()
        payload["error"] = {"class": error_class, "message": error_message}
        return self._write(output_dir / ".graphify_state" / "build-trace.failed.json", payload)

    @staticmethod
    def _write(path: Path, payload: dict) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return path
