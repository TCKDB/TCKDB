"""Service-layer tests for browse_species (/scientific/species/browse).

Mirrors ``test_search_species.py`` wherever the two surfaces share
behaviour (visibility, pagination, sort rejection, include validation),
and adds the tests that are specific to browse: no identifier required,
deterministic multi-page coverage over a tied candidate set, an honest
``pagination.total``, and a metadata-only record shape.
"""

from __future__ import annotations

import pytest

from app.db.models.common import (
    RecordReviewStatus,
    SpeciesEntryStateKind,
    SubmissionRecordType,
)
from app.schemas.reads.scientific_species import (
    ElementMatchMode,
    SpeciesBrowseRequest,
    SpeciesEntrySectionIds,
)
from app.services.scientific_read.species import browse_species
from tests.services.scientific_read._factories import (
    make_species,
    make_species_entry,
    make_thermo_scalar,
    next_inchi_key,
    set_review,
    unique_smiles,
)

# ---------------------------------------------------------------------------
# The headline feature: no identifier required
# ---------------------------------------------------------------------------


def test_browse_with_no_filters_lists_every_created_species(db_session):
    """The whole point of the endpoint: an empty request is not refused.

    Three species, not one -- a fixture with a single row cannot tell
    "the endpoint returned the right species" from "the endpoint returned
    whatever it had". All three ids must come back, and nothing else the
    test did not create.
    """
    ids = {
        make_species(db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("BR")).id
        for _ in range(3)
    }
    # No species_entry needed: a species with no entries is still a
    # browse candidate, with an empty ``entries`` list on the wire.

    response = browse_species(db_session, SpeciesBrowseRequest())

    returned_ids = {r.species_id for r in response.records}
    assert ids <= returned_ids
    assert response.pagination.total >= 3


def test_browse_does_not_raise_missing_identifier(db_session):
    """The 422 ``search_species`` raises for an empty request must not fire here."""
    # No species at all: an empty corpus is a 200 with no records, not an
    # error -- the same "empty result, not a refusal" contract as search.
    response = browse_species(db_session, SpeciesBrowseRequest())
    assert response.pagination.total >= 0  # does not raise


# ---------------------------------------------------------------------------
# Secondary filters narrow the listing
# ---------------------------------------------------------------------------


def test_browse_by_charge_narrows(db_session):
    neutral = make_species(
        db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("CHG0"), charge=0
    )
    charged = make_species(
        db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("CHG1"), charge=1
    )

    response = browse_species(db_session, SpeciesBrowseRequest(charge=1))

    returned_ids = {r.species_id for r in response.records}
    assert charged.id in returned_ids
    assert neutral.id not in returned_ids


def test_browse_by_multiplicity_narrows(db_session):
    singlet = make_species(
        db_session,
        smiles=unique_smiles(),
        inchi_key=next_inchi_key("MULT1"),
        multiplicity=1,
    )
    triplet = make_species(
        db_session,
        smiles=unique_smiles(),
        inchi_key=next_inchi_key("MULT3"),
        multiplicity=3,
    )

    response = browse_species(db_session, SpeciesBrowseRequest(multiplicity=3))

    returned_ids = {r.species_id for r in response.records}
    assert triplet.id in returned_ids
    assert singlet.id not in returned_ids


def test_browse_by_formula_narrows_and_is_served_back(db_session):
    water = make_species(db_session, smiles="O", inchi_key=next_inchi_key("BRFORMH2O"))
    make_species(db_session, smiles="C1CC1", inchi_key=next_inchi_key("BRFORMC3H6"))

    response = browse_species(db_session, SpeciesBrowseRequest(formula="H2O"))

    matching = [r for r in response.records if r.species_id == water.id]
    assert len(matching) == 1
    assert matching[0].formula == "H2O"
    assert all(r.formula == "H2O" for r in response.records)


def test_browse_by_formula_nonexistent_returns_empty(db_session):
    make_species(db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("BRFX"))

    response = browse_species(
        db_session, SpeciesBrowseRequest(formula="DOES_NOT_EXIST")
    )

    assert response.records == []
    assert response.pagination.total == 0


# ---------------------------------------------------------------------------
# Composition filters: elements / elem_mode / max_heavy_atoms / min_heavy_atoms
#
# "Heavy atom" means the conventional chemistry sense: non-hydrogen, by
# atomic number -- not "whatever the SMILES happened to spell out". See
# species.py::_heavy_atom_count_expr and ::_formula_has_element_expr for
# the full rationale. The values asserted below were independently
# confirmed against the live RDKit cartridge (``docker exec ... psql -c
# "select mol_formula(...), mol_numheavyatoms(...)"``) before being
# written into these tests, not guessed:
#   [CH3]      -> formula CH3,   1 heavy atom  (bracket radical, implicit H)
#   CC         -> formula C2H6,  2 heavy atoms
#   CC#N       -> formula C2H3N, 3 heavy atoms
#   N          -> formula H3N,   1 heavy atom
#   O          -> formula H2O,   1 heavy atom
#   [H][H]     -> formula H2,    0 heavy atoms (explicit H atoms)
#   [OH-]      -> formula HO-,   1 heavy atom  (charged)
#   [2H]O[2H]  -> formula H2O,   1 heavy atom  (isotope-labelled, ignored)
#   c1ccccc1   -> formula C6H6,  6 heavy atoms (benzene)
#   Cc1ccccc1  -> formula C7H8,  7 heavy atoms (toluene)
# ---------------------------------------------------------------------------


def test_elements_filter_narrows_to_species_containing_that_element(db_session):
    methyl = make_species(
        db_session,
        smiles="[CH3]",
        multiplicity=2,
        inchi_key=next_inchi_key("BRELCH3"),
    )
    ammonia = make_species(db_session, smiles="N", inchi_key=next_inchi_key("BRELNH3"))

    response = browse_species(db_session, SpeciesBrowseRequest(elements="C"))

    returned_ids = {r.species_id for r in response.records}
    assert methyl.id in returned_ids
    assert ammonia.id not in returned_ids


def test_elements_filter_is_case_insensitive(db_session):
    methyl = make_species(
        db_session,
        smiles="[CH3]",
        multiplicity=2,
        inchi_key=next_inchi_key("BRELCASE"),
    )

    response = browse_species(db_session, SpeciesBrowseRequest(elements="c"))

    assert methyl.id in {r.species_id for r in response.records}


def test_elem_mode_all_requires_every_element_elem_mode_any_requires_one(db_session):
    """The headline distinction ``elem_mode`` exists for.

    Three species that genuinely tell ``all`` and ``any`` apart: ethane
    (C, H only), ammonia (N, H only), and acetonitrile (C, H, *and* N).
    Querying ``elements=C,N``:

    * ``elem_mode=all`` must return only acetonitrile -- the one species
      with *both* C and N.
    * ``elem_mode=any`` must return all three -- each has at least one
      of C or N.

    A fixture with only one matching species could not tell "elem_mode
    behaves as documented" from "elem_mode is ignored and the filter
    always behaves like elem_mode=any" (or vice versa); this one can,
    because ``all`` must exclude two of the three species ``any``
    includes -- the two modes are asserted to disagree on this fixture,
    not merely to each return *something*.
    """
    ethane = make_species(db_session, smiles="CC", inchi_key=next_inchi_key("BREMETH"))
    ammonia = make_species(db_session, smiles="N", inchi_key=next_inchi_key("BREMAMM"))
    acetonitrile = make_species(
        db_session, smiles="CC#N", inchi_key=next_inchi_key("BREMACN")
    )
    fixture_ids = {ethane.id, ammonia.id, acetonitrile.id}

    all_mode = browse_species(
        db_session,
        SpeciesBrowseRequest(elements="C,N", elem_mode=ElementMatchMode.all),
    )
    any_mode = browse_species(
        db_session,
        SpeciesBrowseRequest(elements="C,N", elem_mode=ElementMatchMode.any),
    )

    all_ids = {r.species_id for r in all_mode.records} & fixture_ids
    any_ids = {r.species_id for r in any_mode.records} & fixture_ids

    assert all_ids == {acetonitrile.id}
    assert any_ids == fixture_ids
    # The two modes must actually differ on this fixture -- if they
    # produced the same set, elem_mode would be a no-op.
    assert all_ids != any_ids


def test_elem_mode_defaults_to_all(db_session):
    ethane = make_species(db_session, smiles="CC", inchi_key=next_inchi_key("BREDEF"))

    response = browse_species(db_session, SpeciesBrowseRequest(elements="C,N"))

    assert ethane.id not in {r.species_id for r in response.records}


def test_max_heavy_atoms_boundary_is_inclusive(db_session):
    """Off-by-one at the boundary: benzene (6 heavy atoms) vs. toluene (7).

    ``max_heavy_atoms=6`` must include the species with exactly 6 and
    exclude the one with exactly 7 -- an off-by-one in either direction
    (``<`` instead of ``<=``, or the comparison flipped) would move one
    of these two across the line.
    """
    benzene = make_species(
        db_session, smiles="c1ccccc1", inchi_key=next_inchi_key("BRHA6")
    )
    toluene = make_species(
        db_session, smiles="Cc1ccccc1", inchi_key=next_inchi_key("BRHA7")
    )

    response = browse_species(db_session, SpeciesBrowseRequest(max_heavy_atoms=6))

    returned_ids = {r.species_id for r in response.records}
    assert benzene.id in returned_ids
    assert toluene.id not in returned_ids


def test_min_heavy_atoms_boundary_is_inclusive(db_session):
    """``min_heavy_atoms`` symmetric with ``max_heavy_atoms``: same
    benzene/toluene pair, boundary on the other side.
    """
    benzene = make_species(
        db_session, smiles="c1ccccc1", inchi_key=next_inchi_key("BRHAMIN6")
    )
    toluene = make_species(
        db_session, smiles="Cc1ccccc1", inchi_key=next_inchi_key("BRHAMIN7")
    )

    response = browse_species(db_session, SpeciesBrowseRequest(min_heavy_atoms=7))

    returned_ids = {r.species_id for r in response.records}
    assert toluene.id in returned_ids
    assert benzene.id not in returned_ids


def test_heavy_atom_count_excludes_implicit_hydrogens_on_bracket_radical(db_session):
    """``[CH3]`` -- a radical written with explicit brackets -- has 1
    heavy atom, not 4. Pins that RDKit's implicit-hydrogen bookkeeping
    inside brackets does not leak into the cartridge's heavy-atom count.
    """
    methyl = make_species(
        db_session,
        smiles="[CH3]",
        multiplicity=2,
        inchi_key=next_inchi_key("BRHACH3"),
    )

    matched = browse_species(db_session, SpeciesBrowseRequest(max_heavy_atoms=1))
    excluded = browse_species(db_session, SpeciesBrowseRequest(max_heavy_atoms=0))

    assert methyl.id in {r.species_id for r in matched.records}
    assert methyl.id not in {r.species_id for r in excluded.records}


def test_heavy_atom_count_excludes_explicit_hydrogen_atoms(db_session):
    """Molecular hydrogen written with two *explicit* atoms (``[H][H]``)
    has 0 heavy atoms -- an explicit H is still H.
    """
    h2 = make_species(db_session, smiles="[H][H]", inchi_key=next_inchi_key("BRHAH2"))

    zero_or_fewer = browse_species(db_session, SpeciesBrowseRequest(max_heavy_atoms=0))
    at_least_one = browse_species(db_session, SpeciesBrowseRequest(min_heavy_atoms=1))

    assert h2.id in {r.species_id for r in zero_or_fewer.records}
    assert h2.id not in {r.species_id for r in at_least_one.records}


def test_charged_species_heavy_atom_count_ignores_the_ionic_charge(db_session):
    """Hydroxide (``[OH-]``, formula ``HO-``) has 1 heavy atom -- the
    trailing charge marker in the formula is not itself an atom, and must
    not throw off either the element match or the heavy-atom count.
    """
    hydroxide = make_species(
        db_session, smiles="[OH-]", charge=-1, inchi_key=next_inchi_key("BRHAOH")
    )

    response = browse_species(
        db_session, SpeciesBrowseRequest(elements="O", max_heavy_atoms=1)
    )

    assert hydroxide.id in {r.species_id for r in response.records}


def test_isotope_labelled_species_matches_the_unlabelled_element(db_session):
    """Heavy water (``[2H]O[2H]``) still matches ``elements=H``.

    The cartridge's ``mol_formula()`` does not distinguish isotopes (see
    ``_formula_expr``), so this filter cannot select for or against
    deuteration -- it answers "does this species contain hydrogen", not
    "which isotope of hydrogen".
    """
    heavy_water = make_species(
        db_session, smiles="[2H]O[2H]", inchi_key=next_inchi_key("BRHAD2O")
    )

    response = browse_species(db_session, SpeciesBrowseRequest(elements="H"))

    assert heavy_water.id in {r.species_id for r in response.records}


def test_unknown_element_symbol_is_rejected_not_silently_empty(db_session):
    """A typo'd symbol must 422, not read as 'the archive holds none of it'."""
    with pytest.raises(ValueError, match="unknown_element_symbol"):
        browse_species(db_session, SpeciesBrowseRequest(elements="Xx"))


def test_isotope_token_is_rejected_as_an_unknown_element_symbol(db_session):
    """``D``/``T`` are isotope tokens, not element symbols; RDKit's
    periodic table does not know them, so they 422 the same as any other
    unrecognised symbol -- this filter cannot select on isotopes.
    """
    with pytest.raises(ValueError, match="unknown_element_symbol"):
        browse_species(db_session, SpeciesBrowseRequest(elements="D"))


def test_pagination_total_is_the_filtered_count_not_the_corpus_count(db_session):
    """Composition filters must narrow ``pagination.total``, not just the page.

    Two sulfur-containing species and three that are not. If ``total``
    were (incorrectly) computed from the *unfiltered* candidate set --
    e.g. a composition predicate applied only at the page-slicing step
    and not to the count query that feeds ``total`` -- this would report
    5 instead of 2.
    """
    sulfur_ids = {
        make_species(db_session, smiles="CS", inchi_key=next_inchi_key("BRTOTS1")).id,
        make_species(db_session, smiles="CSC", inchi_key=next_inchi_key("BRTOTS2")).id,
    }
    for _ in range(3):
        make_species(
            db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("BRTOTNOS")
        )

    response = browse_species(db_session, SpeciesBrowseRequest(elements="S", limit=1))

    assert response.pagination.total == 2
    assert response.pagination.total != response.pagination.returned
    assert {r.species_id for r in response.records} <= sulfur_ids


def test_composition_filter_pagination_is_stable_across_query_plans(db_session):
    """The tiebreak's real job, exercised under a composition filter.

    Mirrors ``test_pagination_is_stable_even_across_different_query_plans``
    above, but with ``elements="C"`` narrowing the candidate set --
    confirming the ``review_rank ASC, has_entries DESC, created_at DESC,
    id DESC`` order (and its ``id DESC`` tiebreak in particular) still
    applies to the *filtered* candidate subquery, not just the
    unfiltered one. The composition predicate is applied inside
    ``_browse_candidate_species_stmt``, upstream of
    ``_rank_and_slice_species`` -- if a future change moved composition
    filtering somewhere that bypassed the shared ordering machinery (e.g.
    filtering in Python after the page was already sliced), ties among
    the filtered rows would no longer be broken deterministically and
    this test would catch it as a row on two pages or on neither.
    """
    from sqlalchemy import text

    ids = [
        make_species(
            db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("BRCPLAN")
        ).id
        for _ in range(20)
    ]

    def page(offset: int, limit: int, *, force_groupagg: bool) -> list[int]:
        db_session.execute(
            text(f"SET LOCAL enable_hashagg = {'off' if force_groupagg else 'on'}")
        )
        resp = browse_species(
            db_session,
            SpeciesBrowseRequest(limit=limit, offset=offset, elements="C"),
        )
        return [r.species_id for r in resp.records if r.species_id in ids]

    page_a = page(0, 10, force_groupagg=False)
    page_b = page(10, 10, force_groupagg=True)

    duplicated = set(page_a) & set(page_b)
    combined = set(page_a) | set(page_b)
    dropped = set(ids) - combined

    assert not duplicated, (
        f"rows on both pages: {sorted(duplicated)} (page_a={page_a}, page_b={page_b})"
    )
    assert not dropped, (
        f"rows on neither page: {sorted(dropped)} (page_a={page_a}, page_b={page_b})"
    )
    assert combined == set(ids)


# ---------------------------------------------------------------------------
# Review visibility (same gate as search)
# ---------------------------------------------------------------------------


def test_default_excludes_rejected_and_deprecated_entries(db_session):
    species = make_species(
        db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("BRREJ")
    )
    e_approved = make_species_entry(db_session, species)
    e_rejected = make_species_entry(
        db_session, species, electronic_state_kind=SpeciesEntryStateKind.excited
    )
    set_review(
        db_session,
        record_type=SubmissionRecordType.species_entry,
        record_id=e_approved.id,
        status=RecordReviewStatus.approved,
    )
    set_review(
        db_session,
        record_type=SubmissionRecordType.species_entry,
        record_id=e_rejected.id,
        status=RecordReviewStatus.rejected,
    )

    response = browse_species(db_session, SpeciesBrowseRequest())

    record = next(r for r in response.records if r.species_id == species.id)
    entry_ids = [e.species_entry_id for e in record.entries]
    assert e_approved.id in entry_ids
    assert e_rejected.id not in entry_ids


def test_include_rejected_surfaces_rejected_entries(db_session):
    species = make_species(
        db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("BRRINC")
    )
    e_rejected = make_species_entry(db_session, species)
    set_review(
        db_session,
        record_type=SubmissionRecordType.species_entry,
        record_id=e_rejected.id,
        status=RecordReviewStatus.rejected,
    )

    response = browse_species(
        db_session, SpeciesBrowseRequest(include_rejected=True)
    )

    record = next(r for r in response.records if r.species_id == species.id)
    entry_ids = [e.species_entry_id for e in record.entries]
    assert e_rejected.id in entry_ids


def test_include_deprecated_surfaces_deprecated_entries(db_session):
    species = make_species(
        db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("BRDINC")
    )
    e_deprecated = make_species_entry(db_session, species)
    set_review(
        db_session,
        record_type=SubmissionRecordType.species_entry,
        record_id=e_deprecated.id,
        status=RecordReviewStatus.deprecated,
    )

    hidden = browse_species(db_session, SpeciesBrowseRequest())
    record_hidden = next(r for r in hidden.records if r.species_id == species.id)
    assert e_deprecated.id not in [e.species_entry_id for e in record_hidden.entries]

    shown = browse_species(
        db_session, SpeciesBrowseRequest(include_deprecated=True)
    )
    record_shown = next(r for r in shown.records if r.species_id == species.id)
    assert e_deprecated.id in [e.species_entry_id for e in record_shown.entries]


def test_min_review_status_approved_filters_to_approved(db_session):
    species = make_species(
        db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("BRMIN")
    )
    e_approved = make_species_entry(db_session, species)
    e_under = make_species_entry(
        db_session, species, electronic_state_kind=SpeciesEntryStateKind.excited
    )
    set_review(
        db_session,
        record_type=SubmissionRecordType.species_entry,
        record_id=e_approved.id,
        status=RecordReviewStatus.approved,
    )
    set_review(
        db_session,
        record_type=SubmissionRecordType.species_entry,
        record_id=e_under.id,
        status=RecordReviewStatus.under_review,
    )

    response = browse_species(
        db_session,
        SpeciesBrowseRequest(min_review_status=RecordReviewStatus.approved),
    )

    record = next(r for r in response.records if r.species_id == species.id)
    entry_ids = [e.species_entry_id for e in record.entries]
    assert e_approved.id in entry_ids
    assert e_under.id not in entry_ids


# ---------------------------------------------------------------------------
# #277: an entry-level filter must drop species it leaves with zero
# visible entries, not list them with ``entries: []`` and count them
# toward ``pagination.total`` anyway.
#
# Measured on the deployed instance: 59 species / 60 entries, none
# approved. ``?min_review_status=approved`` reported ``pagination.total:
# 59`` (the unfiltered species count) while every record on the page
# carried ``entries: []`` -- a curator asking "which species are
# approved?" was told "all 59" when the true answer was none. Neither
# review round on #276 caught this because every fixture had at least
# one entry surviving whatever filter was applied; the fixtures below
# deliberately include a species where the filter matches *nothing* and
# one where it matches *some but not all*, which is the only shape that
# can tell "drops the empty ones" apart from "drops everything" or
# "drops nothing".
#
# min_review_status / include_rejected / include_deprecated /
# electronic_state_kind / species_entry_kind are entry-level (they
# narrow species_entry rows -- see
# species.py::_browse_entry_filter_supplied for exactly where each is
# applied). charge / multiplicity / formula / elements / elem_mode /
# max_heavy_atoms / min_heavy_atoms are species-level: they narrow which
# species are candidates before any join to species_entry, so none of
# them can leave a matched species holding zero entries, and none of
# them trigger this drop.
# ---------------------------------------------------------------------------


def test_entry_level_filter_matching_nothing_returns_empty_not_full_corpus(db_session):
    """The exact shape of the measured defect: a filter matching zero
    entries anywhere must report zero, not the unfiltered species count.
    """
    species = make_species(
        db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("BR277NONE")
    )
    make_species_entry(db_session, species)  # not_reviewed; never approved

    response = browse_species(
        db_session,
        SpeciesBrowseRequest(min_review_status=RecordReviewStatus.approved),
    )

    assert response.records == []
    assert response.pagination.total == 0


def test_entry_level_filter_drops_only_the_species_it_leaves_empty(db_session):
    """The fixture that tells "drops the empty ones" apart from the two
    wrong answers: a species with a visible entry, alongside one whose
    only entry fails the filter. Both must be distinguishable from "drop
    everything" (the first assertion) and "drop nothing" (the second).
    """
    matches = make_species(
        db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("BR277SOME1")
    )
    e_approved = make_species_entry(db_session, matches)
    set_review(
        db_session,
        record_type=SubmissionRecordType.species_entry,
        record_id=e_approved.id,
        status=RecordReviewStatus.approved,
    )

    empties = make_species(
        db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("BR277SOME2")
    )
    make_species_entry(db_session, empties)  # not_reviewed; excluded by the filter

    response = browse_species(
        db_session,
        SpeciesBrowseRequest(min_review_status=RecordReviewStatus.approved),
    )

    returned_ids = {r.species_id for r in response.records}
    assert matches.id in returned_ids
    assert empties.id not in returned_ids


def test_entry_level_filter_total_is_the_visible_species_count_not_the_corpus(
    db_session,
):
    """The specific lie in the live output: ``pagination.total`` must
    count species with >=1 visible entry under the entry-level filter,
    not every species-level candidate regardless of entry visibility.
    One approved species among five total -- ``total`` must read 1, not 5.
    """
    approved = make_species(
        db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("BR277TOT1")
    )
    e_approved = make_species_entry(db_session, approved)
    set_review(
        db_session,
        record_type=SubmissionRecordType.species_entry,
        record_id=e_approved.id,
        status=RecordReviewStatus.approved,
    )
    for _ in range(4):
        unreviewed = make_species(
            db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("BR277TOTX")
        )
        make_species_entry(db_session, unreviewed)

    response = browse_species(
        db_session,
        SpeciesBrowseRequest(min_review_status=RecordReviewStatus.approved),
    )

    assert response.pagination.total == 1
    assert len(response.records) == 1
    assert response.records[0].species_id == approved.id


def test_electronic_state_kind_filter_drops_species_with_no_matching_entry(db_session):
    """``electronic_state_kind`` is entry-level too -- same drop, different
    entry-level predicate (a join condition on ``species_entry`` rather
    than the review-status gate).
    """
    ground_only = make_species(
        db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("BR277ES1")
    )
    make_species_entry(db_session, ground_only)

    both_states = make_species(
        db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("BR277ES2")
    )
    make_species_entry(db_session, both_states)
    make_species_entry(
        db_session, both_states, electronic_state_kind=SpeciesEntryStateKind.excited
    )

    response = browse_species(
        db_session,
        SpeciesBrowseRequest(electronic_state_kind=SpeciesEntryStateKind.excited),
    )

    returned_ids = {r.species_id for r in response.records}
    assert both_states.id in returned_ids
    assert ground_only.id not in returned_ids
    assert response.pagination.total == 1


def test_species_level_filter_alone_still_lists_entryless_species(db_session):
    """Species-level filters must never trigger the #277 drop.

    A species matching a species-level filter (``formula``) but with no
    ``species_entry`` rows *at all* is still a browse candidate with
    ``entries: []`` -- unchanged from
    ``test_browse_with_no_filters_lists_every_created_species`` above,
    just narrowed by a species-level filter instead of no filter at all.
    If ``formula=`` were (incorrectly) treated as entry-level, this
    species would vanish from both the page and ``pagination.total``.
    """
    lonely = make_species(db_session, smiles="O", inchi_key=next_inchi_key("BR277LONE"))
    # No species_entry created for `lonely`.

    response = browse_species(db_session, SpeciesBrowseRequest(formula="H2O"))

    matching = [r for r in response.records if r.species_id == lonely.id]
    assert len(matching) == 1
    assert matching[0].entries == []
    assert response.pagination.total >= 1


# ---------------------------------------------------------------------------
# Pagination: honesty and stability
# ---------------------------------------------------------------------------


def test_pagination_default_limit(db_session):
    make_species(db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("BRPAG"))

    response = browse_species(db_session, SpeciesBrowseRequest())

    assert response.pagination.offset == 0
    assert response.pagination.limit == 50


def test_pagination_total_is_the_true_corpus_count_not_the_page_size(db_session):
    """Guards against reporting ``len(records)`` as ``total``.

    Five species exist; a page of two is requested. If ``total`` were
    quietly computed from the returned page instead of the full candidate
    count, this would read 2 instead of 5.
    """
    for _ in range(5):
        make_species(
            db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("BRTOT")
        )

    response = browse_species(db_session, SpeciesBrowseRequest(limit=2))

    assert len(response.records) == 2
    assert response.pagination.returned == 2
    assert response.pagination.total >= 5
    assert response.pagination.total != response.pagination.returned


def test_review_summary_is_computed_before_paging(db_session):
    """``review_summary`` must describe the whole candidate set, not the page.

    Five species, each with one approved entry; a page of two is
    requested. ``review_summary`` is built from
    ``_visible_entry_rows(candidates, ...)`` -- the full candidate set,
    before ``_rank_and_slice_species`` applies offset/limit -- so it must
    report all five regardless of page size, and it must be identical
    whether the page is small or large. A summary that were (incorrectly)
    computed from the returned page instead would report 2, would change
    between the two calls below, and would silently understate what a
    whole-archive catalogue holds.
    """
    for _ in range(5):
        species = make_species(
            db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("BRRSUM")
        )
        entry = make_species_entry(db_session, species)
        set_review(
            db_session,
            record_type=SubmissionRecordType.species_entry,
            record_id=entry.id,
            status=RecordReviewStatus.approved,
        )

    small_page = browse_species(db_session, SpeciesBrowseRequest(limit=2))
    large_page = browse_species(db_session, SpeciesBrowseRequest(limit=200))

    assert small_page.review_summary == large_page.review_summary
    assert small_page.review_summary.approved >= 5
    assert small_page.review_summary.total >= 5
    assert small_page.review_summary.total != small_page.pagination.returned


def test_pagination_is_stable_even_across_different_query_plans(db_session):
    """The tiebreak's real job: pagination must not depend on the plan.

    (Supersedes an earlier version of this test that only compared two
    page fetches under whatever single plan Postgres happened to pick --
    which can look stable by accident, and did: dropping the ``id DESC``
    tiebreak did not fail that version locally even though the guarantee
    really was broken. See ``eb724909`` for the failure that motivated
    the rewrite; keeping the weaker version alongside this one would
    advertise a guarantee it does not provide, so it was removed rather
    than kept as a second, redundant case.)

    Two pages fetched under the *same* plan can look stable by accident --
    a repeated query against unchanged data tends to walk a hash table or
    a heap in the same order every time, so a missing tiebreak can pass a
    naive "fetch twice, compare" check even though nothing in the SQL
    guarantees it (confirmed empirically while writing this test: the
    query this exercises used HashAggregate by default, and a plain
    two-call comparison did not expose the gap).

    What *does* change the order, deterministically and reproducibly, is
    the aggregate strategy PostgreSQL picks -- ``GROUP BY`` output order
    is unspecified by the SQL standard, and ``HashAggregate`` vs a forced
    ``GroupAggregate`` (``enable_hashagg = off``) visit the same tied rows
    in genuinely different orders. A real deployment can land on either
    plan for two different requests (autovacuum, a statistics refresh, a
    replica with different memory settings), so this is not a contrived
    edge case -- it is the mechanism the ``id DESC`` tiebreak exists to
    neutralize. This test forces exactly that plan change between two
    page fetches and asserts they still tile the tied set with no
    duplicate and no gap.
    """
    from sqlalchemy import text

    ids = [
        make_species(
            db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("BRPLAN")
        ).id
        for _ in range(20)
    ]

    def page(offset: int, limit: int, *, force_groupagg: bool) -> list[int]:
        db_session.execute(
            text(f"SET LOCAL enable_hashagg = {'off' if force_groupagg else 'on'}")
        )
        resp = browse_species(
            db_session, SpeciesBrowseRequest(limit=limit, offset=offset)
        )
        return [r.species_id for r in resp.records if r.species_id in ids]

    # Page A under the default plan (HashAggregate on this data shape);
    # page B under a forced GroupAggregate -- as if the two requests hit
    # the database under different conditions, which is the realistic case.
    page_a = page(0, 10, force_groupagg=False)
    page_b = page(10, 10, force_groupagg=True)

    duplicated = set(page_a) & set(page_b)
    combined = set(page_a) | set(page_b)
    dropped = set(ids) - combined

    assert not duplicated, (
        f"rows on both pages: {sorted(duplicated)} (page_a={page_a}, page_b={page_b})"
    )
    assert not dropped, (
        f"rows on neither page: {sorted(dropped)} (page_a={page_a}, page_b={page_b})"
    )
    assert combined == set(ids)


def test_sort_is_deterministic_across_two_calls(db_session):
    make_species(db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("BRSORT"))

    r1 = browse_species(db_session, SpeciesBrowseRequest())
    r2 = browse_species(db_session, SpeciesBrowseRequest())

    assert r1.model_dump() == r2.model_dump()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_client_supplied_sort_rejected(db_session):
    with pytest.raises(ValueError, match="client_sort_not_supported"):
        browse_species(db_session, SpeciesBrowseRequest(sort="anything"))


def test_unknown_include_token_rejected(db_session):
    with pytest.raises(ValueError, match="unknown_include_token"):
        browse_species(db_session, SpeciesBrowseRequest(include=["banana"]))


def test_limit_above_the_cap_is_rejected(db_session):
    with pytest.raises(ValueError, match="limit_too_large"):
        browse_species(db_session, SpeciesBrowseRequest(limit=999))


def test_a_malformed_limit_is_still_invalid_pagination(db_session):
    with pytest.raises(ValueError, match="invalid_pagination"):
        browse_species(db_session, SpeciesBrowseRequest(limit=0))


# ---------------------------------------------------------------------------
# Availability + include sections (same shape as search)
# ---------------------------------------------------------------------------


def test_availability_reports_thermo_when_attached(db_session):
    species = make_species(
        db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("BRAV")
    )
    entry = make_species_entry(db_session, species)
    make_thermo_scalar(db_session, species_entry=entry)

    response = browse_species(db_session, SpeciesBrowseRequest())

    record = next(r for r in response.records if r.species_id == species.id)
    avail = record.entries[0].availability
    assert avail.has_thermo is True
    assert avail.has_statmech is False


@pytest.mark.parametrize(
    "token", ["thermo", "statmech", "transport", "conformers"]
)
def test_section_id_tokens_are_refused_on_browse(db_session, token):
    """The inverse of search's ``include=thermo``: refused, not served.

    These four tokens gate a section whose payload is a bare integer-id
    array (``SpeciesEntrySectionIds.ids``) -- reachable with no
    identifier and no auth on this surface, which is exactly the
    primary-key-harvest shape ``docs/specs/public_identifier_policy.md``
    warns about. ``/species/browse`` therefore never accepts them at
    all: a token that cannot be requested cannot be leaked by a future
    refactor that forgets to strip it. This replaces the old
    ``test_include_thermo_populates_thermo_summary_with_ids``, which
    asserted the behaviour this endpoint now deliberately refuses.
    """
    with pytest.raises(ValueError, match="unknown_include_token"):
        browse_species(db_session, SpeciesBrowseRequest(include=[token]))


def test_section_summaries_are_absent_even_when_the_data_exists(db_session):
    """Not just "absent by default" -- absent unconditionally.

    A species whose entry genuinely has thermo/statmech/transport/
    conformer data attached still serves no ``*_summary`` block on
    browse, because there is no include token that could ever ask for
    one. ``SpeciesEntryAvailability.has_thermo`` etc. is how a browse
    caller learns the data exists; ``species_entry_ref`` is how they
    reach it.
    """
    species = make_species(
        db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("BRNOSUM")
    )
    entry = make_species_entry(db_session, species)
    make_thermo_scalar(db_session, species_entry=entry)

    response = browse_species(db_session, SpeciesBrowseRequest())

    record = next(r for r in response.records if r.species_id == species.id)
    entry_record = record.entries[0]
    assert entry_record.availability.has_thermo is True
    assert entry_record.thermo_summary is None
    assert entry_record.statmech_summary is None
    assert entry_record.transport_summary is None
    assert entry_record.conformers_summary is None


# ---------------------------------------------------------------------------
# Metadata-only contract
# ---------------------------------------------------------------------------

_SPECIES_RECORD_FIELDS = {
    "species_id",
    "species_ref",
    "canonical_smiles",
    "inchi_key",
    "formula",
    "charge",
    "multiplicity",
    "stereo_kind",
    "entries",
}

_ENTRY_RECORD_FIELDS = {
    "species_entry_id",
    "species_entry_ref",
    "species_entry_kind",
    "electronic_state_kind",
    "stereo_label",
    "electronic_state_label",
    "term_symbol",
    "isotope_key",
    "species_entry_label",
    "review",
    "availability",
    "thermo_summary",
    "statmech_summary",
    "transport_summary",
    "conformers_summary",
}

_AVAILABILITY_FIELDS = {
    "has_thermo",
    "has_statmech",
    "has_transport",
    "has_conformers",
    "calculation_count",
}

#: RecordReviewBadge (app/schemas/reads/scientific_common.py) -- the one
#: nested object that *does* survive on the browse shape (the four
#: ``*_summary`` blocks never populate at all; see the "refused" tests
#: above), so it is the one whose exact key set is pinned here too.
_REVIEW_BADGE_FIELDS = {
    "status",
    "reviewed_at",
    "reviewer_kind",
}


def test_record_shape_is_metadata_only(db_session):
    """Pins the exact field set: identity, refs, counts, review -- nothing else.

    A field added to serve an artifact URI, a raw geometry payload or a
    calculation result on this surface would show up here as an
    unexpected key. Every field is asserted by *name*, not merely
    "does not contain a suspicious substring" -- the guard the brief
    warns is vacuous.

    Checked one level deeper than the top-level record and its immediate
    children: ``availability`` and ``review`` are both nested objects,
    and a leak added to either would be invisible to an assertion that
    stopped at the entry record's own key set (a nested dict is just one
    opaque key at that level). The four ``*_summary`` fields are not
    checked the same way -- they are always ``None`` on this surface
    (see ``test_section_summaries_are_absent_even_when_the_data_exists``
    above), so there is no populated nested object to open. Their type,
    ``SpeciesEntrySectionIds``, is pinned directly and separately by
    ``test_species_entry_section_ids_exposes_only_ids`` below, since it
    is shared with ``/species/search`` and a leak there matters even
    though browse can never reach it.
    """
    species = make_species(
        db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("BRSHAPE")
    )
    entry = make_species_entry(db_session, species)
    make_thermo_scalar(db_session, species_entry=entry)
    set_review(
        db_session,
        record_type=SubmissionRecordType.species_entry,
        record_id=entry.id,
        status=RecordReviewStatus.approved,
    )

    response = browse_species(db_session, SpeciesBrowseRequest())
    record = next(r for r in response.records if r.species_id == species.id)

    assert set(record.model_dump().keys()) == _SPECIES_RECORD_FIELDS
    entry_record = record.entries[0]
    assert set(entry_record.model_dump().keys()) == _ENTRY_RECORD_FIELDS
    assert (
        set(entry_record.availability.model_dump().keys()) == _AVAILABILITY_FIELDS
    )
    assert set(entry_record.review.model_dump().keys()) == _REVIEW_BADGE_FIELDS


def test_species_entry_section_ids_exposes_only_ids(db_session):
    """Schema-level guard on the shared type, independent of reachability.

    ``SpeciesEntrySectionIds`` backs ``thermo_summary`` et al. on
    *both* ``/species/search`` (still legal there) and
    ``/species/browse`` (permanently illegal, per the tests above). A
    field added to this type would be invisible to a browse-endpoint
    test forever, since browse can never populate it -- so it is pinned
    directly on the class rather than through either endpoint. This is
    the browse branch's defense against a leak on a type it happens to
    share, not a claim about what search currently does with it.
    """
    assert set(SpeciesEntrySectionIds.model_fields.keys()) == {"ids"}


