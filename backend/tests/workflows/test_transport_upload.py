"""Workflow-layer tests for standalone transport upload persistence.

Targets ``persist_transport_upload`` and verifies that transport rows,
provenance references, and source-calculation links persist correctly,
that Lennard-Jones pair validation fires at the schema layer, and that
append-only semantics hold across repeated uploads.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.app_user import AppUser
from app.db.models.calculation import Calculation
from app.db.models.common import (
    CalculationType,
    ScientificOriginKind,
    TransportCalculationRole,
)
from app.db.models.literature import Literature
from app.db.models.software import SoftwareRelease
from app.db.models.transport import Transport, TransportSourceCalculation
from app.db.models.workflow import WorkflowToolRelease
from app.schemas.fragments.identity import SpeciesEntryIdentityPayload
from app.schemas.workflows.transport_upload import TransportUploadRequest
from app.services.species_resolution import resolve_species_entry
from app.workflows.transport import (
    _assert_calculation_owned_by,
    persist_transport_upload,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_SPECIES_ENTRY = {
    "smiles": "O",
    "charge": 0,
    "multiplicity": 1,
}

_SOFTWARE = {"name": "Gaussian", "version": "16"}
_LOT_DFT = {"method": "B3LYP", "basis": "6-31G(d)"}


def _sp_calc_payload() -> dict:
    return {
        "type": "sp",
        "software_release": _SOFTWARE,
        "level_of_theory": _LOT_DFT,
        "sp_result": {"electronic_energy_hartree": -76.437},
    }


def _freq_calc_payload() -> dict:
    return {
        "type": "freq",
        "software_release": _SOFTWARE,
        "level_of_theory": _LOT_DFT,
        "freq_result": {"n_imag": 0, "zpe_hartree": 0.021},
    }


def _transport_request(**overrides) -> TransportUploadRequest:
    """Build a transport upload request with minimal valid defaults."""
    base: dict = {
        "species_entry": dict(_SPECIES_ENTRY),
        "scientific_origin": "computed",
        "sigma_angstrom": 2.68,
        "epsilon_over_k_k": 572.4,
        "dipole_debye": 1.85,
        "polarizability_angstrom3": 1.45,
        "rotational_relaxation": 4.0,
        "note": "water LJ params",
    }
    base.update(overrides)
    return TransportUploadRequest(**base)


# ---------------------------------------------------------------------------
# Core success cases
# ---------------------------------------------------------------------------


def test_persist_transport_upload_creates_row_with_scalar_fields(db_engine) -> None:
    """Transport scalar fields persist and link to the resolved species entry."""
    with Session(db_engine) as session, session.begin():
        session.add(AppUser(id=1201, username="transport_tester_basic"))
        session.flush()

        transport = persist_transport_upload(
            session, _transport_request(), created_by=1201
        )

        assert transport.id is not None
        assert transport.species_entry_id is not None
        assert transport.created_by == 1201
        assert transport.scientific_origin == ScientificOriginKind.computed
        assert transport.sigma_angstrom == pytest.approx(2.68)
        assert transport.epsilon_over_k_k == pytest.approx(572.4)
        assert transport.dipole_debye == pytest.approx(1.85)
        assert transport.polarizability_angstrom3 == pytest.approx(1.45)
        assert transport.rotational_relaxation == pytest.approx(4.0)
        assert transport.note == "water LJ params"
        assert transport.source_calculations == []


def test_persist_transport_upload_resolves_all_provenance_refs(
    db_engine, monkeypatch,
) -> None:
    """Literature, software release, and workflow tool release all resolve."""
    monkeypatch.setattr(
        "app.services.literature_resolution.fetch_doi_metadata",
        lambda doi: {
            "title": "Transport properties of water",
            "container-title": ["J. Chem. Phys."],
            "issued": 2005,
            "URL": f"https://doi.org/{doi}",
        },
    )

    request = _transport_request(
        species_entry={"smiles": "CC", "charge": 0, "multiplicity": 1},
        literature={
            "doi": "10.1063/1.1234567",
            "title": "Fallback title if DOI lookup fails",
        },
        software_release={"name": "gaussian", "version": "16", "revision": "C.01"},
        workflow_tool_release={"name": "ARC", "version": "1.1.0"},
    )

    with Session(db_engine) as session, session.begin():
        transport = persist_transport_upload(session, request)

        assert transport.literature_id is not None
        assert transport.software_release_id is not None
        assert transport.workflow_tool_release_id is not None

        lit = session.get(Literature, transport.literature_id)
        assert lit is not None
        assert lit.title == "Transport properties of water"

        sr = session.get(SoftwareRelease, transport.software_release_id)
        assert sr is not None
        assert sr.software.name == "Gaussian"
        assert sr.version == "16"

        wtr = session.get(WorkflowToolRelease, transport.workflow_tool_release_id)
        assert wtr is not None
        assert wtr.workflow_tool.name == "ARC"


def test_persist_transport_upload_persists_source_calculations(db_engine) -> None:
    """Inline calcs + source_calculations persist the correct (calc, role) links."""
    distinct = {"smiles": "CCO", "charge": 0, "multiplicity": 1}
    request = TransportUploadRequest(
        species_entry=dict(distinct),
        scientific_origin="computed",
        sigma_angstrom=4.53,
        epsilon_over_k_k=362.6,
        calculations=[
            {"key": "sp1", "calculation": _sp_calc_payload()},
            {"key": "freq1", "calculation": _freq_calc_payload()},
        ],
        source_calculations=[
            {"calculation_key": "sp1", "role": "full_transport"},
            {"calculation_key": "freq1", "role": "supporting_geometry"},
        ],
    )

    with Session(db_engine) as session, session.begin():
        transport = persist_transport_upload(session, request)

        links = session.scalars(
            select(TransportSourceCalculation).where(
                TransportSourceCalculation.transport_id == transport.id
            )
        ).all()
        assert len(links) == 2
        by_role = {lk.role: lk for lk in links}
        assert set(by_role) == {
            TransportCalculationRole.full_transport,
            TransportCalculationRole.supporting_geometry,
        }

        sp_calc = session.get(
            Calculation, by_role[TransportCalculationRole.full_transport].calculation_id
        )
        freq_calc = session.get(
            Calculation,
            by_role[TransportCalculationRole.supporting_geometry].calculation_id,
        )
        assert sp_calc.type == CalculationType.sp
        assert freq_calc.type == CalculationType.freq
        # Owner-consistency: both calcs attached to the transport target species entry.
        assert sp_calc.species_entry_id == transport.species_entry_id
        assert freq_calc.species_entry_id == transport.species_entry_id


def test_repeated_transport_uploads_are_append_only(db_engine) -> None:
    """Two uploads for the same species entry create two distinct transport rows.

    Transport is append-only — repeated uploads for the same species
    entry are valid and must not dedupe.
    """
    distinct = {"smiles": "CCCC", "charge": 0, "multiplicity": 1}
    with Session(db_engine) as session, session.begin():
        first = persist_transport_upload(
            session,
            _transport_request(species_entry=dict(distinct), note="first"),
        )
        second = persist_transport_upload(
            session,
            _transport_request(species_entry=dict(distinct), note="second"),
        )

        assert first.id != second.id
        assert first.species_entry_id == second.species_entry_id

        rows = session.scalars(
            select(Transport)
            .where(Transport.species_entry_id == first.species_entry_id)
            .order_by(Transport.id)
        ).all()
        assert len(rows) == 2
        assert [r.note for r in rows] == ["first", "second"]


def test_transport_without_lj_pair_is_valid(db_engine) -> None:
    """A transport upload with neither sigma nor epsilon is accepted.

    Only the paired presence/absence of the LJ parameters is enforced;
    a transport row can carry only dipole / polarizability / rotational
    relaxation if desired.
    """
    distinct = {"smiles": "C#C", "charge": 0, "multiplicity": 1}
    request = _transport_request(
        species_entry=dict(distinct),
        sigma_angstrom=None,
        epsilon_over_k_k=None,
        note="dipole-only",
    )
    with Session(db_engine) as session, session.begin():
        transport = persist_transport_upload(session, request)
        assert transport.sigma_angstrom is None
        assert transport.epsilon_over_k_k is None
        assert transport.dipole_debye == pytest.approx(1.85)


# ---------------------------------------------------------------------------
# Validation failures
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "missing",
    ["sigma_angstrom", "epsilon_over_k_k"],
)
def test_schema_rejects_partial_lj_pair(missing: str) -> None:
    """Providing only one of sigma / epsilon is rejected at the schema layer."""
    with pytest.raises(
        ValidationError,
        match="sigma_angstrom and epsilon_over_k_k must be provided together",
    ):
        _transport_request(**{missing: None})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sigma_angstrom", 0.0),
        ("sigma_angstrom", -1.0),
        ("epsilon_over_k_k", 0.0),
        ("epsilon_over_k_k", -5.0),
    ],
)
def test_schema_rejects_non_positive_lj_values(field: str, value: float) -> None:
    """sigma_angstrom and epsilon_over_k_k must be strictly positive."""
    with pytest.raises(ValidationError):
        _transport_request(**{field: value})


def test_schema_rejects_negative_rotational_relaxation() -> None:
    """rotational_relaxation must be non-negative (Field(ge=0))."""
    with pytest.raises(ValidationError):
        _transport_request(rotational_relaxation=-0.1)


def test_schema_accepts_zero_rotational_relaxation() -> None:
    """Zero is permitted for rotational_relaxation (Field(ge=0))."""
    request = _transport_request(rotational_relaxation=0.0)
    assert request.rotational_relaxation == 0.0


def test_schema_rejects_duplicate_calculation_keys() -> None:
    """Duplicate calculation keys are rejected by the schema."""
    with pytest.raises(ValidationError, match="unique keys"):
        TransportUploadRequest(
            species_entry=dict(_SPECIES_ENTRY),
            scientific_origin="computed",
            sigma_angstrom=3.5,
            epsilon_over_k_k=100.0,
            calculations=[
                {"key": "dup", "calculation": _sp_calc_payload()},
                {"key": "dup", "calculation": _sp_calc_payload()},
            ],
        )


def test_schema_rejects_source_calculation_with_undefined_key() -> None:
    """A source_calculations entry pointing at an undeclared calc key is rejected."""
    with pytest.raises(ValidationError, match="undefined calculation_key"):
        TransportUploadRequest(
            species_entry=dict(_SPECIES_ENTRY),
            scientific_origin="computed",
            sigma_angstrom=3.5,
            epsilon_over_k_k=100.0,
            calculations=[
                {"key": "sp1", "calculation": _sp_calc_payload()},
            ],
            source_calculations=[
                {"calculation_key": "does_not_exist", "role": "full_transport"},
            ],
        )


def test_schema_rejects_duplicate_source_calculation_pairs() -> None:
    """Duplicate (calculation_key, role) rows are rejected on upload."""
    with pytest.raises(
        ValidationError, match=r"unique by .calculation_key, role"
    ):
        TransportUploadRequest(
            species_entry=dict(_SPECIES_ENTRY),
            scientific_origin="computed",
            sigma_angstrom=3.5,
            epsilon_over_k_k=100.0,
            calculations=[
                {"key": "sp1", "calculation": _sp_calc_payload()},
            ],
            source_calculations=[
                {"calculation_key": "sp1", "role": "full_transport"},
                {"calculation_key": "sp1", "role": "full_transport"},
            ],
        )


def test_wrong_owner_source_calc_rejected_by_workflow_check(db_engine) -> None:
    """The defensive owner-consistency guard fires when a calculation's
    species_entry_id does not match the transport target.

    The upload path cannot produce cross-owner references end-to-end
    because inline calcs are auto-scoped to the transport target's
    species entry. This test exercises the defensive check directly so
    regressions in that guard are caught.
    """
    with Session(db_engine) as session, session.begin():
        species_a = resolve_species_entry(
            session, SpeciesEntryIdentityPayload(**_SPECIES_ENTRY),
        )
        calc = Calculation(type=CalculationType.sp, species_entry_id=species_a.id)
        session.add(calc)
        session.flush()

        with pytest.raises(ValueError, match="not to the transport target"):
            _assert_calculation_owned_by(
                calc,
                species_entry_id=calc.species_entry_id + 1,
                context="test wrong-owner guard",
            )
