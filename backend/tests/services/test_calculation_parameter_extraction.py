"""Tests for the parser → persistence bridge.

Covers:

- Gaussian dispatch via ``calculation.software_release.software.name``.
- ORCA dispatch via the same path.
- Text-sniff fallback when no software_release is wired.
- True replace-all: re-parsing wipes prior parser rows but leaves
  upload-supplied / curated rows intact.
- ``parameters_json``, ``parameters_parser_version``,
  ``parameters_extracted_at`` mirroring onto the ``Calculation`` row.
- Re-parse with empty observations still clears stale parser rows.
- ``ParameterExtractionError`` when neither dispatch path finds software.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.models.calculation import (
    Calculation,
    CalculationParameter,
)
from app.db.models.common import CalculationType, ParameterSource
from app.schemas.fragments.calculation import (
    CalculationParameterObservation,
    CalculationWithResultsPayload,
    OptResultPayload,
)
from app.services.calculation_parameter_extraction import (
    ParameterExtractionError,
    extract_and_store_calculation_parameters,
)
from app.services.calculation_resolution import (
    persist_calculation_parameters,
    resolve_and_persist_calculation_with_results,
)

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
GAUSSIAN_LOG = FIXTURES_DIR / "gaussian" / "opt_g09.log"
ORCA_LOG = FIXTURES_DIR / "orca" / "opt_orca.out"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _make_calculation(
    session: Session,
    *,
    software_name: str | None = "gaussian",
) -> Calculation:
    """Persist a minimal opt calculation with optional software_release.

    When ``software_name`` is ``None`` the calculation is created with no
    software_release, so the bridge is forced down its text-sniff
    fallback (or to raise if the text has no markers).
    """

    species_entry_id = _create_species_entry(
        session, inchi_key=_next_inchi_key("EXTR")
    )

    if software_name is None:
        # Create a calculation row without software_release wiring. The
        # standard upload path requires a software_release, so build the
        # row directly to exercise the no-software fallback.
        calc = Calculation(
            type=CalculationType.opt,
            species_entry_id=species_entry_id,
        )
        session.add(calc)
        session.flush()
        return calc

    upload = CalculationWithResultsPayload(
        type=CalculationType.opt,
        software_release={"name": software_name, "version": "16", "revision": "C.02"},
        level_of_theory={"method": "B3LYP", "basis": "6-31G(d)"},
        opt_result=OptResultPayload(converged=True),
    )
    return resolve_and_persist_calculation_with_results(
        session, upload, species_entry_id=species_entry_id
    )


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def test_gaussian_dispatch_via_software_release(db_engine) -> None:
    """When software_release.name='gaussian', the Gaussian parser runs."""
    text_data = GAUSSIAN_LOG.read_text()
    with Session(db_engine) as session, session.begin():
        calc = _make_calculation(session, software_name="gaussian")

        rows = extract_and_store_calculation_parameters(
            session, calc, text_data
        )
        assert rows, "expected at least one parameter row from Gaussian fixture"
        assert all(r.source is ParameterSource.parser for r in rows)
        assert all(r.parser_version == "gaussian_v1" for r in rows)

        stored = session.get(Calculation, calc.id)
        assert stored.parameters_parser_version == "gaussian_v1"
        assert stored.parameters_json is not None
        assert "route_line" in stored.parameters_json
        assert stored.parameters_extracted_at is not None


def test_orca_dispatch_via_software_release(db_engine) -> None:
    """When software_release.name='orca', the ORCA parser runs."""
    text_data = ORCA_LOG.read_text()
    with Session(db_engine) as session, session.begin():
        calc = _make_calculation(session, software_name="orca")

        rows = extract_and_store_calculation_parameters(
            session, calc, text_data
        )
        assert rows, "expected at least one parameter row from ORCA fixture"
        assert all(r.source is ParameterSource.parser for r in rows)
        assert all(r.parser_version == "orca_v2" for r in rows)

        stored = session.get(Calculation, calc.id)
        assert stored.parameters_parser_version == "orca_v2"


def test_text_sniff_fallback_when_no_software_release(db_engine) -> None:
    """No DB-linked software_release → sniff log markers."""
    text_data = GAUSSIAN_LOG.read_text()
    with Session(db_engine) as session, session.begin():
        calc = _make_calculation(session, software_name=None)

        rows = extract_and_store_calculation_parameters(
            session, calc, text_data
        )
        assert rows
        assert all(r.parser_version == "gaussian_v1" for r in rows)


def test_extraction_raises_when_software_unknown(db_engine) -> None:
    """No software_release and unrecognised text → ParameterExtractionError."""
    with Session(db_engine) as session, session.begin():
        calc = _make_calculation(session, software_name=None)
        with pytest.raises(ParameterExtractionError):
            extract_and_store_calculation_parameters(
                session, calc, "this text contains no recognised ESS markers"
            )


# ---------------------------------------------------------------------------
# Vocab linkage (the seeded vocab keys should now resolve through the FK)
# ---------------------------------------------------------------------------


def test_seeded_vocab_keys_link_through_fk(db_engine) -> None:
    """At least one parser-emitted canonical_key links to seeded vocab."""
    text_data = GAUSSIAN_LOG.read_text()
    with Session(db_engine) as session, session.begin():
        calc = _make_calculation(session, software_name="gaussian")
        extract_and_store_calculation_parameters(session, calc, text_data)

        linked = session.scalars(
            select(CalculationParameter).where(
                CalculationParameter.calculation_id == calc.id,
                CalculationParameter.canonical_key.is_not(None),
            )
        ).all()
        assert linked, (
            "expected at least one row to link through the vocab FK after "
            "Phase 1 seeding"
        )


# ---------------------------------------------------------------------------
# Replace-all semantics
# ---------------------------------------------------------------------------


def test_reparse_replaces_only_parser_rows(db_engine) -> None:
    """Re-parsing wipes parser rows but preserves upload + curated rows."""
    text_data = GAUSSIAN_LOG.read_text()
    with Session(db_engine) as session, session.begin():
        calc = _make_calculation(session, software_name="gaussian")

        # First parse.
        first_rows = extract_and_store_calculation_parameters(
            session, calc, text_data
        )
        first_count = len(first_rows)
        assert first_count > 0

        # Add an upload-supplied row and a curated row.
        persist_calculation_parameters(
            session,
            calc,
            [
                CalculationParameterObservation(
                    raw_key="manual_upload_key",
                    raw_value="x",
                    section="custom",
                ),
            ],
            source=ParameterSource.upload,
        )
        persist_calculation_parameters(
            session,
            calc,
            [
                CalculationParameterObservation(
                    raw_key="curator_override_key",
                    raw_value="y",
                    section="custom",
                ),
            ],
            source=ParameterSource.curated,
        )

        # Re-parse.
        second_rows = extract_and_store_calculation_parameters(
            session, calc, text_data
        )
        assert len(second_rows) == first_count

        all_rows = session.scalars(
            select(CalculationParameter).where(
                CalculationParameter.calculation_id == calc.id
            )
        ).all()

        upload_keys = {r.raw_key for r in all_rows if r.source is ParameterSource.upload}
        curated_keys = {r.raw_key for r in all_rows if r.source is ParameterSource.curated}
        parser_count = sum(1 for r in all_rows if r.source is ParameterSource.parser)

        assert "manual_upload_key" in upload_keys
        assert "curator_override_key" in curated_keys
        assert parser_count == first_count, (
            "second parse must fully replace the first parser batch — "
            "no carryover, no duplication"
        )


def test_empty_reparse_clears_stale_parser_rows(db_engine) -> None:
    """A re-parse with no observations still wipes prior parser rows.

    This guards against drift: if an artifact is re-uploaded with a
    truncated/empty input, the stale parameters from the previous parse
    must not silently linger.
    """
    text_data = GAUSSIAN_LOG.read_text()
    with Session(db_engine) as session, session.begin():
        calc = _make_calculation(session, software_name="gaussian")
        extract_and_store_calculation_parameters(session, calc, text_data)

        # Direct replace-all with empty observations.
        persist_calculation_parameters(
            session,
            calc,
            [],
            source=ParameterSource.parser,
        )

        remaining_parser = session.scalars(
            select(CalculationParameter).where(
                CalculationParameter.calculation_id == calc.id,
                CalculationParameter.source == ParameterSource.parser,
            )
        ).all()
        assert remaining_parser == []


# ---------------------------------------------------------------------------
# Default source for upload-payload-supplied parameters
# ---------------------------------------------------------------------------


def test_upload_payload_parameters_default_to_source_upload(db_engine) -> None:
    """Existing upload flow continues to write source='upload' by default."""
    with Session(db_engine) as session, session.begin():
        species_entry_id = _create_species_entry(
            session, inchi_key=_next_inchi_key("DEFSRC")
        )
        upload = CalculationWithResultsPayload(
            type=CalculationType.opt,
            software_release={"name": "gaussian", "version": "16", "revision": "C.02"},
            level_of_theory={"method": "B3LYP", "basis": "6-31G(d)"},
            opt_result=OptResultPayload(converged=True),
            parameters=[
                CalculationParameterObservation(
                    raw_key="tight",
                    raw_value="tight",
                    canonical_key="opt.convergence",
                    canonical_value="tight",
                    section="opt",
                    value_type="enum",
                ),
            ],
        )
        calc = resolve_and_persist_calculation_with_results(
            session, upload, species_entry_id=species_entry_id
        )

        row = session.scalars(
            select(CalculationParameter).where(
                CalculationParameter.calculation_id == calc.id
            )
        ).one()
        assert row.source is ParameterSource.upload
        assert row.parser_version is None
        # And the seeded vocab now means the FK actually links.
        assert row.canonical_key == "opt.convergence"
