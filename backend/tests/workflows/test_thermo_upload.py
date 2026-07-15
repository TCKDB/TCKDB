"""Workflow-layer tests for thermo upload persistence.

These tests target ``persist_thermo_upload`` (and, where the workflow does
not yet expose a feature, the internal ``persist_thermo`` path) and verify
that thermo rows, NASA polynomials, tabulated points, source-calculation
links, and provenance references persist with scientific fidelity.

Known gaps observed against `docs/thermo_tests.md` and flagged where
relevant in test docstrings:

* ``ThermoUploadRequest`` does not accept ``source_calculations``; the
  workflow hardcodes an empty list in ``resolve_thermo_upload`` (see
  ``app/services/thermo_resolution.py``). Source-calculation persistence
  is therefore exercised via ``persist_thermo`` directly, not end-to-end.
* The thermo workflow does not resolve ``source_calculation_key`` on an
  applied energy correction payload (see comment in
  ``app/workflows/thermo.py``); FSF-backed corrections attached to a
  thermo upload therefore persist with ``source_calculation_id = None``.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.app_user import AppUser
from app.db.models.calculation import Calculation
from app.db.models.common import (
    CalculationType,
    EnergyCorrectionApplicationRole,
    EnergyUnit,
    FrequencyScaleKind,
    PhaseKind,
    ScientificOriginKind,
    ThermoCalculationRole,
)
from app.db.models.energy_correction import AppliedEnergyCorrection
from app.db.models.literature import Literature
from app.db.models.software import SoftwareRelease
from app.db.models.statmech import Statmech
from app.db.models.thermo import (
    Thermo,
    ThermoNASA,
    ThermoPoint,
    ThermoSourceCalculation,
)
from app.db.models.workflow import WorkflowToolRelease
from app.schemas.entities.thermo import (
    ThermoCreate,
    ThermoRead,
    ThermoSourceCalculationCreate,
)
from app.schemas.fragments.identity import SpeciesEntryIdentityPayload
from app.schemas.workflows.thermo_upload import ThermoUploadRequest
from app.services.species_resolution import resolve_species_entry
from app.services.thermo_resolution import persist_thermo
from app.workflows.thermo import _assert_calculation_owned_by, persist_thermo_upload

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_SPECIES_ENTRY = {
    "smiles": "O",
    "charge": 0,
    "multiplicity": 1,
}


def _thermo_request(**overrides) -> ThermoUploadRequest:
    """Build a thermo upload request with minimal valid defaults.

    Overrides replace top-level fields; pass ``species_entry={"smiles":...}``
    to target a different species.
    """
    base: dict = {
        "species_entry": dict(_SPECIES_ENTRY),
        "scientific_origin": "computed",
        "h298_kj_mol": -241.8,
        "s298_j_mol_k": 188.8,
        "h298_uncertainty_kj_mol": 0.5,
        "s298_uncertainty_j_mol_k": 0.2,
        "tmin_k": 200.0,
        "tmax_k": 3000.0,
        "note": "water reference",
    }
    base.update(overrides)
    return ThermoUploadRequest(**base)


def _nasa_block() -> dict:
    """A realistic two-range NASA-7 polynomial payload for water."""
    return {
        "t_low": 200.0,
        "t_mid": 1000.0,
        "t_high": 3500.0,
        "a1": 4.19864056,
        "a2": -2.0364341e-3,
        "a3": 6.52040211e-6,
        "a4": -5.48797062e-9,
        "a5": 1.77197817e-12,
        "a6": -3.02937267e4,
        "a7": -0.849032208,
        "b1": 3.03399249,
        "b2": 2.17691804e-3,
        "b3": -1.64072518e-7,
        "b4": -9.7041987e-11,
        "b5": 1.68200992e-14,
        "b6": -3.00042971e4,
        "b7": 4.9667701,
    }


def _thermo_points() -> list[dict]:
    return [
        {"temperature_k": 298.15, "cp_j_mol_k": 33.59, "h_kj_mol": -241.8,
         "s_j_mol_k": 188.8, "g_kj_mol": -228.6},
        {"temperature_k": 500.0, "cp_j_mol_k": 35.22, "h_kj_mol": -234.9,
         "s_j_mol_k": 206.5, "g_kj_mol": -219.1},
        {"temperature_k": 1000.0, "cp_j_mol_k": 41.27, "h_kj_mol": -215.8,
         "s_j_mol_k": 232.7, "g_kj_mol": -229.8},
    ]


def _make_calculation(
    session: Session, *, species_entry_id: int,
    calc_type: CalculationType = CalculationType.sp,
) -> Calculation:
    """Insert a minimal calculation row tied to a species entry."""
    calc = Calculation(
        type=calc_type,
        species_entry_id=species_entry_id,
    )
    session.add(calc)
    session.flush()
    return calc


# ---------------------------------------------------------------------------
# Layer A — Core success cases
# ---------------------------------------------------------------------------


def test_persist_thermo_upload_creates_row_with_scalar_fields(db_engine) -> None:
    """A1: scalar thermo fields persist and link to the resolved species entry."""
    with Session(db_engine) as session, session.begin():
        # Let Postgres assign the id rather than pinning a specific value:
        # other tests in the session-scoped DB create app_user rows via API
        # endpoints (auto-increment) that can advance the sequence past any
        # hardcoded id, causing a UniqueViolation on later runs.
        user = AppUser(username="thermo_tester_a1")
        session.add(user)
        session.flush()

        thermo = persist_thermo_upload(
            session, _thermo_request(), created_by=user.id
        )

        assert thermo.id is not None
        assert thermo.species_entry_id is not None
        assert thermo.created_by == user.id
        assert thermo.scientific_origin == ScientificOriginKind.computed
        assert thermo.h298_kj_mol == pytest.approx(-241.8)
        assert thermo.s298_j_mol_k == pytest.approx(188.8)
        assert thermo.h298_uncertainty_kj_mol == pytest.approx(0.5)
        assert thermo.s298_uncertainty_j_mol_k == pytest.approx(0.2)
        assert thermo.tmin_k == pytest.approx(200.0)
        assert thermo.tmax_k == pytest.approx(3000.0)
        assert thermo.note == "water reference"

        # No child rows created when none were provided
        assert thermo.nasa is None
        assert thermo.points == []
        assert thermo.source_calculations == []


def test_persist_thermo_upload_creates_and_links_nasa_row(db_engine) -> None:
    """A2: a NASA block creates one child row attached to the parent thermo."""
    with Session(db_engine) as session, session.begin():
        thermo = persist_thermo_upload(
            session,
            _thermo_request(nasa=_nasa_block()),
        )

        nasa_rows = session.scalars(
            select(ThermoNASA).where(ThermoNASA.thermo_id == thermo.id)
        ).all()
        assert len(nasa_rows) == 1
        nasa = nasa_rows[0]
        assert nasa.thermo_id == thermo.id
        assert nasa.t_low == pytest.approx(200.0)
        assert nasa.t_mid == pytest.approx(1000.0)
        assert nasa.t_high == pytest.approx(3500.0)
        # Spot-check one low-range and one high-range coefficient round-trip
        assert nasa.a1 == pytest.approx(4.19864056)
        assert nasa.b6 == pytest.approx(-3.00042971e4)


def test_persist_thermo_upload_persists_tabulated_points(db_engine) -> None:
    """A3: each ThermoPoint is persisted keyed by (thermo_id, temperature_k)."""
    points = _thermo_points()
    with Session(db_engine) as session, session.begin():
        thermo = persist_thermo_upload(
            session, _thermo_request(points=points)
        )

        rows = session.scalars(
            select(ThermoPoint)
            .where(ThermoPoint.thermo_id == thermo.id)
            .order_by(ThermoPoint.temperature_k)
        ).all()
        assert len(rows) == len(points)

        by_temp = {row.temperature_k: row for row in rows}
        for expected in points:
            t = expected["temperature_k"]
            assert t in by_temp
            assert by_temp[t].cp_j_mol_k == pytest.approx(expected["cp_j_mol_k"])
            assert by_temp[t].h_kj_mol == pytest.approx(expected["h_kj_mol"])
            assert by_temp[t].s_j_mol_k == pytest.approx(expected["s_j_mol_k"])
            assert by_temp[t].g_kj_mol == pytest.approx(expected["g_kj_mol"])


def test_persist_thermo_source_calculations_link_by_role(db_engine) -> None:
    """A4: ``thermo_source_calculation`` rows persist the correct (calc, role).

    NOTE: ``ThermoUploadRequest`` has no ``source_calculations`` field, so
    the workflow cannot attach source calcs end-to-end today. This test
    exercises the internal ``persist_thermo`` path directly, which is the
    surface that will be wired up once the upload schema is extended.
    """
    with Session(db_engine) as session, session.begin():
        species_entry = resolve_species_entry(
            session,
            SpeciesEntryIdentityPayload(**_SPECIES_ENTRY),
        )
        calc_sp = _make_calculation(
            session, species_entry_id=species_entry.id,
            calc_type=CalculationType.sp,
        )
        calc_freq = _make_calculation(
            session, species_entry_id=species_entry.id,
            calc_type=CalculationType.freq,
        )

        thermo = persist_thermo(
            session,
            ThermoCreate(
                species_entry_id=species_entry.id,
                scientific_origin=ScientificOriginKind.computed,
                source_calculations=[
                    ThermoSourceCalculationCreate(
                        calculation_id=calc_sp.id,
                        role=ThermoCalculationRole.sp,
                    ),
                    ThermoSourceCalculationCreate(
                        calculation_id=calc_freq.id,
                        role=ThermoCalculationRole.freq,
                    ),
                ],
            ),
        )

        links = session.scalars(
            select(ThermoSourceCalculation).where(
                ThermoSourceCalculation.thermo_id == thermo.id
            )
        ).all()
        assert len(links) == 2
        linked = {(lk.calculation_id, lk.role) for lk in links}
        assert linked == {
            (calc_sp.id, ThermoCalculationRole.sp),
            (calc_freq.id, ThermoCalculationRole.freq),
        }


def test_persist_thermo_upload_resolves_all_provenance_refs(
    db_engine, monkeypatch,
) -> None:
    """A5: literature, software release, and workflow tool release all resolve."""
    monkeypatch.setattr(
        "app.services.literature_resolution.fetch_doi_metadata",
        lambda doi: {
            "title": "Enthalpy of formation of water",
            "container-title": ["J. Phys. Chem. Ref. Data"],
            "issued": 1998,
            "URL": f"https://doi.org/{doi}",
        },
    )

    request = _thermo_request(
        literature={
            "doi": "10.1063/1.555991",
            "title": "Fallback title if DOI lookup fails",
        },
        software_release={"name": "gaussian", "version": "16", "revision": "C.01"},
        workflow_tool_release={"name": "ARC", "version": "1.1.0"},
    )

    with Session(db_engine) as session, session.begin():
        thermo = persist_thermo_upload(session, request)

        assert thermo.literature_id is not None
        assert thermo.software_release_id is not None
        assert thermo.workflow_tool_release_id is not None

        lit = session.get(Literature, thermo.literature_id)
        assert lit is not None
        assert lit.title == "Enthalpy of formation of water"

        sr = session.get(SoftwareRelease, thermo.software_release_id)
        assert sr is not None
        assert sr.software.name == "Gaussian"
        assert sr.version == "16"

        wtr = session.get(WorkflowToolRelease, thermo.workflow_tool_release_id)
        assert wtr is not None
        assert wtr.workflow_tool.name == "ARC"


def test_repeated_thermo_uploads_are_append_only(db_engine) -> None:
    """A6: two uploads for the same species entry create two distinct thermo rows.

    Thermo is an append-only result table — deduplication lives at the species
    identity layer, not the thermo layer. See
    ``memory/feedback_identity_vs_result_tables.md``.
    """
    # Use a distinct species so this test is independent of any other test
    # that writes thermo for water or similar common species.
    distinct_species = {"smiles": "CCO", "charge": 0, "multiplicity": 1}
    with Session(db_engine) as session, session.begin():
        first = persist_thermo_upload(
            session,
            _thermo_request(species_entry=dict(distinct_species), note="first"),
        )
        second = persist_thermo_upload(
            session,
            _thermo_request(species_entry=dict(distinct_species), note="second"),
        )

        assert first.id != second.id
        # Same species entry (dedup happens at identity layer)
        assert first.species_entry_id == second.species_entry_id

        rows = session.scalars(
            select(Thermo)
            .where(Thermo.species_entry_id == first.species_entry_id)
            .order_by(Thermo.id)
        ).all()
        assert len(rows) == 2
        notes = [r.note for r in rows]
        assert notes == ["first", "second"]


# ---------------------------------------------------------------------------
# Layer B — Edge / failure cases
# ---------------------------------------------------------------------------


# --- B1: thermo temperature range ------------------------------------------


def test_schema_rejects_tmin_greater_than_tmax() -> None:
    """B1: tmin_k > tmax_k is rejected at the Pydantic validator layer."""
    with pytest.raises(ValidationError, match="tmin_k must be less than or equal"):
        _thermo_request(tmin_k=3000.0, tmax_k=200.0)


@pytest.mark.parametrize("bad_field", ["tmin_k", "tmax_k"])
def test_schema_rejects_non_positive_temperature(bad_field: str) -> None:
    """B1: tmin_k and tmax_k must be strictly positive (Field(gt=0))."""
    with pytest.raises(ValidationError):
        _thermo_request(**{bad_field: 0.0})
    with pytest.raises(ValidationError):
        _thermo_request(**{bad_field: -1.0})


# --- B2: negative uncertainty ----------------------------------------------


@pytest.mark.parametrize(
    "bad_field",
    ["h298_uncertainty_kj_mol", "s298_uncertainty_j_mol_k"],
)
def test_schema_rejects_negative_uncertainty(bad_field: str) -> None:
    """B2: uncertainty fields must be non-negative (Field(ge=0))."""
    with pytest.raises(ValidationError):
        _thermo_request(**{bad_field: -0.1})


# --- B3: NASA bounds ordering ----------------------------------------------


def test_nasa_rejects_t_mid_not_greater_than_t_low() -> None:
    """B3: t_mid <= t_low is rejected by the NASA validator."""
    nasa = _nasa_block()
    nasa["t_mid"] = nasa["t_low"]  # violates t_mid > t_low
    with pytest.raises(ValidationError, match="t_mid must be greater than t_low"):
        _thermo_request(nasa=nasa)


def test_nasa_rejects_t_high_not_greater_than_t_mid() -> None:
    """B3: t_high <= t_mid is rejected by the NASA validator."""
    nasa = _nasa_block()
    nasa["t_high"] = nasa["t_mid"]
    with pytest.raises(ValidationError, match="t_high must be greater than t_mid"):
        _thermo_request(nasa=nasa)


def test_nasa_rejects_non_positive_t_low() -> None:
    """B3: t_low <= 0 is rejected by Field(gt=0) on ThermoNASABase."""
    nasa = _nasa_block()
    nasa["t_low"] = 0.0
    with pytest.raises(ValidationError):
        _thermo_request(nasa=nasa)


# --- B4: all-or-none temperature bounds ------------------------------------


@pytest.mark.parametrize(
    "missing_fields",
    [("t_low",), ("t_mid",), ("t_high",), ("t_low", "t_mid")],
)
def test_nasa_rejects_partial_temperature_bounds(missing_fields: tuple[str, ...]) -> None:
    """B4: partial NASA bounds violate the all-or-none rule."""
    nasa = _nasa_block()
    for field in missing_fields:
        nasa[field] = None
    with pytest.raises(ValidationError, match="all provided or all omitted"):
        _thermo_request(nasa=nasa)


# --- B5: duplicate thermo points -------------------------------------------


def test_schema_rejects_duplicate_thermo_point_temperatures() -> None:
    """B5: duplicate thermo points at the same temperature are rejected up-front.

    The Pydantic validator catches this before the DB is touched, so the
    (thermo_id, temperature_k) PK conflict never fires in practice.
    """
    dup_points = [
        {"temperature_k": 298.15, "cp_j_mol_k": 33.59, "h_kj_mol": -241.8,
         "s_j_mol_k": 188.8, "g_kj_mol": -228.6},
        {"temperature_k": 298.15, "cp_j_mol_k": 33.60, "h_kj_mol": -241.9,
         "s_j_mol_k": 188.9, "g_kj_mol": -228.7},
    ]
    with pytest.raises(ValidationError, match="unique by temperature_k"):
        _thermo_request(points=dup_points)


# --- B6: source-calculation reference errors -------------------------------


def test_persist_thermo_raises_on_unknown_source_calculation(db_engine) -> None:
    """B6: an unknown calculation_id fails cleanly at DB commit time.

    The workflow route cannot reach this today (no source_calculations in
    the upload schema) but the internal persistence path must still raise
    rather than silently accept a dangling FK.
    """
    with Session(db_engine) as session:
        try:
            with session.begin():
                species_entry = resolve_species_entry(
                    session,
                    _thermo_request().species_entry,
                )
                with pytest.raises(IntegrityError):
                    persist_thermo(
                        session,
                        ThermoCreate(
                            species_entry_id=species_entry.id,
                            scientific_origin=ScientificOriginKind.computed,
                            source_calculations=[
                                ThermoSourceCalculationCreate(
                                    calculation_id=999_999_999,
                                    role=ThermoCalculationRole.sp,
                                ),
                            ],
                        ),
                    )
        except IntegrityError:
            # Session re-raises on commit after flush failure; treat as expected.
            pass


def test_schema_rejects_duplicate_source_calculation_role_pairs(db_engine) -> None:
    """B6: two source-calc rows with the same (calculation_id, role) are rejected.

    Pydantic catches the duplicate before the DB sees the row, protecting
    the ``(thermo_id, calculation_id, role)`` primary key.
    """
    with Session(db_engine) as session, session.begin():
        species_entry = resolve_species_entry(
            session, _thermo_request().species_entry,
        )
        calc = _make_calculation(session, species_entry_id=species_entry.id)

        with pytest.raises(ValidationError, match="unique by .calculation_id, role"):
            ThermoCreate(
                species_entry_id=species_entry.id,
                scientific_origin=ScientificOriginKind.computed,
                source_calculations=[
                    ThermoSourceCalculationCreate(
                        calculation_id=calc.id,
                        role=ThermoCalculationRole.sp,
                    ),
                    ThermoSourceCalculationCreate(
                        calculation_id=calc.id,
                        role=ThermoCalculationRole.sp,
                    ),
                ],
            )


# --- B8: empty-payload behavior --------------------------------------------
#
# Policy: a thermo upload must include at least one scientific payload element
# (a scalar thermo value, a NASA block, or at least one thermo point).
# Provenance-only fields (``literature``, ``software_release``,
# ``workflow_tool_release``, ``note``) do not count. See
# ``app/schemas/workflows/thermo_upload.py::validate_has_scientific_content``
# and ``docs/thermo_tests.md §B8``.


def test_schema_rejects_empty_scientific_thermo_payload(db_engine) -> None:
    """B8: an upload with no scalars, no NASA, and no points is rejected.

    Verifies the schema-level validator fires and that the database sees
    no side-effects: no thermo row, no NASA row, no thermo_point row.
    """
    # Unique species so we can detect any partial leakage deterministically.
    empty_species = {"smiles": "N#N", "charge": 0, "multiplicity": 1}

    # Baseline: any rows for this species that may exist from other tests.
    with Session(db_engine) as session:
        before_entry_ids = {
            row.species_entry_id
            for row in session.scalars(
                select(Thermo).join(Thermo.species_entry)
            ).all()
        }

    with pytest.raises(ValidationError, match="at least one"):
        ThermoUploadRequest(
            species_entry=dict(empty_species),
            scientific_origin="computed",
            note="identity-only, no science",
        )

    # DB-clean check: no new thermo / nasa / point rows resulted from the
    # rejected request (schema validation runs before any session work, so
    # this is a belt-and-suspenders assertion).
    with Session(db_engine) as session:
        after_entries = {
            row.species_entry_id
            for row in session.scalars(
                select(Thermo).join(Thermo.species_entry)
            ).all()
        }
        assert after_entries == before_entry_ids

        # The rejected species must not have any thermo_nasa or thermo_point
        # rows attached via any thermo row (no thermo row exists for it).
        hollow_rows = session.scalars(
            select(Thermo).join(Thermo.species_entry).where(
                Thermo.note == "identity-only, no science"
            )
        ).all()
        assert hollow_rows == []


def test_scalar_only_thermo_upload_is_valid(db_engine) -> None:
    """Scalar-only payloads (no NASA, no points) remain valid."""
    distinct = {"smiles": "CO", "charge": 0, "multiplicity": 1}
    with Session(db_engine) as session, session.begin():
        thermo = persist_thermo_upload(
            session,
            ThermoUploadRequest(
                species_entry=dict(distinct),
                scientific_origin="computed",
                h298_kj_mol=-200.7,
                s298_j_mol_k=239.7,
            ),
        )
        assert thermo.id is not None
        assert thermo.h298_kj_mol == pytest.approx(-200.7)
        assert thermo.nasa is None
        assert session.scalars(
            select(ThermoPoint).where(ThermoPoint.thermo_id == thermo.id)
        ).all() == []


def test_nasa_only_thermo_upload_is_valid(db_engine) -> None:
    """NASA-only payloads (no scalars, no points) remain valid.

    The schema has no rule tying NASA presence to scalar or point presence,
    and NASA carries its own full thermodynamic model, so this is accepted.
    """
    distinct = {"smiles": "C#N", "charge": 0, "multiplicity": 1}
    with Session(db_engine) as session, session.begin():
        thermo = persist_thermo_upload(
            session,
            ThermoUploadRequest(
                species_entry=dict(distinct),
                scientific_origin="computed",
                nasa=_nasa_block(),
            ),
        )
        assert thermo.id is not None
        assert thermo.h298_kj_mol is None
        assert thermo.s298_j_mol_k is None

        nasa_rows = session.scalars(
            select(ThermoNASA).where(ThermoNASA.thermo_id == thermo.id)
        ).all()
        assert len(nasa_rows) == 1


def test_points_only_thermo_upload_is_valid(db_engine) -> None:
    """Points-only payloads (no scalars, no NASA) remain valid.

    Tabulated points are a standalone thermodynamic representation and the
    schema does not require NASA coefficients alongside them.
    """
    distinct = {"smiles": "[C-]#[O+]", "charge": 0, "multiplicity": 1}
    with Session(db_engine) as session, session.begin():
        thermo = persist_thermo_upload(
            session,
            ThermoUploadRequest(
                species_entry=dict(distinct),
                scientific_origin="computed",
                points=_thermo_points(),
            ),
        )
        assert thermo.id is not None
        assert thermo.nasa is None
        assert thermo.h298_kj_mol is None

        rows = session.scalars(
            select(ThermoPoint).where(ThermoPoint.thermo_id == thermo.id)
        ).all()
        assert len(rows) == len(_thermo_points())


# ---------------------------------------------------------------------------
# Source-calculation support via the upload surface
# ---------------------------------------------------------------------------


_SOFTWARE = {"name": "Gaussian", "version": "16"}
_LOT_DFT = {"method": "B3LYP", "basis": "6-31G(d)"}
_LOT_CC = {"method": "CCSD(T)", "basis": "cc-pVTZ"}


def _sp_calc_payload() -> dict:
    return {
        "type": "sp",
        "software_release": _SOFTWARE,
        "level_of_theory": _LOT_CC,
        "sp_result": {"electronic_energy_hartree": -76.437},
    }


def _freq_calc_payload() -> dict:
    return {
        "type": "freq",
        "software_release": _SOFTWARE,
        "level_of_theory": _LOT_DFT,
        "freq_result": {"n_imag": 0, "zpe_hartree": 0.021},
    }


def test_thermo_upload_persists_source_calculations_via_upload(db_engine) -> None:
    """Uploading thermo with declared calcs + source_calculations populates
    ``thermo_source_calculation`` with the right (calculation_id, role)."""
    distinct = {"smiles": "CCCC", "charge": 0, "multiplicity": 1}
    request = ThermoUploadRequest(
        species_entry=dict(distinct),
        scientific_origin="computed",
        h298_kj_mol=-125.7,
        calculations=[
            {"key": "sp1", "calculation": _sp_calc_payload()},
        ],
        source_calculations=[
            {"calculation_key": "sp1", "role": "sp"},
        ],
    )

    with Session(db_engine) as session, session.begin():
        thermo = persist_thermo_upload(session, request)

        links = session.scalars(
            select(ThermoSourceCalculation).where(
                ThermoSourceCalculation.thermo_id == thermo.id
            )
        ).all()
        assert len(links) == 1
        link = links[0]
        assert link.role == ThermoCalculationRole.sp

        calc = session.get(Calculation, link.calculation_id)
        assert calc is not None
        assert calc.species_entry_id == thermo.species_entry_id
        assert calc.type == CalculationType.sp


def test_thermo_upload_with_multiple_source_calculations_and_roles(db_engine) -> None:
    """Multiple inline calcs with distinct roles persist as distinct rows."""
    distinct = {"smiles": "c1ccccc1", "charge": 0, "multiplicity": 1}
    request = ThermoUploadRequest(
        species_entry=dict(distinct),
        scientific_origin="computed",
        h298_kj_mol=82.9,
        calculations=[
            {"key": "sp_cc", "calculation": _sp_calc_payload()},
            {"key": "freq_dft", "calculation": _freq_calc_payload()},
        ],
        source_calculations=[
            {"calculation_key": "sp_cc", "role": "sp"},
            {"calculation_key": "freq_dft", "role": "freq"},
        ],
    )

    with Session(db_engine) as session, session.begin():
        thermo = persist_thermo_upload(session, request)

        links = session.scalars(
            select(ThermoSourceCalculation).where(
                ThermoSourceCalculation.thermo_id == thermo.id
            )
        ).all()
        assert len(links) == 2
        by_role = {lk.role: lk for lk in links}
        assert set(by_role) == {ThermoCalculationRole.sp, ThermoCalculationRole.freq}

        sp_calc = session.get(Calculation, by_role[ThermoCalculationRole.sp].calculation_id)
        freq_calc = session.get(Calculation, by_role[ThermoCalculationRole.freq].calculation_id)
        assert sp_calc.type == CalculationType.sp
        assert freq_calc.type == CalculationType.freq
        # Owner-consistency enforced: both calcs attached to the same species entry
        assert sp_calc.species_entry_id == thermo.species_entry_id
        assert freq_calc.species_entry_id == thermo.species_entry_id


def test_applied_correction_source_calculation_key_resolves_to_id(db_engine) -> None:
    """Applied corrections attached to a thermo upload no longer drop their
    source-calculation provenance.

    The FSF correction path requires ``source_calculation_key`` by schema.
    The workflow must resolve that key to a real ``source_calculation_id``
    rather than persisting NULL.
    """
    distinct = {"smiles": "CCC", "charge": 0, "multiplicity": 1}
    request = ThermoUploadRequest(
        species_entry=dict(distinct),
        scientific_origin="computed",
        h298_kj_mol=-104.0,
        calculations=[
            {"key": "freq_for_fsf", "calculation": _freq_calc_payload()},
        ],
        applied_energy_corrections=[
            {
                "frequency_scale_factor": {
                    "level_of_theory": _LOT_DFT,
                    "scale_kind": FrequencyScaleKind.zpe.value,
                    "value": 0.977,
                },
                "application_role": EnergyCorrectionApplicationRole.zpe.value,
                "value": 0.0215,
                "value_unit": EnergyUnit.hartree.value,
                "source_calculation_key": "freq_for_fsf",
            }
        ],
    )

    with Session(db_engine) as session, session.begin():
        thermo = persist_thermo_upload(session, request)

        applied = session.scalars(
            select(AppliedEnergyCorrection).where(
                AppliedEnergyCorrection.target_species_entry_id
                == thermo.species_entry_id
            )
        ).all()
        assert len(applied) == 1
        ac = applied[0]
        assert ac.source_calculation_id is not None, (
            "workflow must resolve source_calculation_key, not persist NULL"
        )

        calc = session.get(Calculation, ac.source_calculation_id)
        assert calc is not None
        assert calc.type == CalculationType.freq
        assert calc.species_entry_id == thermo.species_entry_id


def test_schema_rejects_source_calculation_with_undefined_key() -> None:
    """An undefined ``source_calculations[*].calculation_key`` is rejected at
    schema-validation time — before any DB work happens."""
    with pytest.raises(ValidationError, match="undefined calculation_key"):
        ThermoUploadRequest(
            species_entry=dict(_SPECIES_ENTRY),
            scientific_origin="computed",
            h298_kj_mol=-100.0,
            calculations=[
                {"key": "sp1", "calculation": _sp_calc_payload()},
            ],
            source_calculations=[
                {"calculation_key": "does_not_exist", "role": "sp"},
            ],
        )


def test_schema_rejects_duplicate_calculation_keys() -> None:
    """Duplicate calculation keys are rejected by the schema."""
    with pytest.raises(ValidationError, match="unique keys"):
        ThermoUploadRequest(
            species_entry=dict(_SPECIES_ENTRY),
            scientific_origin="computed",
            h298_kj_mol=-100.0,
            calculations=[
                {"key": "sp1", "calculation": _sp_calc_payload()},
                {"key": "sp1", "calculation": _sp_calc_payload()},
            ],
        )


def test_schema_rejects_duplicate_source_calculation_pairs_on_upload() -> None:
    """Duplicate (calculation_key, role) pairs are rejected on upload."""
    with pytest.raises(ValidationError, match=r"unique by .calculation_key"):
        ThermoUploadRequest(
            species_entry=dict(_SPECIES_ENTRY),
            scientific_origin="computed",
            h298_kj_mol=-100.0,
            calculations=[
                {"key": "sp1", "calculation": _sp_calc_payload()},
            ],
            source_calculations=[
                {"calculation_key": "sp1", "role": "sp"},
                {"calculation_key": "sp1", "role": "sp"},
            ],
        )


def test_schema_rejects_applied_correction_with_undefined_source_calc_key() -> None:
    """An applied correction's ``source_calculation_key`` must point at a
    declared calculation — no silent provenance loss."""
    with pytest.raises(ValidationError, match="does not reference a declared"):
        ThermoUploadRequest(
            species_entry=dict(_SPECIES_ENTRY),
            scientific_origin="computed",
            h298_kj_mol=-100.0,
            calculations=[],  # intentionally no declared calculations
            applied_energy_corrections=[
                {
                    "frequency_scale_factor": {
                        "level_of_theory": _LOT_DFT,
                        "scale_kind": FrequencyScaleKind.zpe.value,
                        "value": 0.977,
                    },
                    "application_role": EnergyCorrectionApplicationRole.zpe.value,
                    "value": 0.0215,
                    "value_unit": EnergyUnit.hartree.value,
                    "source_calculation_key": "ghost",
                }
            ],
        )


def test_wrong_owner_source_calc_rejected_by_workflow_check(db_engine) -> None:
    """Owner-consistency check fires when a resolved calculation's
    ``species_entry_id`` does not match the thermo target.

    End-to-end the upload path cannot produce cross-owner references
    because inline calcs are auto-scoped to the thermo target's species
    entry. This test exercises the defensive check directly so regressions
    in that guard are caught.
    """
    with Session(db_engine) as session, session.begin():
        species_a = resolve_species_entry(
            session, SpeciesEntryIdentityPayload(**_SPECIES_ENTRY),
        )
        calc_a = _make_calculation(session, species_entry_id=species_a.id)
        # Guard runs on the still-attached ORM instance inside the session.
        with pytest.raises(ValueError, match="different species entry"):
            _assert_calculation_owned_by(
                calc_a,
                species_entry_id=calc_a.species_entry_id + 1,
                context="test wrong-owner guard",
            )


def test_applied_correction_with_wrong_owner_source_calc_leaves_no_partial(
    db_engine,
) -> None:
    """If owner-consistency fails for an applied correction's source calc,
    the whole thermo transaction rolls back — no partial thermo row, no
    half-written applied correction, no dangling source link.

    We simulate the cross-owner scenario by monkeypatching
    ``_assert_calculation_owned_by`` to raise only when the workflow
    reaches the applied-correction check (after the thermo row + source
    calc are already flushed in-session).
    """
    import app.workflows.thermo as thermo_module

    distinct = {"smiles": "CCCCC", "charge": 0, "multiplicity": 1}
    request = ThermoUploadRequest(
        species_entry=dict(distinct),
        scientific_origin="computed",
        h298_kj_mol=-146.8,
        note="wrong-owner-sentinel",
        calculations=[
            {"key": "freq1", "calculation": _freq_calc_payload()},
        ],
        applied_energy_corrections=[
            {
                "frequency_scale_factor": {
                    "level_of_theory": _LOT_DFT,
                    "scale_kind": FrequencyScaleKind.zpe.value,
                    "value": 0.977,
                },
                "application_role": EnergyCorrectionApplicationRole.zpe.value,
                "value": 0.0215,
                "value_unit": EnergyUnit.hartree.value,
                "source_calculation_key": "freq1",
            }
        ],
    )

    real_check = thermo_module._assert_calculation_owned_by
    calls = {"n": 0}

    def _fail_on_second_call(calculation, *, species_entry_id, context):
        calls["n"] += 1
        # First call = inline calc resolution (pass). Second call = applied
        # correction owner check (fail to simulate cross-owner).
        if calls["n"] == 1:
            return real_check(
                calculation, species_entry_id=species_entry_id, context=context,
            )
        raise ValueError(
            f"{context}: calculation id={calculation.id} belongs to a "
            f"different species entry (simulated cross-owner)."
        )

    with pytest.raises(ValueError, match="simulated cross-owner"):
        with Session(db_engine) as session, session.begin():
            original = thermo_module._assert_calculation_owned_by
            thermo_module._assert_calculation_owned_by = _fail_on_second_call
            try:
                persist_thermo_upload(session, request)
            finally:
                thermo_module._assert_calculation_owned_by = original

    # Verify no partial persistence.
    with Session(db_engine) as verify:
        leaked_thermo = session_scalar_count(
            verify, Thermo, note="wrong-owner-sentinel",
        )
        assert leaked_thermo == 0
        leaked_applied = verify.scalars(
            select(AppliedEnergyCorrection).where(
                AppliedEnergyCorrection.note == "wrong-owner-sentinel",
            )
        ).all()
        assert leaked_applied == []


# ---------------------------------------------------------------------------
# Layer D — Transaction and rollback behavior
# ---------------------------------------------------------------------------


def test_child_nasa_failure_rolls_back_parent_thermo(db_engine) -> None:
    """D1: if NASA insertion fails after the parent is flushed, the whole
    transaction rolls back and no partial thermo row remains.

    We simulate a late failure by driving an explicit IntegrityError inside
    the same transaction (via an invalid source-calculation FK on the
    internal path), then verifying no thermo rows with our unique note
    leaked across the rollback boundary.
    """
    unique_note = "rollback-sentinel-D1"

    with Session(db_engine) as outer_session, outer_session.begin():
        # Any pre-existing data is already committed from other tests.
        baseline = session_scalar_count(outer_session, Thermo, note=unique_note)
        assert baseline == 0

    with pytest.raises(IntegrityError):
        with Session(db_engine) as session, session.begin():
            species_entry = resolve_species_entry(
                session, _thermo_request().species_entry,
            )
            # Persist a parent thermo that carries our sentinel note, then
            # attach an invalid source-calc link to force a late failure.
            persist_thermo(
                session,
                ThermoCreate(
                    species_entry_id=species_entry.id,
                    scientific_origin=ScientificOriginKind.computed,
                    note=unique_note,
                    source_calculations=[
                        ThermoSourceCalculationCreate(
                            calculation_id=999_999_999,
                            role=ThermoCalculationRole.sp,
                        ),
                    ],
                ),
            )
            # session.begin() context will call commit on exit; the FK
            # violation surfaces there, triggering rollback.

    with Session(db_engine) as verify_session:
        leaked = session_scalar_count(verify_session, Thermo, note=unique_note)
        assert leaked == 0, "rollback should have removed the parent thermo row"


def session_scalar_count(session: Session, model, **filters) -> int:
    """Count rows for ``model`` matching simple equality filters."""
    stmt = select(model)
    for field, value in filters.items():
        stmt = stmt.where(getattr(model, field) == value)
    return len(session.scalars(stmt).all())


# ---------------------------------------------------------------------------
# DR-0028: existing_calculation_id source-calculation references
# ---------------------------------------------------------------------------
#
# Thermo uploads may reference calcs that were already persisted by a prior
# upload (e.g. ARC's conformer step uploads opt/freq/sp; thermo links to
# those rows rather than re-declaring them inline). The schema accepts
# either calculation_key (inline) or existing_calculation_id (DB row) per
# entry, with exactly-one-of validation.


def _make_persisted_calc(
    session: Session,
    *,
    species_entry_id: int,
    calc_type: CalculationType,
) -> Calculation:
    """Insert a calculation row in its own committed state, like a prior upload."""
    return _make_calculation(
        session, species_entry_id=species_entry_id, calc_type=calc_type,
    )


def test_thermo_upload_links_existing_calculation_ids_for_opt_freq_sp(
    db_engine,
) -> None:
    """DR-0028: existing_calculation_id references for opt/freq/sp produce
    thermo_source_calculation rows that point at those exact calc ids."""
    distinct = {"smiles": "CC", "charge": 0, "multiplicity": 1}
    with Session(db_engine) as session, session.begin():
        species_entry = resolve_species_entry(
            session, SpeciesEntryIdentityPayload(**distinct),
        )
        calc_opt = _make_persisted_calc(
            session, species_entry_id=species_entry.id,
            calc_type=CalculationType.opt,
        )
        calc_freq = _make_persisted_calc(
            session, species_entry_id=species_entry.id,
            calc_type=CalculationType.freq,
        )
        calc_sp = _make_persisted_calc(
            session, species_entry_id=species_entry.id,
            calc_type=CalculationType.sp,
        )

        request = ThermoUploadRequest(
            species_entry=dict(distinct),
            scientific_origin="computed",
            h298_kj_mol=-83.7,
            source_calculations=[
                {"existing_calculation_id": calc_opt.id, "role": "opt"},
                {"existing_calculation_id": calc_freq.id, "role": "freq"},
                {"existing_calculation_id": calc_sp.id, "role": "sp"},
            ],
        )

        thermo = persist_thermo_upload(session, request)

        links = session.scalars(
            select(ThermoSourceCalculation).where(
                ThermoSourceCalculation.thermo_id == thermo.id
            )
        ).all()
        assert len(links) == 3
        by_role = {lk.role: lk.calculation_id for lk in links}
        assert by_role == {
            ThermoCalculationRole.opt: calc_opt.id,
            ThermoCalculationRole.freq: calc_freq.id,
            ThermoCalculationRole.sp: calc_sp.id,
        }


def test_thermo_upload_with_existing_calc_id_creates_no_duplicate_calc(
    db_engine,
) -> None:
    """DR-0028: linking an existing calc must NOT insert a new calculation
    row — the whole point is to avoid row explosion."""
    distinct = {"smiles": "CCN", "charge": 0, "multiplicity": 1}
    with Session(db_engine) as session, session.begin():
        species_entry = resolve_species_entry(
            session, SpeciesEntryIdentityPayload(**distinct),
        )
        calc = _make_persisted_calc(
            session, species_entry_id=species_entry.id,
            calc_type=CalculationType.sp,
        )
        before = session.scalars(
            select(Calculation).where(
                Calculation.species_entry_id == species_entry.id
            )
        ).all()
        assert len(before) == 1

        persist_thermo_upload(
            session,
            ThermoUploadRequest(
                species_entry=dict(distinct),
                scientific_origin="computed",
                h298_kj_mol=-47.5,
                source_calculations=[
                    {"existing_calculation_id": calc.id, "role": "sp"},
                ],
            ),
        )

        after = session.scalars(
            select(Calculation).where(
                Calculation.species_entry_id == species_entry.id
            )
        ).all()
        assert {row.id for row in after} == {calc.id}


def test_thermo_upload_mixed_inline_and_existing_calc_references(db_engine) -> None:
    """DR-0028: a single source_calculations list may mix inline keys with
    existing-id references. Both produce link rows; inline calcs are
    inserted as new calc rows, existing references reuse prior rows."""
    distinct = {"smiles": "CCO", "charge": 0, "multiplicity": 1}
    with Session(db_engine) as session, session.begin():
        species_entry = resolve_species_entry(
            session, SpeciesEntryIdentityPayload(**distinct),
        )
        existing_freq = _make_persisted_calc(
            session, species_entry_id=species_entry.id,
            calc_type=CalculationType.freq,
        )
        existing_calc_count_before = len(session.scalars(
            select(Calculation).where(
                Calculation.species_entry_id == species_entry.id
            )
        ).all())

        request = ThermoUploadRequest(
            species_entry=dict(distinct),
            scientific_origin="computed",
            h298_kj_mol=-235.3,
            calculations=[
                {"key": "sp_inline", "calculation": _sp_calc_payload()},
            ],
            source_calculations=[
                {"calculation_key": "sp_inline", "role": "sp"},
                {"existing_calculation_id": existing_freq.id, "role": "freq"},
            ],
        )

        thermo = persist_thermo_upload(session, request)

        links = session.scalars(
            select(ThermoSourceCalculation).where(
                ThermoSourceCalculation.thermo_id == thermo.id
            )
        ).all()
        assert len(links) == 2
        roles = {lk.role for lk in links}
        assert roles == {ThermoCalculationRole.sp, ThermoCalculationRole.freq}

        all_calcs = session.scalars(
            select(Calculation).where(
                Calculation.species_entry_id == species_entry.id
            )
        ).all()
        # One new inline calc was added; the existing freq calc is reused.
        assert len(all_calcs) == existing_calc_count_before + 1
        assert existing_freq.id in {c.id for c in all_calcs}
        # The freq link points at the pre-existing row, not a new one.
        freq_link = next(
            lk for lk in links if lk.role == ThermoCalculationRole.freq
        )
        assert freq_link.calculation_id == existing_freq.id


def test_thermo_upload_existing_calc_id_not_found_raises_not_found(
    db_engine,
) -> None:
    """DR-0028 Req 2: a missing existing_calculation_id must surface as 404
    via NotFoundError (mapped to HTTP 404 by the API exception handler)."""
    from app.api.errors import NotFoundError

    distinct = {"smiles": "CN", "charge": 0, "multiplicity": 1}
    request = ThermoUploadRequest(
        species_entry=dict(distinct),
        scientific_origin="computed",
        h298_kj_mol=-30.0,
        source_calculations=[
            {"existing_calculation_id": 999_999_999, "role": "sp"},
        ],
    )

    with pytest.raises(NotFoundError, match="does not exist"):
        with Session(db_engine) as session, session.begin():
            persist_thermo_upload(session, request)


def test_thermo_upload_existing_calc_id_wrong_species_entry_raises_422(
    db_engine,
) -> None:
    """DR-0028 Req 2: an existing_calculation_id whose species_entry_id
    differs from the thermo target's must surface as 422 (ValueError) and
    must NOT leak the conflicting species_entry_id in the message."""
    species_a = {"smiles": "[OH]", "charge": 0, "multiplicity": 2}
    species_b = {"smiles": "[H]", "charge": 0, "multiplicity": 2}

    with pytest.raises(ValueError, match="different species entry") as exc_info:
        with Session(db_engine) as session, session.begin():
            entry_a = resolve_species_entry(
                session, SpeciesEntryIdentityPayload(**species_a),
            )
            calc_a = _make_persisted_calc(
                session, species_entry_id=entry_a.id,
                calc_type=CalculationType.sp,
            )

            request = ThermoUploadRequest(
                species_entry=dict(species_b),
                scientific_origin="computed",
                h298_kj_mol=217.998,
                source_calculations=[
                    {"existing_calculation_id": calc_a.id, "role": "sp"},
                ],
            )
            persist_thermo_upload(session, request)

    # The error must not leak any internal id (DR-0028 Req 2).
    detail = str(exc_info.value)
    assert "species_entry_id=" not in detail
    assert "id=" not in detail


def test_thermo_upload_role_freq_with_opt_calc_raises_422(db_engine) -> None:
    """DR-0028 Req 1: role=freq pointing at a calc whose type=opt is rejected."""
    distinct = {"smiles": "CCC", "charge": 0, "multiplicity": 1}
    with pytest.raises(ValueError, match="incompatible"):
        with Session(db_engine) as session, session.begin():
            species_entry = resolve_species_entry(
                session, SpeciesEntryIdentityPayload(**distinct),
            )
            calc_opt = _make_persisted_calc(
                session, species_entry_id=species_entry.id,
                calc_type=CalculationType.opt,
            )
            persist_thermo_upload(
                session,
                ThermoUploadRequest(
                    species_entry=dict(distinct),
                    scientific_origin="computed",
                    h298_kj_mol=-104.0,
                    source_calculations=[
                        {"existing_calculation_id": calc_opt.id, "role": "freq"},
                    ],
                ),
            )


def test_thermo_upload_role_sp_with_freq_calc_raises_422(db_engine) -> None:
    """DR-0028 Req 1: role=sp pointing at a calc whose type=freq is rejected."""
    distinct = {"smiles": "CCCO", "charge": 0, "multiplicity": 1}
    with pytest.raises(ValueError, match="incompatible"):
        with Session(db_engine) as session, session.begin():
            species_entry = resolve_species_entry(
                session, SpeciesEntryIdentityPayload(**distinct),
            )
            calc_freq = _make_persisted_calc(
                session, species_entry_id=species_entry.id,
                calc_type=CalculationType.freq,
            )
            persist_thermo_upload(
                session,
                ThermoUploadRequest(
                    species_entry=dict(distinct),
                    scientific_origin="computed",
                    h298_kj_mol=-272.6,
                    source_calculations=[
                        {"existing_calculation_id": calc_freq.id, "role": "sp"},
                    ],
                ),
            )


def test_thermo_upload_inline_calc_role_type_mismatch_raises_422(db_engine) -> None:
    """DR-0028 Req 1: the role/type compatibility check applies to inline
    calcs too. Declaring a freq inline and tagging it role=opt is the same
    error class as a typo'd existing_calculation_id."""
    distinct = {"smiles": "CCCN", "charge": 0, "multiplicity": 1}
    with pytest.raises(ValueError, match="incompatible"):
        with Session(db_engine) as session, session.begin():
            persist_thermo_upload(
                session,
                ThermoUploadRequest(
                    species_entry=dict(distinct),
                    scientific_origin="computed",
                    h298_kj_mol=-90.0,
                    calculations=[
                        {"key": "freq1", "calculation": _freq_calc_payload()},
                    ],
                    source_calculations=[
                        {"calculation_key": "freq1", "role": "opt"},
                    ],
                ),
            )


def test_schema_rejects_both_calculation_key_and_existing_id_set() -> None:
    """DR-0028 Req 2: both reference fields set on one entry is rejected
    at the Pydantic layer (422)."""
    with pytest.raises(
        ValidationError, match="exactly one of calculation_key or "
    ):
        ThermoUploadRequest(
            species_entry=dict(_SPECIES_ENTRY),
            scientific_origin="computed",
            h298_kj_mol=-100.0,
            calculations=[
                {"key": "sp1", "calculation": _sp_calc_payload()},
            ],
            source_calculations=[
                {
                    "calculation_key": "sp1",
                    "existing_calculation_id": 1,
                    "role": "sp",
                }
            ],
        )


def test_schema_rejects_neither_calculation_key_nor_existing_id_set() -> None:
    """DR-0028 Req 2: an entry with neither reference is rejected (422)."""
    with pytest.raises(
        ValidationError, match="exactly one of calculation_key or "
    ):
        ThermoUploadRequest(
            species_entry=dict(_SPECIES_ENTRY),
            scientific_origin="computed",
            h298_kj_mol=-100.0,
            source_calculations=[
                {"role": "sp"},
            ],
        )


def test_schema_rejects_existing_calculation_id_zero_or_negative() -> None:
    """existing_calculation_id must be a positive integer (gt=0)."""
    with pytest.raises(ValidationError):
        ThermoUploadRequest(
            species_entry=dict(_SPECIES_ENTRY),
            scientific_origin="computed",
            h298_kj_mol=-100.0,
            source_calculations=[
                {"existing_calculation_id": 0, "role": "sp"},
            ],
        )


def test_schema_allows_role_composite_with_any_calc_type(db_engine) -> None:
    """DR-0028 Req 1: role=composite has no strict type check at v0 — it
    describes a scientific origin rather than a specific job type."""
    distinct = {"smiles": "CCCC=O", "charge": 0, "multiplicity": 1}
    with Session(db_engine) as session, session.begin():
        species_entry = resolve_species_entry(
            session, SpeciesEntryIdentityPayload(**distinct),
        )
        # A freq calc tagged as role=composite is accepted.
        calc = _make_persisted_calc(
            session, species_entry_id=species_entry.id,
            calc_type=CalculationType.freq,
        )
        thermo = persist_thermo_upload(
            session,
            ThermoUploadRequest(
                species_entry=dict(distinct),
                scientific_origin="computed",
                h298_kj_mol=-205.0,
                source_calculations=[
                    {
                        "existing_calculation_id": calc.id,
                        "role": "composite",
                    },
                ],
            ),
        )
        links = session.scalars(
            select(ThermoSourceCalculation).where(
                ThermoSourceCalculation.thermo_id == thermo.id
            )
        ).all()
        assert len(links) == 1
        assert links[0].role == ThermoCalculationRole.composite
        assert links[0].calculation_id == calc.id


# ---------------------------------------------------------------------------
# Reference-state semantics (2026-07-15): reference pressure, phase,
# ΔfH°(0 K), and statmech linkage.
# ---------------------------------------------------------------------------


def _make_statmech(
    session: Session, *, species_entry_id: int
) -> Statmech:
    """Insert a minimal statmech row tied to a species entry."""
    statmech = Statmech(
        species_entry_id=species_entry_id,
        scientific_origin=ScientificOriginKind.computed,
    )
    session.add(statmech)
    session.flush()
    return statmech


def test_thermo_upload_persists_reference_state_fields(db_engine) -> None:
    """Reference pressure, phase, and ΔfH°(0 K) persist and read back."""
    distinct = {"smiles": "CCCCCC", "charge": 0, "multiplicity": 1}
    with Session(db_engine) as session, session.begin():
        thermo = persist_thermo_upload(
            session,
            _thermo_request(
                species_entry=dict(distinct),
                reference_pressure_bar=1.01325,  # legacy 1 atm reference
                phase=PhaseKind.gas.value,
                enthalpy_formation_0k_kj_mol=-224.3,
                enthalpy_formation_0k_uncertainty_kj_mol=0.4,
            ),
        )

        assert thermo.reference_pressure_bar == pytest.approx(1.01325)
        assert thermo.phase == PhaseKind.gas
        assert thermo.enthalpy_formation_0k_kj_mol == pytest.approx(-224.3)
        assert thermo.enthalpy_formation_0k_uncertainty_kj_mol == pytest.approx(0.4)

        # The read schema surfaces the new fields.
        read = ThermoRead.model_validate(thermo)
        assert read.reference_pressure_bar == pytest.approx(1.01325)
        assert read.phase == PhaseKind.gas
        assert read.enthalpy_formation_0k_kj_mol == pytest.approx(-224.3)
        assert read.enthalpy_formation_0k_uncertainty_kj_mol == pytest.approx(0.4)


def test_thermo_upload_defaults_reference_pressure_and_phase(db_engine) -> None:
    """Omitting reference pressure / phase applies the IUPAC 1 bar, gas defaults."""
    distinct = {"smiles": "CCCCCCC", "charge": 0, "multiplicity": 1}
    request = ThermoUploadRequest(
        species_entry=dict(distinct),
        scientific_origin="computed",
        h298_kj_mol=-187.8,
    )
    assert request.reference_pressure_bar == pytest.approx(1.0)
    assert request.phase == PhaseKind.gas

    with Session(db_engine) as session, session.begin():
        thermo = persist_thermo_upload(session, request)
        assert thermo.reference_pressure_bar == pytest.approx(1.0)
        assert thermo.phase == PhaseKind.gas
        # ΔfH°(0 K) is unspecified when not provided.
        assert thermo.enthalpy_formation_0k_kj_mol is None


def test_thermo_upload_allows_condensed_phase_override(db_engine) -> None:
    """A liquid-phase record overrides the gas default."""
    distinct = {"smiles": "CCCCCCCC", "charge": 0, "multiplicity": 1}
    with Session(db_engine) as session, session.begin():
        thermo = persist_thermo_upload(
            session,
            _thermo_request(
                species_entry=dict(distinct),
                phase=PhaseKind.liquid.value,
            ),
        )
        assert thermo.phase == PhaseKind.liquid


@pytest.mark.parametrize("origin", ["experimental", "estimated"])
def test_non_computed_origin_does_not_default_phase_or_pressure(
    db_engine, origin: str,
) -> None:
    """Experimental/literature/estimated uploads must NOT be silently
    stamped gas @ 1 bar. Only computed uploads get the QC defaults; other
    origins leave phase/reference_pressure_bar unset unless provided."""
    request = ThermoUploadRequest(
        species_entry={"smiles": "O=C=O", "charge": 0, "multiplicity": 1},
        scientific_origin=origin,
        h298_kj_mol=-393.5,
        s298_j_mol_k=213.8,
    )
    # Schema-level: defaults are not applied for non-computed origins.
    assert request.reference_pressure_bar is None
    assert request.phase is None

    with Session(db_engine) as session, session.begin():
        thermo = persist_thermo_upload(session, request)
        assert thermo.reference_pressure_bar is None
        assert thermo.phase is None


def test_computed_origin_defaults_phase_and_pressure(db_engine) -> None:
    """A computed upload without phase/pressure defaults to gas @ 1 bar."""
    request = ThermoUploadRequest(
        species_entry={"smiles": "CCCCCCCCCCC", "charge": 0, "multiplicity": 1},
        scientific_origin="computed",
        h298_kj_mol=-270.8,
    )
    assert request.reference_pressure_bar == pytest.approx(1.0)
    assert request.phase == PhaseKind.gas

    with Session(db_engine) as session, session.begin():
        thermo = persist_thermo_upload(session, request)
        assert thermo.reference_pressure_bar == pytest.approx(1.0)
        assert thermo.phase == PhaseKind.gas


def test_explicit_reference_state_honored_for_experimental_origin(
    db_engine,
) -> None:
    """Explicit phase/pressure are honored regardless of origin: an
    experimental liquid record keeps its own values, not the QC defaults."""
    request = ThermoUploadRequest(
        species_entry={"smiles": "OCCO", "charge": 0, "multiplicity": 1},
        scientific_origin="experimental",
        h298_kj_mol=-460.0,
        phase=PhaseKind.liquid.value,
        reference_pressure_bar=1.01325,
    )
    assert request.phase == PhaseKind.liquid
    assert request.reference_pressure_bar == pytest.approx(1.01325)

    with Session(db_engine) as session, session.begin():
        thermo = persist_thermo_upload(session, request)
        assert thermo.phase == PhaseKind.liquid
        assert thermo.reference_pressure_bar == pytest.approx(1.01325)


def test_explicit_none_phase_honored_for_computed_origin() -> None:
    """An explicit ``phase=None`` on a computed upload is honored (not
    overwritten with gas): model_fields_set distinguishes omitted from
    explicitly-provided-None."""
    request = ThermoUploadRequest(
        species_entry=dict(_SPECIES_ENTRY),
        scientific_origin="computed",
        h298_kj_mol=-241.8,
        phase=None,
        reference_pressure_bar=None,
    )
    assert request.phase is None
    assert request.reference_pressure_bar is None


def test_thermo_upload_links_existing_statmech(db_engine) -> None:
    """A computed thermo cites its statmech basis via existing_statmech_id."""
    distinct = {"smiles": "CCCCCCCCC", "charge": 0, "multiplicity": 1}
    with Session(db_engine) as session, session.begin():
        species_entry = resolve_species_entry(
            session, SpeciesEntryIdentityPayload(**distinct),
        )
        statmech = _make_statmech(session, species_entry_id=species_entry.id)

        thermo = persist_thermo_upload(
            session,
            ThermoUploadRequest(
                species_entry=dict(distinct),
                scientific_origin="computed",
                h298_kj_mol=-155.0,
                existing_statmech_id=statmech.id,
            ),
        )

        assert thermo.statmech_id == statmech.id
        # Relationship resolves to the same statmech record.
        assert thermo.statmech is not None
        assert thermo.statmech.id == statmech.id
        # Back-ref is populated.
        assert thermo.id in {t.id for t in statmech.thermo_records}


def test_thermo_upload_statmech_not_found_raises_not_found(db_engine) -> None:
    """A missing existing_statmech_id surfaces as 404 (NotFoundError)."""
    from app.api.errors import NotFoundError

    distinct = {"smiles": "CCCCCCCCCC", "charge": 0, "multiplicity": 1}
    with pytest.raises(NotFoundError, match="does not exist"):
        with Session(db_engine) as session, session.begin():
            persist_thermo_upload(
                session,
                ThermoUploadRequest(
                    species_entry=dict(distinct),
                    scientific_origin="computed",
                    h298_kj_mol=-155.0,
                    existing_statmech_id=999_999_999,
                ),
            )


def test_thermo_upload_statmech_wrong_species_entry_raises_422(db_engine) -> None:
    """A statmech owned by a different species entry is rejected (422) and
    the message must not leak internal ids."""
    species_a = {"smiles": "[NH2]", "charge": 0, "multiplicity": 2}
    species_b = {"smiles": "[CH3]", "charge": 0, "multiplicity": 2}

    with pytest.raises(ValueError, match="different species entry") as exc_info:
        with Session(db_engine) as session, session.begin():
            entry_a = resolve_species_entry(
                session, SpeciesEntryIdentityPayload(**species_a),
            )
            statmech_a = _make_statmech(session, species_entry_id=entry_a.id)
            persist_thermo_upload(
                session,
                ThermoUploadRequest(
                    species_entry=dict(species_b),
                    scientific_origin="computed",
                    h298_kj_mol=146.7,
                    existing_statmech_id=statmech_a.id,
                ),
            )
    detail = str(exc_info.value)
    assert "species_entry_id=" not in detail
    assert "id=" not in detail


def test_schema_rejects_negative_enthalpy_formation_0k_uncertainty() -> None:
    """ΔfH°(0 K) uncertainty must be non-negative (Field(ge=0))."""
    with pytest.raises(ValidationError):
        _thermo_request(enthalpy_formation_0k_uncertainty_kj_mol=-0.1)


def test_schema_rejects_non_positive_reference_pressure() -> None:
    """reference_pressure_bar must be strictly positive when provided."""
    with pytest.raises(ValidationError):
        _thermo_request(reference_pressure_bar=0.0)
    with pytest.raises(ValidationError):
        _thermo_request(reference_pressure_bar=-1.0)


def test_schema_rejects_non_positive_existing_statmech_id() -> None:
    """existing_statmech_id must be a positive integer (gt=0)."""
    with pytest.raises(ValidationError):
        _thermo_request(existing_statmech_id=0)
