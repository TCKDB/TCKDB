"""An ``energy_ordering`` energy is held against what TCKDB stores for its calculation (#638).

#637 compared the stated numbers with each other and with nothing TCKDB holds, so
a record could cite a single point that stores -40.9 Eh, state -40.20 Eh from it,
and pass. These run at the persistence seam because the stored values must be
shaped exactly (a NULL energy, a missing ZPE, two geometries) and because the
service has to enforce the rule for a payload that never went through the wire
schemas.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from tckdb_schemas.fragments.calculation import composite_arithmetic_tolerance_hartree
from tckdb_schemas.fragments.ts_validation_evidence import (
    TransitionStateComparedEnergy,
    TransitionStateValidationEvidenceIn,
)
from tckdb_schemas.upload_warning import UploadWarning

from app.api.error_contract import CodedValueError
from app.db.models.calculation import CalculationInputGeometry
from app.db.models.common import CalculationType
from app.db.models.geometry import Geometry
from app.db.models.transition_state import TransitionStateValidationEnergy
from app.services.transition_state_validation import (
    persist_transition_state_validation_evidence,
)
from tests.services.scientific_read._factories import (
    attach_freq_result,
    attach_input_geometry,
    attach_opt_result,
    attach_output_geometry,
    attach_sp_result,
    make_calculation,
    make_chem_reaction,
    make_geometry,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_transition_state,
    make_transition_state_entry,
    next_inchi_key,
)

_MISMATCH = "ts_energy_ordering_stated_energy_mismatch"
_NOT_COMPARED = "transition_state_energy_ordering_not_compared"

#: ``(stated electronic, stored ZPE)`` of the three participants. E0 = electronic + ZPE.
_ELECTRONIC = {"ts": -1.0, "reactant:1": -2.0, "product:1": -3.0}
_ZPE = 0.01
_TOL_ELECTRONIC = composite_arithmetic_tolerance_hartree(2)
_TOL_E0 = composite_arithmetic_tolerance_hartree(3)


def _fixture(db_session, tag: str, *, electronic=None, zpe=_ZPE, freq_geometry_differs=False):
    """``A -> B`` with a saddle point: an sp and a freq for each, one geometry per participant.

    ``electronic`` overrides the stored sp energies by participant (``None`` stores no
    energy row at all); ``zpe`` is the stored ZPE of every freq (``None`` stores none).
    """
    electronic = {**_ELECTRONIC, **(electronic or {})}
    reactant = make_species(db_session, inchi_key=next_inchi_key(f"{tag}R"))
    product = make_species(db_session, inchi_key=next_inchi_key(f"{tag}P"))
    reactant_entry = make_species_entry(db_session, reactant)
    product_entry = make_species_entry(db_session, product)
    reaction_entry = make_reaction_entry(
        db_session,
        reaction=make_chem_reaction(db_session, reactants=[reactant], products=[product]),
        reactant_entries=[reactant_entry],
        product_entries=[product_entry],
    )
    ts_entry = make_transition_state_entry(
        db_session,
        transition_state=make_transition_state(db_session, reaction_entry=reaction_entry),
    )
    owners = {
        "ts": {"transition_state_entry_id": ts_entry.id},
        "reactant:1": {"species_entry_id": reactant_entry.id},
        "product:1": {"species_entry_id": product_entry.id},
    }
    calcs: dict[str, object] = {}
    for participant, owner in owners.items():
        geometry = make_geometry(db_session)
        sp = make_calculation(db_session, type=CalculationType.sp, **owner)
        attach_input_geometry(db_session, calculation=sp, geometry=geometry)
        if electronic[participant] is not None:
            attach_sp_result(db_session, calculation=sp, electronic_energy_hartree=electronic[participant])
        freq = make_calculation(db_session, type=CalculationType.freq, **owner)
        attach_input_geometry(
            db_session,
            calculation=freq,
            geometry=make_geometry(db_session) if freq_geometry_differs else geometry,
        )
        attach_freq_result(
            db_session,
            calculation=freq,
            frequencies_cm1=[-1500.0, 100.0] if participant == "ts" else [100.0, 200.0],
            zpe_hartree=zpe,
        )
        calcs[participant] = (sp, freq)
    return {"reaction_entry": reaction_entry, "ts_entry": ts_entry, "calcs": calcs}


def _keys(fx) -> dict[str, int]:
    keys: dict[str, int] = {}
    for participant, (sp, freq) in fx["calcs"].items():
        keys[f"{participant}-sp"] = sp.id
        keys[f"{participant}-freq"] = freq.id
    return keys


def _energy(participant, kind, value, source, scale=None):
    energy = {
        "participant": participant,
        "energy_kind": kind,
        "energy_hartree": value,
        "source_calculation_key": f"{participant}-{source}",
    }
    if scale is not None:
        energy["zpe_scale_factor"] = scale
    return energy


def _record(*, electronic=None, e0=None, scale=None, passed=True) -> TransitionStateValidationEvidenceIn:
    """Electronic energies from the sps and E0s from the freqs; defaults agree with ``_fixture``."""
    energies = []
    if electronic is not False:
        for participant, value in {**_ELECTRONIC, **(electronic or {})}.items():
            energies.append(_energy(participant, "electronic", value, "sp"))
    if e0 is not None:
        for participant in _ELECTRONIC:
            energies.append(
                _energy(
                    participant,
                    "e0",
                    e0.get(participant, _ELECTRONIC[participant] + _ZPE),
                    "freq",
                    (scale or {}).get(participant),
                )
            )
    return TransitionStateValidationEvidenceIn(
        kind="energy_ordering", passed=passed, rationale="TS above both wells", energies=energies
    )


def _persist(db_session, fx, records, *, warnings=None, reconstruction=None):
    records = records if isinstance(records, list) else [records]
    return persist_transition_state_validation_evidence(
        db_session,
        records,
        transition_state_entry_id=fx["ts_entry"].id,
        reconstruction_calculation_ids=reconstruction or [None] * len(records),
        calculation_ids_by_key=_keys(fx),
        subject_label="ts",
        field_path="validation_evidence",
        reaction_entry_id=fx["reaction_entry"].id,
        transition_state_geometry_id=None,
        warnings=warnings,
    )


def _stored(db_session):
    return {
        (e.participant, e.energy_kind): (e.stored_energy_comparison, e.not_compared_reason)
        for e in db_session.scalars(select(TransitionStateValidationEnergy))
    }


# ---------------------------------------------------------------------------
# The #637 fixture
# ---------------------------------------------------------------------------


def test_the_637_fixture_is_refused(db_session) -> None:
    """The cited sp stores -40.9 Eh; the record states -40.20 Eh from it."""
    fx = _fixture(db_session, "FIX637", electronic={"ts": -40.9, "reactant:1": -39.75, "product:1": -40.5})
    record = _record(electronic={"ts": -40.20, "reactant:1": -39.75, "product:1": -40.5})
    with pytest.raises(CodedValueError) as caught:
        _persist(db_session, fx, record)
    assert caught.value.code == _MISMATCH
    context = caught.value.context
    assert context["participant"] == "ts"
    assert context["energy_kind"] == "electronic"
    assert context["stated_hartree"] == -40.20
    assert context["stored_hartree"] == -40.9
    assert context["field"] == "validation_evidence[0].energies[0]"
    # Nothing was stored, and no row id reached the message.
    assert _stored(db_session) == {}
    assert f"id={fx['ts_entry'].id}" not in str(caught.value)


def test_a_stated_value_that_matches_is_stored_as_agreeing(db_session) -> None:
    fx = _fixture(db_session, "AGREE")
    warnings: list[UploadWarning] = []
    _persist(db_session, fx, _record(e0={}), warnings=warnings)
    db_session.flush()
    assert set(_stored(db_session).values()) == {("agrees", None)}
    assert _NOT_COMPARED not in {w.code for w in warnings}


# ---------------------------------------------------------------------------
# Tolerance: the shared printed-precision helper, n = 2 and n = 3
# ---------------------------------------------------------------------------


def test_the_electronic_tolerance_is_the_helpers_with_two_quantities(db_session) -> None:
    fx = _fixture(db_session, "TOLE")
    inside = _record(electronic={"ts": _ELECTRONIC["ts"] + 0.99 * _TOL_ELECTRONIC})
    _persist(db_session, fx, inside)
    outside = _record(electronic={"ts": _ELECTRONIC["ts"] + 1.01 * _TOL_ELECTRONIC}, passed=False)
    with pytest.raises(CodedValueError) as caught:
        _persist(db_session, fx, outside)
    assert caught.value.code == _MISMATCH
    assert caught.value.context["tolerance_hartree"] == _TOL_ELECTRONIC


def test_the_unscaled_e0_tolerance_is_the_helpers_with_three_quantities(db_session) -> None:
    """1.2 Tol(2) is outside for an electronic energy and inside for an E0 sum (no factor stated)."""
    assert _TOL_E0 > _TOL_ELECTRONIC
    fx = _fixture(db_session, "TOL0")
    stored_e0 = _ELECTRONIC["ts"] + _ZPE
    _persist(db_session, fx, _record(e0={"ts": stored_e0 + 0.99 * _TOL_E0}))
    db_session.flush()
    assert _stored(db_session)[("ts", "e0")] == ("agrees", None)


def test_the_top_of_the_unscaled_agreeing_band_is_the_three_quantity_tolerance(db_session) -> None:
    """A slightly scaled E0 just past Tol(3), with no factor, is not an agreement."""
    fx = _fixture(db_session, "TOP0")
    stored_e0 = _ELECTRONIC["ts"] + _ZPE
    _persist(db_session, fx, _record(e0={"ts": stored_e0 + 1.01 * _TOL_E0}))
    db_session.flush()
    assert _stored(db_session)[("ts", "e0")] == ("not_compared", "zpe_scaling_unstated")


def test_the_scaled_e0_tolerance_covers_the_sum_and_the_factors_printed_precision(db_session) -> None:
    scale = 0.98
    fx = _fixture(db_session, "TOLS")
    rounded = 2 + scale + _ZPE * 5e-5 / 5e-7
    tolerance = composite_arithmetic_tolerance_hartree(rounded)
    assert tolerance > _TOL_E0
    stored_e0 = _ELECTRONIC["ts"] + scale * _ZPE
    _persist(db_session, fx, _record(e0={"ts": stored_e0 + 0.99 * tolerance}, scale={"ts": scale}))
    outside = _record(e0={"ts": stored_e0 + 1.01 * tolerance}, scale={"ts": scale}, passed=False)
    with pytest.raises(CodedValueError) as caught:
        _persist(db_session, fx, outside)
    assert caught.value.code == _MISMATCH
    assert caught.value.context["tolerance_hartree"] == tolerance


# ---------------------------------------------------------------------------
# E0 = stored electronic + stored ZPE
# ---------------------------------------------------------------------------


def test_an_unscaled_e0_with_no_factor_agrees(db_session) -> None:
    fx = _fixture(db_session, "E0SUM")
    _persist(db_session, fx, _record(e0={}))
    db_session.flush()
    assert _stored(db_session)[("reactant:1", "e0")] == ("agrees", None)


def test_a_scaled_e0_with_its_factor_agrees_and_the_factor_is_stored(db_session) -> None:
    scale = 0.98
    fx = _fixture(db_session, "SCALED")
    e0 = {p: _ELECTRONIC[p] + scale * _ZPE for p in _ELECTRONIC}
    _persist(db_session, fx, _record(e0=e0, scale=dict.fromkeys(_ELECTRONIC, scale)))
    db_session.flush()
    assert _stored(db_session)[("reactant:1", "e0")] == ("agrees", None)
    factors = {
        e.participant: e.zpe_scale_factor
        for e in db_session.scalars(select(TransitionStateValidationEnergy))
        if e.energy_kind == "e0"
    }
    assert factors == dict.fromkeys(_ELECTRONIC, scale)


def test_an_e0_with_a_wrong_factor_is_refused(db_session) -> None:
    """E0 built with 0.98 but stating 1.0: the sum the producer declared is not the one it used."""
    fx = _fixture(db_session, "WRONGS")
    wrong = _record(
        e0={"reactant:1": _ELECTRONIC["reactant:1"] + 0.98 * _ZPE}, scale={"reactant:1": 1.0}
    )
    with pytest.raises(CodedValueError) as caught:
        _persist(db_session, fx, wrong)
    context = caught.value.context
    assert caught.value.code == _MISMATCH
    assert (context["participant"], context["energy_kind"]) == ("reactant:1", "e0")
    assert context["stored_hartree"] == pytest.approx(_ELECTRONIC["reactant:1"] + _ZPE)
    assert context["stored_zpe_hartree"] == _ZPE
    assert context["zpe_scale_factor"] == 1.0
    assert _stored(db_session) == {}


def test_a_scaled_e0_with_no_factor_is_recorded_not_refused_and_warns(db_session) -> None:
    fx = _fixture(db_session, "NOFACTOR")
    warnings: list[UploadWarning] = []
    e0 = {"reactant:1": _ELECTRONIC["reactant:1"] + 0.98 * _ZPE}
    _persist(db_session, fx, _record(e0=e0), warnings=warnings)
    db_session.flush()
    stored = _stored(db_session)
    assert stored[("reactant:1", "e0")] == ("not_compared", "zpe_scaling_unstated")
    assert stored[("ts", "e0")] == ("agrees", None)
    (warning,) = [w for w in warnings if w.code == _NOT_COMPARED]
    assert "zpe_scale_factor" in warning.message
    assert "'reactant:1' e0 (zpe_scaling_unstated)" in warning.message


def test_the_e0_refusal_names_a_non_zero_record_and_energy_index(db_session) -> None:
    fx = _fixture(db_session, "E0INDEX")
    ts_freq = fx["calcs"]["ts"][1]
    mode = TransitionStateValidationEvidenceIn(
        kind="imaginary_mode", passed=True, rationale="r", imaginary_frequency_count=1
    )
    ordering = _record(e0={"product:1": -2.0}, scale={"product:1": 1.0})
    with pytest.raises(CodedValueError) as caught:
        _persist(db_session, fx, [mode, ordering], reconstruction=[ts_freq.id, None])
    assert caught.value.code == _MISMATCH
    # Electronic energies are indexes 0-2, the E0s 3-5 in participant order: product:1 is 5.
    assert caught.value.context["field"] == "validation_evidence[1].energies[5]"
    assert caught.value.context["energy_kind"] == "e0"


def test_an_e0_is_paired_with_an_opt_at_the_same_geometry(db_session) -> None:
    """An opt's energy is at its final output geometry, which the freq must share."""
    fx = _fixture(db_session, "E0OPT", electronic={"ts": None})
    ts_freq = fx["calcs"]["ts"][1]
    geometry = db_session.get(
        Geometry,
        db_session.scalars(
            select(CalculationInputGeometry.geometry_id).where(CalculationInputGeometry.calculation_id == ts_freq.id)
        ).one(),
    )
    opt = make_calculation(
        db_session, type=CalculationType.opt, transition_state_entry_id=fx["ts_entry"].id
    )
    attach_opt_result(db_session, calculation=opt, final_energy_hartree=_ELECTRONIC["ts"])
    attach_output_geometry(db_session, calculation=opt, geometry=geometry)
    keys = _keys(fx) | {"ts-sp": opt.id}
    persist_transition_state_validation_evidence(
        db_session,
        [_record(e0={})],
        transition_state_entry_id=fx["ts_entry"].id,
        reconstruction_calculation_ids=[None],
        calculation_ids_by_key=keys,
        subject_label="ts",
        field_path="validation_evidence",
        reaction_entry_id=fx["reaction_entry"].id,
        transition_state_geometry_id=None,
    )
    db_session.flush()
    assert _stored(db_session)[("ts", "electronic")] == ("agrees", None)
    assert _stored(db_session)[("ts", "e0")] == ("agrees", None)


# ---------------------------------------------------------------------------
# What cannot be compared is recorded, warned about, and never a pass
# ---------------------------------------------------------------------------


def test_a_null_stored_energy_is_not_compared(db_session) -> None:
    fx = _fixture(db_session, "NULLE", electronic={"ts": None})
    warnings: list[UploadWarning] = []
    # The stated -1.0 would contradict a stored 0.0, so a NULL read as zero would refuse here.
    _persist(db_session, fx, _record(), warnings=warnings)
    db_session.flush()
    assert _stored(db_session)[("ts", "electronic")] == ("not_compared", "stored_energy_not_stated")
    assert _stored(db_session)[("reactant:1", "electronic")] == ("agrees", None)
    (warning,) = [w for w in warnings if w.code == _NOT_COMPARED]
    assert warning.field == "validation_evidence[0].energies"
    assert "'ts' electronic (stored_energy_not_stated)" in warning.message


def test_an_e0_whose_freq_stores_no_zpe_is_not_compared(db_session) -> None:
    fx = _fixture(db_session, "NOZPE", zpe=None)
    warnings: list[UploadWarning] = []
    _persist(db_session, fx, _record(e0={}), warnings=warnings)
    db_session.flush()
    stored = _stored(db_session)
    assert stored[("ts", "e0")] == ("not_compared", "zpe_not_stated")
    assert stored[("ts", "electronic")] == ("agrees", None)
    assert _NOT_COMPARED in {w.code for w in warnings}


def test_an_e0_at_a_different_geometry_than_its_electronic_energy_is_not_compared(db_session) -> None:
    fx = _fixture(db_session, "GEOM", freq_geometry_differs=True)
    warnings: list[UploadWarning] = []
    # A wrong E0 that would be refused if the pairing were guessed.
    _persist(db_session, fx, _record(e0={"ts": -0.5}), warnings=warnings)
    db_session.flush()
    assert _stored(db_session)[("ts", "e0")] == ("not_compared", "geometry_not_paired")
    assert _NOT_COMPARED in {w.code for w in warnings}


def test_an_e0_with_no_electronic_entry_to_pair_is_not_compared(db_session) -> None:
    fx = _fixture(db_session, "NOPAIR")
    warnings: list[UploadWarning] = []
    _persist(db_session, fx, _record(electronic=False, e0={"ts": -0.5}), warnings=warnings)
    db_session.flush()
    assert set(_stored(db_session).values()) == {("not_compared", "no_electronic_energy_to_pair")}
    assert _NOT_COMPARED in {w.code for w in warnings}


# ---------------------------------------------------------------------------
# Positions and bypassed validation
# ---------------------------------------------------------------------------


def test_the_refusal_names_a_non_zero_record_and_energy_index(db_session) -> None:
    fx = _fixture(db_session, "INDEX")
    ts_freq = fx["calcs"]["ts"][1]
    mode = TransitionStateValidationEvidenceIn(
        kind="imaginary_mode", passed=True, rationale="r", imaginary_frequency_count=1
    )
    ordering = _record(electronic={"product:1": -3.5})
    with pytest.raises(CodedValueError) as caught:
        _persist(db_session, fx, [mode, ordering], reconstruction=[ts_freq.id, None])
    assert caught.value.code == _MISMATCH
    assert caught.value.context["field"] == "validation_evidence[1].energies[2]"
    assert caught.value.context["participant"] == "product:1"


def test_the_service_enforces_it_for_a_payload_that_skipped_validation(db_session) -> None:
    """``model_construct`` runs no validator, so only the service stands in the way."""
    fx = _fixture(db_session, "BYPASS")
    energies = [
        TransitionStateComparedEnergy.model_construct(
            participant="ts", energy_kind="electronic", energy_hartree=-0.2, source_calculation_key="ts-sp"
        ),
        TransitionStateComparedEnergy.model_construct(
            participant="reactant:1", energy_kind="electronic", energy_hartree=-2.0,
            source_calculation_key="reactant:1-sp",
        ),
        TransitionStateComparedEnergy.model_construct(
            participant="product:1", energy_kind="electronic", energy_hartree=-3.0,
            source_calculation_key="product:1-sp",
        ),
    ]
    record = TransitionStateValidationEvidenceIn.model_construct(
        kind="energy_ordering", passed=True, rationale="r", energies=energies
    )
    keys = _keys(fx)
    with pytest.raises(CodedValueError) as caught:
        persist_transition_state_validation_evidence(
            db_session,
            [record],
            transition_state_entry_id=fx["ts_entry"].id,
            reconstruction_calculation_ids=[None],
            calculation_ids_by_key=keys,
            subject_label="ts",
            field_path="validation_evidence",
            reaction_entry_id=fx["reaction_entry"].id,
            transition_state_geometry_id=None,
        )
    assert caught.value.code == _MISMATCH
    assert caught.value.context["stored_hartree"] == _ELECTRONIC["ts"]
