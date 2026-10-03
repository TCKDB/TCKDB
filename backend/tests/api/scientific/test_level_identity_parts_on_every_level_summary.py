"""Every part of a level's identity that the notation writes reaches every ``LevelOfTheorySummary`` builder (ADR 0021, P7a).

The notation of a record's levels and the ML export's label write the auxiliary and CABS basis, the solvent model,
and the spin and core treatment, so the summaries they are built from must carry them. Five builders do not hold a
``LevelOfTheory`` row: they select columns and build the summary from positional tuples or small carriers. The AST
invariant (``test_level_summary_carries_composite_scheme.py``) only checks that each keyword is *passed*; a loader
that passes ``None``, or reads the wrong tuple index, satisfies it and silently drops the part from every notation.
So this reads the values back, one per part per builder.

The level states every part at once, each with a distinct value, so a builder that reads another column's value
(a swapped index) fails as well as one that returns ``None``.
"""

from __future__ import annotations

import pytest
from tckdb_schemas.fragments.refs import LevelOfTheoryRef

from app.db.models.common import CalculationType
from app.services.calculation_resolution import resolve_level_of_theory_ref
from app.services.scientific_read import kinetics as kinetics_read
from app.services.scientific_read import provenance as provenance_read
from app.services.scientific_read import thermo as thermo_read
from tests.api.scientific.test_levels_of_theory_on_evidence_summary import _TS_ENTRY_URL, _ts_entry
from tests.services.scientific_read._factories import (
    make_calculation,
    make_species,
    make_species_entry,
    next_inchi_key,
)

#: Each part with the value the level below states for it, as the summary reports it.
PARTS = {
    "aux_basis": "cc-pVTZ/C",
    "cabs_basis": "cc-pVTZ-F12-CABS",
    "solvent_model": "smd",
    "spin_treatment": "unrestricted",
    "core_treatment": "frozen_core",
}
EXPECTED_LABEL = (
    "CCSD(T)-F12/cc-pVTZ-F12 (aux=cc-pVTZ/C, cabs=cc-pVTZ-F12-CABS, disp=D3BJ, solvent=smd:water, "
    "spin=unrestricted, core=frozen_core)"
)


def _full_level(db_session):
    lot = resolve_level_of_theory_ref(
        db_session,
        LevelOfTheoryRef(
            method="CCSD(T)-F12",
            basis="cc-pVTZ-F12",
            aux_basis=PARTS["aux_basis"],
            cabs_basis=PARTS["cabs_basis"],
            dispersion="D3BJ",
            solvent="water",
            solvent_model=PARTS["solvent_model"],
            spin_treatment=PARTS["spin_treatment"],
            core_treatment=PARTS["core_treatment"],
        ),
    )
    db_session.flush()
    return lot


def _value(summary, part: str):
    got = getattr(summary, part) if not isinstance(summary, dict) else summary[part]
    return getattr(got, "value", got)


def _owned_calc(db_session, lot):
    entry = make_species_entry(db_session, make_species(db_session, inchi_key=next_inchi_key("IDPT")))
    return make_calculation(db_session, type=CalculationType.sp, species_entry_id=entry.id, lot_id=lot.id)


def test_the_fixture_level_states_every_part_it_is_asserted_on(db_session):
    lot = _full_level(db_session)
    assert {part: _value(lot, part) for part in PARTS} == PARTS
    assert (lot.dispersion, lot.solvent) == ("D3BJ", "water")


@pytest.mark.parametrize("part", PARTS)
def test_thermo_level_summary_reads_the_part(db_session, part):
    calc = _owned_calc(db_session, _full_level(db_session))
    meta = thermo_read._calc_lot_meta(db_session, {calc.id})[calc.id]
    assert _value(thermo_read._lot_summary(meta), part) == PARTS[part]


@pytest.mark.parametrize("part", PARTS)
def test_kinetics_level_summary_reads_the_part(db_session, part):
    calc = _owned_calc(db_session, _full_level(db_session))
    meta = kinetics_read._calc_metadata(db_session, {calc.id})[calc.id]
    assert _value(kinetics_read._lot_summary_for_calc(meta), part) == PARTS[part]


@pytest.mark.parametrize("part", PARTS)
def test_reaction_full_calculation_evidence_reads_the_part(db_session, part):
    ts, (entry,) = _ts_entry(db_session)
    make_calculation(
        db_session,
        type=CalculationType.opt,
        transition_state_entry_id=entry.id,
        lot_id=_full_level(db_session).id,
    )
    (item,) = provenance_read._build_calculations_section(db_session, ts.reaction_entry_id)
    assert _value(item.level_of_theory, part) == PARTS[part]


@pytest.mark.parametrize("part", PARTS)
def test_species_calculation_search_reads_the_part(client, db_session, part):
    species = make_species(db_session, smiles="C[CH2]", inchi_key=next_inchi_key("IDSR"))
    entry = make_species_entry(db_session, species)
    make_calculation(db_session, type=CalculationType.sp, species_entry_id=entry.id, lot_id=_full_level(db_session).id)
    [record] = client.get("/api/v1/scientific/species-calculations/search?smiles=C[CH2]").json()["records"]
    assert _value(record["level_of_theory"], part) == PARTS[part]


@pytest.mark.parametrize("part", PARTS)
def test_levels_by_owner_read_reports_the_part(client, db_session, part):
    _, (entry,) = _ts_entry(db_session)
    make_calculation(
        db_session,
        type=CalculationType.opt,
        transition_state_entry_id=entry.id,
        lot_id=_full_level(db_session).id,
    )
    levels = client.get(_TS_ENTRY_URL.format(entry.public_ref)).json()["record"]["evidence_summary"][
        "levels_of_theory"
    ]
    assert [_value(lot, part) for lot in levels["opt"]] == [PARTS[part]]


def test_the_notation_of_a_thermo_summary_writes_every_part(db_session):
    """The same summary, end to end: what the loaders carry is what the notation writes."""
    from app.schemas.reads.scientific_common import level_label

    calc = _owned_calc(db_session, _full_level(db_session))
    meta = thermo_read._calc_lot_meta(db_session, {calc.id})[calc.id]
    assert level_label(thermo_read._lot_summary(meta)) == EXPECTED_LABEL
    kmeta = kinetics_read._calc_metadata(db_session, {calc.id})[calc.id]
    assert level_label(kinetics_read._lot_summary_for_calc(kmeta)) == EXPECTED_LABEL
