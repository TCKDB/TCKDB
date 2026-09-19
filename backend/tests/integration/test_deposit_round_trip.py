"""Out-of-process round trip of a publication deposit.

The archive suite tests ``restore_archive`` in-process only, against a
"fresh target" it manufactures by deleting rows. These tests are the first
to restore through the real CLI (``backend/scripts/tckdb_archive.py
restore``) as a **subprocess** into a database that was made by nothing but
``alembic upgrade head`` -- which is how the first test here found that no
such restore had ever succeeded (see ``MIGRATION_WRITTEN_TABLES`` in
``app/services/archive/registry.py``).

The second test is the deposit round trip the B3 work package promises: a
small corpus is published and deposited from the test session, the deposit's
archive is restored by subprocess into a scratch database, the
manuscript-number generators are run against the restored database by the
real runner script, and their output must be byte-identical to the
``expected_outputs/`` the deposit shipped.

The author allowlist here is read from the database rather than written
down, because the shared test database legitimately holds committed
accounts other test files created. That makes the allowlist refusals
unfalsifiable *in this file*; they are exercised with real strangers in
``tests/services/deposit/test_build.py``.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.models.app_user import AppUser
from app.db.models.common import CalculationType, SubmissionKind, SubmissionRecordType
from app.db.models.dataset_release import DatasetRelease
from app.db.models.submission import Submission, SubmissionRecordLink
from app.services.archive import write_archive
from app.services.deposit import verify_deposit, write_deposit
from app.services.release.curation import add_selection, publish_release
from app.services.release.manifest import freeze_manifest, load_manifest, verify_release
from scripts.paper.registry import GENERATORS
from tests import conftest  # the root harness, not this directory's conftest
from tests.conftest import scratch_database_name
from tests.services.scientific_read._factories import (
    attach_freq_result,
    attach_hessian,
    attach_thermo_source_calculation,
    make_calculation,
    make_chem_reaction,
    make_geometry,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_thermo_scalar,
    make_transition_state,
    make_transition_state_entry,
    make_workflow_tool_release,
)

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_ROOT.parent
ARCHIVE_CLI = BACKEND_ROOT / "scripts" / "tckdb_archive.py"
GENERATOR_CLI = BACKEND_ROOT / "scripts" / "paper" / "generate_expected_outputs.py"
DEPOSIT_CLI = BACKEND_ROOT / "scripts" / "ops" / "build_publication_deposit.py"
GENERATOR_DIR = BACKEND_ROOT / "scripts" / "paper"


def _subprocess_env(db_name: str) -> dict[str, str]:
    """Environment for a CLI run against ``db_name`` **from this checkout**.

    ``app`` and ``tckdb_schemas`` are installed editable from whichever
    checkout ran ``pip install -e``; inside a worktree that is the other
    checkout. Putting this checkout first on ``PYTHONPATH`` is what makes the
    subprocess exercise the code under review.
    """
    env = conftest._db_env(db_name)
    schemas = REPO_ROOT / "schemas" / "python" / "tckdb-schemas"
    env["PYTHONPATH"] = os.pathsep.join(
        [str(BACKEND_ROOT), str(schemas)] + [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p]
    )
    return env


def _run(argv: list[str], db_name: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *argv],
        cwd=BACKEND_ROOT,
        env=_subprocess_env(db_name),
        capture_output=True,
        text=True,
    )


@pytest.fixture
def scratch_db():
    """A database made by nothing but ``alembic upgrade head``; dropped afterwards."""
    db_name = scratch_database_name("deposit_round_trip")
    conftest._recreate_test_database(db_name)
    subprocess.run(
        ["conda", "run", "-n", "tckdb_env", "alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        env=conftest._db_env(db_name),
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        yield db_name
    finally:
        conftest._drop_test_database(db_name)


def _publish(session, release, curator, thermo, species_entry):
    add_selection(
        session,
        release=release,
        record_type=SubmissionRecordType.thermo,
        record_id=thermo.id,
        subject_type=SubmissionRecordType.species_entry,
        subject_id=species_entry.id,
        rationale="Lower-energy composite single point; frequencies all real.",
        selected_by=curator.id,
    )
    publish_release(session, release)
    return freeze_manifest(session, release, created_by=curator.id)


def _seed_evidence(session, curator):
    """Give every generator something non-trivial to count.

    The lineage subject is a second species entry with two *unreviewed*
    candidates: the release fixture's candidates are already approved, and
    the accepted-science guard refuses new child rows on an approved record.

    No ``calculation_artifact`` rows: the archive would then need the object
    store on both sides of the subprocess boundary. Digest lineage is covered
    in-process by the generator unit tests.
    """
    lineage_species = make_species(session, smiles="CCC")
    lineage_entry = make_species_entry(session, species=lineage_species)
    candidates = [
        make_thermo_scalar(session, species_entry=lineage_entry, h298_kj_mol=-104.7, s298_j_mol_k=270.2),
        make_thermo_scalar(session, species_entry=lineage_entry, h298_kj_mol=-104.7, s298_j_mol_k=270.2),
    ]
    tool = make_workflow_tool_release(session, name="arc", version="1.1.0", git_commit="a" * 40)
    calculation = make_calculation(
        session,
        type=CalculationType.sp,
        species_entry_id=lineage_entry.id,
        workflow_tool_release_id=tool.id,
    )
    for thermo in candidates:
        attach_thermo_source_calculation(session, thermo=thermo, calculation=calculation)
        submission = Submission(
            created_by=curator.id,
            submission_kind=SubmissionKind.computed_species,
            title=f"deposit fixture {thermo.public_ref}",
        )
        session.add(submission)
        session.flush()
        session.add(
            SubmissionRecordLink(
                submission_id=submission.id,
                record_type=SubmissionRecordType.thermo,
                record_id=thermo.id,
            )
        )
    session.flush()

    reactant = make_species(session, smiles="C=C")
    product = make_species(session, smiles="CC")
    reaction = make_chem_reaction(session, reactants=[reactant], products=[product])
    reaction_entry = make_reaction_entry(
        session,
        reaction=reaction,
        reactant_entries=[make_species_entry(session, species=reactant)],
        product_entries=[make_species_entry(session, species=product)],
    )
    transition_state = make_transition_state(session, reaction_entry=reaction_entry)
    with_hessian = make_transition_state_entry(session, transition_state=transition_state)
    without = make_transition_state_entry(session, transition_state=transition_state, multiplicity=1)

    freq = make_calculation(session, type=CalculationType.freq, transition_state_entry_id=with_hessian.id)
    attach_freq_result(session, calculation=freq, frequencies_cm1=[-1234.5, 800.0, 1500.0])
    geometry = make_geometry(session, natoms=2, xyz_text="2\nts\nC 0 0 0\nC 0 0 1.4\n")
    attach_hessian(session, calculation=freq, geometry=geometry, natoms=2, lower_triangle=[0.1] * 21)

    two_imaginary = make_calculation(session, type=CalculationType.freq, transition_state_entry_id=without.id)
    attach_freq_result(session, calculation=two_imaginary, frequencies_cm1=[-900.0, -50.0, 1200.0])
    session.flush()
    return lineage_entry


def _release_bytes(manifest) -> dict[str, bytes]:
    return {row.path: bytes(row.content) for row in manifest.artifacts}


# ---------------------------------------------------------------------------
# 1. The reproduction: CLI restore into a fresh database
# ---------------------------------------------------------------------------


def test_cli_restore_into_a_fresh_database_reproduces_the_release_bytes(
    db_session, tmp_path, scratch_db, draft_release, curator, thermo_candidates, species_entry
):
    manifest = _publish(db_session, draft_release, curator, thermo_candidates[0], species_entry)
    source_bytes = _release_bytes(manifest)
    assert source_bytes, "the release must ship artifacts for the comparison to mean anything"

    archive_path = tmp_path / "corpus.archive.tar"
    write_archive(db_session, archive_path)

    result = _run([str(ARCHIVE_CLI), "restore", str(archive_path)], scratch_db)
    assert result.returncode == 0, f"restore failed:\n{result.stdout}\n{result.stderr}"

    engine = create_engine(conftest._database_url(scratch_db), future=True)
    try:
        with Session(engine) as restored:
            release = restored.scalars(select(DatasetRelease).where(DatasetRelease.tag == draft_release.tag)).one()
            restored_manifest = load_manifest(restored, release)
            assert restored_manifest is not None
            report = verify_release(restored, release)
            assert report.ok, report.problems
            assert restored_manifest.content_sha256 == manifest.content_sha256
            restored_bytes = _release_bytes(restored_manifest)
            assert restored_bytes == source_bytes
            recorded = {row.path: row.sha256 for row in manifest.artifacts}
            for path, content in restored_bytes.items():
                assert hashlib.sha256(content).hexdigest() == recorded[path]
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# 2. The deposit round trip
# ---------------------------------------------------------------------------


def test_deposit_round_trip_reproduces_expected_outputs_byte_for_byte(
    db_session, tmp_path, scratch_db, draft_release, curator, thermo_candidates, species_entry
):
    lineage_entry = _seed_evidence(db_session, curator)
    _publish(db_session, draft_release, curator, thermo_candidates[0], species_entry)
    allowlist = sorted(db_session.scalars(select(AppUser.username)))

    deposit_dir = tmp_path / "deposit"
    result = write_deposit(
        db_session,
        release_tag=draft_release.tag,
        output_dir=deposit_dir,
        author_accounts=allowlist,
        repo_root=REPO_ROOT,
        generators=GENERATORS,
        generator_dir=GENERATOR_DIR,
        require_clean_tree=False,
        require_exact_tag=False,
    )
    roles = {member.role for member in result.members}
    assert roles == {
        "release_manifest",
        "release_artifact",
        "evidence_archive",
        "source_pin",
        "result_generator",
        "expected_output",
        "protocol",
        "privacy",
    }
    expected_files = sorted(p.name for p in (deposit_dir / "expected_outputs").iterdir())
    assert expected_files == sorted(f"{name}{suffix}" for name in GENERATORS for suffix in (".json", ".md"))
    accounts = (deposit_dir / "ACCOUNTS.md").read_text()
    assert "@" not in accounts, "ACCOUNTS.md must not carry an email address"

    offline = verify_deposit(deposit_dir)
    assert offline.ok, offline.problems
    assert offline.members_checked == len(result.members)

    archive_member = next(m for m in result.members if m.role == "evidence_archive")
    restore = _run([str(ARCHIVE_CLI), "restore", str(deposit_dir / archive_member.path)], scratch_db)
    assert restore.returncode == 0, f"restore failed:\n{restore.stdout}\n{restore.stderr}"

    regenerated = tmp_path / "regenerated"
    generate = _run([str(GENERATOR_CLI), "--output-dir", str(regenerated)], scratch_db)
    assert generate.returncode == 0, f"generators failed:\n{generate.stdout}\n{generate.stderr}"
    regenerated_files = sorted(p.name for p in regenerated.iterdir())
    assert regenerated_files == expected_files
    for name in expected_files:
        assert (regenerated / name).read_bytes() == (deposit_dir / "expected_outputs" / name).read_bytes(), name

    # The counts must reflect the seeded evidence, not pass on an empty corpus.
    evidence = json.loads((regenerated / "transition_state_evidence.json").read_text())
    assert evidence["transition_state_entries"] >= 2
    assert evidence["with_exactly_one_imaginary_mode"] >= 1
    assert evidence["with_stored_hessian"] >= 1
    lineage = json.loads((regenerated / "candidate_lineage.json").read_text())
    assert lineage["species_entries_with_multiple_candidates"] >= 1
    entry = next(row for row in lineage["lineage"] if row["species_entry_ref"] == lineage_entry.public_ref)
    assert entry["candidate_count"] == 2
    assert entry["distinct_submissions"] == 2
    assert entry["distinct_workflow_tool_release_commits"] == 1
    selected = json.loads((regenerated / "selected_thermo_by_species.json").read_text())
    assert any(row["thermo_ref"] == thermo_candidates[0].public_ref for row in selected["selected_thermo"])

    engine = create_engine(conftest._database_url(scratch_db), future=True)
    try:
        with Session(engine) as restored:
            report = verify_deposit(deposit_dir, session=restored)
            assert report.ok, report.problems
            assert report.database_checked
    finally:
        engine.dispose()

    cli = _run([str(DEPOSIT_CLI), "verify", str(deposit_dir), "--db"], scratch_db)
    assert cli.returncode == 0, f"verify --db failed:\n{cli.stdout}\n{cli.stderr}"
