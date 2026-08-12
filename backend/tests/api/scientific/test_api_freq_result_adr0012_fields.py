"""``n_imag`` never travels alone on an ordinary frequency read.

ADR 0012 §"What a record must carry" says ``n_imag`` must be accompanied
by the count above tau, the tau used, and how tau was chosen. Until this
file, the ordinary ``include=results`` projection of ``calc_freq_result``
carried ``n_imag`` and ``imag_freq_cm1`` and none of the rest; the tau,
its basis and the structural flag were reachable only through the
``include=imaginary_mode_projections`` heavy include, and only down its
*not determinable* branch.

The sharp end is the structural flag. It is the signal that excludes a
record from default transition-state consumption, so a consumer filtering
on ``n_imag == 1`` was being handed records ADR 0012 considers excluded
with nothing in the payload saying so. That is the specific defect these
tests pin, and it is the reason the fields go on the *cheap* projection
rather than a second opt-in block: a flag a consumer has to ask for is a
flag that does not protect the consumer who did not know to ask.

Nothing here is recomputed. Every field is read straight off
``calc_freq_result`` as the upload wrote it, which is ADR 0012's own
requirement -- recomputing tau at read time would let a parser
improvement silently re-decide a historical record.
"""

from __future__ import annotations

import json

from app.db.models.common import CalculationType, ImaginaryModeDisposition
from tests.services.scientific_read._factories import (
    attach_freq_result,
    make_calculation,
    make_species,
    make_species_entry,
    next_inchi_key,
)

#: Every field ADR 0012 requires to travel with ``n_imag``, plus the two
#: that already did. Asserted as an exact key set rather than field by
#: field: the failure mode this file exists to prevent is a field that is
#: silently *absent*, and only an exact comparison catches a rename.
_EXPECTED_FREQ_KEYS = {
    "n_imag",
    "imag_freq_cm1",
    "zpe_hartree",
    "zpe_uncertainty_hartree",
    "reaction_coordinate_mode_index",
    "imaginary_mode_tau_cm1",
    "imaginary_mode_tau_basis",
    "imaginary_mode_structural_flag",
    "n_imag_at_or_above_tau",
}


def _freq_calc(db_session):
    species = make_species(db_session, inchi_key=next_inchi_key("FRQ"))
    entry = make_species_entry(db_session, species)
    return make_calculation(
        db_session,
        type=CalculationType.freq,
        species_entry_id=entry.id,
    )


def _read_freq_block(client, calc):
    resp = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}?include=results"
    )
    assert resp.status_code == 200, resp.text
    results = resp.json()["record"]["results"]
    assert results["kind"] == "freq"
    freq = results["freq"]
    print(json.dumps(freq, indent=2, sort_keys=True))
    return freq


def test_default_results_read_carries_the_whole_adr_0012_judgement(
    client, db_session
):
    """ADR 0012's motivating record, read the ordinary way.

    -1300 / -42 / -13 cm-1: a correct saddle point whose reaction
    coordinate is mode 1, judged at tau = 15 cm-1 on the analytic-tight
    row of the protocol table, and flagged because -42 sits above that
    tau with a declared disposition. Every one of those facts is on the
    cheap projection.
    """
    calc = _freq_calc(db_session)
    attach_freq_result(
        db_session,
        calculation=calc,
        frequencies_cm1=[-1300.0, -42.0, -13.0, 620.0],
        zpe_hartree=0.0431,
        imaginary_dispositions=[
            None,
            ImaginaryModeDisposition.torsion,
            ImaginaryModeDisposition.rigid_body_residue,
            None,
        ],
        reaction_coordinate_mode_index=1,
        imaginary_mode_tau_cm1=15.0,
        imaginary_mode_tau_basis="analytic_tight",
        imaginary_mode_structural_flag=True,
    )

    freq = _read_freq_block(client, calc)

    assert set(freq) == _EXPECTED_FREQ_KEYS
    assert freq["n_imag"] == 3
    assert freq["imag_freq_cm1"] == -1300.0
    assert freq["reaction_coordinate_mode_index"] == 1
    assert freq["imaginary_mode_tau_cm1"] == 15.0
    assert freq["imaginary_mode_tau_basis"] == "analytic_tight"
    assert freq["imaginary_mode_structural_flag"] is True
    # -1300 and -42 are at or above tau; -13 is below it.
    assert freq["n_imag_at_or_above_tau"] == 2


def test_a_flagged_first_order_saddle_says_so_without_an_opt_in(
    client, db_session
):
    """The case the flag exists for, and the one that was invisible.

    A consumer filtering on ``n_imag == 1`` is selecting records it
    believes are clean first-order saddles. A record can report
    ``n_imag == 1`` *and* carry the structural flag -- ADR 0012's whole
    argument is that the count is a property of the protocol, not of the
    structure -- and before this change the ordinary read gave that
    consumer no way to tell the two apart.
    """
    calc = _freq_calc(db_session)
    attach_freq_result(
        db_session,
        calculation=calc,
        frequencies_cm1=[-870.0, 410.0],
        reaction_coordinate_mode_index=1,
        imaginary_mode_tau_cm1=50.0,
        imaginary_mode_tau_basis="finite_difference_gradient",
        imaginary_mode_structural_flag=True,
    )

    freq = _read_freq_block(client, calc)

    assert freq["n_imag"] == 1
    assert freq["imaginary_mode_structural_flag"] is True, (
        "a record excluded from default transition-state consumption must "
        "say so on the projection a consumer actually reads"
    )
    assert freq["imaginary_mode_tau_cm1"] == 50.0
    assert freq["imaginary_mode_tau_basis"] == "finite_difference_gradient"
    assert freq["n_imag_at_or_above_tau"] == 1


def test_never_judged_reports_nulls_rather_than_a_clean_bill(client, db_session):
    """A record deposited before ADR 0012 was never judged under it.

    All four persisted fields are NULL on such a record, and the
    projection reports NULL rather than ``false``/``0``: "not judged" and
    "judged and not flagged" are different facts and collapsing them
    would be exactly the claim the migration refused to backfill.

    ``n_imag_at_or_above_tau`` is null for the same reason -- there is no
    tau to count against -- even though ``n_imag`` and the mode rows are
    both present.
    """
    calc = _freq_calc(db_session)
    attach_freq_result(
        db_session,
        calculation=calc,
        frequencies_cm1=[-412.0, 700.0],
        zpe_hartree=0.01,
    )

    freq = _read_freq_block(client, calc)

    assert set(freq) == _EXPECTED_FREQ_KEYS
    assert freq["n_imag"] == 1
    assert freq["reaction_coordinate_mode_index"] is None
    assert freq["imaginary_mode_tau_cm1"] is None
    assert freq["imaginary_mode_tau_basis"] is None
    assert freq["imaginary_mode_structural_flag"] is None
    assert freq["n_imag_at_or_above_tau"] is None


def test_a_minimum_counts_zero_against_its_tau(client, db_session):
    """Tau recorded, no imaginary modes: the count is 0, not null.

    The distinction matters in the opposite direction from the test
    above. Here tau *is* known, so "nothing above it" is a measurement
    and reporting null would understate what the record says.
    """
    calc = _freq_calc(db_session)
    attach_freq_result(
        db_session,
        calculation=calc,
        frequencies_cm1=[120.0, 700.0, 3100.0],
        imaginary_mode_tau_cm1=30.0,
        imaginary_mode_tau_basis="analytic_default",
    )

    freq = _read_freq_block(client, calc)

    assert freq["n_imag"] == 0
    assert freq["n_imag_at_or_above_tau"] == 0
    assert freq["imaginary_mode_tau_basis"] == "analytic_default"
