"""API tests for the applied energy correction on the public read surface.

Before this shipped, TCKDB served an energy-correction scheme's *recipe*
— all 45 per-bond parameters of a Petersson BAC, addressable at
``/scientific/energy-correction-schemes/{ref}?include=corrections`` — and
served an index saying which species the scheme had been applied to, and
never served **the number that was added**. These tests pin the two
surfaces that now carry it and the facts that make it readable:

* the energy the correction accompanies is the **uncorrected** one, so
  the correction is an addend and not an adjustment already folded in;
* the applied value's unit is stated and never silently converted;
* a record with no correction is distinguishable from a caller who did
  not ask for corrections.
"""

from __future__ import annotations

import pytest

from app.db.models.common import (
    AppliedCorrectionComponentKind,
    CalculationType,
    EnergyCorrectionApplicationRole,
    EnergyCorrectionSchemeKind,
    EnergyUnit,
    FrequencyScaleKind,
    TransitionStateEntryStatus,
)
from app.db.models.reaction import ChemReaction, ReactionEntry
from app.db.models.transition_state import TransitionState, TransitionStateEntry
from app.services.scientific_read.calculations import (
    _CORRECTION_COMPONENT_LIMIT,
)
from tests.services.scientific_read._factories import (
    attach_sp_result,
    make_applied_energy_correction,
    make_calculation,
    make_energy_correction_scheme,
    make_frequency_scale_factor,
    make_species,
    make_species_entry,
    next_inchi_key,
)

# The single-point energy every fixture below deposits. Chosen to look
# like a real one so the "is this pre- or post-correction?" assertions
# read as chemistry rather than as arithmetic on 1.0.
RAW_SP_ENERGY_HARTREE = -78.62375631

# A Petersson BAC total taken from the hosted instance's own data, in the
# unit it is stored in there.
BAC_KCAL_MOL = -1.4083247988699648
BAC_HARTREE = -0.0022443084241441243

# An atom-energy total from the same source, stored in hartree.
AEC_HARTREE = 78.59928369852641


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _calc_url(ref: str, **params) -> str:
    base = f"/api/v1/scientific/calculations/{ref}"
    if not params:
        return base
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{base}?{qs}"


def _ecs_url(ref: str, **params) -> str:
    base = f"/api/v1/scientific/energy-correction-schemes/{ref}"
    if not params:
        return base
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{base}?{qs}"


def _sp_calc_with_energy(db_session, tag: str):
    """A species-owned SP calculation carrying a deposited energy."""
    species = make_species(db_session, inchi_key=next_inchi_key(tag))
    entry = make_species_entry(db_session, species)
    calc = make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry.id
    )
    attach_sp_result(
        db_session,
        calculation=calc,
        electronic_energy_hartree=RAW_SP_ENERGY_HARTREE,
    )
    return entry, calc


def _ts_entry(db_session, label: str) -> TransitionStateEntry:
    rxn = ChemReaction(reversible=True)
    db_session.add(rxn)
    db_session.flush()
    rxe = ReactionEntry(reaction_id=rxn.id)
    db_session.add(rxe)
    db_session.flush()
    ts = TransitionState(reaction_entry_id=rxe.id, label=label)
    db_session.add(ts)
    db_session.flush()
    tse = TransitionStateEntry(
        transition_state_id=ts.id,
        charge=0,
        multiplicity=2,
        unmapped_smiles="[CH2]",
        status=TransitionStateEntryStatus.optimized,
    )
    db_session.add(tse)
    db_session.flush()
    return tse


def _bac_applied(db_session, *, entry, calc, scheme_name: str):
    scheme = make_energy_correction_scheme(
        db_session,
        name=scheme_name,
        kind=EnergyCorrectionSchemeKind.bac_petersson,
        units=EnergyUnit.kcal_mol,
    )
    applied = make_applied_energy_correction(
        db_session,
        target_species_entry=entry,
        scheme=scheme,
        source_calculation=calc,
        application_role=EnergyCorrectionApplicationRole.bac_total,
        value=BAC_KCAL_MOL,
        value_unit=EnergyUnit.kcal_mol,
        note="Per-species BAC computed by Arkane (bac_type=p).",
        components=[
            (AppliedCorrectionComponentKind.bond, "C-H", 6, -0.23472079981166077,
             -1.4083247988699648),
        ],
    )
    return scheme, applied


# ===========================================================================
# THE JUSTIFYING TEST — applied value present vs absent vs unasked
# ===========================================================================


def test_calculation_with_correction_serves_the_applied_value(
    client, db_session
):
    """The number that was added is on the wire, with its unit."""
    entry, calc = _sp_calc_with_energy(db_session, "AECHAS")
    _bac_applied(db_session, entry=entry, calc=calc, scheme_name="has-bac")

    body = client.get(
        _calc_url(calc.public_ref, include="energy_corrections")
    ).json()
    corrections = body["record"]["energy_corrections"]

    assert len(corrections) == 1
    entry_ = corrections[0]
    assert entry_["applied_value"] == BAC_KCAL_MOL
    assert entry_["applied_value_unit"] == "kcal_mol"
    assert entry_["applied_value_hartree"] == pytest.approx(
        BAC_HARTREE, rel=1e-12
    )
    assert entry_["application_role"] == "bac_total"


def test_calculation_without_correction_serves_an_empty_list(
    client, db_session
):
    """Asked for, and there is none: an empty list, not an absent key."""
    _, calc = _sp_calc_with_energy(db_session, "AECNON")

    body = client.get(
        _calc_url(calc.public_ref, include="energy_corrections")
    ).json()
    record = body["record"]

    assert "energy_corrections" in record
    assert record["energy_corrections"] == []
    assert record["available_sections"]["has_energy_corrections"] is False


def test_unrequested_corrections_are_absent_not_empty(client, db_session):
    """Not asked for: the key is gone entirely.

    This is the third state, and it is the one that makes the other two
    readable. Without it, ``[]`` would mean both "this record has no
    correction" and "you did not ask", and a consumer reading the first
    as the second (or the reverse) has no way to find out.
    """
    entry, calc = _sp_calc_with_energy(db_session, "AECUNR")
    _bac_applied(db_session, entry=entry, calc=calc, scheme_name="unreq-bac")

    record = client.get(_calc_url(calc.public_ref)).json()["record"]

    assert "energy_corrections" not in record
    # ... but the record still says one exists, so a caller who did not
    # ask can find out that asking is worth it.
    assert record["available_sections"]["has_energy_corrections"] is True


def test_three_states_are_mutually_distinguishable(client, db_session):
    """The three cases produce three different wire shapes."""
    entry_a, calc_a = _sp_calc_with_energy(db_session, "AEC3ST")
    _bac_applied(db_session, entry=entry_a, calc=calc_a, scheme_name="3st-bac")
    _, calc_b = _sp_calc_with_energy(db_session, "AEC3SU")

    has = client.get(
        _calc_url(calc_a.public_ref, include="energy_corrections")
    ).json()["record"]
    has_not = client.get(
        _calc_url(calc_b.public_ref, include="energy_corrections")
    ).json()["record"]
    unasked = client.get(_calc_url(calc_a.public_ref)).json()["record"]

    shapes = {
        "has": has.get("energy_corrections", "<absent>"),
        "has_not": has_not.get("energy_corrections", "<absent>"),
        "unasked": unasked.get("energy_corrections", "<absent>"),
    }
    assert shapes["has_not"] == []
    assert shapes["unasked"] == "<absent>"
    assert len(shapes["has"]) == 1
    assert shapes["has"] != shapes["has_not"] != shapes["unasked"]


# ===========================================================================
# Pre- or post-correction: the served energy is the uncorrected one
# ===========================================================================


def test_served_energy_is_the_uncorrected_one(client, db_session):
    """The SP energy is the deposited value; the correction is an addend.

    Deposit an energy, apply a correction to it, and read both back. If
    the API were serving a corrected energy, the energy would differ from
    what was deposited by the correction's magnitude. It does not: the
    stored electronic energy is untouched and the correction is a
    separate, additive quantity a consumer may choose to apply.
    """
    entry, calc = _sp_calc_with_energy(db_session, "AECPRE")
    _bac_applied(db_session, entry=entry, calc=calc, scheme_name="pre-bac")

    body = client.get(
        _calc_url(calc.public_ref, include="results,energy_corrections")
    ).json()
    record = body["record"]

    served_energy = record["results"]["sp"]["electronic_energy_hartree"]
    correction = record["energy_corrections"][0]

    assert served_energy == RAW_SP_ENERGY_HARTREE
    assert served_energy != pytest.approx(
        RAW_SP_ENERGY_HARTREE + BAC_HARTREE, abs=1e-12
    )
    # And the corrected energy is a sum the consumer can now take, in one
    # unit, without a conversion factor of their own.
    assert served_energy + correction["applied_value_hartree"] == pytest.approx(
        RAW_SP_ENERGY_HARTREE + BAC_HARTREE, rel=1e-12
    )


def test_energy_is_identical_with_and_without_a_correction(
    client, db_session
):
    """Two identical calculations, one corrected, serve the same energy.

    The control for the test above: it is the *presence* of a correction
    that must not move the energy, and comparing the corrected record
    against an uncorrected sibling shows that directly.
    """
    entry_a, calc_a = _sp_calc_with_energy(db_session, "AECCTL")
    _bac_applied(db_session, entry=entry_a, calc=calc_a, scheme_name="ctl-bac")
    _, calc_b = _sp_calc_with_energy(db_session, "AECCTM")

    def energy_of(calc):
        body = client.get(_calc_url(calc.public_ref, include="results")).json()
        return body["record"]["results"]["sp"]["electronic_energy_hartree"]

    assert energy_of(calc_a) == energy_of(calc_b) == RAW_SP_ENERGY_HARTREE


# ===========================================================================
# Units — stated, never silently converted
# ===========================================================================


def test_hartree_correction_is_not_rescaled(client, db_session):
    """A correction stored in hartree comes back in hartree, unchanged."""
    entry, calc = _sp_calc_with_energy(db_session, "AECHRT")
    scheme = make_energy_correction_scheme(
        db_session,
        name="hartree-aec",
        kind=EnergyCorrectionSchemeKind.atom_energy,
        units=EnergyUnit.hartree,
    )
    make_applied_energy_correction(
        db_session,
        target_species_entry=entry,
        scheme=scheme,
        source_calculation=calc,
        application_role=EnergyCorrectionApplicationRole.aec_total,
        value=AEC_HARTREE,
        value_unit=EnergyUnit.hartree,
    )

    body = client.get(
        _calc_url(calc.public_ref, include="energy_corrections")
    ).json()
    got = body["record"]["energy_corrections"][0]

    assert got["applied_value"] == AEC_HARTREE
    assert got["applied_value_unit"] == "hartree"
    assert got["applied_value_hartree"] == AEC_HARTREE


def test_two_units_coexist_on_one_calculation(client, db_session):
    """kcal/mol and hartree corrections on one record keep their own units.

    This is the case that forbids a single fixed-unit column: a real
    deposit carries a BAC total in kcal/mol and an atom-energy total in
    hartree against the same single-point energy. Both are served as
    stored, each labelled, and both projected into hartree so they can be
    summed with the energy they belong to.
    """
    entry, calc = _sp_calc_with_energy(db_session, "AECTWO")
    _bac_applied(db_session, entry=entry, calc=calc, scheme_name="two-bac")
    aec_scheme = make_energy_correction_scheme(
        db_session,
        name="two-aec",
        kind=EnergyCorrectionSchemeKind.atom_energy,
        units=EnergyUnit.hartree,
    )
    make_applied_energy_correction(
        db_session,
        target_species_entry=entry,
        scheme=aec_scheme,
        source_calculation=calc,
        application_role=EnergyCorrectionApplicationRole.aec_total,
        value=AEC_HARTREE,
        value_unit=EnergyUnit.hartree,
    )

    body = client.get(
        _calc_url(calc.public_ref, include="energy_corrections")
    ).json()
    by_role = {
        c["application_role"]: c
        for c in body["record"]["energy_corrections"]
    }

    assert by_role["bac_total"]["applied_value_unit"] == "kcal_mol"
    assert by_role["bac_total"]["applied_value"] == BAC_KCAL_MOL
    assert by_role["aec_total"]["applied_value_unit"] == "hartree"
    assert by_role["aec_total"]["applied_value"] == AEC_HARTREE
    # Neither stored value was rewritten into the other's unit.
    assert by_role["bac_total"]["applied_value"] != pytest.approx(
        BAC_HARTREE, abs=1e-12
    )


def test_negative_correction_keeps_its_sign(client, db_session):
    """A correction that lowers an energy is served negative."""
    entry, calc = _sp_calc_with_energy(db_session, "AECSGN")
    _bac_applied(db_session, entry=entry, calc=calc, scheme_name="sgn-bac")

    body = client.get(
        _calc_url(calc.public_ref, include="energy_corrections")
    ).json()
    got = body["record"]["energy_corrections"][0]

    assert got["applied_value"] < 0
    assert got["applied_value_hartree"] < 0


# ===========================================================================
# Components
# ===========================================================================


def test_components_are_served_with_the_total(client, db_session):
    """The breakdown travels inside the same include as the total."""
    entry, calc = _sp_calc_with_energy(db_session, "AECCMP")
    _bac_applied(db_session, entry=entry, calc=calc, scheme_name="cmp-bac")

    body = client.get(
        _calc_url(calc.public_ref, include="energy_corrections")
    ).json()
    got = body["record"]["energy_corrections"][0]

    assert got["component_count"] == 1
    assert got["components_truncated"] is False
    assert got["components"] == [
        {
            "component_kind": "bond",
            "key": "C-H",
            "multiplicity": 6,
            "parameter_value": -0.23472079981166077,
            "contribution_value": -1.4083247988699648,
        }
    ]


def test_correction_without_components_reports_zero_not_missing(
    client, db_session
):
    """A total with no breakdown says so, and does not look truncated."""
    entry, calc = _sp_calc_with_energy(db_session, "AECNCP")
    scheme = make_energy_correction_scheme(
        db_session, name="nocomp-bac", units=EnergyUnit.kcal_mol
    )
    make_applied_energy_correction(
        db_session,
        target_species_entry=entry,
        scheme=scheme,
        source_calculation=calc,
        application_role=EnergyCorrectionApplicationRole.bac_total,
        value=0.0,
        value_unit=EnergyUnit.kcal_mol,
    )

    body = client.get(
        _calc_url(calc.public_ref, include="energy_corrections")
    ).json()
    got = body["record"]["energy_corrections"][0]

    assert got["component_count"] == 0
    assert got["components"] == []
    assert got["components_truncated"] is False
    # Zero is a measurement here, not a missing value.
    assert got["applied_value"] == 0.0


# ===========================================================================
# Provenance pointers
# ===========================================================================


def test_scheme_pointer_identifies_the_recipe(client, db_session):
    entry, calc = _sp_calc_with_energy(db_session, "AECSCH")
    scheme, _ = _bac_applied(
        db_session, entry=entry, calc=calc, scheme_name="ptr-bac"
    )

    body = client.get(
        _calc_url(calc.public_ref, include="energy_corrections")
    ).json()
    got = body["record"]["energy_corrections"][0]

    assert got["energy_correction_scheme_ref"] == scheme.public_ref
    assert got["energy_correction_scheme_name"] == "ptr-bac"
    assert got["energy_correction_scheme_kind"] == "bac_petersson"
    assert got["frequency_scale_factor_ref"] is None


def test_frequency_scale_factor_sourced_correction(client, db_session):
    """The other provenance branch: a ZPE scaled by a stored factor."""
    entry, calc = _sp_calc_with_energy(db_session, "AECFSF")
    fsf = make_frequency_scale_factor(
        db_session, scale_kind=FrequencyScaleKind.zpe, value=0.9806
    )
    make_applied_energy_correction(
        db_session,
        target_species_entry=entry,
        frequency_scale_factor=fsf,
        source_calculation=calc,
        application_role=EnergyCorrectionApplicationRole.zpe,
        value=0.0213,
        value_unit=EnergyUnit.hartree,
    )

    body = client.get(
        _calc_url(calc.public_ref, include="energy_corrections")
    ).json()
    got = body["record"]["energy_corrections"][0]

    assert got["frequency_scale_factor_ref"] == fsf.public_ref
    assert got["energy_correction_scheme_ref"] is None
    assert got["application_role"] == "zpe"
    assert got["applied_value"] == 0.0213


def test_transition_state_target_is_typed(client, db_session):
    """A correction on a saddle point names its target as one."""
    tse = _ts_entry(db_session, "aec-ts")
    calc = make_calculation(
        db_session,
        type=CalculationType.sp,
        transition_state_entry_id=tse.id,
    )
    attach_sp_result(
        db_session,
        calculation=calc,
        electronic_energy_hartree=RAW_SP_ENERGY_HARTREE,
    )
    scheme = make_energy_correction_scheme(
        db_session, name="ts-bac", units=EnergyUnit.kcal_mol
    )
    make_applied_energy_correction(
        db_session,
        target_transition_state_entry=tse,
        scheme=scheme,
        source_calculation=calc,
        application_role=EnergyCorrectionApplicationRole.bac_total,
        value=BAC_KCAL_MOL,
        value_unit=EnergyUnit.kcal_mol,
    )

    body = client.get(
        _calc_url(calc.public_ref, include="energy_corrections")
    ).json()
    got = body["record"]["energy_corrections"][0]

    assert got["target_record_type"] == "transition_state_entry"
    assert got["target_record_ref"] == tse.public_ref
    assert got["target_endpoint"] == (
        f"/api/v1/scientific/transition-state-entries/{tse.public_ref}"
    )


def test_correction_of_another_calculation_does_not_leak_in(
    client, db_session
):
    """The join is on ``source_calculation_id`` and on nothing else.

    Two calculations on the *same* species entry, one corrected. The
    correction belongs to the energy it was computed against, so it must
    appear on that calculation and not on its sibling — otherwise the
    reader is told a number applies to an energy it does not.
    """
    entry, corrected_calc = _sp_calc_with_energy(db_session, "AECSIB")
    sibling = make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry.id
    )
    attach_sp_result(
        db_session, calculation=sibling, electronic_energy_hartree=-78.6
    )
    _bac_applied(
        db_session, entry=entry, calc=corrected_calc, scheme_name="sib-bac"
    )

    corrected = client.get(
        _calc_url(corrected_calc.public_ref, include="energy_corrections")
    ).json()["record"]
    other = client.get(
        _calc_url(sibling.public_ref, include="energy_corrections")
    ).json()["record"]

    assert len(corrected["energy_corrections"]) == 1
    assert other["energy_corrections"] == []
    assert corrected["available_sections"]["has_energy_corrections"] is True
    assert other["available_sections"]["has_energy_corrections"] is False


# ===========================================================================
# Include-token plumbing
# ===========================================================================


def test_include_all_expands_to_energy_corrections(client, db_session):
    entry, calc = _sp_calc_with_energy(db_session, "AECALL")
    _bac_applied(db_session, entry=entry, calc=calc, scheme_name="all-bac")

    body = client.get(_calc_url(calc.public_ref, include="all")).json()

    assert "energy_corrections" in body["request"]["include"]
    assert len(body["record"]["energy_corrections"]) == 1


def test_internal_id_is_stripped_by_default(client, db_session):
    entry, calc = _sp_calc_with_energy(db_session, "AECIID")
    _bac_applied(db_session, entry=entry, calc=calc, scheme_name="iid-bac")

    body = client.get(
        _calc_url(calc.public_ref, include="energy_corrections")
    ).json()
    got = body["record"]["energy_corrections"][0]

    assert "applied_energy_correction_id" not in got
    assert "target_record_id" not in got
    # The ref-shaped identity survives the strip.
    assert got["target_record_ref"] is not None


def test_internal_id_restored_on_opt_in(
    client, db_session, allow_internal_ids
):
    entry, calc = _sp_calc_with_energy(db_session, "AECIIR")
    _, applied = _bac_applied(
        db_session, entry=entry, calc=calc, scheme_name="iir-bac"
    )

    body = client.get(
        _calc_url(
            calc.public_ref, include="energy_corrections,internal_ids"
        )
    ).json()
    got = body["record"]["energy_corrections"][0]

    assert got["applied_energy_correction_id"] == applied.id
    assert got["target_record_id"] == entry.id


# ===========================================================================
# The scheme surface — recipe and result together
# ===========================================================================


def test_used_by_carries_the_applied_value(client, db_session):
    """``include=used_by`` no longer serves a bare pointer."""
    entry, calc = _sp_calc_with_energy(db_session, "AECUSB")
    scheme, _ = _bac_applied(
        db_session, entry=entry, calc=calc, scheme_name="usedby-bac"
    )

    body = client.get(_ecs_url(scheme.public_ref, include="used_by")).json()
    usage = body["record"]["used_by"][0]

    assert usage["record_ref"] == entry.public_ref
    assert usage["applied_value"] == BAC_KCAL_MOL
    assert usage["applied_value_unit"] == "kcal_mol"
    assert usage["applied_value_hartree"] == pytest.approx(
        BAC_HARTREE, rel=1e-12
    )
    assert usage["application_role"] == "bac_total"
    assert usage["component_count"] == 1


def test_used_by_names_the_energy_it_corrects(client, db_session):
    """The usage entry points back at the calculation it was computed on.

    Without this the applied value is a magnitude attached to a species
    with no statement of what it is an addend to.
    """
    entry, calc = _sp_calc_with_energy(db_session, "AECUSC")
    scheme, _ = _bac_applied(
        db_session, entry=entry, calc=calc, scheme_name="usedcalc-bac"
    )

    body = client.get(_ecs_url(scheme.public_ref, include="used_by")).json()
    usage = body["record"]["used_by"][0]

    assert usage["source_calculation_ref"] == calc.public_ref
    assert usage["source_calculation_endpoint"] == (
        f"/api/v1/scientific/calculations/{calc.public_ref}"
    )

    # And that pointer resolves to the uncorrected energy.
    followed = client.get(
        _calc_url(calc.public_ref, include="results")
    ).json()
    assert (
        followed["record"]["results"]["sp"]["electronic_energy_hartree"]
        == RAW_SP_ENERGY_HARTREE
    )


def test_used_by_without_a_source_calculation_is_null_not_omitted(
    client, db_session
):
    """A correction that recorded no source calculation says so."""
    species = make_species(db_session, inchi_key=next_inchi_key("AECNSC"))
    entry = make_species_entry(db_session, species)
    scheme = make_energy_correction_scheme(
        db_session, name="nosrc-bac", units=EnergyUnit.kcal_mol
    )
    make_applied_energy_correction(
        db_session,
        target_species_entry=entry,
        scheme=scheme,
        application_role=EnergyCorrectionApplicationRole.bac_total,
        value=BAC_KCAL_MOL,
        value_unit=EnergyUnit.kcal_mol,
    )

    body = client.get(_ecs_url(scheme.public_ref, include="used_by")).json()
    usage = body["record"]["used_by"][0]

    assert "source_calculation_ref" in usage
    assert usage["source_calculation_ref"] is None
    assert usage["source_calculation_endpoint"] is None
    # The value is still there — the missing pointer does not withhold it.
    assert usage["applied_value"] == BAC_KCAL_MOL


def test_used_by_still_absent_without_the_token(client, db_session):
    """The pre-existing omission convention is unchanged."""
    entry, calc = _sp_calc_with_energy(db_session, "AECUSD")
    scheme, _ = _bac_applied(
        db_session, entry=entry, calc=calc, scheme_name="unasked-bac"
    )

    record = client.get(_ecs_url(scheme.public_ref)).json()["record"]

    assert "used_by" not in record
    assert record["evidence_summary"]["has_applied_usage"] is True


# ===========================================================================
# The components are the same numbers as the total
# ===========================================================================


def test_served_components_sum_to_the_served_total(client, db_session):
    """A reader who adds the served components reaches the served total.

    Both are read off stored rows and neither is recomputed, so this is an
    identity over a complete set rather than a coincidence of one fixture.
    It is the property that makes the breakdown usable as a reconciliation
    check: measured on all 164 applied corrections on the hosted instance,
    ``sum(contribution_value) == value`` exactly.

    The fixture mirrors a real Petersson BAC: several bond types, mixed
    signs, and a total that is not any single component restated.
    """
    entry, calc = _sp_calc_with_energy(db_session, "AECSUM")
    scheme = make_energy_correction_scheme(
        db_session, name="sum-bac", units=EnergyUnit.kcal_mol
    )
    parts = [
        (AppliedCorrectionComponentKind.bond, "C-C", 2, -2.214639347011169,
         -4.429278694022338),
        (AppliedCorrectionComponentKind.bond, "C-H", 6, -0.23472079981166077,
         -1.4083247988699648),
        (AppliedCorrectionComponentKind.bond, "C-O", 1, -2.0692673022727166,
         -2.0692673022727166),
        (AppliedCorrectionComponentKind.bond, "C=C", 1, -2.6818763979930718,
         -2.6818763979930718),
        (AppliedCorrectionComponentKind.bond, "H-S", 1, 0.2421215092503637,
         0.2421215092503637),
        (AppliedCorrectionComponentKind.bond, "O-S", 1, -2.7142930163638104,
         -2.7142930163638104),
    ]
    total = sum(p[4] for p in parts)
    make_applied_energy_correction(
        db_session,
        target_species_entry=entry,
        scheme=scheme,
        source_calculation=calc,
        application_role=EnergyCorrectionApplicationRole.bac_total,
        value=total,
        value_unit=EnergyUnit.kcal_mol,
        components=parts,
    )

    body = client.get(
        _calc_url(calc.public_ref, include="energy_corrections")
    ).json()
    got = body["record"]["energy_corrections"][0]

    assert got["components_truncated"] is False
    assert got["component_count"] == len(parts) == len(got["components"])
    assert sum(c["contribution_value"] for c in got["components"]) == (
        pytest.approx(got["applied_value"], rel=1e-12)
    )
    # The total is not merely one of the components restated.
    assert got["applied_value"] not in {
        c["contribution_value"] for c in got["components"]
    }


def test_a_truncated_breakdown_says_so_and_its_sum_is_short(
    client, db_session
):
    """Past the cap the flag fires, and the served rows no longer sum.

    The point of the flag: a partial set of full-precision contributions
    is exactly the shape a reader would mistake for a complete one, and
    its sum is a wrong correction rather than an obviously broken one.
    ``applied_value`` stays whole and ``component_count`` stays the full
    count, so nothing about the total is lost.
    """
    entry, calc = _sp_calc_with_energy(db_session, "AECTRN")
    scheme = make_energy_correction_scheme(
        db_session, name="trunc-bac", units=EnergyUnit.kcal_mol
    )
    over_cap = _CORRECTION_COMPONENT_LIMIT + 7
    parts = [
        (AppliedCorrectionComponentKind.bond, f"X{i:04d}-H", 1, -0.01, -0.01)
        for i in range(over_cap)
    ]
    total = sum(p[4] for p in parts)
    make_applied_energy_correction(
        db_session,
        target_species_entry=entry,
        scheme=scheme,
        source_calculation=calc,
        application_role=EnergyCorrectionApplicationRole.bac_total,
        value=total,
        value_unit=EnergyUnit.kcal_mol,
        components=parts,
    )

    body = client.get(
        _calc_url(calc.public_ref, include="energy_corrections")
    ).json()
    got = body["record"]["energy_corrections"][0]

    assert got["components_truncated"] is True
    assert got["component_count"] == over_cap
    assert len(got["components"]) == _CORRECTION_COMPONENT_LIMIT
    # The whole correction survives intact ...
    assert got["applied_value"] == pytest.approx(total, rel=1e-12)
    # ... and the served rows demonstrably do not reconstitute it, which is
    # precisely why the flag has to be read before summing them.
    served_sum = sum(c["contribution_value"] for c in got["components"])
    assert served_sum != pytest.approx(got["applied_value"], rel=1e-9)
    assert abs(served_sum) < abs(got["applied_value"])
