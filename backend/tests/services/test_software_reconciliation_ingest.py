"""Ingest-level tests for software provenance reconciliation (DR-0008).

The pure reconciliation function is unit-tested in
``test_software_reconciliation.py``. This module verifies the *wiring*:

* the declared-only outcome is recorded at the primary calculation-ingest
  seam (``resolve_and_persist_calculation_with_results``), with no banner;
* the parser seam (``extract_and_store_calculation_parameters``) upgrades
  the status to matched / enriched / mismatch and records the observed
  banner, using a real Gaussian log fixture;
* a calculation with no declared software but a parsed banner records
  parsed_only;
* a version/revision-only mismatch is recorded, never raised, and the
  declared value keeps winning on ``software_release_id`` (non-blocking);
* a *name* mismatch confirmed by a real artifact is corrected at write
  time -- ``software_release_id`` is repointed at the observed program,
  the declared string survives on ``declared_software_banner``, and a
  warning is produced (issue #305's ARC follow-up, 2026-09).
"""

from __future__ import annotations

import os

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.models.calculation import Calculation
from app.db.models.common import CalculationType, SoftwareReconciliationStatus
from app.schemas.fragments.calculation import (
    CalculationWithResultsPayload,
    OptResultPayload,
)
from app.schemas.upload_warning import UploadWarning
from app.services.calculation_parameter_extraction import (
    extract_and_store_calculation_parameters,
)
from app.services.calculation_resolution import (
    W_SOFTWARE_IDENTITY_CORRECTED_FROM_ARTIFACT,
    resolve_and_persist_calculation_with_results,
)

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "..", "fixtures")
OPT_G09_LOG = os.path.join(FIXTURES_DIR, "gaussian", "opt_g09.log")
SP_ORCA_MINIMAL_LOG = os.path.join(FIXTURES_DIR, "orca", "sp_orca_minimal.txt")

_LOT = {"method": "B3LYP", "basis": "6-31G(d)"}

_INCHI_COUNTER = 0


def _next_inchi_key(prefix: str) -> str:
    global _INCHI_COUNTER
    _INCHI_COUNTER += 1
    stem = f"{prefix}{_INCHI_COUNTER:0>21}"
    return stem[:27]


def _create_species_entry(session: Session, *, inchi_key: str) -> int:
    species_id = session.connection().execute(
        text(
            """
            INSERT INTO species (kind, smiles, inchi_key, charge, multiplicity, stereo_kind)
            VALUES ('molecule', :smiles, :inchi_key, 0, 1, 'achiral')
            RETURNING id
            """
        ),
        {"smiles": inchi_key, "inchi_key": inchi_key},
    ).scalar_one()
    return session.connection().execute(
        text(
            """
            INSERT INTO species_entry (species_id)
            VALUES (:species_id)
            RETURNING id
            """
        ),
        {"species_id": species_id},
    ).scalar_one()


def _opt_upload(software_release: dict) -> CalculationWithResultsPayload:
    return CalculationWithResultsPayload(
        type=CalculationType.opt,
        software_release=software_release,
        level_of_theory=_LOT,
        opt_result=OptResultPayload(converged=True),
    )


def _gaussian_log_text() -> str:
    with open(OPT_G09_LOG) as fh:
        return fh.read()


def _orca_log_text() -> str:
    with open(SP_ORCA_MINIMAL_LOG) as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# 1. Primary seam records declared_only (no banner available on upload).
# ---------------------------------------------------------------------------


def test_declared_only_recorded_at_primary_seam(db_conn) -> None:
    with Session(db_conn) as session, session.begin():
        species_entry_id = _create_species_entry(
            session, inchi_key=_next_inchi_key("RECDECL")
        )
        upload = _opt_upload({"name": "gaussian", "version": "09", "revision": "D.01"})

        calc = resolve_and_persist_calculation_with_results(
            session, upload, species_entry_id=species_entry_id
        )

        assert (
            calc.software_reconciliation_status
            == SoftwareReconciliationStatus.declared_only
        )
        assert calc.observed_software_banner is None


# ---------------------------------------------------------------------------
# 2. Parser seam upgrades declared_only -> matched when the banner agrees.
# ---------------------------------------------------------------------------


def test_matched_after_parser_seam(db_conn) -> None:
    with Session(db_conn) as session, session.begin():
        species_entry_id = _create_species_entry(
            session, inchi_key=_next_inchi_key("RECMATCH")
        )
        # Declared agrees with the Gaussian 09 RevD.01 banner in the fixture.
        upload = _opt_upload({"name": "gaussian", "version": "09", "revision": "D.01"})
        calc = resolve_and_persist_calculation_with_results(
            session, upload, species_entry_id=species_entry_id
        )
        assert (
            calc.software_reconciliation_status
            == SoftwareReconciliationStatus.declared_only
        )

        extract_and_store_calculation_parameters(
            session, calc, _gaussian_log_text()
        )

        assert (
            calc.software_reconciliation_status
            == SoftwareReconciliationStatus.matched
        )
        assert calc.observed_software_banner is not None
        assert "gaussian" in calc.observed_software_banner
        assert "EM64L-G09RevD.01" in calc.observed_software_banner


# ---------------------------------------------------------------------------
# 3. Parser seam records enriched when the user gave only a partial ref.
# ---------------------------------------------------------------------------


def test_enriched_after_parser_seam(db_conn) -> None:
    with Session(db_conn) as session, session.begin():
        species_entry_id = _create_species_entry(
            session, inchi_key=_next_inchi_key("RECENR")
        )
        # Only the name is declared; the parser fills version + revision.
        upload = _opt_upload({"name": "gaussian"})
        calc = resolve_and_persist_calculation_with_results(
            session, upload, species_entry_id=species_entry_id
        )

        extract_and_store_calculation_parameters(
            session, calc, _gaussian_log_text()
        )

        assert (
            calc.software_reconciliation_status
            == SoftwareReconciliationStatus.enriched
        )
        assert calc.observed_software_banner is not None


# ---------------------------------------------------------------------------
# 4. Parser seam records mismatch when the declared version disagrees.
#    Non-blocking: extraction still succeeds and the calc is intact.
# ---------------------------------------------------------------------------


def test_mismatch_after_parser_seam_is_non_blocking(db_conn) -> None:
    with Session(db_conn) as session, session.begin():
        species_entry_id = _create_species_entry(
            session, inchi_key=_next_inchi_key("RECMIS")
        )
        # Declared Gaussian 16 vs observed Gaussian 09 -> real mismatch.
        upload = _opt_upload({"name": "gaussian", "version": "16"})
        calc = resolve_and_persist_calculation_with_results(
            session, upload, species_entry_id=species_entry_id
        )

        rows = extract_and_store_calculation_parameters(
            session, calc, _gaussian_log_text()
        )

        # Recorded, not rejected: the calculation persisted and parameters
        # were still extracted.
        assert (
            calc.software_reconciliation_status
            == SoftwareReconciliationStatus.mismatch
        )
        assert calc.software_release_id is not None  # declared value still wins
        assert calc.observed_software_banner is not None
        assert isinstance(rows, list)


# ---------------------------------------------------------------------------
# 5. Parser seam records parsed_only when there is no declared software.
# ---------------------------------------------------------------------------


def test_parsed_only_when_no_declared_software(db_conn) -> None:
    with Session(db_conn) as session, session.begin():
        species_entry_id = _create_species_entry(
            session, inchi_key=_next_inchi_key("RECPARSED")
        )
        # Importer-style calculation created without a software_release.
        calc = Calculation(
            type=CalculationType.opt,
            species_entry_id=species_entry_id,
        )
        session.add(calc)
        session.flush()
        assert calc.software_release_id is None

        extract_and_store_calculation_parameters(
            session, calc, _gaussian_log_text()
        )

        assert (
            calc.software_reconciliation_status
            == SoftwareReconciliationStatus.parsed_only
        )
        assert calc.observed_software_banner is not None


# ---------------------------------------------------------------------------
# 6. A NAME mismatch confirmed by a real artifact is corrected at write
#    time: software_release_id repoints to the observed program, the
#    declared string survives, and a warning is produced.
#
#    This is the live ARC defect (issue #305 follow-up): a per-job-type
#    version map borrows an unrelated job's declared software name, so
#    ``name='gaussian'`` sits next to a genuinely-observed ORCA artifact.
# ---------------------------------------------------------------------------


def test_name_mismatch_with_confirming_artifact_corrects_software_identity(
    db_conn,
) -> None:
    with Session(db_conn) as session, session.begin():
        species_entry_id = _create_species_entry(
            session, inchi_key=_next_inchi_key("RECCORR")
        )
        # Declared "gaussian" -- the ARC-side bug this guards against --
        # but the uploaded artifact is a real ORCA log.
        upload = _opt_upload({"name": "gaussian", "version": "ORCA 6.0.0"})
        calc = resolve_and_persist_calculation_with_results(
            session, upload, species_entry_id=species_entry_id
        )
        assert calc.declared_software_banner is None  # nothing corrected yet

        warnings: list[UploadWarning] = []
        extract_and_store_calculation_parameters(
            session, calc, _orca_log_text(), warnings=warnings
        )

        assert (
            calc.software_reconciliation_status
            == SoftwareReconciliationStatus.mismatch
        )
        # software_release_id now points at the observed program, not the
        # declared one -- this is the assertion that catches "corrected
        # even with no artifact" landing on the wrong branch, and also
        # catches a correction that silently keeps pointing at Gaussian.
        assert calc.software_release is not None
        assert calc.software_release.software.name == "ORCA"
        # The originally-declared string must not be silently discarded.
        assert calc.declared_software_banner is not None
        assert "gaussian" in calc.declared_software_banner.lower()
        assert "ORCA 6.0.0" in calc.declared_software_banner
        assert calc.observed_software_banner is not None
        assert "orca" in calc.observed_software_banner.lower()
        # And it must warn, naming both sides.
        matching = [
            w for w in warnings if w.code == W_SOFTWARE_IDENTITY_CORRECTED_FROM_ARTIFACT
        ]
        assert len(matching) == 1, warnings
        assert "gaussian" in matching[0].message.lower()
        assert "orca" in matching[0].message.lower()


def test_name_mismatch_without_artifact_leaves_declaration_untouched(
    db_conn,
) -> None:
    """The case covering all 494 live rows: no artifact was ever uploaded,
    so there is nothing to reconcile against. The declaration stands
    exactly as sent -- this is the assertion that catches an over-eager
    corrector that "fixes" a name mismatch with no evidence at all."""
    with Session(db_conn) as session, session.begin():
        species_entry_id = _create_species_entry(
            session, inchi_key=_next_inchi_key("RECNOART")
        )
        upload = _opt_upload({"name": "gaussian", "version": "ORCA 6.0.0"})
        calc = resolve_and_persist_calculation_with_results(
            session, upload, species_entry_id=species_entry_id
        )

        # No artifact upload / extraction happens at all -- only the
        # calculation-creation seam runs, exactly like the 494 live rows.
        assert (
            calc.software_reconciliation_status
            == SoftwareReconciliationStatus.declared_only
        )
        assert calc.software_reconciliation_status != SoftwareReconciliationStatus.mismatch
        assert calc.declared_software_banner is None
        assert calc.observed_software_banner is None
        assert calc.software_release is not None
        assert calc.software_release.software.name == "Gaussian"
        assert calc.software_release.version == "ORCA 6.0.0"


def test_confirming_artifact_with_matching_declaration_is_matched_no_correction(
    db_conn,
) -> None:
    """A declaration the artifact fully confirms (same name, same version)
    must not be "corrected" onto a different release, and must not warn --
    there is nothing to flag."""
    with Session(db_conn) as session, session.begin():
        species_entry_id = _create_species_entry(
            session, inchi_key=_next_inchi_key("RECORCAOK")
        )
        # sp_orca_minimal.txt's banner is "Program Version 5.0.4 - RELEASE -".
        upload = _opt_upload({"name": "orca", "version": "5.0.4"})
        calc = resolve_and_persist_calculation_with_results(
            session, upload, species_entry_id=species_entry_id
        )
        declared_release_id = calc.software_release_id

        warnings: list[UploadWarning] = []
        extract_and_store_calculation_parameters(
            session, calc, _orca_log_text(), warnings=warnings
        )

        assert (
            calc.software_reconciliation_status
            == SoftwareReconciliationStatus.matched
        )
        assert calc.software_release_id == declared_release_id  # unchanged
        assert calc.declared_software_banner is None
        matching = [
            w for w in warnings if w.code == W_SOFTWARE_IDENTITY_CORRECTED_FROM_ARTIFACT
        ]
        assert matching == []
