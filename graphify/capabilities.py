# graphify/capabilities.py
from __future__ import annotations

import json


def _has_file_locking() -> bool:
    try:
        import fcntl  # noqa: F401
        return True
    except ImportError:
        return False


def capabilities_payload() -> dict:
    parallel_safe = _has_file_locking()
    return {
        "schema_version": 1,
        "tabular_sidecar": {
            "schema_version": 1,
            "write_modes": ["shared-serial", "staging-merge"],
            "default_write_mode": "staging-merge",
            "supports_domain_staging_merge": True,
            "supports_build_trace": True,
            "parallel_safe": parallel_safe,
        },
    }


def capabilities_json() -> str:
    return json.dumps(capabilities_payload(), indent=2, sort_keys=True) + "\n"
