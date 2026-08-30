"""Frequency results on ``/scientific/species-calculations/search``.

Before this surface projected them, a ``freq`` record carried exactly one
result key -- ``energy: null`` -- and the (since-deleted) public landing
page rendered a card titled "frequencies" whose only line read
*Electronic energy — not recorded*. The numbers were stored the whole
time: measured on the
deployed database, species entry ``spe_bcbdjwkip75yoziblpntwzblzu``
(``[CH3]``) has four frequency calculations, each with ``n_imag = 0``,
``zpe_hartree ~ 0.029723`` and six ``calc_freq_mode`` rows -- ``3N-6``
for four atoms, non-linear. Nothing read them back out.

The contract asserted here is the one ``energy`` already had, mirrored:

    key present, populated  -> this result belongs here and is recorded
    key present, all null   -> this result belongs here and is missing
    key ``null``            -> this kind of record has no such result

and, for the per-mode array, the third state the include machinery adds:

    key absent              -> you did not ask
"""

from __future__ import annotations

import pytest

from app.db.models.common import CalculationType
from tests.services.scientific_read._factories import (
    attach_freq_result,
    make_calculation,
    make_lot,
    make_species,
    make_species_entry,
)

_SEARCH = "/api/v1/scientific/species-calculations/search"

#: The six harmonic modes of a methyl radical, near enough. Six is
#: ``3N-6`` for four atoms, which is what makes this a realistic list
#: rather than an arbitrary one.
_CH3_MODES = [580.0, 1400.0, 1400.0, 3050.0, 3230.0, 3230.0]
_CH3_ZPE_HARTREE = 0.029723


@pytest.fixture
def methyl(db_session):
    """One species entry with a freq calc, an opt calc, and a bare freq calc.

    The third one is the case a single well-formed record cannot test: a
    ``freq`` calculation whose ``calc_freq_result`` row was never
    deposited. It is what separates "no frequency result belongs here"
    from "one belongs here and is missing".
    """
    session = db_session
    lot = make_lot(session, method="wb97xd", basis="def2tzvp")
    entry = make_species_entry(
        session, make_species(session, smiles="[CH3]", multiplicity=2)
    )
    freq = make_calculation(
        session,
        type=CalculationType.freq,
        species_entry_id=entry.id,
        lot_id=lot.id,
    )
    attach_freq_result(
        session,
        calculation=freq,
        frequencies_cm1=_CH3_MODES,
        zpe_hartree=_CH3_ZPE_HARTREE,
        reduced_masses_amu=[1.1, 1.05, 1.05, 1.03, 1.11, 1.11],
        force_constants_mdyne_angstrom=[0.22, 1.2, 1.2, 6.4, 6.6, 6.6],
        imaginary_mode_tau_cm1=50.0,
        imaginary_mode_tau_basis="analytic_default",
    )
    opt = make_calculation(
        session,
        type=CalculationType.opt,
        species_entry_id=entry.id,
        lot_id=lot.id,
    )
    unparsed = make_calculation(
        session,
        type=CalculationType.freq,
        species_entry_id=entry.id,
        lot_id=lot.id,
    )
    session.flush()
    return {
        "species_entry_ref": entry.public_ref,
        "freq_ref": freq.public_ref,
        "opt_ref": opt.public_ref,
        "unparsed_freq_ref": unparsed.public_ref,
    }


def _records(client, methyl, *include: str) -> dict[str, dict]:
    """Every record for the fixture entry, keyed by ``calculation_ref``."""
    query = "".join(f"&include={token}" for token in include)
    response = client.get(
        f"{_SEARCH}?species_entry_ref={methyl['species_entry_ref']}{query}"
    )
    assert response.status_code == 200, response.text
    body = response.json()
    return {r["calculation"]["calculation_ref"]: r for r in body["records"]}


# ---------------------------------------------------------------------------
# The summary block
# ---------------------------------------------------------------------------


def test_a_freq_record_carries_its_frequency_result(client, methyl):
    """The numbers on ``calc_freq_result`` reach the wire."""
    record = _records(client, methyl)[methyl["freq_ref"]]

    frequency = record["frequency"]
    assert frequency is not None, (
        "a freq calculation with a stored calc_freq_result row returned no "
        "frequency block; this is the defect the whole change is about"
    )
    assert frequency["n_imag"] == 0
    assert frequency["zpe_hartree"] == pytest.approx(_CH3_ZPE_HARTREE)
    assert frequency["imag_freq_cm1"] is None
    # ADR 0012's stored judgement travels with ``n_imag`` rather than
    # behind an include, on this surface as on /calculations/*.
    assert frequency["imaginary_mode_tau_cm1"] == pytest.approx(50.0)
    assert frequency["imaginary_mode_tau_basis"] == "analytic_default"
    assert frequency["n_imag_at_or_above_tau"] == 0


def test_the_two_transition_state_fields_are_null_on_a_species_minimum(
    client, methyl
):
    """Null there is an answer, not a gap.

    ``StationaryPointKind`` has no saddle-point member -- a species entry
    is a ``minimum`` or a ``vdw_complex`` -- so no calculation this
    surface returns has a designated reaction coordinate, and
    ``imaginary_mode_structural_flag`` is never judged. The fields are
    still served, because trimming the shared fragment into a
    species-only variant would give one table two public shapes for the
    sake of two keys whose ``null`` is correct.
    """
    frequency = _records(client, methyl)[methyl["freq_ref"]]["frequency"]

    assert frequency["reaction_coordinate_mode_index"] is None
    assert frequency["imaginary_mode_structural_flag"] is None
    # The other two ADR 0012 columns are *not* transition-state business:
    # tau is recorded for every frequency upload, minima included.
    assert frequency["imaginary_mode_tau_cm1"] is not None
    assert frequency["imaginary_mode_tau_basis"] is not None


def test_a_non_freq_record_carries_no_frequency_block(client, methyl):
    """``frequency: null`` is the mirror of ``energy: null`` on a freq record."""
    opt = _records(client, methyl)[methyl["opt_ref"]]

    assert opt["frequency"] is None, (
        "an opt calculation produces no frequency result, so the block must "
        "read null rather than an all-null object -- the second would say "
        "'one belongs here and is missing'"
    )
    # And the block it *does* carry is still there, unchanged.
    assert opt["energy"] == {"energy_hartree": None, "energy_kind": "final_energy"}


def test_the_two_result_blocks_are_exact_mirrors(client, methyl):
    """Whatever ``energy`` does on a freq record, ``frequency`` does on an opt one."""
    records = _records(client, methyl)
    freq = records[methyl["freq_ref"]]
    opt = records[methyl["opt_ref"]]

    assert freq["energy"] is None and freq["frequency"] is not None
    assert opt["frequency"] is None and opt["energy"] is not None


def test_a_freq_record_with_no_stored_result_keeps_the_block(client, methyl):
    """Present-and-empty is the middle state, and it must survive.

    A ``freq`` calculation whose ``calc_freq_result`` row was never
    deposited gets an all-null block, not ``null``. Collapsing it into
    ``null`` would make "this job produced no frequencies" and "this job
    produced frequencies nobody parsed" the same wire value.
    """
    record = _records(client, methyl)[methyl["unparsed_freq_ref"]]

    frequency = record["frequency"]
    assert frequency is not None
    assert set(frequency.values()) == {None}


def test_an_imaginary_mode_on_a_claimed_minimum_is_visible(client, db_session):
    """The count above tau is the reason the ADR 0012 fields are kept here.

    A species entry is filed as a ``minimum``. If its frequency
    calculation carries an imaginary mode above the protocol's noise
    floor, it is not one — and this surface is where a consumer picking
    a geometry to reuse would otherwise never find that out. The count
    is derived by comparing two persisted numbers (stored ``|omega|``
    against stored tau) and resolves nothing.
    """
    session = db_session
    lot = make_lot(session, method="wb97xd", basis="def2svp")
    entry = make_species_entry(
        session, make_species(session, smiles="CCO", multiplicity=1)
    )
    calc = make_calculation(
        session,
        type=CalculationType.freq,
        species_entry_id=entry.id,
        lot_id=lot.id,
    )
    attach_freq_result(
        session,
        calculation=calc,
        # One mode well below the noise floor, one well above it.
        frequencies_cm1=[-18.0, -240.0, 900.0, 1600.0],
        zpe_hartree=0.05,
        imaginary_mode_tau_cm1=50.0,
        imaginary_mode_tau_basis="analytic_default",
    )
    session.flush()

    response = client.get(
        f"{_SEARCH}?species_entry_ref={entry.public_ref}&include=freq_modes"
    )
    assert response.status_code == 200, response.text
    record = response.json()["records"][0]

    assert record["frequency"]["n_imag"] == 2
    assert record["frequency"]["imag_freq_cm1"] == pytest.approx(-18.0)
    assert record["frequency"]["n_imag_at_or_above_tau"] == 1, (
        "two imaginary modes are stored and one of them clears tau=50; "
        "reporting 2 would ignore the noise floor and reporting 0 would "
        "ignore the mode that cleared it"
    )
    imaginary = [m for m in record["freq_modes"] if m["is_imaginary"]]
    assert [m["frequency_cm1"] for m in imaginary] == [-18.0, -240.0]


# ---------------------------------------------------------------------------
# The include gate on the per-mode array
# ---------------------------------------------------------------------------


def test_the_per_mode_array_is_absent_until_it_is_asked_for(client, methyl):
    for record in _records(client, methyl).values():
        assert "freq_modes" not in record, (
            "the per-mode array is the one block on this surface that grows "
            "with both the molecule and the page size; it must not ride "
            "along on a default search"
        )


def test_include_freq_modes_returns_the_full_ordered_array(client, methyl):
    record = _records(client, methyl, "freq_modes")[methyl["freq_ref"]]

    modes = record["freq_modes"]
    assert [m["mode_index"] for m in modes] == [1, 2, 3, 4, 5, 6]
    assert [m["frequency_cm1"] for m in modes] == _CH3_MODES
    assert all(m["is_imaginary"] is False for m in modes)
    assert modes[0]["reduced_mass_amu"] == pytest.approx(1.1)
    assert modes[0]["force_constant_mdyne_angstrom"] == pytest.approx(0.22)


def test_a_requested_but_modeless_record_gets_an_empty_list(client, methyl):
    """Asked and there are none is ``[]``, which is not the same as absent."""
    records = _records(client, methyl, "freq_modes")

    assert records[methyl["opt_ref"]]["freq_modes"] == []
    assert records[methyl["unparsed_freq_ref"]]["freq_modes"] == []


def test_include_all_expands_to_the_per_mode_array(client, methyl):
    """Bounded by ``3N-6``, so it belongs in ``all`` -- unlike ``points``.

    The tabulated-kinetics ``points`` include is held out of ``all``
    because a k(T,P) table has no bound. A frequency list does: the
    molecule's degrees of freedom. That is the distinction, and it is why
    this token is public rather than internal.
    """
    response = client.get(
        f"{_SEARCH}?species_entry_ref={methyl['species_entry_ref']}&include=all"
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert "freq_modes" in body["request"]["include"]
    by_ref = {r["calculation"]["calculation_ref"]: r for r in body["records"]}
    assert len(by_ref[methyl["freq_ref"]]["freq_modes"]) == len(_CH3_MODES)


def test_the_post_variant_serves_the_same_shape(client, methyl):
    """GET and POST parity, gate included."""
    body = {"species_entry_ref": methyl["species_entry_ref"]}

    default = client.post(_SEARCH, json=body)
    assert default.status_code == 200, default.text
    gated = client.post(_SEARCH, json={**body, "include": ["freq_modes"]})
    assert gated.status_code == 200, gated.text

    def by_ref(response):
        return {
            r["calculation"]["calculation_ref"]: r
            for r in response.json()["records"]
        }

    plain = by_ref(default)[methyl["freq_ref"]]
    assert plain["frequency"]["zpe_hartree"] == pytest.approx(_CH3_ZPE_HARTREE)
    assert "freq_modes" not in plain

    expanded = by_ref(gated)[methyl["freq_ref"]]
    assert len(expanded["freq_modes"]) == len(_CH3_MODES)
