"""Phase B regression tests: every scientific read endpoint exposes ``*_ref``
siblings for every ``*_id`` field, and the legacy id fields are preserved.

The point of this file is not to re-exercise the per-endpoint behavior
(that's covered by the other ``test_*.py`` modules) but to assert the
Phase B public-identifier contract: every response now carries a public
ref alongside the integer id.
"""

from __future__ import annotations

import re

from app.db.models.common import (
    CalculationType,
)
from app.schemas.reads.scientific_kinetics import KineticsReadRequest
from app.schemas.reads.scientific_kinetics_search import KineticsSearchRequest
from app.schemas.reads.scientific_provenance import (
    ReactionFullReadRequest,
    ReviewDetail,
)
from app.schemas.reads.scientific_reactions import (
    ReactionDirectionQuery,
    ReactionSearchRequest,
)
from app.schemas.reads.scientific_species import SpeciesSearchRequest
from app.schemas.reads.scientific_species_calculations import (
    SpeciesCalculationsSearchRequest,
)
from app.schemas.reads.scientific_thermo import ThermoReadRequest
from app.schemas.reads.scientific_thermo_search import ThermoSearchRequest
from app.services.scientific_read.kinetics import get_reaction_kinetics
from app.services.scientific_read.kinetics_search import search_kinetics
from app.services.scientific_read.provenance import get_reaction_full
from app.services.scientific_read.reactions import search_reactions
from app.services.scientific_read.species import search_species
from app.services.scientific_read.species_calculations_search import (
    search_species_calculations,
)
from app.services.scientific_read.thermo import get_species_thermo
from app.services.scientific_read.thermo_search import search_thermo
from tests.services.scientific_read._factories import (
    make_calculation,
    make_chem_reaction,
    make_kinetics,
    make_lot,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_thermo_scalar,
    next_inchi_key,
)

# Public refs follow ``<prefix>_<26-char body>`` (see PublicRefMixin).
_REF_PATTERN = re.compile(r"^[a-z]+_[A-Za-z0-9]+$")


def _looks_like_ref(value: str) -> bool:
    return isinstance(value, str) and bool(_REF_PATTERN.match(value))


# ---------------------------------------------------------------------------
# search_species
# ---------------------------------------------------------------------------


def test_search_species_exposes_refs(db_session):
    # Use a SMILES unlikely to collide with other tests' fixture rows that
    # may have leaked into the shared dev DB during prior runs.
    species = make_species(
        db_session, smiles="C#CC#C", inchi_key=next_inchi_key("REF1")
    )
    entry = make_species_entry(db_session, species)

    response = search_species(
        db_session, SpeciesSearchRequest(smiles="C#CC#C")
    )

    matching = [r for r in response.records if r.species_id == species.id]
    assert len(matching) == 1
    record = matching[0]
    assert record.species_id == species.id
    assert record.species_ref == species.public_ref
    assert _looks_like_ref(record.species_ref)

    assert len(record.entries) == 1
    entry_record = record.entries[0]
    assert entry_record.species_entry_id == entry.id
    assert entry_record.species_entry_ref == entry.public_ref
    assert _looks_like_ref(entry_record.species_entry_ref)


# ---------------------------------------------------------------------------
# search_reactions
# ---------------------------------------------------------------------------


def test_search_reactions_exposes_refs(db_session):
    a = make_species(db_session, smiles="C#CC#CCC", inchi_key=next_inchi_key("RX1A"))
    b = make_species(db_session, smiles="C#CCC#CC", inchi_key=next_inchi_key("RX1B"))
    a_entry = make_species_entry(db_session, a)
    b_entry = make_species_entry(db_session, b)
    chem = make_chem_reaction(db_session, reactants=[a], products=[b])
    entry = make_reaction_entry(
        db_session,
        reaction=chem,
        reactant_entries=[a_entry],
        product_entries=[b_entry],
    )

    response = search_reactions(
        db_session,
        ReactionSearchRequest(
            reactants=["C#CC#CCC"],
            products=["C#CCC#CC"],
            direction=ReactionDirectionQuery.forward,
        ),
    )

    matching = [r for r in response.records if r.reaction_entry_id == entry.id]
    assert len(matching) == 1
    rec = matching[0]
    assert rec.reaction_id == chem.id and rec.reaction_ref == chem.public_ref
    assert (
        rec.reaction_entry_id == entry.id
        and rec.reaction_entry_ref == entry.public_ref
    )
    for participant in [*rec.reactants, *rec.products]:
        assert _looks_like_ref(participant.species_entry_ref)


# ---------------------------------------------------------------------------
# get_species_thermo / search_thermo
# ---------------------------------------------------------------------------


def test_get_species_thermo_exposes_refs(db_session):
    species = make_species(
        db_session, smiles="C#CC#CCO", inchi_key=next_inchi_key("TH1")
    )
    entry = make_species_entry(db_session, species)
    thermo = make_thermo_scalar(db_session, species_entry=entry)

    response = get_species_thermo(
        db_session, species_entry_id=entry.id, request=ThermoReadRequest()
    )

    assert response.species_entry_id == entry.id
    assert response.species_entry_ref == entry.public_ref
    assert _looks_like_ref(response.species_entry_ref)

    record = response.records[0]
    assert record.thermo_id == thermo.id
    assert record.thermo_ref == thermo.public_ref
    assert _looks_like_ref(record.thermo_ref)


def test_search_thermo_exposes_species_and_thermo_refs(db_session):
    species = make_species(
        db_session, smiles="C#CC#CCOC", inchi_key=next_inchi_key("THS1")
    )
    entry = make_species_entry(db_session, species)
    thermo = make_thermo_scalar(db_session, species_entry=entry)

    response = search_thermo(
        db_session, ThermoSearchRequest(smiles="C#CC#CCOC")
    )

    matching = [r for r in response.records if r.thermo.thermo_id == thermo.id]
    assert len(matching) == 1
    rec = matching[0]
    assert rec.species.species_id == species.id
    assert rec.species.species_ref == species.public_ref
    assert rec.species.species_entry_id == entry.id
    assert rec.species.species_entry_ref == entry.public_ref
    assert rec.thermo.thermo_id == thermo.id
    assert rec.thermo.thermo_ref == thermo.public_ref


# ---------------------------------------------------------------------------
# get_reaction_kinetics / search_kinetics
# ---------------------------------------------------------------------------


def _seed_reaction_with_kinetics(db_session):
    a = make_species(
        db_session, smiles="C#CC#CCCC", inchi_key=next_inchi_key("KX1A")
    )
    b = make_species(
        db_session, smiles="C#CCC#CCC", inchi_key=next_inchi_key("KX1B")
    )
    a_entry = make_species_entry(db_session, a)
    b_entry = make_species_entry(db_session, b)
    chem = make_chem_reaction(db_session, reactants=[a], products=[b])
    reaction_entry = make_reaction_entry(
        db_session,
        reaction=chem,
        reactant_entries=[a_entry],
        product_entries=[b_entry],
    )
    kinetics = make_kinetics(db_session, reaction_entry=reaction_entry)
    return chem, reaction_entry, kinetics


def test_get_reaction_kinetics_exposes_refs(db_session):
    _, reaction_entry, kinetics = _seed_reaction_with_kinetics(db_session)

    response = get_reaction_kinetics(
        db_session, reaction_entry_id=reaction_entry.id, request=KineticsReadRequest()
    )

    assert response.reaction_entry_id == reaction_entry.id
    assert response.reaction_entry_ref == reaction_entry.public_ref
    assert _looks_like_ref(response.reaction_entry_ref)

    record = response.records[0]
    assert record.kinetics_id == kinetics.id
    assert record.kinetics_ref == kinetics.public_ref
    assert _looks_like_ref(record.kinetics_ref)
    # Provenance keys must always be present per Phase 2.2; refs are nullable.
    assert hasattr(record.provenance, "transition_state_entry_ref")
    assert hasattr(record.provenance, "ts_opt_calculation_ref")


def test_search_kinetics_exposes_reaction_context_refs(db_session):
    chem, reaction_entry, kinetics = _seed_reaction_with_kinetics(db_session)

    response = search_kinetics(
        db_session,
        KineticsSearchRequest(
            reactants=["C#CC#CCCC"],
            products=["C#CCC#CCC"],
            direction=ReactionDirectionQuery.forward,
        ),
    )

    matching = [
        r for r in response.records if r.kinetics.kinetics_id == kinetics.id
    ]
    assert len(matching) == 1
    rec = matching[0]
    assert rec.reaction.reaction_ref == chem.public_ref
    assert rec.reaction.reaction_entry_ref == reaction_entry.public_ref
    assert rec.kinetics.kinetics_ref == kinetics.public_ref


# ---------------------------------------------------------------------------
# search_species_calculations
# ---------------------------------------------------------------------------


def test_search_species_calculations_exposes_refs(db_session):
    species = make_species(
        db_session, smiles="C#CC#CC", inchi_key=next_inchi_key("SC1")
    )
    entry = make_species_entry(db_session, species)
    lot = make_lot(db_session)
    calc = make_calculation(
        db_session,
        type=CalculationType.opt,
        species_entry_id=entry.id,
        lot_id=lot.id,
    )

    response = search_species_calculations(
        db_session,
        SpeciesCalculationsSearchRequest(smiles="C#CC#CC"),
    )

    matching = [r for r in response.records if r.calculation.calculation_id == calc.id]
    assert len(matching) == 1
    rec = matching[0]

    # Species context refs.
    assert rec.species.species_ref == species.public_ref
    assert rec.species.species_entry_ref == entry.public_ref

    # Calculation core ref.
    assert rec.calculation.calculation_id == calc.id
    assert rec.calculation.calculation_ref == calc.public_ref

    # Level of theory ref.
    assert rec.level_of_theory is not None
    assert rec.level_of_theory.level_of_theory_id == lot.id
    assert rec.level_of_theory.level_of_theory_ref == lot.public_ref

    # Geometry object arrays must coexist with the legacy id arrays.
    assert isinstance(rec.geometry.input_geometry_ids, list)
    assert isinstance(rec.geometry.input_geometries, list)
    assert isinstance(rec.geometry.output_geometry_ids, list)
    assert isinstance(rec.geometry.output_geometries, list)

    # Provenance: supporting calculations object array preserved alongside ids.
    prov = rec.provenance
    assert isinstance(prov.supporting_calculation_ids, list)
    assert isinstance(prov.supporting_calculations, list)
    # submission_ref is nullable but the field must exist.
    assert hasattr(prov, "submission_ref")


# ---------------------------------------------------------------------------
# get_reaction_full
# ---------------------------------------------------------------------------


def test_get_reaction_full_exposes_refs(db_session):
    chem, reaction_entry, _ = _seed_reaction_with_kinetics(db_session)

    response = get_reaction_full(
        db_session,
        reaction_entry_id=reaction_entry.id,
        request=ReactionFullReadRequest(
            include=["species", "kinetics"],
            include_review=ReviewDetail.summary,
        ),
    )

    assert response.reaction_entry.id == reaction_entry.id
    assert response.reaction_entry.reaction_entry_ref == reaction_entry.public_ref
    assert response.reaction_entry.reaction_id == chem.id
    assert response.reaction_entry.reaction_ref == chem.public_ref

    assert response.species is not None
    for participant in [*response.species.reactants, *response.species.products]:
        assert _looks_like_ref(participant.species_entry_ref)
