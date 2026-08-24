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
    "filename",
    ["LICENSE", "LICENSE-DATA", "CITATION.cff", "CHANGELOG.md", "SECURITY.md"],
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


def test_the_data_license_is_cc_by_4_0_and_says_so_unambiguously():
    """`LICENSE-DATA` is a notice, not a copy — so the notice has to be exact.

    CC recommends naming the license and linking the canonical text rather
    than vendoring the legal code, which drifts. That only works if the name,
    the SPDX id and the link are all present and all agree.
    """
    text = (REPO_ROOT / "LICENSE-DATA").read_text()
    assert "Creative Commons Attribution 4.0 International" in text
    assert "CC-BY-4.0" in text, "the SPDX id a release stores must appear"
    assert "https://creativecommons.org/licenses/by/4.0/" in text
    assert "legalcode" in text, "link the legal code, not only the deed"


def test_the_data_license_states_the_boundary_with_the_code_license():
    """Two licenses is only correct if a reader can tell which covers what."""
    text = (REPO_ROOT / "LICENSE-DATA").read_text()
    assert "MIT" in text and "LICENSE`" in text
    for covered in ("Deposited scientific records", "Raw calculation artifacts"):
        assert covered in text
    assert "source code" in text.lower()


def test_the_data_license_excludes_the_third_party_test_fixtures():
    """Fixtures carried in from other projects are not deposited data.

    They are code-adjacent test inputs under their upstream licenses, and a
    data license that swept them in would be claiming rights over RMG-Py's and
    ARC's files. Each path is asserted to exist so the exclusion list fails
    loudly when a fixture is renamed rather than going quietly stale.
    """
    text = (REPO_ROOT / "LICENSE-DATA").read_text()
    third_party = [
        "backend/tests/fixtures/molpro/molpro_TS_freq.out",
        "backend/tests/fixtures/orca/Orca_TS_test.hess",
        "backend/tests/fixtures/psi4/opt_freq_singlet.out",
        "backend/tests/fixtures/psi4/opt_freq_dft_ts_singlet.out",
        "backend/tests/fixtures/psi4/io_error_truncated.out",
        "backend/tests/fixtures/psi4/sp_mrcc_triplet.dat",
    ]
    for path in third_party:
        assert (REPO_ROOT / path).is_file(), f"{path} moved; update LICENSE-DATA"
        assert path in text, f"{path} is third-party and must be excluded by name"
    assert "RMG-Py" in text and "ARC" in text


def test_the_multi_contributor_licensing_constraint_is_written_down():
    """The one thing that stops being true when a second person uploads.

    An operator can license their own deposits and nobody else's. That is moot
    while every depositor is the operator and becomes urgent the moment one is
    not — so it is recorded on both sides of the system, where the implementer
    of either side will meet it, rather than in a review transcript.
    """
    license_data = (REPO_ROOT / "LICENSE-DATA").read_text()
    assert "own deposits and nobody else's" in license_data
    assert "upload contract" in license_data

    ingestion = (
        BACKEND_ROOT / "docs" / "specs" / "ingestion_submission_model.md"
    ).read_text()
    assert "upload contract" in ingestion
    assert "second contributor" in ingestion
    assert "not consent" in ingestion

    release_spec = (
        BACKEND_ROOT / "docs" / "specs" / "dataset_release_and_profiles.md"
    ).read_text()
    assert "own deposits and nobody else's" in release_spec
    assert "upload contract" in release_spec


def test_citation_file_names_the_data_license_without_faking_a_cff_field():
    """CFF 1.2.0 has one `license` key, and it describes the software.

    Listing `[MIT, CC-BY-4.0]` would validate and would be false — it says one
    work is offered under either license. So `license:` stays MIT and the data
    license is stated in prose, which is what a reader of this file needs to
    learn either way.
    """
    text = (REPO_ROOT / "CITATION.cff").read_text()
    declared = [
        line.strip()
        for line in text.splitlines()
        if line.strip().startswith("license:") and not line.strip().startswith("#")
    ]
    assert declared == ["license: MIT"], (
        "the CFF `license` key describes the software; the data license "
        f"belongs in prose, got {declared!r}"
    )
    assert "CC BY 4.0" in text or "CC-BY-4.0" in text
    assert "LICENSE-DATA" in text


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
