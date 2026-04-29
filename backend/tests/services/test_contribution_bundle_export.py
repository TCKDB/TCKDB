"""Tests for the local contribution bundle export service.

Verifies that selected ``Thermo`` and ``Kinetics`` rows can be turned into
valid :class:`ContributionBundleV0` payloads and that missing data fails
clearly. See ``docs/roadmaps/local-bundle-export-v0-spec.md``.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest
from sqlalchemy.orm import Session

from app.schemas.workflows.contribution_bundle import (
    BUNDLE_FORMAT,
    BUNDLE_VERSION,
    BundleKind,
    BundleSourceInstanceKind,
    ContributionBundleV0,
)
from app.schemas.workflows.kinetics_upload import KineticsUploadRequest
from app.schemas.workflows.thermo_upload import ThermoUploadRequest
from app.services.contribution_bundle_export import (
    ContributionBundleExportError,
    export_kinetics_bundle,
    export_thermo_bundle,
)
from app.workflows.kinetics import persist_kinetics_upload
from app.workflows.thermo import persist_thermo_upload


REPO_ROOT = Path(__file__).resolve().parents[2]
CLI_SCRIPT = REPO_ROOT / "scripts" / "export_contribution_bundle.py"


@contextmanager
def _isolated_session(db_engine) -> Iterator[Session]:
    """Open a connection-bound session that always rolls back on exit.

    Tests in this module call persist_thermo_upload / persist_kinetics_upload
    which create real species rows. Without an outer rollback those rows
    commit to the shared session-scoped test DB and pollute later tests
    (e.g. species_entry_review tests that recreate the hydrogen species).
    """
    connection = db_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _seed_thermo(session: Session, *, smiles: str, note: str) -> int:
    """Persist one thermo row via the real upload workflow and return its id."""
    request = ThermoUploadRequest(
        species_entry={"smiles": smiles, "charge": 0, "multiplicity": 1},
        scientific_origin="computed",
        h298_kj_mol=-241.8,
        s298_j_mol_k=188.8,
        h298_uncertainty_kj_mol=0.5,
        s298_uncertainty_j_mol_k=0.2,
        tmin_k=200.0,
        tmax_k=3000.0,
        note=note,
    )
    thermo = persist_thermo_upload(session, request)
    session.flush()
    return thermo.id


def _seed_kinetics(session: Session, *, note: str) -> int:
    request = KineticsUploadRequest(
        reaction={
            "reversible": False,
            "reactants": [
                {
                    "species_entry": {
                        "smiles": "[H]", "charge": 0, "multiplicity": 2,
                    }
                },
                {
                    "species_entry": {
                        "smiles": "[H]", "charge": 0, "multiplicity": 2,
                    }
                },
            ],
            "products": [
                {
                    "species_entry": {
                        "smiles": "[H][H]", "charge": 0, "multiplicity": 1,
                    }
                }
            ],
        },
        scientific_origin="computed",
        model_kind="modified_arrhenius",
        software_release={"name": "gaussian", "version": "09", "revision": "D.01"},
        workflow_tool_release={"name": "ARC", "version": "1.0.0"},
        a=1.23e12,
        a_units="cm3_mol_s",
        n=0.5,
        reported_ea=12.3,
        reported_ea_units="kj_mol",
        tmin_k=300.0,
        tmax_k=2000.0,
        degeneracy=2.0,
        tunneling_model="eckart",
        note=note,
    )
    kinetics = persist_kinetics_upload(session, request)
    session.flush()
    return kinetics.id


# ---------------------------------------------------------------------------
# Test 1 — thermo export validates as ContributionBundleV0
# ---------------------------------------------------------------------------


def test_export_thermo_bundle_validates_and_carries_thermo_only(db_engine) -> None:
    with _isolated_session(db_engine) as session:
        thermo_id = _seed_thermo(session, smiles="O", note="export-thermo-1")

        bundle = export_thermo_bundle(
            session,
            thermo_ids=[thermo_id],
            title="Thermo bundle test",
            summary="Single thermo record exported for tests.",
            exporter_label="tester",
        )

    # Re-validate by serializing through the schema; round-trip must hold.
    payload = bundle.model_dump(mode="json")
    revalidated = ContributionBundleV0.model_validate(payload)

    assert revalidated.bundle_format == BUNDLE_FORMAT
    assert revalidated.bundle_version == BUNDLE_VERSION
    assert revalidated.bundle_kind is BundleKind.thermo
    assert len(revalidated.records.thermo_uploads) == 1
    assert revalidated.records.kinetics_uploads == []

    upload = revalidated.records.thermo_uploads[0]
    assert upload.species_entry.smiles == "O"
    assert upload.note == "export-thermo-1"
    assert upload.h298_kj_mol == pytest.approx(-241.8)

    # Local refs cover the root + dependency closure for thermo.
    assert any(k.startswith("thermo:") for k in revalidated.local_refs)
    assert any(k.startswith("species_entry:") for k in revalidated.local_refs)
    assert any(k.startswith("species:") for k in revalidated.local_refs)

    # Source instance metadata is populated.
    assert revalidated.source_instance.instance_kind is BundleSourceInstanceKind.local
    assert revalidated.source_instance.schema_version
    assert revalidated.exporter.local_user_label == "tester"
    assert revalidated.submission.title == "Thermo bundle test"


def test_export_thermo_bundle_carries_provenance_when_present(db_engine) -> None:
    """Provenance refs reconstructed when the source row carries them."""
    with _isolated_session(db_engine) as session:
        request = ThermoUploadRequest(
            species_entry={"smiles": "CO", "charge": 0, "multiplicity": 1},
            scientific_origin="computed",
            h298_kj_mol=-200.7,
            s298_j_mol_k=239.7,
            software_release={"name": "gaussian", "version": "16"},
            workflow_tool_release={"name": "ARC", "version": "1.1.0"},
            note="provenance-test",
        )
        thermo = persist_thermo_upload(session, request)
        session.flush()

        bundle = export_thermo_bundle(
            session,
            thermo_ids=[thermo.id],
            title="Thermo provenance",
            summary="Carry software/workflow provenance through export.",
            exporter_label="tester",
        )

    upload = bundle.records.thermo_uploads[0]
    assert upload.software_release is not None
    assert upload.software_release.name == "Gaussian"
    assert upload.software_release.version == "16"
    assert upload.workflow_tool_release is not None
    assert upload.workflow_tool_release.name == "ARC"


# ---------------------------------------------------------------------------
# Test 2 — kinetics export validates and reaction identity round-trips
# ---------------------------------------------------------------------------


def test_export_kinetics_bundle_validates_and_carries_kinetics_only(
    db_engine,
) -> None:
    with _isolated_session(db_engine) as session:
        kinetics_id = _seed_kinetics(session, note="export-kinetics-1")

        bundle = export_kinetics_bundle(
            session,
            kinetics_ids=[kinetics_id],
            title="Kinetics bundle test",
            summary="Single kinetics record exported for tests.",
            exporter_label="tester",
        )

    payload = bundle.model_dump(mode="json")
    revalidated = ContributionBundleV0.model_validate(payload)

    assert revalidated.bundle_kind is BundleKind.kinetics
    assert revalidated.records.thermo_uploads == []
    assert len(revalidated.records.kinetics_uploads) == 1

    upload = revalidated.records.kinetics_uploads[0]
    # Reaction identity round-trips as ordered structured participants.
    reactant_smiles = [r.species_entry.smiles for r in upload.reaction.reactants]
    product_smiles = [p.species_entry.smiles for p in upload.reaction.products]
    assert reactant_smiles == ["[H]", "[H]"]
    assert product_smiles == ["[H][H]"]
    assert upload.reaction.reversible is False

    # Arrhenius round-trips through canonical kj_mol storage.
    assert upload.a == pytest.approx(1.23e12)
    assert upload.a_units.value == "cm3_mol_s"
    assert upload.reported_ea == pytest.approx(12.3)
    assert upload.reported_ea_units.value == "kj_mol"

    # Provenance refs survive.
    assert upload.software_release is not None
    assert upload.software_release.name == "Gaussian"
    assert upload.workflow_tool_release is not None
    assert upload.workflow_tool_release.name == "ARC"

    # Local refs include the root + reaction + species closure.
    assert any(k.startswith("kinetics:") for k in revalidated.local_refs)
    assert any(k.startswith("reaction:") for k in revalidated.local_refs)
    assert any(k.startswith("species_entry:") for k in revalidated.local_refs)


# ---------------------------------------------------------------------------
# Test 3 — missing root id fails clearly, no bundle written
# ---------------------------------------------------------------------------


def test_export_thermo_bundle_missing_id_fails_clearly(db_engine) -> None:
    with _isolated_session(db_engine) as session:
        with pytest.raises(
            ContributionBundleExportError,
            match=r"thermo_id=999999999.*no such thermo row",
        ):
            export_thermo_bundle(
                session,
                thermo_ids=[999_999_999],
                title="missing",
                summary="missing",
                exporter_label="tester",
            )


def test_export_kinetics_bundle_missing_id_fails_clearly(db_engine) -> None:
    with _isolated_session(db_engine) as session:
        with pytest.raises(
            ContributionBundleExportError,
            match=r"kinetics_id=999999999.*no such kinetics row",
        ):
            export_kinetics_bundle(
                session,
                kinetics_ids=[999_999_999],
                title="missing",
                summary="missing",
                exporter_label="tester",
            )


def test_export_thermo_bundle_rejects_empty_id_list(db_engine) -> None:
    with _isolated_session(db_engine) as session:
        with pytest.raises(
            ContributionBundleExportError,
            match="At least one thermo_id is required",
        ):
            export_thermo_bundle(
                session,
                thermo_ids=[],
                title="empty",
                summary="empty",
                exporter_label="tester",
            )


# ---------------------------------------------------------------------------
# Test 4 — incomplete dependency closure fails clearly
# ---------------------------------------------------------------------------


def test_export_kinetics_bundle_fails_when_structure_participants_missing(
    db_engine,
) -> None:
    """Simulate a kinetics row whose reaction entry has no structure
    participants. Real workflow uploads always create participants, so we
    fabricate the broken state via raw ORM manipulation in a rolled-back
    transaction.
    """
    from app.db.models.reaction import ReactionEntryStructureParticipant

    with _isolated_session(db_engine) as session:
        kinetics_id = _seed_kinetics(session, note="missing-deps")
        # Refresh and surgically remove all structure participants for this
        # reaction entry, simulating an incomplete dependency closure.
        from app.db.models.kinetics import Kinetics
        kinetics = session.get(Kinetics, kinetics_id)
        assert kinetics is not None
        entry_id = kinetics.reaction_entry_id

        session.query(ReactionEntryStructureParticipant).filter(
            ReactionEntryStructureParticipant.reaction_entry_id == entry_id
        ).delete()
        session.flush()
        session.expire_all()

        with pytest.raises(
            ContributionBundleExportError,
            match="no structure participants",
        ):
            export_kinetics_bundle(
                session,
                kinetics_ids=[kinetics_id],
                title="broken",
                summary="broken",
                exporter_label="tester",
            )


# ---------------------------------------------------------------------------
# Test 5 — CLI writes a valid JSON bundle
# ---------------------------------------------------------------------------


@pytest.fixture
def _seeded_thermo_for_cli(db_engine) -> int:
    """Commit one thermo row so the CLI subprocess can see it.

    The CLI opens its own DB session against the dev/test database, so the
    fixture must commit (not roll back) and clean up after the test.
    """
    from app.db.models.species import Species, SpeciesEntry
    from app.db.models.thermo import Thermo

    with Session(db_engine) as session:
        with session.begin():
            thermo_id = _seed_thermo(
                session, smiles="C#N", note="cli-test-thermo"
            )

    yield thermo_id

    # Cleanup must actually commit because the CLI's subprocess holds a
    # separate connection that has already committed; an isolated/rolled-
    # back session here would leave the seed row in the test DB and
    # pollute later tests.
    with Session(db_engine) as session, session.begin():
        thermo = session.get(Thermo, thermo_id)
        if thermo is None:
            return
        species_entry_id = thermo.species_entry_id
        session.delete(thermo)
        session.flush()
        species_entry = session.get(SpeciesEntry, species_entry_id)
        if species_entry is not None:
            species_id = species_entry.species_id
            session.delete(species_entry)
            session.flush()
            species = session.get(Species, species_id)
            if species is not None and not species.entries:
                session.delete(species)


def test_cli_writes_valid_bundle_json(tmp_path, _seeded_thermo_for_cli) -> None:
    output = tmp_path / "thermo-bundle.tckdb.json"

    cmd = [
        sys.executable,
        str(CLI_SCRIPT),
        "--kind", "thermo",
        "--thermo-id", str(_seeded_thermo_for_cli),
        "--output", str(output),
        "--title", "CLI test bundle",
        "--summary", "Bundle written by CLI smoke test.",
        "--exporter-label", "cli-tester",
    ]

    # The CLI reads DB settings from the same env vars conftest pins for
    # the test database.
    import os
    env = os.environ.copy()
    env.setdefault("DB_USER", "tckdb")
    env.setdefault("DB_PASSWORD", "tckdb")
    env.setdefault("DB_HOST", "127.0.0.1")
    env.setdefault("DB_PORT", "5432")
    env["DB_NAME"] = os.environ.get("DB_TEST_NAME", "tckdb_test")

    result = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"CLI failed: stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert output.exists()

    parsed = json.loads(output.read_text(encoding="utf-8"))
    bundle = ContributionBundleV0.model_validate(parsed)
    assert bundle.bundle_kind is BundleKind.thermo
    assert len(bundle.records.thermo_uploads) == 1
    assert "cli-test-thermo" in result.stdout or output.name in result.stdout


# ---------------------------------------------------------------------------
# Test 6 — no raw secrets in output
# ---------------------------------------------------------------------------


# Field-name patterns that would indicate raw credential leakage. We look
# for the JSON key shape ("foo":) so the literal word "secret" or "key"
# in user-supplied free text (titles, summaries, notes) does not produce
# false positives.
_SECRET_FIELD_PATTERNS = [
    re.compile(r'"api[_-]?key"\s*:', re.IGNORECASE),
    re.compile(r'"password"\s*:', re.IGNORECASE),
    re.compile(r'"key_hash"\s*:', re.IGNORECASE),
    re.compile(r'"session[_-]?token"\s*:', re.IGNORECASE),
    re.compile(r'"access[_-]?token"\s*:', re.IGNORECASE),
    re.compile(r'"refresh[_-]?token"\s*:', re.IGNORECASE),
    re.compile(r'"client[_-]?secret"\s*:', re.IGNORECASE),
]


def test_exported_bundle_has_no_raw_secrets(db_engine) -> None:
    with _isolated_session(db_engine) as session:
        thermo_id = _seed_thermo(session, smiles="N#N", note="leak-check")
        kinetics_id = _seed_kinetics(session, note="leak-check-kin")

        thermo_bundle = export_thermo_bundle(
            session,
            thermo_ids=[thermo_id],
            title="leak check",
            summary="ensure exporter does not emit credential field names",
            exporter_label="tester",
        )
        kinetics_bundle = export_kinetics_bundle(
            session,
            kinetics_ids=[kinetics_id],
            title="leak check",
            summary="ensure exporter does not emit credential field names",
            exporter_label="tester",
        )

    for bundle in (thermo_bundle, kinetics_bundle):
        serialized = json.dumps(bundle.model_dump(mode="json"))
        for pattern in _SECRET_FIELD_PATTERNS:
            assert not pattern.search(serialized), (
                f"unexpected credential-shaped field {pattern.pattern!r} "
                "in exported bundle"
            )
