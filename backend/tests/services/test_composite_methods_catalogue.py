"""The named-composite-method catalogue's own rules (ADR 0021).

A catalogue entry is only worth having if every value in it can be traced, so
the rules are tests: every value cited, every NULL explained, every key the
identity key of its own spelling, the lookalike recipes kept apart.
"""

from __future__ import annotations

import re

import pytest

from app.chemistry.composite_methods import (
    BY_KEY,
    CATALOGUE,
    CompositeMethod,
    composite_method_for,
)
from app.chemistry.method_names import method_identity_key

_REQUIRED = {
    "cbs-qb3", "rocbs-qb3", "cbs-apno", "cbs-4m", "g3", "g3b3", "g3mp2", "g3mp2b3",
    "g4", "g4mp2", "w1", "w1u", "w1bd", "w1ro", "w2",
}

_DOI = re.compile(r"^10\.\d{4,9}/\S+$")


def test_the_catalogue_covers_exactly_the_requested_methods():
    assert {e.key for e in CATALOGUE} == _REQUIRED
    assert len(CATALOGUE) == len(BY_KEY) == len(_REQUIRED)


@pytest.mark.parametrize("entry", CATALOGUE, ids=lambda e: e.key)
def test_the_key_is_the_identity_key_of_its_own_spelling(entry: CompositeMethod):
    assert method_identity_key(entry.key) == entry.key
    assert composite_method_for(entry.key) is entry


@pytest.mark.parametrize("entry", CATALOGUE, ids=lambda e: e.key)
def test_every_entry_has_a_doi_and_a_paper(entry: CompositeMethod):
    assert _DOI.match(entry.paper_doi), entry.paper_doi
    assert len(entry.paper) > 40
    assert entry.citations, "entry-level citation (spelling / scope) is missing"


@pytest.mark.parametrize("entry", CATALOGUE, ids=lambda e: e.key)
def test_every_value_is_cited_and_every_null_is_explained(entry: CompositeMethod):
    for level in (entry.geometry_level, entry.frequency_level):
        if level is not None:
            assert level.method
            assert level.citations and all(len(c) > 30 for c in level.citations)
    if entry.recipe_zpe_scale_factor is not None:
        assert 0.8 < entry.recipe_zpe_scale_factor < 1.1
        assert entry.zpe_citations and all(len(c) > 30 for c in entry.zpe_citations)
    else:
        assert entry.zpe_citations == ()
    stated = {
        "geometry_level": entry.geometry_level is not None,
        "frequency_level": entry.frequency_level is not None,
        "recipe_zpe_scale_factor": entry.recipe_zpe_scale_factor is not None,
    }
    notes = " ".join(entry.not_stated)
    for field, present in stated.items():
        if not present:
            assert field in notes, f"{entry.key}: {field} is NULL with no reason given"


def test_a_value_is_null_rather_than_guessed_where_the_source_is_silent():
    """Pinned so a later edit cannot fill a NULL without adding its citation."""
    assert BY_KEY["rocbs-qb3"].geometry_level is None
    for key in ("w1u", "w1bd", "w1ro"):
        assert BY_KEY[key].geometry_level is None
        assert BY_KEY[key].recipe_zpe_scale_factor is None
    assert BY_KEY["w2"].frequency_level is None
    assert BY_KEY["w2"].recipe_zpe_scale_factor is None


def test_the_cited_values():
    cbs = BY_KEY["cbs-qb3"]
    assert (cbs.geometry_level.method, cbs.geometry_level.basis) == ("B3LYP", "CBSB7")
    assert cbs.frequency_level == cbs.geometry_level
    assert cbs.recipe_zpe_scale_factor == 0.99
    g4 = BY_KEY["g4"]
    assert (g4.geometry_level.method, g4.geometry_level.basis) == ("B3LYP", "6-31G(2df,p)")
    assert g4.recipe_zpe_scale_factor == 0.9854
    w1 = BY_KEY["w1"]
    assert (w1.geometry_level.method, w1.geometry_level.basis) == ("B3LYP", "cc-pVTZ+1")
    assert w1.recipe_zpe_scale_factor == 0.985
    g3 = BY_KEY["g3"]
    assert (g3.geometry_level.method, g3.geometry_level.basis) == ("MP2(FU)", "6-31G(d)")
    assert (g3.frequency_level.method, g3.frequency_level.basis) == ("HF", "6-31G(d)")
    assert (BY_KEY["w2"].geometry_level.method, BY_KEY["w2"].geometry_level.basis) == (
        "CCSD(T)",
        "cc-pVQZ+1",
    )


def test_the_lookalike_recipes_are_four_and_two_entries():
    assert {"w1", "w1u", "w1bd", "w1ro"} <= set(BY_KEY)
    assert {"cbs-qb3", "rocbs-qb3"} <= set(BY_KEY)
    assert len({id(BY_KEY[k]) for k in ("w1", "w1u", "w1bd", "w1ro")}) == 4


@pytest.mark.parametrize(
    ("spelling", "key"),
    [
        ("CBS-QB3", "cbs-qb3"),
        ("cbsqb3", "cbs-qb3"),
        ("G4(MP2)", "g4mp2"),
        ("g3(mp2)b3", "g3mp2b3"),
        ("W1BD", "w1bd"),
    ],
)
def test_lookup_goes_through_the_identity_key(spelling, key):
    assert composite_method_for(spelling) is BY_KEY[key]


@pytest.mark.parametrize(
    "spelling", ["W1-BD", "cbs-qb3-paraskevas", "cbsqb32023", "b3lyp", "wb97xd", ""]
)
def test_a_non_method_has_no_entry(spelling):
    assert composite_method_for(spelling) is None


def test_only_gaussian_is_claimed_as_a_program():
    for entry in CATALOGUE:
        assert set(entry.programs) <= {"gaussian"}
    assert BY_KEY["w1"].programs == () and BY_KEY["w2"].programs == ()
