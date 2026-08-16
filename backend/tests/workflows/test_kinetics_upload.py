from __future__ import annotations

import math

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.error_contract import CodedValueError
from app.api.errors import NotFoundError
from app.db.models.app_user import AppUser
from app.db.models.common import (
    ArrheniusAUnits,
    KineticsDegeneracyConvention,
    KineticsUncertaintyKind,
    ReactionRole,
)
from app.db.models.kinetics import Kinetics
from app.db.models.literature import Literature
from app.db.models.reaction import ReactionEntryStructureParticipant
from app.db.models.statmech import Statmech
from app.db.models.transition_state import TransitionStateEntry
from app.schemas.reads.scientific_kinetics import KineticsReadRequest
from app.schemas.workflows.kinetics_upload import KineticsUploadRequest
from app.schemas.workflows.network_pdep_upload import NetworkPDepUploadRequest
from app.services.scientific_read.kinetics import get_reaction_kinetics
from app.workflows.kinetics import persist_kinetics_upload
from app.workflows.network_pdep import persist_network_pdep_upload


def _shape_kinetics_request(**overrides) -> KineticsUploadRequest:
    """Noncomputed fixture for functional-form and unit-shape validation.

    Computed fixtures must supply resolvable statmech/TS evidence; tests that
    exercise only a rate-law shape use an experimental record deliberately.
    """
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
        "scientific_origin": "experimental",
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
        "note": "upload note",
    }
    defaults.update(overrides)
    return KineticsUploadRequest(**defaults)


_kinetics_request = _shape_kinetics_request


def test_computed_kinetics_without_interpretations_is_accepted_and_warned() -> None:
    """`scientific_origin='computed'` does not mean the statmech lives here.

    A rate read out of a CHEMKIN mechanism, or an Arkane TST result deposited
    without its partition functions, is computed in origin and carries no
    interpretation assignments. Rejecting it would lose a real record, so the
    gap is reported as a warning instead.
    """
    from app.services.provenance_warnings import (
        W_MISSING_KINETICS_INTERPRETATIONS,
        collect_kinetics_content_warnings,
    )

    request = KineticsUploadRequest(
        reaction={"reversible": False, "reactants": [{"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}}], "products": [{"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}}]},
        scientific_origin="computed", a=1.0, a_units="per_s",
    )
    assert request.interpretation_assignments == []
    assert [w.code for w in collect_kinetics_content_warnings(request)] == [
        W_MISSING_KINETICS_INTERPRETATIONS
    ]


def test_partial_interpretation_set_is_still_rejected() -> None:
    """Offering assignments is the reproducibility claim; a partial set is not.

    This is the requirement the origin-scoped rule was standing in for: once a
    record says "these are the partition functions I was built from", every
    participant must be named.
    """
    with pytest.raises(ValidationError, match="missing: \\['product:1'\\]"):
        KineticsUploadRequest(
            reaction={"reversible": False, "reactants": [{"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}}], "products": [{"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}}]},
            scientific_origin="computed", a=1.0, a_units="per_s",
            interpretation_assignments=[
                {
                    "role": "reactant", "participant_index": 1, "statmech_ref": "sm_a",
                    "ensemble_policy": "single_structure",
                    "standard_state_convention": "ideal_gas_1_bar",
                    "degeneracy_interpretation": "reaction_path_degeneracy",
                }
            ],
        )


def test_tunneling_label_without_evidence_is_accepted_and_warned() -> None:
    """``tunneling_model`` is a reported label, not a reproducibility claim.

    A literature rate whose authors state "Wigner tunneling was applied" has
    no imaginary frequency and no artifact for the depositor to attach.
    Demanding typed evidence for a label would force exactly the invention
    this schema exists to prevent.
    """
    from app.services.provenance_warnings import (
        W_MISSING_TUNNELING_APPLICATION,
        collect_kinetics_content_warnings,
    )

    request = KineticsUploadRequest(
        reaction={"reversible": False, "reactants": [{"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}}], "products": [{"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}}]},
        scientific_origin="experimental", a=1.0, a_units="per_s", tunneling_model="wigner",
    )
    assert request.tunneling_application is None
    assert W_MISSING_TUNNELING_APPLICATION in {
        w.code for w in collect_kinetics_content_warnings(request)
    }


def test_tunneling_label_must_match_its_evidence() -> None:
    """Evidence, once offered, must be evidence *for the declared model*."""
    with pytest.raises(ValidationError, match="must match tunneling_model"):
        KineticsUploadRequest(
            reaction={"reversible": False, "reactants": [{"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}}], "products": [{"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}}]},
            scientific_origin="experimental", a=1.0, a_units="per_s",
            tunneling_model="wigner",
            tunneling_application={
                "model": "eckart", "transition_state_entry_ref": "tse_1",
                "imaginary_frequency_cm1": -900.0,
                "reactant_energy_kj_mol": 0.0, "product_energy_kj_mol": -10.0,
                "forward_barrier_kj_mol": 40.0, "reverse_barrier_kj_mol": 50.0,
                "energy_zero_convention": "separated_reactants",
                "energy_correction_convention": "electronic_plus_zpe",
            },
        )


def test_computed_kinetics_round_trips_explicit_subject_assignments_and_wigner(
    db_conn,
) -> None:
    """A deposited computed rate retains every statmech subject and Wigner input."""
    from tests.workflows.test_network_pdep_upload import _parallel_path_payload

    payload = _parallel_path_payload()
    # Add real RRHO source evidence for every kinetics participant; the
    # PDep workflow, rather than a hand-built ORM row, persists all subjects.
    for species_key, freq_key, geometry_key in (
        ("ethylperoxy", "etoo_kinetics_freq", "etoo_geom"),
        ("ethene", "ethene_kinetics_freq", "ethene_geom"),
        ("HO2", "ho2_kinetics_freq", "HO2_geom"),
    ):
        species = next(item for item in payload["species"] if item["key"] == species_key)
        species.setdefault("calculations", []).append(
            {"key": freq_key, "type": "freq", "geometry_key": geometry_key,
             "software_release": {"name": "Gaussian", "version": "16"},
             "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"}, "freq_n_imag": 0}
        )
        species["statmech"] = {
            "statmech_treatment": "rrho",
            "source_calculations": [{"calculation_key": freq_key, "role": "freq"}],
        }

    conventions = {
        "ensemble_policy": "single_structure",
        "standard_state_convention": "ideal_gas_1_bar",
        "degeneracy_interpretation": "reaction_path_degeneracy",
    }

    with Session(db_conn) as session, session.begin():
        session.add(AppUser(id=77, username="computed_kinetics_roundtrip"))
        session.flush()
        persist_network_pdep_upload(session, NetworkPDepUploadRequest(**payload), created_by=77)
        # The HO2-elimination step: one reactant, two products, and TWO
        # distinct saddle points on the SAME reaction entry.
        ts_entry = next(
            entry
            for entry in session.scalars(select(TransitionStateEntry)).all()
            if len(
                [
                    participant
                    for participant in entry.transition_state.reaction_entry.structure_participants
                    if participant.role == ReactionRole.product
                ]
            )
            == 2
        )
        reaction_participants = sorted(
            ts_entry.transition_state.reaction_entry.structure_participants,
            key=lambda participant: (participant.role.value, participant.participant_index),
        )
        species_statmech = {
            statmech.species_entry_id: statmech
            for statmech in session.scalars(
                select(Statmech).where(Statmech.species_entry_id.is_not(None))
            )
        }
        (reactant_sm,) = [
            species_statmech[participant.species_entry_id]
            for participant in reaction_participants
            if participant.role == ReactionRole.reactant
        ]
        product_one_sm, product_two_sm = [
            species_statmech[participant.species_entry_id]
            for participant in reaction_participants
            if participant.role == ReactionRole.product
        ]
        ts_sm = session.scalars(select(Statmech).where(Statmech.transition_state_entry_id == ts_entry.id)).first()

        def _species_content(statmech):
            species = statmech.species_entry.species
            return {
                "species_entry": {
                    "smiles": species.smiles,
                    "charge": species.charge,
                    "multiplicity": species.multiplicity,
                }
            }

        request = KineticsUploadRequest(
            reaction={
                "reversible": True,
                "reactants": [_species_content(reactant_sm)],
                "products": [
                    _species_content(product_one_sm),
                    _species_content(product_two_sm),
                ],
            },
            scientific_origin="computed", a=1.0e12, a_units="per_s",
            interpretation_assignments=[
                {"role": "reactant", "participant_index": 1, "statmech_ref": reactant_sm.public_ref, **conventions},
                {"role": "product", "participant_index": 1, "statmech_ref": product_one_sm.public_ref, **conventions},
                {"role": "product", "participant_index": 2, "statmech_ref": product_two_sm.public_ref, **conventions},
                {"role": "transition_state", "statmech_ref": ts_sm.public_ref, "transition_state_entry_ref": ts_entry.public_ref, **conventions},
            ],
            tunneling_application={"model": "wigner", "transition_state_entry_ref": ts_entry.public_ref, "imaginary_frequency_cm1": -1500.0},
        )
        invalid_payload = request.model_dump(mode="json")
        invalid_payload["interpretation_assignments"][1]["statmech_ref"] = reactant_sm.public_ref
        before = session.scalar(select(func.count(Kinetics.id)))
        # #195: this was a bare ValueError matched on "must belong to its
        # declared reaction participant", so it reached a client as
        # validation_error. It now names the field it refused and carries
        # a code, which is what a client can branch on.
        with pytest.raises(CodedValueError) as mismatched:
            persist_kinetics_upload(session, KineticsUploadRequest.model_validate(invalid_payload), created_by=77)
        assert mismatched.value.code == "kinetics_interpretation_statmech_owner_mismatch"
        assert mismatched.value.context["field"] == "interpretation_assignments[1].statmech_ref"
        assert mismatched.value.context["owner_kind"] == "species_entry"
        assert session.scalar(select(func.count(Kinetics.id))) == before

        wrong_direction = request.model_dump(mode="json")
        wrong_direction["reaction"]["reversible"] = False
        with pytest.raises(ValueError, match="submitted reaction content and direction do not match"):
            persist_kinetics_upload(session, KineticsUploadRequest.model_validate(wrong_direction), created_by=77)
        assert session.scalar(select(func.count(Kinetics.id))) == before

        kinetics = persist_kinetics_upload(session, request, created_by=77)
        response = get_reaction_kinetics(
            session,
            reaction_entry_id=kinetics.reaction_entry_id,
            request=KineticsReadRequest(include={"interpretations"}),
        )
        record = next(item for item in response.records if item.kinetics_id == kinetics.id)
        assert [item.subject_key for item in record.interpretation_assignments] == ["product:1", "product:2", "reactant:1", "transition_state"]
        assert record.tunneling_application.model == "wigner"
        assert record.tunneling_application.transition_state_entry_ref == ts_entry.public_ref


def test_persist_kinetics_upload_resolves_reaction_and_provenance(
    db_conn,
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

    with Session(db_conn) as session, session.begin():
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
    db_conn,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.literature_resolution.fetch_doi_metadata",
        lambda doi: {"title": "Shared DOI title", "URL": f"https://doi.org/{doi}"},
    )

    request = _kinetics_request()

    with Session(db_conn) as session, session.begin():
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


@pytest.mark.parametrize(
    "convention",
    ["already_applied", "not_applied", "unknown"],
)
def test_persist_kinetics_upload_preserves_degeneracy_convention(
    db_conn, convention
) -> None:
    request = _kinetics_request(
        degeneracy_convention=convention,
        literature=None,
        software_release=None,
        workflow_tool_release=None,
    )
    with Session(db_conn) as session, session.begin():
        kinetics = persist_kinetics_upload(session, request)
        assert kinetics.degeneracy_convention.value == convention


def test_kinetics_upload_defaults_degeneracy_convention_to_unknown() -> None:
    request = _kinetics_request()
    assert (
        request.degeneracy_convention
        is KineticsDegeneracyConvention.unknown
    )


@pytest.mark.parametrize("value", [None, 1.0e-12, 1, 2.5])
def test_kinetics_upload_accepts_optional_finite_positive_degeneracy(value) -> None:
    assert _kinetics_request(degeneracy=value).degeneracy == value


@pytest.mark.parametrize(
    ("value", "error_type"),
    [
        (0, "greater_than"),
        (-1.0, "greater_than"),
        (math.nan, "finite_number"),
        (math.inf, "finite_number"),
        (-math.inf, "finite_number"),
    ],
)
def test_kinetics_upload_rejects_non_positive_or_nonfinite_degeneracy(
    value,
    error_type,
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        _kinetics_request(degeneracy=value)

    assert [(error["loc"], error["type"]) for error in exc_info.value.errors()] == [
        (("degeneracy",), error_type)
    ]


def test_persist_kinetics_upload_carries_multiplicative_uncertainty(
    db_conn,
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

    with Session(db_conn) as session, session.begin():
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


def test_tunneling_model_defaults_to_null_without_application(db_conn, monkeypatch) -> None:
    _patch_doi(monkeypatch)
    with Session(db_conn) as session, session.begin():
        kinetics = persist_kinetics_upload(session, _kinetics_request())
        assert kinetics.tunneling_model is None


def test_pressure_context_high_p_limit_persists(db_conn, monkeypatch) -> None:
    from app.db.models.common import PressureContext

    _patch_doi(monkeypatch)
    with Session(db_conn) as session, session.begin():
        kinetics = persist_kinetics_upload(
            session, _kinetics_request(pressure_context="high_p_limit")
        )
        assert kinetics.pressure_context == PressureContext.high_p_limit
        assert kinetics.pressure_bar is None


def test_apparent_at_pressure_persists_with_pressure(db_conn, monkeypatch) -> None:
    from app.db.models.common import PressureContext

    _patch_doi(monkeypatch)
    with Session(db_conn) as session, session.begin():
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


def test_troe_falloff_and_third_body_persist(db_conn, monkeypatch) -> None:
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
    with Session(db_conn) as session, session.begin():
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


def test_standalone_plog_persists(db_conn, monkeypatch) -> None:
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
    with Session(db_conn) as session, session.begin():
        kinetics = persist_kinetics_upload(session, request)
        session.flush()
        entries = session.scalars(
            select(KineticsPlog)
            .where(KineticsPlog.kinetics_id == kinetics.id)
            .order_by(KineticsPlog.entry_index)
        ).all()
        assert [e.pressure_bar for e in entries] == [0.1, 1.0]


def test_standalone_chebyshev_persists(db_conn, monkeypatch) -> None:
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
    with Session(db_conn) as session, session.begin():
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


def test_persist_simple_third_body_flag(db_conn, monkeypatch) -> None:
    _patch_doi(monkeypatch)
    payload = _kinetics_request().model_dump()
    payload["is_third_body"] = True
    payload["a_units"] = "cm6_mol2_s"
    request = KineticsUploadRequest.model_validate(payload)
    with Session(db_conn) as session, session.begin():
        kinetics = persist_kinetics_upload(session, request)
        session.flush()
        assert kinetics.is_third_body is True
        assert kinetics.a_units == ArrheniusAUnits.cm6_mol2_s


# ---------------------------------------------------------------------------
# DR-0036: direction, sum-of-Arrhenius (multi_arrhenius), network bridge
# ---------------------------------------------------------------------------


def test_forward_and_reverse_kinetics_persist_distinctly(db_conn, monkeypatch):
    """Forward and reverse fits persist with distinct direction (previously
    indistinguishable). The single-reaction_entry coexistence case is covered
    at the read layer in test_get_reaction_kinetics."""
    from app.db.models.common import KineticsDirection

    _patch_doi(monkeypatch)
    with Session(db_conn) as session, session.begin():
        fwd = persist_kinetics_upload(
            session, _kinetics_request(direction="forward")
        )
        rev = persist_kinetics_upload(
            session, _kinetics_request(direction="reverse")
        )
        assert fwd.id != rev.id
        assert fwd.direction == KineticsDirection.forward
        assert rev.direction == KineticsDirection.reverse


def test_direction_defaults_to_null(db_conn, monkeypatch):
    _patch_doi(monkeypatch)
    with Session(db_conn) as session, session.begin():
        k = persist_kinetics_upload(session, _kinetics_request())
        assert k.direction is None


def test_multi_arrhenius_persists_summed_terms(db_conn, monkeypatch):
    from app.db.models.kinetics import KineticsArrheniusEntry

    _patch_doi(monkeypatch)
    payload = _kinetics_request().model_dump()
    payload["model_kind"] = "multi_arrhenius"
    payload.pop("a")  # scalar A must be unset for a DUPLICATE sum
    payload["arrhenius_entries"] = [
        {"entry_index": 1, "a": 1.0e12, "a_units": "cm3_mol_s", "n": 0.0,
         "reported_ea": 50.0, "reported_ea_units": "kj_mol"},
        {"entry_index": 2, "a": 3.0e11, "a_units": "cm3_mol_s", "n": 0.5,
         "reported_ea": 60.0, "reported_ea_units": "kj_mol"},
    ]
    request = KineticsUploadRequest.model_validate(payload)

    with Session(db_conn) as session, session.begin():
        k = persist_kinetics_upload(session, request)
        session.flush()
        assert k.a is None
        entries = session.scalars(
            select(KineticsArrheniusEntry)
            .where(KineticsArrheniusEntry.kinetics_id == k.id)
            .order_by(KineticsArrheniusEntry.entry_index)
        ).all()
        assert [e.entry_index for e in entries] == [1, 2]
        assert [e.a for e in entries] == [1.0e12, 3.0e11]
        assert [e.ea_kj_mol for e in entries] == [50.0, 60.0]


def test_multi_arrhenius_requires_two_terms():
    payload = _kinetics_request().model_dump()
    payload["model_kind"] = "multi_arrhenius"
    payload.pop("a")
    payload["arrhenius_entries"] = [
        {"entry_index": 1, "a": 1.0e12, "a_units": "cm3_mol_s"}
    ]
    with pytest.raises(ValidationError, match="at least two"):
        KineticsUploadRequest.model_validate(payload)


def test_multi_arrhenius_rejects_scalar_a():
    payload = _kinetics_request().model_dump()
    payload["model_kind"] = "multi_arrhenius"
    payload["arrhenius_entries"] = [
        {"entry_index": 1, "a": 1.0e12, "a_units": "cm3_mol_s"},
        {"entry_index": 2, "a": 2.0e12, "a_units": "cm3_mol_s"},
    ]
    with pytest.raises(ValidationError, match="must not set the scalar"):
        KineticsUploadRequest.model_validate(payload)


def test_arrhenius_entries_require_multi_arrhenius_kind():
    payload = _kinetics_request().model_dump()
    payload["arrhenius_entries"] = [
        {"entry_index": 1, "a": 1.0e12, "a_units": "cm3_mol_s"},
        {"entry_index": 2, "a": 2.0e12, "a_units": "cm3_mol_s"},
    ]
    with pytest.raises(ValidationError, match="only valid when"):
        KineticsUploadRequest.model_validate(payload)


def _make_network_kinetics(session):
    """Minimal network_kinetics row for the bridge tests."""
    from app.db.models.common import (
        NetworkChannelKind,
        NetworkKineticsModelKind,
        NetworkStateKind,
    )
    from app.db.models.network import Network
    from app.db.models.network_pdep import (
        NetworkChannel,
        NetworkKinetics,
        NetworkSolve,
        NetworkState,
    )

    net = Network(name="bridge-net")
    session.add(net)
    session.flush()
    src = NetworkState(
        network_id=net.id, kind=NetworkStateKind.well, composition_hash="a" * 64
    )
    sink = NetworkState(
        network_id=net.id, kind=NetworkStateKind.bimolecular,
        composition_hash="b" * 64,
    )
    session.add_all([src, sink])
    session.flush()
    channel = NetworkChannel(
        network_id=net.id, source_state_id=src.id, sink_state_id=sink.id,
        kind=NetworkChannelKind.dissociation,
        channel_key="bridge_dissociation_path",
    )
    solve = NetworkSolve(network_id=net.id)
    session.add_all([channel, solve])
    session.flush()
    nk = NetworkKinetics(
        channel_id=channel.id, solve_id=solve.id,
        model_kind=NetworkKineticsModelKind.plog,
    )
    session.add(nk)
    session.flush()
    return nk


def test_network_bridge_resolves(db_conn, monkeypatch):
    _patch_doi(monkeypatch)
    # Other tests select NetworkKinetics without a network filter, so a
    # committed bridge row here would pollute them; ``db_conn`` is what
    # keeps this one out of their way.
    with Session(db_conn) as session:
        nk = _make_network_kinetics(session)
        k = persist_kinetics_upload(
            session,
            _kinetics_request(network_kinetics_ref=nk.public_ref),
        )
        assert k.network_kinetics_id == nk.id
        assert k.network_kinetics is not None
        assert k.network_kinetics.id == nk.id
        session.rollback()


def test_network_bridge_unknown_id_rejected(db_conn, monkeypatch):
    """A ref naming no row is a 404 with a code, not a bare ValueError.

    Changed in #204: this used to match a substring of the message, which
    is the assertion that survives any status and any code. It now asserts
    the ``code`` and the structured ``context`` a client actually branches
    on. ``NotFoundError`` is not a ``ValueError``, so the old
    ``pytest.raises(ValueError, ...)`` would have gone red rather than
    silently passing -- which is how this one was found.
    """
    _patch_doi(monkeypatch)
    with Session(db_conn) as session, session.begin():
        with pytest.raises(NotFoundError) as refused:
            persist_kinetics_upload(
                session,
                _kinetics_request(network_kinetics_ref="nkin_missing"),
            )
    assert refused.value.code == "unknown_network_kinetics_ref"
    assert refused.value.context == {
        "field": "network_kinetics_ref",
        "kind": "network_kinetics",
        "ref": "nkin_missing",
    }


# ---------------------------------------------------------------------------
# Sibling A-factor units vs molecularity (multi_arrhenius / PLOG / falloff k0)
# ---------------------------------------------------------------------------


def _multi_arrhenius_payload(entries: list[dict]) -> dict:
    payload = _kinetics_request().model_dump()
    payload["model_kind"] = "multi_arrhenius"
    payload.pop("a")  # scalar A must be unset for a DUPLICATE sum
    payload["arrhenius_entries"] = entries
    return payload


def test_multi_arrhenius_term_wrong_order_units_rejected():
    """A bimolecular reaction's summed terms are the same rate, so a term with
    unimolecular (per_s) units is rejected and the offending term is named."""
    payload = _multi_arrhenius_payload([
        {"entry_index": 1, "a": 1.0e12, "a_units": "cm3_mol_s"},
        {"entry_index": 2, "a": 3.0e11, "a_units": "per_s"},
    ])
    with pytest.raises(ValidationError, match=r"arrhenius_entries\[2\].a_units"):
        KineticsUploadRequest.model_validate(payload)


def test_multi_arrhenius_valid_term_units_pass():
    """Every term carrying the correct bimolecular units validates."""
    payload = _multi_arrhenius_payload([
        {"entry_index": 1, "a": 1.0e12, "a_units": "cm3_mol_s"},
        {"entry_index": 2, "a": 3.0e11, "a_units": "cm3_mol_s"},
    ])
    request = KineticsUploadRequest.model_validate(payload)
    assert [e.a_units for e in request.arrhenius_entries] == [
        ArrheniusAUnits.cm3_mol_s,
        ArrheniusAUnits.cm3_mol_s,
    ]


def test_multi_arrhenius_term_none_units_allowed():
    """A term omitting a_units is skipped (units are optional)."""
    payload = _multi_arrhenius_payload([
        {"entry_index": 1, "a": 1.0e12, "a_units": "cm3_mol_s"},
        {"entry_index": 2, "a": 3.0e11},  # no a_units
    ])
    request = KineticsUploadRequest.model_validate(payload)
    assert request.arrhenius_entries[1].a_units is None


def test_plog_entry_wrong_order_units_rejected():
    """A PLOG entry's A is the reaction rate at that pressure, so a
    termolecular (order-3) unit on a bimolecular reaction is rejected and the
    entry is named."""
    payload = _kinetics_request().model_dump()
    payload["model_kind"] = "plog"
    payload["plog_entries"] = [
        {"entry_index": 1, "pressure_bar": 0.1, "a": 1.0e10, "a_units": "cm3_mol_s"},
        {"entry_index": 2, "pressure_bar": 1.0, "a": 2.0e10, "a_units": "cm6_mol2_s"},
    ]
    with pytest.raises(ValidationError, match=r"plog_entries\[2\].a_units"):
        KineticsUploadRequest.model_validate(payload)


# ---------------------------------------------------------------------------
# Naming the offending term must not cost the term's code
# ---------------------------------------------------------------------------
#
# ``_validate_a_units_named`` prefixes the field path onto the check's own
# sentence. ``CodedValidationError`` is a ``ValueError``, so re-raising a
# plain ``ValueError`` to add that prefix destroyed the declared code at the
# raise site -- upstream of every promotion rule #159/#161/#164 built. These
# pin both halves of the repair: the code survives, and the sentence does not
# move by a byte.

#: What the check itself says for order-3 units on a bimolecular reaction.
#: Written out rather than derived so that rewording the check fails here
#: instead of silently agreeing with itself.
_ORDER_3_ON_BIMOLECULAR = (
    "a_units 'cm6_mol2_s' is incompatible with bimolecular reaction "
    "(molecularity=2). Expected one of: "
    "['cm3_mol_s', 'cm3_molecule_s', 'm3_mol_s']."
)


def _sole_error(payload: dict) -> dict:
    """The single Pydantic error the payload produces.

    More than one and the envelope falls back by design, which would make
    every assertion below pass for the wrong reason.
    """
    with pytest.raises(ValidationError) as caught:
        KineticsUploadRequest.model_validate(payload)
    errors = caught.value.errors()
    assert len(errors) == 1, errors
    return errors[0]


def _multi_arrhenius_order_3_payload() -> dict:
    return _multi_arrhenius_payload([
        {"entry_index": 1, "a": 1.0e12, "a_units": "cm3_mol_s"},
        {"entry_index": 2, "a": 3.0e11, "a_units": "cm6_mol2_s"},
    ])


def _plog_order_3_payload() -> dict:
    payload = _kinetics_request().model_dump()
    payload["model_kind"] = "plog"
    payload["plog_entries"] = [
        {"entry_index": 1, "pressure_bar": 0.1, "a": 1.0e10, "a_units": "cm3_mol_s"},
        {"entry_index": 2, "pressure_bar": 1.0, "a": 2.0e10, "a_units": "cm6_mol2_s"},
    ]
    return payload


@pytest.mark.parametrize(
    ("field", "build"),
    [
        ("arrhenius_entries[2].a_units", _multi_arrhenius_order_3_payload),
        ("plog_entries[2].a_units", _plog_order_3_payload),
    ],
)
def test_a_named_sibling_a_factor_keeps_its_code_and_its_sentence(field, build):
    """Every call site of the wrapper, not just the one that was reported.

    ``entry_index`` is 2 in both cases so a fix that hard-coded index 1 --
    or that named the wrong term -- is visible here rather than plausible.
    """
    error = _sole_error(build())
    declared = error["ctx"]["error"]

    # The code the check declared reaches the envelope's reader as an
    # attribute of the exception. Nothing is parsed out of the sentence.
    assert declared.code == "arrhenius_a_units_molecularity_mismatch"
    # Byte-for-byte what the lossy ``raise ValueError(f"{field}: {exc}")``
    # produced: field path, ": ", then the check's own prose unchanged.
    assert str(declared) == f"{field}: {_ORDER_3_ON_BIMOLECULAR}"
    # The field is also machine-readable, alongside the facts the check
    # already attached.
    assert declared.context["field"] == field
    assert declared.context["molecularity"] == 2
    assert declared.context["a_units"] == "cm6_mol2_s"


def test_the_falloff_k0_call_site_keeps_its_code_too():
    """The third call site, whose molecularity is k∞'s plus one.

    Kept separate from the parametrized pair because its expected sentence
    is a different one -- order-2 units judged against order 3 -- and
    folding it in would have meant asserting a sentence neither case
    actually produces.
    """
    payload = _kinetics_request().model_dump()
    payload["model_kind"] = "troe"
    payload["a_units"] = "cm3_mol_s"
    payload["falloff"] = {
        "low_a": 1.0e30,
        "low_a_units": "cm3_mol_s",  # order-2, but k0 must be order-3
        "low_n": -3.0,
        "low_ea_kj_mol": 0.0,
        "troe_alpha": 0.5,
        "troe_t3": 100.0,
        "troe_t1": 1000.0,
        "troe_t2": 5000.0,
    }
    declared = _sole_error(payload)["ctx"]["error"]
    assert declared.code == "arrhenius_a_units_molecularity_mismatch"
    assert str(declared) == (
        "falloff.low_a_units: a_units 'cm3_mol_s' is incompatible with "
        "termolecular reaction (molecularity=3). Expected one of: "
        "['cm6_mol2_s', 'cm6_molecule2_s', 'm6_mol2_s']."
    )
    assert declared.context["field"] == "falloff.low_a_units"


def test_the_second_code_the_wrapper_can_carry_also_survives():
    """``validate_a_units_for_molecularity`` declares two codes, not one.

    The falloff call site asks for ``len(reactants) + 1``, so a termolecular
    reaction asks about molecularity 4 -- which the units table does not
    define, and which the check refuses with
    ``unsupported_reaction_molecularity`` rather than with the mismatch
    code. It travelled through the same lossy wrapper, so it was lost the
    same way and is repaired by the same change. Pinned separately because
    a fix that special-cased the one code in the bug report would still
    pass every other test here.
    """
    payload = _kinetics_request().model_dump()
    payload["reaction"]["reactants"] = payload["reaction"]["reactants"] + [
        payload["reaction"]["reactants"][0]
    ]
    payload["model_kind"] = "troe"
    payload["a_units"] = "cm6_mol2_s"  # order 3, correct for three reactants
    payload["falloff"] = {
        "low_a": 1.0e30,
        "low_a_units": "cm6_mol2_s",  # k0 would need order 4, which does not exist
        "low_n": -3.0,
        "low_ea_kj_mol": 0.0,
        "troe_alpha": 0.5,
        "troe_t3": 100.0,
        "troe_t1": 1000.0,
        "troe_t2": 5000.0,
    }
    declared = _sole_error(payload)["ctx"]["error"]
    assert declared.code == "unsupported_reaction_molecularity"
    assert str(declared) == (
        "falloff.low_a_units: Unsupported reaction molecularity: 4. "
        "Expected 1 (unimolecular), 2 (bimolecular), or 3 (termolecular)."
    )
    assert declared.context["field"] == "falloff.low_a_units"
    assert declared.context["molecularity"] == 4


def test_the_field_path_is_not_itself_promotable_as_a_code():
    """The #159 trap, checked rather than assumed.

    The repaired message still *starts* with a snake_case token followed by
    a colon -- ``a_units``, inside ``plog_entries[2].a_units`` -- which is
    exactly the shape #159 narrowed promotion to. Promotion is gated on the
    catalogue, so a field path cannot be published as a code; if that gate
    ever loosened, this fix would be the thing that fabricated one.
    """
    from app.api.error_contract import MESSAGE_PREFIX_CODES, validation_detail_code

    for field in (
        "arrhenius_entries[2].a_units",
        "plog_entries[2].a_units",
        "falloff.low_a_units",
        "a_units",
    ):
        assert field not in MESSAGE_PREFIX_CODES

    payload = _kinetics_request().model_dump()
    payload["model_kind"] = "plog"
    payload["plog_entries"] = [
        {"entry_index": 2, "pressure_bar": 1.0, "a": 2.0e10, "a_units": "cm6_mol2_s"},
    ]
    with pytest.raises(ValidationError) as caught:
        KineticsUploadRequest.model_validate(payload)
    promoted = validation_detail_code(caught.value.errors(), fallback="validation_error")
    assert promoted == "arrhenius_a_units_molecularity_mismatch"


def test_falloff_low_a_units_wrong_order_rejected():
    """k0 is one order higher than k∞; a bimolecular reaction's low-pressure
    limit must be order-3, so order-2 low_a_units is rejected and named."""
    payload = _kinetics_request().model_dump()
    payload["model_kind"] = "troe"
    payload["a_units"] = "cm3_mol_s"
    payload["falloff"] = {
        "low_a": 1.0e30,
        "low_a_units": "cm3_mol_s",  # order-2, but k0 must be order-3
        "low_n": -3.0,
        "low_ea_kj_mol": 0.0,
        "troe_alpha": 0.5,
        "troe_t3": 100.0,
        "troe_t1": 1000.0,
        "troe_t2": 5000.0,
    }
    with pytest.raises(ValidationError, match=r"falloff.low_a_units"):
        KineticsUploadRequest.model_validate(payload)


def test_falloff_low_a_units_order_plus_one_passes():
    """A bimolecular falloff reaction's k0 at order-3 (cm6_mol2_s) validates."""
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
    assert request.falloff.low_a_units == ArrheniusAUnits.cm6_mol2_s


def test_falloff_low_a_units_none_allowed():
    """Omitting low_a_units skips the k0 order check (units are optional)."""
    payload = _kinetics_request().model_dump()
    payload["model_kind"] = "troe"
    payload["a_units"] = "cm3_mol_s"
    payload["falloff"] = {
        "low_a": 1.0e30,
        "low_n": -3.0,
        "low_ea_kj_mol": 0.0,
        "troe_alpha": 0.5,
        "troe_t3": 100.0,
        "troe_t1": 1000.0,
        "troe_t2": 5000.0,
    }
    request = KineticsUploadRequest.model_validate(payload)
    assert request.falloff.low_a_units is None


def test_falloff_with_third_body_flag_no_double_count():
    """Regression: a falloff reaction flagged is_third_body must NOT add the
    simple-third-body +1 on top of the k0 +1. Main line stays k∞ at order 2
    (cm3_mol_s) and k0 is exactly order 3 (cm6_mol2_s) -- both must PASS."""
    payload = _kinetics_request().model_dump()
    payload["model_kind"] = "troe"
    payload["is_third_body"] = True
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
    assert request.is_third_body is True
    assert request.a_units == ArrheniusAUnits.cm3_mol_s
    assert request.falloff.low_a_units == ArrheniusAUnits.cm6_mol2_s


def test_plog_entry_correct_order_units_pass():
    """A bimolecular PLOG whose entries carry the matching order-2 units
    validates (accept counterpart to the wrong-order reject test)."""
    payload = _kinetics_request().model_dump()
    payload["model_kind"] = "plog"
    payload["plog_entries"] = [
        {"entry_index": 1, "pressure_bar": 0.1, "a": 1.0e10, "a_units": "cm3_mol_s"},
        {"entry_index": 2, "pressure_bar": 1.0, "a": 2.0e10, "a_units": "cm3_mol_s"},
    ]
    request = KineticsUploadRequest.model_validate(payload)
    assert [e.a_units for e in request.plog_entries] == [
        ArrheniusAUnits.cm3_mol_s,
        ArrheniusAUnits.cm3_mol_s,
    ]


def test_multi_arrhenius_third_body_terms_use_order_plus_one():
    """A simple third-body (+M) bimolecular multi_arrhenius raises the
    main-line order to 3, so each summed term's a_units must be order 3
    (cm6_mol2_s) -- correct units PASS."""
    payload = _multi_arrhenius_payload([
        {"entry_index": 1, "a": 1.0e12, "a_units": "cm6_mol2_s"},
        {"entry_index": 2, "a": 3.0e11, "a_units": "cm6_mol2_s"},
    ])
    payload["is_third_body"] = True
    payload["a_units"] = None  # scalar rate lives in the summed terms
    request = KineticsUploadRequest.model_validate(payload)
    assert [e.a_units for e in request.arrhenius_entries] == [
        ArrheniusAUnits.cm6_mol2_s,
        ArrheniusAUnits.cm6_mol2_s,
    ]


def test_multi_arrhenius_third_body_rejects_order2_term():
    """With is_third_body the effective order is 3, so an order-2 term
    (cm3_mol_s) is rejected and the offending term is named."""
    payload = _multi_arrhenius_payload([
        {"entry_index": 1, "a": 1.0e12, "a_units": "cm6_mol2_s"},
        {"entry_index": 2, "a": 3.0e11, "a_units": "cm3_mol_s"},
    ])
    payload["is_third_body"] = True
    payload["a_units"] = None  # scalar rate lives in the summed terms
    with pytest.raises(ValidationError, match=r"arrhenius_entries\[2\].a_units"):
        KineticsUploadRequest.model_validate(payload)


# ---------------------------------------------------------------------------
# Computed-origin interpretation completeness and typed conventions
# ---------------------------------------------------------------------------


_CONVENTIONS = {
    "ensemble_policy": "single_structure",
    "standard_state_convention": "ideal_gas_1_bar",
    "degeneracy_interpretation": "reaction_path_degeneracy",
}

_BIMOLECULAR_REACTION = {
    "reversible": False,
    "reactants": [
        {"species_entry": {"smiles": "[CH3]", "charge": 0, "multiplicity": 2}},
        {"species_entry": {"smiles": "[OH]", "charge": 0, "multiplicity": 2}},
    ],
    "products": [
        {"species_entry": {"smiles": "CO", "charge": 0, "multiplicity": 1}},
    ],
}


def test_computed_kinetics_rejects_partial_interpretation_set() -> None:
    """A partial set looks like provenance while accounting for nothing.

    ``CH3 + OH -> CH3OH`` with only ``reactant:1`` named leaves the OH and the
    methanol partition functions entirely unstated.
    """
    with pytest.raises(ValidationError, match="missing: \\['product:1', 'reactant:2'\\]"):
        KineticsUploadRequest(
            reaction=_BIMOLECULAR_REACTION,
            scientific_origin="computed",
            a=1.0e13,
            a_units="cm3_mol_s",
            interpretation_assignments=[
                {"role": "reactant", "participant_index": 1, "statmech_ref": "sm_x", **_CONVENTIONS},
            ],
        )


def test_computed_kinetics_accepts_a_complete_interpretation_set() -> None:
    request = KineticsUploadRequest(
        reaction=_BIMOLECULAR_REACTION,
        scientific_origin="computed",
        a=1.0e13,
        a_units="cm3_mol_s",
        interpretation_assignments=[
            {"role": "reactant", "participant_index": 1, "statmech_ref": "sm_a", **_CONVENTIONS},
            {"role": "reactant", "participant_index": 2, "statmech_ref": "sm_b", **_CONVENTIONS},
            {"role": "product", "participant_index": 1, "statmech_ref": "sm_c", **_CONVENTIONS},
        ],
    )
    assert len(request.interpretation_assignments) == 3


def test_computed_kinetics_with_tunneling_requires_a_ts_interpretation() -> None:
    """A tunneling correction is applied to a TS, so the TS must be named."""
    with pytest.raises(ValidationError, match="missing: \\['transition_state'\\]"):
        KineticsUploadRequest(
            reaction=_BIMOLECULAR_REACTION,
            scientific_origin="computed",
            a=1.0e13,
            a_units="cm3_mol_s",
            interpretation_assignments=[
                {"role": "reactant", "participant_index": 1, "statmech_ref": "sm_a", **_CONVENTIONS},
                {"role": "reactant", "participant_index": 2, "statmech_ref": "sm_b", **_CONVENTIONS},
                {"role": "product", "participant_index": 1, "statmech_ref": "sm_c", **_CONVENTIONS},
            ],
            tunneling_application={
                "model": "wigner",
                "transition_state_entry_ref": "tse_1",
                "imaginary_frequency_cm1": -900.0,
            },
        )


def test_participant_index_is_bounded_at_the_schema_boundary() -> None:
    """An out-of-range slot is a 422, not a late persistence-time failure."""
    with pytest.raises(ValidationError, match="outside the declared reactant list"):
        KineticsUploadRequest(
            reaction=_BIMOLECULAR_REACTION,
            scientific_origin="computed",
            a=1.0e13,
            a_units="cm3_mol_s",
            interpretation_assignments=[
                {"role": "reactant", "participant_index": 99, "statmech_ref": "sm_a", **_CONVENTIONS},
            ],
        )


def test_interpretation_conventions_reject_free_text() -> None:
    with pytest.raises(ValidationError):
        KineticsUploadRequest(
            reaction=_BIMOLECULAR_REACTION,
            scientific_origin="computed",
            a=1.0e13,
            a_units="cm3_mol_s",
            interpretation_assignments=[
                {
                    "role": "reactant", "participant_index": 1, "statmech_ref": "sm_a",
                    "ensemble_policy": "lowest-energy",
                    "standard_state_convention": "1-bar",
                    "degeneracy_interpretation": "already-applied",
                },
            ],
        )


def test_interpretation_other_convention_requires_a_note() -> None:
    with pytest.raises(ValidationError, match="convention_note is required"):
        KineticsUploadRequest(
            reaction=_BIMOLECULAR_REACTION,
            scientific_origin="computed",
            a=1.0e13,
            a_units="cm3_mol_s",
            interpretation_assignments=[
                {
                    "role": "reactant", "participant_index": 1, "statmech_ref": "sm_a",
                    "ensemble_policy": "other",
                    "standard_state_convention": "ideal_gas_1_bar",
                    "degeneracy_interpretation": "reaction_path_degeneracy",
                },
            ],
        )


def test_other_tunneling_model_must_stay_replayable() -> None:
    """``{"model": "other"}`` alone is an unfalsifiable claim, not evidence."""
    base = {
        "reaction": _BIMOLECULAR_REACTION,
        "scientific_origin": "experimental",
        "a": 1.0e13,
        "a_units": "cm3_mol_s",
    }
    with pytest.raises(ValidationError, match="requires model_identifier"):
        KineticsUploadRequest(
            **base,
            tunneling_application={"model": "other", "transition_state_entry_ref": "tse_1"},
        )
    with pytest.raises(ValidationError, match="requires a result artifact"):
        KineticsUploadRequest(
            **base,
            tunneling_application={
                "model": "other",
                "transition_state_entry_ref": "tse_1",
                "model_identifier": "zero_curvature_tunneling",
            },
        )
    request = KineticsUploadRequest(
        **base,
        tunneling_application={
            "model": "other",
            "transition_state_entry_ref": "tse_1",
            "model_identifier": "zero_curvature_tunneling",
            "result_artifact_calculation_ref": "calc_1",
            "result_artifact_sha256": "a" * 64,
        },
    )
    assert request.tunneling_application.model_identifier == "zero_curvature_tunneling"
    # The label is normalised from the evidence block at parse time.
    assert request.tunneling_model.value == "other"


def test_model_identifier_is_a_machine_token_and_only_for_other() -> None:
    base = {
        "reaction": _BIMOLECULAR_REACTION,
        "scientific_origin": "experimental",
        "a": 1.0e13,
        "a_units": "cm3_mol_s",
    }
    with pytest.raises(ValidationError):
        KineticsUploadRequest(
            **base,
            tunneling_application={
                "model": "other",
                "transition_state_entry_ref": "tse_1",
                "model_identifier": "Zero Curvature Tunneling",
                "result_artifact_calculation_ref": "calc_1",
                "result_artifact_sha256": "a" * 64,
            },
        )
    with pytest.raises(ValidationError, match="only valid when model='other'"):
        KineticsUploadRequest(
            **base,
            tunneling_application={
                "model": "wigner",
                "transition_state_entry_ref": "tse_1",
                "imaginary_frequency_cm1": -900.0,
                "model_identifier": "wigner_1932",
            },
        )


def test_computed_statmech_without_source_calculations_cannot_back_a_rate(
    db_conn,
) -> None:
    """Deposit-time statmech only warns; a rate that DEPENDS on it must not.

    This is where "reproducible computed partition function" is actually
    claimed, so this is where the source link is required. A monatomic species
    or an experimental record may legitimately name no calculation — but a
    computed statmech that a computed rate coefficient is built from may not.
    """
    from contextlib import contextmanager

    from app.db.models.statmech import Statmech
    from tests.services.scientific_read._factories import (
        make_species,
        make_species_entry,
        next_inchi_key,
    )

    @contextmanager
    def _rolled_back_session(engine):
        opened = Session(bind=engine, expire_on_commit=False)
        try:
            yield opened
        finally:
            opened.close()

    with _rolled_back_session(db_conn) as session:
        # One species entry: the H atom is both sides of this trivial rate, so
        # the workflow resolves both participants onto the entry that owns the
        # statmech under test.
        atom = make_species(session, smiles="[H]", inchi_key=next_inchi_key("USEA"))
        atom_entry = make_species_entry(session, atom)
        bare = Statmech(
            species_entry_id=atom_entry.id, scientific_origin="computed"
        )
        session.add(bare)
        session.flush()

        request = KineticsUploadRequest(
            reaction={
                "reversible": False,
                "reactants": [
                    {"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}}
                ],
                "products": [
                    {"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}}
                ],
            },
            scientific_origin="computed",
            a=1.0,
            a_units="per_s",
            interpretation_assignments=[
                {
                    "role": "reactant", "participant_index": 1,
                    "statmech_ref": bare.public_ref, **_CONVENTIONS,
                },
                {
                    "role": "product", "participant_index": 1,
                    "statmech_ref": bare.public_ref, **_CONVENTIONS,
                },
            ],
        )
        with pytest.raises(ValueError, match="no source calculations"):
            persist_kinetics_upload(session, request, created_by=None)


# ---------------------------------------------------------------------------
# NB6: PLOG / Chebyshev are not third-body reactions
# ---------------------------------------------------------------------------


def _plog_bimolecular(**overrides) -> dict:
    """A bimolecular PLOG payload; ``a_units`` overridable to probe the order."""
    base: dict = {
        "reaction": _BIMOLECULAR_REACTION,
        "scientific_origin": "experimental",
        "model_kind": "plog",
        "plog_entries": [
            {
                "entry_index": 1,
                "pressure_bar": 1.0,
                "a": 1.0e13,
                "a_units": "cm3_mol_s",
                "n": 0.0,
                "ea_kj_mol": 40.0,
            }
        ],
    }
    base.update(overrides)
    return base


def test_plog_rejects_is_third_body() -> None:
    """CHEMKIN has no third-body PLOG: the pressure dependence is already fit."""
    with pytest.raises(ValidationError, match="cannot be a third-body reaction"):
        KineticsUploadRequest(**_plog_bimolecular(is_third_body=True))


def test_chebyshev_rejects_is_third_body() -> None:
    with pytest.raises(ValidationError, match="cannot be a third-body reaction"):
        KineticsUploadRequest(
            reaction=_BIMOLECULAR_REACTION,
            scientific_origin="experimental",
            model_kind="chebyshev",
            is_third_body=True,
            chebyshev={
                "n_temperature": 2, "n_pressure": 2,
                "tmin_k": 300.0, "tmax_k": 2000.0,
                "pmin_bar": 0.01, "pmax_bar": 100.0,
                "coefficients": [[1.0, 2.0], [3.0, 4.0]],
            },
        )


def test_plog_a_units_are_judged_at_the_true_molecularity() -> None:
    """Both directions of the unit consequence the flag used to invert.

    ``is_third_body`` raised ``_main_line_molecularity`` by one, so for a
    bimolecular PLOG the CORRECT ``cm3_mol_s`` was rejected as if the rate
    were termolecular, while the WRONG ``cm6_mol2_s`` sailed through.
    """
    # Correct units for a bimolecular rate now validate.
    request = KineticsUploadRequest(**_plog_bimolecular())
    assert request.plog_entries[0].a_units.value == "cm3_mol_s"

    # Third-order units on a bimolecular rate are rejected — previously they
    # were accepted because the flag had inflated the expected order.
    with pytest.raises(ValidationError, match="incompatible with bimolecular"):
        KineticsUploadRequest(
            **_plog_bimolecular(
                plog_entries=[
                    {
                        "entry_index": 1,
                        "pressure_bar": 1.0,
                        "a": 1.0e13,
                        "a_units": "cm6_mol2_s",
                        "n": 0.0,
                        "ea_kj_mol": 40.0,
                    }
                ]
            )
        )


def test_transition_state_interpretation_rejects_conformer_selection() -> None:
    """NB3: a 422 at the boundary, not an IntegrityError at flush.

    ``kinetics_interpretation_subject_shape`` requires a NULL
    conformer_selection_id for a TS subject, so letting this through produced
    a 500-shaped failure deep in the persistence seam.
    """
    with pytest.raises(ValidationError, match="conformer_selection is only valid"):
        KineticsUploadRequest(
            reaction=_BIMOLECULAR_REACTION,
            scientific_origin="computed",
            a=1.0e13,
            a_units="cm3_mol_s",
            interpretation_assignments=[
                {"role": "reactant", "participant_index": 1, "statmech_ref": "sm_a", **_CONVENTIONS},
                {"role": "reactant", "participant_index": 2, "statmech_ref": "sm_b", **_CONVENTIONS},
                {"role": "product", "participant_index": 1, "statmech_ref": "sm_c", **_CONVENTIONS},
                {
                    "role": "transition_state",
                    "statmech_ref": "sm_ts",
                    "transition_state_entry_ref": "tse_1",
                    "conformer_selection": {
                        "species_entry": {"smiles": "CO", "charge": 0, "multiplicity": 1},
                        "selection_kind": "lowest_energy",
                    },
                    **_CONVENTIONS,
                },
            ],
        )


def test_interpretation_set_without_a_transition_state_is_warned() -> None:
    """NB9: a TST rate with no Q-double-dagger is named, not silently accepted."""
    from app.services.provenance_warnings import (
        W_MISSING_TS_INTERPRETATION,
        collect_kinetics_content_warnings,
    )

    request = KineticsUploadRequest(
        reaction=_BIMOLECULAR_REACTION,
        scientific_origin="computed",
        a=1.0e13,
        a_units="cm3_mol_s",
        interpretation_assignments=[
            {"role": "reactant", "participant_index": 1, "statmech_ref": "sm_a", **_CONVENTIONS},
            {"role": "reactant", "participant_index": 2, "statmech_ref": "sm_b", **_CONVENTIONS},
            {"role": "product", "participant_index": 1, "statmech_ref": "sm_c", **_CONVENTIONS},
        ],
    )
    assert W_MISSING_TS_INTERPRETATION in {
        w.code for w in collect_kinetics_content_warnings(request)
    }

    # A rate whose parameterization comes from a master-equation fit has no
    # single dividing surface, and is not asked for one.
    fitted = KineticsUploadRequest(
        **{
            **request.model_dump(mode="json", exclude_none=True),
            "network_kinetics_ref": "nkin_abc",
        }
    )
    assert W_MISSING_TS_INTERPRETATION not in {
        w.code for w in collect_kinetics_content_warnings(fitted)
    }
