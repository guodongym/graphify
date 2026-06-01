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
    has_locking = _has_file_locking()
    if has_locking:
        write_modes = ["shared-serial", "staging-merge"]
        default_mode = "staging-merge"
    else:
        write_modes = ["shared-serial"]
        default_mode = "shared-serial"
    return {
        "schema_version": 1,
        "tabular_sidecar": {
            "schema_version": 1,
            "write_modes": write_modes,
            "default_write_mode": default_mode,
            "supports_domain_staging_merge": has_locking,
            "supports_build_trace": True,
            "parallel_safe": has_locking,
        },
    }


def capabilities_json() -> str:
    return json.dumps(capabilities_payload(), indent=2, sort_keys=True) + "\n"
