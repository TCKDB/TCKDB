"""Service-layer tests for browse_species (/scientific/species/browse).

Mirrors ``test_search_species.py`` wherever the two surfaces share
behaviour (visibility, pagination, sort rejection, include validation),
and adds the tests that are specific to browse: no identifier required,
deterministic multi-page coverage over a tied candidate set, an honest
``pagination.total``, and a metadata-only record shape.
"""

from __future__ import annotations

import pytest
from rdkit import Chem
from rdkit.Chem import inchi as _inchi

from app.db.models.common import (
    RecordReviewStatus,
    SpeciesEntryStateKind,
    SubmissionRecordType,
)
from app.db.models.species import SpeciesEntry
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


def test_two_letter_symbol_is_not_matched_by_its_one_letter_prefix(db_session):
    """The regex lookahead's whole reason to exist, pinned with a fixture
    that can actually tell it apart from a naive substring match.

    Every other composition fixture in this file happens to avoid any
    two-letter element symbol, so ``elements=C`` matching ``Cl`` (or
    ``elements=N`` matching ``Na``) went untested even though the
    behaviour is load-bearing (see ``_formula_has_element_expr``).
    ``ClCl`` (molecular chlorine, formula ``Cl2``) has no carbon at all;
    ``[Na+]`` (sodium cation, formula ``Na+``) has no nitrogen at all. If
    the ``(?![a-z])`` lookahead were dropped, ``elements=C`` would match
    the ``C`` inside ``Cl2`` and ``elements=N`` would match the ``N``
    inside ``Na+``, and both species would wrongly appear.
    """
    chlorine = make_species(
        db_session, smiles="ClCl", inchi_key=next_inchi_key("BRELCL2")
    )
    sodium = make_species(
        db_session, smiles="[Na+]", charge=1, inchi_key=next_inchi_key("BRELNA")
    )

    by_carbon = browse_species(db_session, SpeciesBrowseRequest(elements="C"))
    by_nitrogen = browse_species(db_session, SpeciesBrowseRequest(elements="N"))

    assert chlorine.id not in {r.species_id for r in by_carbon.records}
    assert sodium.id not in {r.species_id for r in by_nitrogen.records}
    # And the two-letter symbols themselves still match correctly.
    by_chlorine = browse_species(db_session, SpeciesBrowseRequest(elements="Cl"))
    by_sodium = browse_species(db_session, SpeciesBrowseRequest(elements="Na"))
    assert chlorine.id in {r.species_id for r in by_chlorine.records}
    assert sodium.id in {r.species_id for r in by_sodium.records}


def test_dummy_atom_wildcard_is_rejected_not_silently_empty(db_session):
    """``elements=*`` must 422, not read as "the archive holds none of it".

    RDKit's periodic table treats ``*`` as a *dummy atom* wildcard:
    ``GetAtomicNumber("*")`` returns ``0`` instead of raising, so a naive
    "did the lookup raise" check accepts it, and no Hill-notation formula
    ever contains a literal ``*`` -- so the (unguarded) filter would
    silently match nothing. That is exactly the failure this endpoint's
    unknown-symbol contract exists to prevent, reached through a
    non-letter token rather than a misspelled one.
    """
    make_species(db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("BRELWILD"))

    with pytest.raises(ValueError, match="unknown_element_symbol"):
        browse_species(db_session, SpeciesBrowseRequest(elements="*"))


def test_too_many_element_symbols_is_rejected(db_session):
    """The symbol-count cap, independent of the string-length cap.

    ``MAX_ELEMENT_SYMBOLS`` bounds how many distinct symbols one request
    may name -- each one adds another unindexed per-row cartridge regex
    to a public, unauthenticated query. Eleven real, valid symbols (one
    more than the cap) must be refused, not merely truncated or slowed.
    """
    from app.schemas.reads._field_bounds import MAX_ELEMENT_SYMBOLS

    too_many = ",".join(
        ["C", "H", "N", "O", "S", "Cl", "Br", "F", "P", "I", "Na"][
            : MAX_ELEMENT_SYMBOLS + 1
        ]
    )
    with pytest.raises(ValueError, match="too_many_element_symbols"):
        browse_species(db_session, SpeciesBrowseRequest(elements=too_many))


def test_element_symbols_at_the_cap_are_accepted(db_session):
    """The cap is inclusive: exactly the limit must not be refused."""
    from app.schemas.reads._field_bounds import MAX_ELEMENT_SYMBOLS

    at_cap = ",".join(
        ["C", "H", "N", "O", "S", "Cl", "Br", "F", "P", "I", "Na"][:MAX_ELEMENT_SYMBOLS]
    )
    # Must not raise.
    browse_species(db_session, SpeciesBrowseRequest(elements=at_cap))


def test_elements_that_parse_to_nothing_are_not_echoed_as_a_filter(db_session):
    """``elements=" , "`` applies no filter (see ``_parse_elements_filter``)
    and must not claim one in the request echo either.

    Gating the echo on ``request.elements is not None`` (rather than on
    the parsed symbol list) would report ``{"elements": " , ", "elem_mode":
    "all"}`` on a response that filtered nothing -- a caller reading the
    echo back would believe a filter ran that never touched a single row.
    """
    response = browse_species(db_session, SpeciesBrowseRequest(elements=" , "))

    assert "elements" not in response.request.filter
    assert "elem_mode" not in response.request.filter


def test_contradictory_heavy_atom_bounds_return_an_honest_empty_set(db_session):
    """``min_heavy_atoms > max_heavy_atoms`` names an empty range.

    Chosen deliberately over a 422: ``search_species`` already treats
    contradictory *identifiers* the same way ("Multiple inconsistent
    identifiers return an empty result set, not a validation error");
    an impossible composition range gets the same honest-empty-set
    answer rather than a special-cased refusal.
    """
    make_species(db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("BRHACONTRA"))

    response = browse_species(
        db_session, SpeciesBrowseRequest(min_heavy_atoms=5, max_heavy_atoms=2)
    )

    assert response.records == []
    assert response.pagination.total == 0



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
    """``include_deprecated=True`` widens visibility so a species whose
    *only* entry is deprecated becomes visible again.

    **Behaviour change, deliberate, made in the #277 follow-up below.**
    Before that fix, the plain default call returned this species with
    the deprecated entry merely absent from ``entries`` (``entries: []``
    on the wire). That is precisely the shape #277 says is wrong: the
    species has an entry, the entry is just not currently visible, and a
    catalogue listing that fact as "this species has no entries" is the
    same lie whether the invisibility comes from an explicit
    ``min_review_status=``, a ``profile=curated`` floor, or (this case)
    the endpoint's own default posture, which excludes deprecated/rejected
    without the caller asking for anything. #277's fix does not
    distinguish *why* an entry is invisible, so it does not carve out an
    exception for "invisible because nobody asked to see more" -- doing
    so would leave a fourth surviving instance of the exact bug class the
    fix exists to close. The species now vanishes entirely from the
    default listing (:func:`app.services.scientific_read.species._has_any_entry_expr`
    is what keeps a species with *zero* entries from suffering the same
    fate -- that case is still listed, always).
    """
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
    assert species.id not in {r.species_id for r in hidden.records}

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
# #277: a candidate species left with zero *visible* entries must be
# dropped, not listed with ``entries: []`` and counted toward
# ``pagination.total`` anyway.
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
# The first fix gated the drop on whether the caller had typed one of
# five specific request fields (min_review_status / include_rejected /
# include_deprecated / electronic_state_kind / species_entry_kind). That
# missed the read-profile floor (``?profile=curated`` narrows visibility
# to ``approved`` with no request field set at all -- see
# test_api_species_browse.py::test_profile_curated_drops_species_with_no_approved_entries)
# and inverted itself for the two widening flags (see
# test_widening_flag_does_not_shrink_the_result_set below). The rule is
# now unconditional: species.py::_rank_and_slice_species always drops a
# species that has *some* entries but none visible, on every browse call
# -- and species.py::_has_any_entry_expr is the structural, filter-
# independent check that keeps a species with *zero* entries at all
# (never dropped, by any filter) distinguishable from one whose entries
# were filtered to zero.
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


def test_widening_flag_does_not_shrink_the_result_set(db_session):
    """``include_rejected`` / ``include_deprecated`` only ever *widen*
    visibility, so they must never make a listed species disappear.

    A species with zero ``species_entry`` rows at all is listed by
    default (nothing to filter). Under the old five-field classification,
    ``include_rejected`` counted as "an entry-level filter was supplied"
    and unconditionally required a visible entry -- which dropped this
    exact species, so asking to see *more* (rejected records too)
    silently returned *less*. ``_has_any_entry_expr`` fixes this
    structurally: a species with no entries at all is never dropped, so
    the two calls below must agree.
    """
    lonely = make_species(
        db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("BR277WIDE")
    )

    default = browse_species(db_session, SpeciesBrowseRequest())
    widened = browse_species(db_session, SpeciesBrowseRequest(include_rejected=True))

    default_ids = {r.species_id for r in default.records}
    widened_ids = {r.species_id for r in widened.records}
    assert lonely.id in default_ids
    assert lonely.id in widened_ids


def test_profile_curated_is_not_reachable_at_the_service_layer_by_default(db_session):
    """Documents the boundary of what a service-level test can pin here.

    ``current_read_profile()`` reads a context variable published by an
    ``async`` FastAPI dependency on ``scientific_router``
    (``app/api/routes/scientific/_profile.py``); outside a request it
    falls back to ``exploratory`` unconditionally (see
    ``app/services/scientific_read/profile.py``). The #277-follow-up
    regression for ``profile=curated`` therefore has to run through the
    real HTTP dependency chain to mean anything -- see
    ``test_api_species_browse.py::test_profile_curated_drops_species_with_no_approved_entries``,
    which is the test that actually reaches the case the review flagged.
    This one just pins that a bare service call (no request context) is
    unaffected, so the two tests are not silently exercising the same
    path twice.
    """
    species = make_species(
        db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("BR277NOCTX")
    )
    make_species_entry(db_session, species)  # not_reviewed

    response = browse_species(db_session, SpeciesBrowseRequest())

    assert species.id in {r.species_id for r in response.records}


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




# ---------------------------------------------------------------------------
# Structure filter (query_smiles / query_smarts / mode / similarity_threshold)
#
# The browse-page counterpart of /species/structure-search's own three
# modes, added so a catalogue reader can narrow by chemical structure in
# the SAME request as every other browse filter. See
# SpeciesBrowseRequest's own docstring and species.py's
# _apply_structure_filter for the contract; these tests exercise it as a
# filter that composes with the rest of the candidate query, not as a
# second standalone search.
# ---------------------------------------------------------------------------


def _real_inchi_key(smiles: str) -> str:
    mol = Chem.MolFromSmiles(smiles)
    assert mol is not None, f"RDKit could not parse fixture SMILES {smiles!r}"
    return _inchi.MolToInchiKey(mol)


def _make_real_species(db_session, smiles: str, **species_kwargs):
    """A Species + SpeciesEntry pair whose ``mol``/``inchi_key`` are real
    RDKit values, not test placeholders -- required for the cartridge
    operators (``@>``, ``tanimoto_sml``) and the exact-mode InChIKey
    comparison to mean anything."""
    species = make_species(
        db_session,
        smiles=smiles,
        inchi_key=_real_inchi_key(smiles),
        **species_kwargs,
    )
    entry = make_species_entry(db_session, species)
    return species, entry


def test_structure_filter_absent_by_default(db_session):
    """Neither query_smiles nor query_smarts supplied: no filter applied,
    same as every other optional field -- the mirror-image of
    test_browse_with_no_filters_lists_every_created_species, pinned here
    because a mode default that accidentally required a query would be
    invisible to that test (it never sets mode at all)."""
    _, entry = _make_real_species(db_session, "CCO")

    response = browse_species(db_session, SpeciesBrowseRequest())

    returned_ids = {r.species_id for r in response.records}
    assert entry.species_id in returned_ids


def test_structure_filter_substructure_by_smiles_narrows(db_session):
    _, propanol_entry = _make_real_species(db_session, "CCCO")
    methane, _ = _make_real_species(db_session, "C")

    response = browse_species(
        db_session,
        SpeciesBrowseRequest(query_smiles="CCO", mode="substructure"),
    )

    returned_ids = {r.species_id for r in response.records}
    assert propanol_entry.species_id in returned_ids
    assert methane.id not in returned_ids


def test_structure_filter_substructure_by_smarts_narrows(db_session):
    _, ethanol_entry = _make_real_species(db_session, "CCO")
    methane, _ = _make_real_species(db_session, "C")

    response = browse_species(
        db_session,
        SpeciesBrowseRequest(query_smarts="[#6]O", mode="substructure"),
    )

    returned_ids = {r.species_id for r in response.records}
    assert ethanol_entry.species_id in returned_ids
    assert methane.id not in returned_ids


def test_structure_filter_similarity_self_match(db_session):
    _, ethanol_entry = _make_real_species(db_session, "CCO")
    benzene, _ = _make_real_species(db_session, "c1ccccc1")

    response = browse_species(
        db_session,
        SpeciesBrowseRequest(
            query_smiles="CCO", mode="similarity", similarity_threshold=0.95
        ),
    )

    returned_ids = {r.species_id for r in response.records}
    assert ethanol_entry.species_id in returned_ids
    assert benzene.id not in returned_ids


def test_structure_filter_exact_by_smiles_matches_inchi_key(db_session):
    _, ethanol_entry = _make_real_species(db_session, "CCO")
    ethylamine, _ = _make_real_species(db_session, "CCN")

    response = browse_species(
        db_session,
        SpeciesBrowseRequest(query_smiles="CCO", mode="exact"),
    )

    returned_ids = {r.species_id for r in response.records}
    assert ethanol_entry.species_id in returned_ids
    assert ethylamine.id not in returned_ids


def test_structure_filter_composes_with_charge_in_one_query(db_session):
    """The composition guarantee the design brief calls out by name:
    setting charge= AND a structure query must AND-combine, not silently
    drop one of the two. Four species cover every quadrant (matches
    structure only / charge only / both / neither) so a filter silently
    ignored in either direction is caught -- a fixture with only the
    "both" case could pass even if the structure filter were a no-op."""
    both, _ = _make_real_species(db_session, "CCO", charge=1)
    structure_only, _ = _make_real_species(db_session, "CCCO", charge=0)
    charge_only, _ = _make_real_species(db_session, "CCN", charge=1)
    neither, _ = _make_real_species(db_session, "CCS", charge=0)

    response = browse_species(
        db_session,
        SpeciesBrowseRequest(
            charge=1, query_smiles="CCO", mode="substructure"
        ),
    )

    returned_ids = {r.species_id for r in response.records}
    assert both.id in returned_ids
    assert structure_only.id not in returned_ids
    assert charge_only.id not in returned_ids
    assert neither.id not in returned_ids


def test_structure_filter_both_query_fields_raises_multiple_structure_queries(
    db_session,
):
    with pytest.raises(Exception) as excinfo:
        browse_species(
            db_session,
            SpeciesBrowseRequest(query_smiles="CCO", query_smarts="[#6]O"),
        )
    assert "multiple_structure_queries" in str(excinfo.value)


def test_structure_filter_smarts_under_similarity_is_invalid(db_session):
    with pytest.raises(Exception) as excinfo:
        browse_species(
            db_session,
            SpeciesBrowseRequest(query_smarts="[#6]O", mode="similarity"),
        )
    assert "invalid_structure_query" in str(excinfo.value)


def test_structure_filter_unparseable_smarts_is_invalid(db_session):
    with pytest.raises(Exception) as excinfo:
        browse_species(
            db_session,
            SpeciesBrowseRequest(query_smarts="not a smarts(((", mode="substructure"),
        )
    assert "invalid_structure_query" in str(excinfo.value)


def test_structure_filter_unparseable_smiles_is_invalid(db_session):
    with pytest.raises(Exception) as excinfo:
        browse_species(
            db_session,
            SpeciesBrowseRequest(query_smiles="not-a-smiles(((", mode="substructure"),
        )
    assert "invalid_structure_query" in str(excinfo.value)


def test_structure_filter_echoed_in_request_filter(db_session):
    _make_real_species(db_session, "CCO")

    response = browse_species(
        db_session,
        SpeciesBrowseRequest(query_smiles="CCO", mode="substructure"),
    )

    assert response.request.filter["query_smiles"] == "CCO"
    assert response.request.filter["mode"] == "substructure"
    assert "similarity_threshold" not in response.request.filter


def test_structure_filter_similarity_threshold_echoed_only_in_similarity_mode(
    db_session,
):
    _make_real_species(db_session, "CCO")

    response = browse_species(
        db_session,
        SpeciesBrowseRequest(query_smiles="CCO", mode="similarity"),
    )

    # Omitted similarity_threshold still echoes the effective default.
    assert response.request.filter["similarity_threshold"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Smallest-match-first ordering, under an active substructure/similarity
# structure filter. The owner's ask: typing a SMILES incrementally, a
# substructure match returns everything containing that fragment, and the
# smallest match should sort first (see _rank_and_slice_species's
# ``order_by_size`` docstring for the full design). Nothing here touches
# ordinary browse (no structure filter) or exact mode -- both are covered
# by dedicated tests below that assert the OLD order still applies.
# ---------------------------------------------------------------------------


def test_structure_filter_substructure_orders_smallest_match_first(db_session):
    """The headline case: four DIFFERENT heavy-atom counts, not two -- a
    fixture where any two rows share a count cannot distinguish a correct
    ascending comparator from a reversed one, and a fixture of only two
    rows barely exercises the sort at all."""
    methanol, _ = _make_real_species(db_session, "CO")  # 2 heavy atoms
    ethanol, _ = _make_real_species(db_session, "CCO")  # 3
    propanol, _ = _make_real_species(db_session, "CCCO")  # 4
    butanol, _ = _make_real_species(db_session, "CCCCO")  # 5

    response = browse_species(
        db_session,
        SpeciesBrowseRequest(query_smiles="CO", mode="substructure", limit=200),
    )

    ours = {methanol.id, ethanol.id, propanol.id, butanol.id}
    returned_ids = [r.species_id for r in response.records if r.species_id in ours]
    assert returned_ids == [methanol.id, ethanol.id, propanol.id, butanol.id]


def test_structure_filter_similarity_also_orders_smallest_match_first(db_session):
    """Similarity mode gets the same size sort as substructure -- both are
    the "more than one size of match is normal" modes named in the
    ``order_by_size`` derivation in ``browse_species``."""
    methanol, _ = _make_real_species(db_session, "CO")  # 2 heavy atoms
    ethanol, _ = _make_real_species(db_session, "CCO")  # 3
    propanol, _ = _make_real_species(db_session, "CCCO")  # 4

    response = browse_species(
        db_session,
        SpeciesBrowseRequest(
            query_smiles="CO", mode="similarity", similarity_threshold=0.1, limit=200
        ),
    )

    ours = {methanol.id, ethanol.id, propanol.id}
    returned_ids = [r.species_id for r in response.records if r.species_id in ours]
    assert returned_ids == [methanol.id, ethanol.id, propanol.id]


def test_structure_filter_size_ties_are_stable_across_repeated_requests(db_session):
    """Heavy-atom-count ties are the common case (many species share a
    count), so the sort needs a deterministic tiebreak -- otherwise two
    identical requests could return a tied pair in a different relative
    order.

    Comparing two calls under the SAME query plan can look stable by
    accident -- unchanged data tends to walk a hash table or a heap in the
    same order every time even with no tiebreak at all in the SQL (see
    ``test_pagination_is_stable_even_across_different_query_plans``'s own
    docstring, which hit exactly this while writing that test). So this
    forces a plan change (``HashAggregate`` vs a forced ``GroupAggregate``)
    between the two calls, same technique, rather than repeating the same
    plan twice."""
    from sqlalchemy import text

    ethanol, _ = _make_real_species(db_session, "CCO")  # 3 heavy atoms
    dme, _ = _make_real_species(db_session, "COC")  # 3 heavy atoms, different shape
    mhp, _ = _make_real_species(
        db_session, "COO"
    )  # methyl hydroperoxide, 3 heavy atoms, a third distinct shape --
    # three tied rows, not two: with only a pair, a coin-flip 50% chance
    # of an accidentally-matching random order can pass this test even
    # with no real tiebreak at all (confirmed while writing this test).
    tied_ids = {ethanol.id, dme.id, mhp.id}

    def order(*, force_groupagg: bool) -> list[int]:
        db_session.execute(
            text(f"SET LOCAL enable_hashagg = {'off' if force_groupagg else 'on'}")
        )
        response = browse_species(
            db_session,
            SpeciesBrowseRequest(query_smiles="CO", mode="substructure", limit=200),
        )
        return [r.species_id for r in response.records if r.species_id in tied_ids]

    first = order(force_groupagg=False)
    second = order(force_groupagg=True)
    assert set(first) == tied_ids
    assert first == second


def test_structure_filter_size_sort_pagination_has_no_duplicates_or_gaps(db_session):
    """The assertion that actually catches a missing unique tiebreak on the
    size-sort path: fetching page 1 then page 2 of a size-ordered listing
    must tile the matching set exactly -- no id returned on both pages,
    none dropped on neither.

    Three of the eight fixture species share ONE heavy-atom count (4), and
    that tied trio is placed so it straddles the ``offset=4`` page
    boundary (ascending sizes 2, 3, 4, 4, 4, 5, 6, 7 -- page one takes the
    first four, page two the rest, splitting the tied trio across the
    boundary). A tie that does not straddle the boundary can't expose a
    missing tiebreak: the pages would still tile correctly regardless of
    which order the tied rows come back in. The two page fetches are also
    forced onto different aggregate plans (same ``enable_hashagg`` technique
    as the ties test above and ``test_pagination_is_stable_even_across_different_query_plans``),
    since two same-plan calls can look stable by accident."""
    from sqlalchemy import text

    fixture_smiles = [
        "CO",  # methanol, 2 heavy atoms
        "CCO",  # ethanol, 3
        "CCCO",  # butanol (linear), 4
        "CC(C)O",  # isopropanol (branched), 4 -- ties with butanol
        "COCC",  # ethyl methyl ether, 4 -- ties with both
        "CCCCO",  # pentanol, 5
        "CCCCCO",  # hexanol, 6
        "CCCCCCO",  # heptanol, 7
    ]
    species_ids = [_make_real_species(db_session, s)[0].id for s in fixture_smiles]

    def page(offset: int, limit: int, *, force_groupagg: bool) -> list[int]:
        db_session.execute(
            text(f"SET LOCAL enable_hashagg = {'off' if force_groupagg else 'on'}")
        )
        response = browse_species(
            db_session,
            SpeciesBrowseRequest(
                query_smiles="CO", mode="substructure", offset=offset, limit=limit
            ),
        )
        return [r.species_id for r in response.records if r.species_id in species_ids]

    page_a = page(0, 4, force_groupagg=False)
    page_b = page(4, 4, force_groupagg=True)

    duplicated = set(page_a) & set(page_b)
    combined = page_a + page_b
    dropped = set(species_ids) - set(combined)
    assert not duplicated, f"row(s) on both pages: {sorted(duplicated)}"
    assert not dropped, f"row(s) missing from both pages: {sorted(dropped)}"
    assert set(combined) == set(species_ids)


def test_no_structure_filter_leaves_the_default_order_unchanged(db_session):
    """Guardrail: the size sort must never leak into an ordinary browse
    with no active structure filter. Two species, small then large, with
    NO structure filter: the existing default order is created_at DESC
    (then id DESC) among ties, so the LATER-created (larger) species must
    lead -- the opposite of what a leaked size sort would produce."""
    small, _ = _make_real_species(db_session, "CO")  # 2 heavy atoms, created first
    large, _ = _make_real_species(
        db_session, "CCCCCCCCCCO"
    )  # 11 heavy atoms, created second

    response = browse_species(db_session, SpeciesBrowseRequest(limit=200))

    returned_ids = [
        r.species_id for r in response.records if r.species_id in (small.id, large.id)
    ]
    assert returned_ids == [large.id, small.id]


def test_structure_filter_exact_mode_can_return_multiple_species_unsized(db_session):
    """``species.inchi_key`` is explicitly non-unique -- see
    ``Species.__table_args__``: "one InChIKey may map to several species"
    -- so an ``exact`` match CAN return more than one row; this is
    verified here rather than assumed. Because ``exact`` is not given the
    size sort (see ``_rank_and_slice_species``'s ``order_by_size``
    docstring for why), the two rows keep the ordinary
    ``best_rank/has_entries/created_at DESC/id DESC`` order: the
    LATER-created (larger) row leads, the opposite of what a leaked size
    sort would produce. Same synthetic-shared-InChIKey technique as
    production data can genuinely produce (distinct species, one
    InChIKey)."""
    target_key = _real_inchi_key("CO")
    small = make_species(db_session, smiles=unique_smiles(), inchi_key=target_key)
    make_species_entry(db_session, small)
    large = make_species(db_session, smiles=unique_smiles(), inchi_key=target_key)
    make_species_entry(db_session, large)

    response = browse_species(
        db_session,
        SpeciesBrowseRequest(query_smiles="CO", mode="exact", limit=200),
    )

    returned_ids = [
        r.species_id for r in response.records if r.species_id in (small.id, large.id)
    ]
    assert set(returned_ids) == {small.id, large.id}
    assert returned_ids == [large.id, small.id]


def test_structure_filter_size_sort_keeps_unparseable_smiles_species_visible(
    db_session,
):
    """A species whose OWN ``smiles`` RDKit cannot parse yields a NULL
    ``heavy_atom_count`` (:func:`_heavy_atom_count_expr`'s documented
    NULL-on-unparseable behaviour) -- ``.nulls_last()`` must keep that row
    in the listing, not drop it, and it must land deterministically (after
    every successfully-sized row).

    Contrived on purpose: production species resolution derives
    ``species_entry.mol`` from the SAME ``species.smiles`` (see
    ``make_species_entry``'s docstring), so the two normally fail
    together -- which is exactly why a species with unparseable ``smiles``
    can never reach a structure-filtered candidate set through the normal
    pipeline (``_apply_structure_filter`` requires ``species_entry.mol IS
    NOT NULL``). This fixture deliberately decouples them (a valid ``mol``
    on an entry whose parent species carries garbage ``smiles``) so the
    NULLS-LAST path is actually exercised rather than assumed safe because
    it is unreachable in practice.
    """
    garbled = make_species(
        db_session, smiles="not-a-smiles(((", inchi_key=next_inchi_key("BRNULLSZ")
    )
    db_session.add(SpeciesEntry(species_id=garbled.id, mol="CCO"))
    db_session.flush()
    ethanol, _ = _make_real_species(db_session, "CCO")  # 3 heavy atoms, real size

    response = browse_species(
        db_session,
        SpeciesBrowseRequest(query_smiles="CO", mode="substructure", limit=200),
    )

    ours = {garbled.id, ethanol.id}
    returned_ids = [r.species_id for r in response.records if r.species_id in ours]
    # Not dropped, and sorted after the species with a known size.
    assert returned_ids == [ethanol.id, garbled.id]


def test_structure_filter_size_sort_keeps_id_as_the_final_tiebreak(db_session):
    """A direct, plan-independent guard for the missing-unique-tiebreak
    defect the pagination test above targets behaviourally.

    Two Postgres rows tied on every other key have no order guaranteed by
    the SQL standard, and empirically (checked while writing this suite,
    forcing ``enable_hashagg`` on/off between two page fetches the same
    way ``test_pagination_is_stable_even_across_different_query_plans``
    does for the unfiltered case) this particular query shape did not
    reproduce a visible reordering at any fixture size tried -- Postgres's
    sort happened to preserve input order for every tied group tried here,
    which is NOT a guarantee this code can rely on. So this test does not
    depend on provoking that nondeterminism at runtime: it captures the
    actual SQL Postgres is sent for a size-sorted browse and asserts
    ``id`` is present as the LAST key in the ``ORDER BY`` clause, after
    ``heavy_atom_count`` -- the thing that makes the full key list a total
    order regardless of what any given Postgres version does with ties on
    a given day.
    """
    from sqlalchemy import event

    _make_real_species(db_session, "CCCO")

    captured: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        captured.append(statement)

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", _capture)
    try:
        browse_species(
            db_session,
            SpeciesBrowseRequest(query_smiles="CO", mode="substructure"),
        )
    finally:
        event.remove(bind, "before_cursor_execute", _capture)

    order_by_statements = [
        s for s in captured if "ORDER BY" in s and "heavy_atom_count" in s
    ]
    assert order_by_statements, (
        "no captured statement both sorted and referenced heavy_atom_count "
        f"-- captured {len(captured)} statement(s) total"
    )
    for statement in order_by_statements:
        # Not a bare ".id" substring search: the CASE expression that
        # computes best_rank/has_entries legitimately contains
        # "species_entry.id IS NOT NULL" earlier in the same ORDER BY
        # clause, which a looser check matches by accident. The compiled
        # sort key itself is this exact, unambiguous fragment.
        order_by_clause = statement.split("ORDER BY", 1)[1]
        heavy_pos = order_by_clause.find("heavy_atom_count")
        id_key_pos = order_by_clause.find("candidate_species.id DESC")
        assert heavy_pos != -1, statement
        assert id_key_pos != -1, (
            "candidate_species.id DESC is not present as an ORDER BY key: "
            f"{order_by_clause!r}"
        )
        assert heavy_pos < id_key_pos, (
            "heavy_atom_count must lead id, not the other way around: "
            f"{order_by_clause!r}"
        )
