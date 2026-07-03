from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.app_user import AppUser
from app.db.models.common import ArrheniusAUnits, KineticsUncertaintyKind
from app.db.models.kinetics import Kinetics
from app.db.models.literature import Literature
from app.db.models.reaction import ReactionEntryStructureParticipant
from app.schemas.workflows.kinetics_upload import KineticsUploadRequest
from app.workflows.kinetics import persist_kinetics_upload


def _kinetics_request(**overrides) -> KineticsUploadRequest:
    defaults = {
        "reaction": {
            "reversible": False,
            "reactants": [
                {
                    "species_entry": {
                        "smiles": "[H]",
                        "charge": 0,
                        "multiplicity": 2,
                    }
                },
                {
                    "species_entry": {
                        "smiles": "[H]",
                        "charge": 0,
                        "multiplicity": 2,
                    }
                },
            ],
            "products": [
                {
                    "species_entry": {
                        "smiles": "[H][H]",
                        "charge": 0,
                        "multiplicity": 1,
                    }
                }
            ],
        },
        "scientific_origin": "computed",
        "model_kind": "modified_arrhenius",
        "software_release": {"name": "gaussian", "version": "09", "revision": "D.01"},
        "workflow_tool_release": {"name": "ARC", "version": "1.0.0"},
        "literature": {
            "doi": "10.1000/example.doi",
            "title": "Fallback title if DOI lookup is unavailable",
        },
        "a": 1.23e12,
        "a_units": "cm3_mol_s",
        "n": 0.5,
        "reported_ea": 12.3,
        "reported_ea_units": "kj_mol",
        "tmin_k": 300.0,
        "tmax_k": 2000.0,
        "degeneracy": 2.0,
        "tunneling_model": "eckart",
        "note": "upload note",
    }
    defaults.update(overrides)
    return KineticsUploadRequest(**defaults)


def test_persist_kinetics_upload_resolves_reaction_and_provenance(
    db_engine,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.literature_resolution.fetch_doi_metadata",
        lambda doi: {
            "title": "Hydrogen recombination kinetics",
            "container-title": ["J. Chem. Phys."],
            "issued": 2024,
            "volume": "123",
            "issue": "4",
            "page": "100-110",
            "publisher": "AIP",
            "URL": f"https://doi.org/{doi}",
        },
    )

    with Session(db_engine) as session, session.begin():
        user = AppUser(username="kinetics_tester")
        session.add(user)
        session.flush()
        kinetics = persist_kinetics_upload(
            session, _kinetics_request(), created_by=user.id
        )

        assert kinetics.id is not None
        assert kinetics.reaction_entry_id is not None
        assert kinetics.created_by == user.id
        assert kinetics.software_release is not None
        assert kinetics.software_release.software.name == "Gaussian"
        assert kinetics.workflow_tool_release is not None
        assert kinetics.workflow_tool_release.workflow_tool.name == "ARC"
        assert kinetics.literature is not None
        assert kinetics.literature.title == "Hydrogen recombination kinetics"

        participants = session.scalars(
            select(ReactionEntryStructureParticipant).where(
                ReactionEntryStructureParticipant.reaction_entry_id
                == kinetics.reaction_entry_id
            )
        ).all()
        assert len(participants) == 3


def test_persist_kinetics_upload_reuses_existing_literature_by_doi(
    db_engine,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.literature_resolution.fetch_doi_metadata",
        lambda doi: {"title": "Shared DOI title", "URL": f"https://doi.org/{doi}"},
    )

    request = _kinetics_request()

    with Session(db_engine) as session, session.begin():
        before_kinetics = len(session.scalars(select(Kinetics)).all())
        first = persist_kinetics_upload(session, request)
        after_first_literature = len(session.scalars(select(Literature)).all())
        second = persist_kinetics_upload(session, request)
        after_second_literature = len(session.scalars(select(Literature)).all())

        assert first.literature_id == second.literature_id
        # Second call must not create a duplicate Literature row
        assert after_second_literature == after_first_literature

        kinetics_rows = session.scalars(select(Kinetics)).all()
        assert len(kinetics_rows) == before_kinetics + 2


def test_a_uncertainty_requires_kind() -> None:
    payload = _kinetics_request().model_dump()
    payload["a_uncertainty"] = 2.0  # multiplicative factor, but kind omitted
    with pytest.raises(ValidationError, match="a_uncertainty_kind"):
        KineticsUploadRequest.model_validate(payload)


def test_a_uncertainty_kind_requires_value() -> None:
    payload = _kinetics_request().model_dump()
    payload["a_uncertainty_kind"] = "multiplicative"  # kind without value
    with pytest.raises(ValidationError, match="a_uncertainty_kind"):
        KineticsUploadRequest.model_validate(payload)


def test_multiplicative_a_uncertainty_must_be_ge_1() -> None:
    payload = _kinetics_request().model_dump()
    payload["a_uncertainty"] = 0.5
    payload["a_uncertainty_kind"] = "multiplicative"
    with pytest.raises(ValidationError, match=">= 1.0"):
        KineticsUploadRequest.model_validate(payload)


def test_additive_a_uncertainty_accepts_small_values() -> None:
    payload = _kinetics_request().model_dump()
    payload["a_uncertainty"] = 1e10  # absolute, same units as A
    payload["a_uncertainty_kind"] = "additive"
    request = KineticsUploadRequest.model_validate(payload)
    assert request.a_uncertainty_kind == KineticsUncertaintyKind.additive


def test_persist_kinetics_upload_carries_multiplicative_uncertainty(
    db_engine,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.literature_resolution.fetch_doi_metadata",
        lambda doi: {"title": "stub", "URL": f"https://doi.org/{doi}"},
    )

    payload = _kinetics_request().model_dump()
    payload["a_uncertainty"] = 2.0
    payload["a_uncertainty_kind"] = "multiplicative"
    request = KineticsUploadRequest.model_validate(payload)

    with Session(db_engine) as session, session.begin():
        kinetics = persist_kinetics_upload(session, request)
        assert kinetics.a_uncertainty == 2.0
        assert kinetics.a_uncertainty_kind == KineticsUncertaintyKind.multiplicative


# ---------------------------------------------------------------------------
# DR-0032 Part A: tunneling enum + pressure context (k-infinity designation)
# ---------------------------------------------------------------------------


def _patch_doi(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.literature_resolution.fetch_doi_metadata",
        lambda doi: {"title": "t", "issued": 2024, "URL": f"https://doi.org/{doi}"},
    )


def test_tunneling_model_persists_as_enum(db_engine, monkeypatch) -> None:
    from app.db.models.common import TunnelingModel

    _patch_doi(monkeypatch)
    with Session(db_engine) as session, session.begin():
        kinetics = persist_kinetics_upload(session, _kinetics_request())
        assert kinetics.tunneling_model == TunnelingModel.eckart


def test_pressure_context_high_p_limit_persists(db_engine, monkeypatch) -> None:
    from app.db.models.common import PressureContext

    _patch_doi(monkeypatch)
    with Session(db_engine) as session, session.begin():
        kinetics = persist_kinetics_upload(
            session, _kinetics_request(pressure_context="high_p_limit")
        )
        assert kinetics.pressure_context == PressureContext.high_p_limit
        assert kinetics.pressure_bar is None


def test_apparent_at_pressure_persists_with_pressure(db_engine, monkeypatch) -> None:
    from app.db.models.common import PressureContext

    _patch_doi(monkeypatch)
    with Session(db_engine) as session, session.begin():
        kinetics = persist_kinetics_upload(
            session,
            _kinetics_request(
                pressure_context="apparent_at_pressure", pressure_bar=1.01325
            ),
        )
        assert kinetics.pressure_context == PressureContext.apparent_at_pressure
        assert kinetics.pressure_bar == 1.01325


def test_apparent_at_pressure_without_pressure_bar_rejected() -> None:
    with pytest.raises(ValidationError, match="requires pressure_bar"):
        _kinetics_request(pressure_context="apparent_at_pressure")


# ---------------------------------------------------------------------------
# DR-0032 Part B: falloff (Troe) + third-body efficiencies
# ---------------------------------------------------------------------------


def test_troe_falloff_and_third_body_persist(db_engine, monkeypatch) -> None:
    from app.db.models.kinetics import (
        KineticsFalloff,
        KineticsThirdBodyEfficiency,
    )

    _patch_doi(monkeypatch)
    request = _kinetics_request(
        model_kind="troe",
        falloff={
            "low_a": 1.0e30,
            "low_a_units": "cm6_mol2_s",
            "low_n": -3.0,
            "low_ea_kj_mol": 0.0,
            "troe_alpha": 0.5,
            "troe_t3": 100.0,
            "troe_t1": 1000.0,
            "troe_t2": 5000.0,
        },
        third_body_efficiencies=[
            {"collider": {"smiles": "O", "charge": 0, "multiplicity": 1},
             "efficiency": 6.0},
            {"collider": {"smiles": "[Ar]", "charge": 0, "multiplicity": 1},
             "efficiency": 0.7},
        ],
    )
    with Session(db_engine) as session, session.begin():
        kinetics = persist_kinetics_upload(session, request)
        session.flush()

        fo = session.get(KineticsFalloff, kinetics.id)
        assert fo is not None
        assert fo.low_a == 1.0e30
        assert fo.troe_alpha == 0.5
        assert fo.troe_t2 == 5000.0

        tbs = session.scalars(
            select(KineticsThirdBodyEfficiency).where(
                KineticsThirdBodyEfficiency.kinetics_id == kinetics.id
            )
        ).all()
        assert {round(t.efficiency, 2) for t in tbs} == {6.0, 0.7}


def test_negative_third_body_efficiency_rejected() -> None:
    with pytest.raises(ValidationError):
        _kinetics_request(
            third_body_efficiencies=[
                {"collider": {"smiles": "O", "charge": 0, "multiplicity": 1},
                 "efficiency": -1.0}
            ],
        )


# ---------------------------------------------------------------------------
# DR-0032 Part C: standalone PLOG / Chebyshev fits (no ME network)
# ---------------------------------------------------------------------------


def test_standalone_plog_persists(db_engine, monkeypatch) -> None:
    from app.db.models.kinetics import KineticsPlog

    _patch_doi(monkeypatch)
    request = _kinetics_request(
        model_kind="plog",
        plog_entries=[
            {"entry_index": 1, "pressure_bar": 0.1, "a": 1.0e10, "n": 0.0,
             "ea_kj_mol": 50.0, "a_units": "cm3_mol_s"},
            {"entry_index": 2, "pressure_bar": 1.0, "a": 2.0e10, "n": 0.1,
             "ea_kj_mol": 52.0, "a_units": "cm3_mol_s"},
        ],
    )
    with Session(db_engine) as session, session.begin():
        kinetics = persist_kinetics_upload(session, request)
        session.flush()
        entries = session.scalars(
            select(KineticsPlog)
            .where(KineticsPlog.kinetics_id == kinetics.id)
            .order_by(KineticsPlog.entry_index)
        ).all()
        assert [e.pressure_bar for e in entries] == [0.1, 1.0]


def test_standalone_chebyshev_persists(db_engine, monkeypatch) -> None:
    from app.db.models.kinetics import KineticsChebyshev

    _patch_doi(monkeypatch)
    request = _kinetics_request(
        model_kind="chebyshev",
        chebyshev={
            "n_temperature": 2,
            "n_pressure": 2,
            "tmin_k": 300.0,
            "tmax_k": 2000.0,
            "pmin_bar": 0.01,
            "pmax_bar": 100.0,
            "coefficients": [[1.0, 0.1], [0.2, 0.02]],
        },
    )
    with Session(db_engine) as session, session.begin():
        kinetics = persist_kinetics_upload(session, request)
        session.flush()
        cheb = session.get(KineticsChebyshev, kinetics.id)
        assert cheb is not None
        assert cheb.n_temperature == 2
        assert cheb.coefficients == [[1.0, 0.1], [0.2, 0.02]]


# ---------------------------------------------------------------------------
# Simple third-body (+M) reactions: main-line A-units order is molecularity+1
# ---------------------------------------------------------------------------


def test_simple_third_body_accepts_order3_units() -> None:
    """A + B + M -> C: the [M] term raises the main-line order to 3, so
    order-3 units (cm6_mol2_s) must validate for a two-reactant reaction."""
    payload = _kinetics_request().model_dump()
    payload["is_third_body"] = True
    payload["a_units"] = "cm6_mol2_s"
    request = KineticsUploadRequest.model_validate(payload)
    assert request.is_third_body is True
    assert request.a_units == ArrheniusAUnits.cm6_mol2_s


def test_ordinary_bimolecular_rejects_order3_units() -> None:
    """Without the third-body marker a two-reactant reaction stays order-2,
    so order-3 units are still rejected (validator stays strict)."""
    payload = _kinetics_request().model_dump()
    payload["is_third_body"] = False
    payload["a_units"] = "cm6_mol2_s"
    with pytest.raises(ValidationError, match="bimolecular"):
        KineticsUploadRequest.model_validate(payload)


def test_third_body_rejects_order2_units() -> None:
    """A simple third-body two-reactant reaction is effectively order-3, so
    order-2 units (cm3_mol_s) must be rejected."""
    payload = _kinetics_request().model_dump()
    payload["is_third_body"] = True
    payload["a_units"] = "cm3_mol_s"
    with pytest.raises(ValidationError, match="termolecular"):
        KineticsUploadRequest.model_validate(payload)


def test_falloff_main_line_uses_k_inf_order() -> None:
    """A falloff reaction's main line is k∞ (order = real reactants), so
    order-2 units validate even though a [M]/(+M) collider is present; the
    third-body flag stays False for falloff."""
    payload = _kinetics_request().model_dump()
    payload["model_kind"] = "troe"
    payload["a_units"] = "cm3_mol_s"
    payload["falloff"] = {
        "low_a": 1.0e30,
        "low_a_units": "cm6_mol2_s",
        "low_n": -3.0,
        "low_ea_kj_mol": 0.0,
        "troe_alpha": 0.5,
        "troe_t3": 100.0,
        "troe_t1": 1000.0,
        "troe_t2": 5000.0,
    }
    request = KineticsUploadRequest.model_validate(payload)
    assert request.is_third_body is False
    assert request.a_units == ArrheniusAUnits.cm3_mol_s


def test_persist_simple_third_body_flag(db_engine, monkeypatch) -> None:
    _patch_doi(monkeypatch)
    payload = _kinetics_request().model_dump()
    payload["is_third_body"] = True
    payload["a_units"] = "cm6_mol2_s"
    request = KineticsUploadRequest.model_validate(payload)
    with Session(db_engine) as session, session.begin():
        kinetics = persist_kinetics_upload(session, request)
        session.flush()
        assert kinetics.is_third_body is True
        assert kinetics.a_units == ArrheniusAUnits.cm6_mol2_s
