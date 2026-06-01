from __future__ import annotations

from pathlib import Path

from graphify.tabular_sidecar_paths import plan_sidecar_paths, safe_domain_slug


def test_default_canonical_path_uses_active_graphify_output(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    active_output = repo / "graphify-out"

    paths = plan_sidecar_paths(
        repo_root=repo,
        active_graphify_output=active_output,
        sidecar_db_path=None,
        domain_id="skill-core",
        run_id="run-1",
        attempt_id="attempt-1",
    )

    assert paths.canonical_db == active_output / "sidecar" / "tabular.sqlite"
    assert paths.staging_db.parent == active_output / "sidecar" / "staging" / "run-1"
    assert paths.staging_db.name.startswith("skill-core-")
    assert paths.staging_db.name.endswith("-attempt-1.sqlite")


def test_sidecar_db_override_places_staging_next_to_override(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    canonical = tmp_path / "custom" / "tabular.sqlite"

    paths = plan_sidecar_paths(
        repo_root=repo,
        active_graphify_output=repo / "graphify-out",
        sidecar_db_path=canonical,
        domain_id="skill/core",
        run_id="run-2",
        attempt_id="attempt-2",
    )

    assert paths.canonical_db == canonical
    assert paths.staging_db.parent == canonical.parent / "staging" / "run-2"
    assert paths.staging_db.name.startswith("skill-core-")
    assert paths.staging_db.name.endswith("-attempt-2.sqlite")


def test_safe_domain_slug_is_stable_and_filename_safe():
    assert safe_domain_slug("client/settings/skill core") == "client-settings-skill-core"
    assert safe_domain_slug("...") == "domain"
