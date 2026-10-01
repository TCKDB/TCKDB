"""A stated core treatment reaches every ``LevelOfTheorySummary`` builder (ADR 0021, P4).

Five of the summary builders do not hold a ``LevelOfTheory`` row: they select
columns and build the summary from positional tuples or small carriers (the
thermo and kinetics calculation metadata, the reaction-full provenance rows, the
species calculation search rows, and the levels-by-owner read). Each had
``core_treatment`` appended as a selected column. A builder that forgot to pass
it, or that read the wrong tuple index, would report a frozen-core level as
"not stated" and pass every test that only uses levels with no core treatment.

Every test here uses a level that states ``frozen_core`` and a second that
states ``all_electron``, and asserts the value read back, so a builder that
returns ``None`` or reads another column fails.
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

STATES = ["frozen_core", "all_electron"]


def _level(db_session, core_treatment: str):
    lot = resolve_level_of_theory_ref(
        db_session,
        LevelOfTheoryRef(method="CCSD(T)", basis="cc-pCVTZ", core_treatment=core_treatment),
    )
    db_session.flush()
    return lot


def _unstated(db_session):
    lot = resolve_level_of_theory_ref(db_session, LevelOfTheoryRef(method="CCSD(T)", basis="cc-pCVTZ"))
    db_session.flush()
    return lot


def _owned_calc(db_session, lot, calc_type=CalculationType.sp):
    entry = make_species_entry(db_session, make_species(db_session, inchi_key=next_inchi_key("CTRT")))
    return make_calculation(db_session, type=calc_type, species_entry_id=entry.id, lot_id=lot.id)


@pytest.mark.parametrize("state", STATES)
def test_thermo_level_summary_reads_the_stated_core_treatment(db_session, state):
    calc = _owned_calc(db_session, _level(db_session, state))
    meta = thermo_read._calc_lot_meta(db_session, {calc.id})[calc.id]
    assert thermo_read._lot_summary(meta).core_treatment.value == state


def test_thermo_level_summary_reads_null_for_an_unstated_level(db_session):
    calc = _owned_calc(db_session, _unstated(db_session))
    meta = thermo_read._calc_lot_meta(db_session, {calc.id})[calc.id]
    assert thermo_read._lot_summary(meta).core_treatment is None


@pytest.mark.parametrize("state", STATES)
def test_kinetics_level_summary_reads_the_stated_core_treatment(db_session, state):
    calc = _owned_calc(db_session, _level(db_session, state))
    meta = kinetics_read._calc_metadata(db_session, {calc.id})[calc.id]
    assert kinetics_read._lot_summary_for_calc(meta).core_treatment.value == state


def test_kinetics_level_summary_reads_null_for_an_unstated_level(db_session):
    calc = _owned_calc(db_session, _unstated(db_session))
    meta = kinetics_read._calc_metadata(db_session, {calc.id})[calc.id]
    assert kinetics_read._lot_summary_for_calc(meta).core_treatment is None


@pytest.mark.parametrize("state", STATES)
def test_reaction_full_calculation_evidence_reads_the_stated_core_treatment(db_session, state):
    ts, (entry,) = _ts_entry(db_session)
    lot = _level(db_session, state)
    make_calculation(
        db_session, type=CalculationType.opt, transition_state_entry_id=entry.id, lot_id=lot.id
    )
    items = provenance_read._build_calculations_section(db_session, ts.reaction_entry_id)
    assert [i.level_of_theory.core_treatment.value for i in items] == [state]


def test_reaction_full_calculation_evidence_reads_null_for_an_unstated_level(db_session):
    ts, (entry,) = _ts_entry(db_session)
    make_calculation(
        db_session,
        type=CalculationType.opt,
        transition_state_entry_id=entry.id,
        lot_id=_unstated(db_session).id,
    )
    items = provenance_read._build_calculations_section(db_session, ts.reaction_entry_id)
    assert [i.level_of_theory.core_treatment for i in items] == [None]


@pytest.mark.parametrize("state", STATES)
def test_species_calculation_search_reads_the_stated_core_treatment(client, db_session, state):
    species = make_species(db_session, smiles="C[CH2]", inchi_key=next_inchi_key("CTSR"))
    entry = make_species_entry(db_session, species)
    make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry.id, lot_id=_level(db_session, state).id
    )
    body = client.get("/api/v1/scientific/species-calculations/search?smiles=C[CH2]").json()
    [record] = body["records"]
    assert record["level_of_theory"]["core_treatment"] == state


def test_species_calculation_search_reads_null_for_an_unstated_level(client, db_session):
    species = make_species(db_session, smiles="OCCO", inchi_key=next_inchi_key("CTSN"))
    entry = make_species_entry(db_session, species)
    make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry.id, lot_id=_unstated(db_session).id
    )
    [record] = client.get("/api/v1/scientific/species-calculations/search?smiles=OCCO").json()["records"]
    assert record["level_of_theory"]["core_treatment"] is None


@pytest.mark.parametrize("state", STATES)
def test_levels_by_owner_read_reports_the_stated_core_treatment(client, db_session, state):
    _, (entry,) = _ts_entry(db_session)
    make_calculation(
        db_session,
        type=CalculationType.opt,
        transition_state_entry_id=entry.id,
        lot_id=_level(db_session, state).id,
    )
    levels = client.get(_TS_ENTRY_URL.format(entry.public_ref)).json()["record"]["evidence_summary"][
        "levels_of_theory"
    ]
    assert [lot["core_treatment"] for lot in levels["opt"]] == [state]


def test_levels_by_owner_read_keeps_two_core_treatments_apart(client, db_session):
    """Same method and basis, different core treatment: two levels, each reported as it is."""
    ts, (first, second) = _ts_entry(db_session, n_entries=2)
    for entry, state in ((first, "frozen_core"), (second, "all_electron")):
        make_calculation(
            db_session,
            type=CalculationType.opt,
            transition_state_entry_id=entry.id,
            lot_id=_level(db_session, state).id,
        )
    levels = client.get(f"/api/v1/scientific/transition-states/{ts.public_ref}").json()["record"][
        "evidence_summary"
    ]["levels_of_theory"]
    assert sorted(lot["core_treatment"] for lot in levels["opt"]) == ["all_electron", "frozen_core"]
