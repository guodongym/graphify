# graphify/capabilities.py
from __future__ import annotations

import json


def capabilities_payload() -> dict:
    return {
        "schema_version": 1,
        "tabular_sidecar": {
            "schema_version": 1,
            "write_modes": ["shared-serial", "staging-merge"],
            "default_write_mode": "staging-merge",
            "supports_domain_staging_merge": True,
            "supports_build_trace": True,
        },
    }


def capabilities_json() -> str:
    return json.dumps(capabilities_payload(), indent=2, sort_keys=True) + "\n"
