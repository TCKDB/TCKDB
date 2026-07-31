"""Repository-level release metadata must actually exist and agree with itself.

The Stage 3 review found `backend/pyproject.toml` claiming a license of
``TBD — see repository root`` while the repository root shipped an MIT
``LICENSE``, and no changelog, citation or security contact anywhere. Those are
one-line files that silently rot, so they get a test.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "filename", ["LICENSE", "CITATION.cff", "CHANGELOG.md", "SECURITY.md"]
)
def test_release_metadata_file_exists(filename):
    path = REPO_ROOT / filename
    assert path.is_file(), f"{filename} is missing from the repository root"
    assert path.read_text().strip(), f"{filename} is empty"


def test_backend_declares_the_repository_license():
    data = tomllib.loads((BACKEND_ROOT / "pyproject.toml").read_text())
    license_value = data["project"]["license"]
    assert license_value == "MIT", (
        "backend/pyproject.toml must declare the repository's actual license, "
        f"got {license_value!r}"
    )
    assert "MIT License" in (REPO_ROOT / "LICENSE").read_text()


def test_backend_does_not_mix_pep639_license_with_a_license_classifier():
    """setuptools 77+ errors when both are present; keep them from drifting back."""
    data = tomllib.loads((BACKEND_ROOT / "pyproject.toml").read_text())
    classifiers = data["project"]["classifiers"]
    assert not [c for c in classifiers if c.startswith("License ::")]


def test_every_python_package_declares_a_real_license():
    """No package may reintroduce a placeholder license."""
    manifests = [
        BACKEND_ROOT / "pyproject.toml",
        REPO_ROOT / "clients" / "python" / "pyproject.toml",
        REPO_ROOT / "schemas" / "python" / "tckdb-schemas" / "pyproject.toml",
        REPO_ROOT / "integrations" / "mcp" / "pyproject.toml",
    ]
    for manifest in manifests:
        if not manifest.is_file():
            continue
        project = tomllib.loads(manifest.read_text())["project"]
        value = project.get("license")
        text = value if isinstance(value, str) else (value or {}).get("text", "")
        assert "TBD" not in text.upper(), f"{manifest} still has a placeholder license"
        assert text, f"{manifest} declares no license"


def test_citation_file_does_not_claim_an_unminted_doi():
    """The stage deliberately ships release machinery without minting a DOI."""
    text = (REPO_ROOT / "CITATION.cff").read_text()
    assert "cff-version:" in text
    doi_lines = [
        line
        for line in text.splitlines()
        if line.strip().startswith("doi:") and not line.strip().startswith("#")
    ]
    assert doi_lines == [], "CITATION.cff must not assert a DOI that was never minted"


def test_changelog_states_the_maturity_policy_and_the_two_version_axes():
    text = (REPO_ROOT / "CHANGELOG.md").read_text()
    assert "Maturity and version policy" in text
    # Software versioning and dataset-release versioning are separate axes;
    # conflating them is what the release layer exists to prevent.
    assert "dataset release" in text.lower()
    assert "tckdb-client" in text and "tckdb-backend" in text


def test_security_file_separates_vulnerabilities_from_scientific_disputes():
    text = (REPO_ROOT / "SECURITY.md").read_text()
    assert "Reporting a vulnerability" in text
    assert "Scientific-data contact" in text


def test_release_runbook_exists_and_documents_the_doi_step():
    runbook = BACKEND_ROOT / "docs" / "deployment" / "cutting_a_dataset_release.md"
    assert runbook.is_file()
    text = runbook.read_text()
    assert "Zenodo" in text
    assert "/api/v1/releases/{release_handle}/doi" in text or "/doi" in text


def test_known_operational_boundaries_are_recorded_where_a_reader_will_look():
    """Two accepted trade-offs must not survive only in a review transcript.

    Both were reviewed and accepted rather than fixed, so the reasoning has to
    live somewhere a future maintainer encounters it before rediscovering the
    behaviour and treating it as a defect.
    """
    spec = (
        BACKEND_ROOT / "docs" / "specs" / "dataset_release_and_profiles.md"
    ).read_text()
    assert "Known operational boundaries" in spec

    # NB6: inline release bytes in the recovery archive, with a revisit trigger.
    assert "rows.ndjson" in spec
    assert "blobs/" in spec
    assert "Trigger for revisiting" in spec or "Revisit when" in spec

    # The DISABLE TRIGGER / ownership boundary, and where the fix belongs.
    assert "DISABLE TRIGGER" in spec
    assert "database_roles.md" in spec

    archive_spec = (BACKEND_ROOT / "docs" / "specs" / "tckdb_archive_v1.md").read_text()
    assert "$bytes" in archive_spec
    assert "Revisit when" in archive_spec

    roles = (
        BACKEND_ROOT / "docs" / "deployment" / "database_roles.md"
    ).read_text()
    # A reader of the roles doc must learn that the release tables share the
    # posture, not just the accepted-science ones.
    assert "release_selection" in roles
    assert "dataset_release_and_profiles.md" in roles
