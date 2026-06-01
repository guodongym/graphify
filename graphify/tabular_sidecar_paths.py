from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re


@dataclass(frozen=True)
class SidecarPathPlan:
    canonical_db: Path
    staging_db: Path
    run_id: str
    attempt_id: str


def safe_domain_slug(domain_id: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", domain_id).strip(".-_")
    return slug or "domain"


def _domain_hash(domain_id: str) -> str:
    return sha256(domain_id.encode("utf-8")).hexdigest()[:12]


def plan_sidecar_paths(
    *,
    repo_root: Path,
    active_graphify_output: Path,
    sidecar_db_path: Path | None,
    domain_id: str,
    run_id: str,
    attempt_id: str,
) -> SidecarPathPlan:
    del repo_root  # reserved for future use; kept in signature for API stability
    canonical_db = (
        sidecar_db_path.resolve()
        if sidecar_db_path is not None
        else (active_graphify_output / "sidecar" / "tabular.sqlite").resolve()
    )
    name = f"{safe_domain_slug(domain_id)}-{_domain_hash(domain_id)}-{attempt_id}.sqlite"
    staging_db = canonical_db.parent / "staging" / run_id / name
    return SidecarPathPlan(
        canonical_db=canonical_db,
        staging_db=staging_db,
        run_id=run_id,
        attempt_id=attempt_id,
    )
