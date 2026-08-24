"""Display names for RMG reaction-family identifiers.

The point of these tests is not that the 125 names render *prettily* — it is
that an incomplete chemistry dictionary is safe. Three properties carry that:

* an unmapped token is left alone (never dropped, never guessed);
* a family whose meaning is genuinely unresolved is shown as its raw
  identifier rather than half-translated into something that reads
  authoritative;
* "unresolved" is a named list, not "absent from the dictionary" — otherwise
  adding a legitimate expansion would silently change which families get
  translated.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.chemistry.reaction_family_display import (
    TOKEN_EXPANSIONS,
    UNRESOLVED_FAMILIES,
    UNRESOLVED_TOKENS,
    is_unresolved_reaction_family,
    reaction_family_display_name,
)
from app.db.models.reaction import ReactionFamily
from app.schemas.reaction_family import CANONICAL_REACTION_FAMILIES

# The five families measured to carry a token nobody could resolve, plus the
# one excluded by name. Anything else appearing here means the refusal rule
# widened and families that should read as English stopped doing so.
EXPECTED_RAW_FAMILIES = {
    "Intra_R_Add_ExoTetCyclic",
    "R_Addition_COm",
    "R_Addition_CSm",
    "Surface_Carbonate_2F_Decomposition",
    "Surface_Carbonate_CO_2F_Decomposition",
    "Surface_Carbonate_F_CO_Decomposition",
}


# ---------------------------------------------------------------------------
# Every stored family renders
# ---------------------------------------------------------------------------


def test_every_canonical_family_renders_non_empty():
    """All 125 seeded identifiers produce a display name; none raises."""
    assert len(CANONICAL_REACTION_FAMILIES) == 125
    for name in sorted(CANONICAL_REACTION_FAMILIES):
        display = reaction_family_display_name(name)
        assert display, f"{name} rendered empty"
        assert display.strip() == display
        assert "_" not in display or name in EXPECTED_RAW_FAMILIES


def test_every_seeded_family_row_renders(db_session):
    """Drive the real vocabulary as the database actually holds it.

    The canonical constant and the seeded ``reaction_family`` table are two
    different artefacts; a display name has to survive the rows, not just the
    constant.
    """
    names = list(db_session.scalars(select(ReactionFamily.name)))
    assert len(names) == 125
    assert set(names) == set(CANONICAL_REACTION_FAMILIES)
    for name in names:
        assert reaction_family_display_name(name)


def test_blank_name_is_rejected():
    with pytest.raises(ValueError):
        reaction_family_display_name("   ")


# ---------------------------------------------------------------------------
# Layer 1 — mechanical splitting
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "identifier, expected",
    [
        # underscores and camelCase
        ("Surface_Adsorption_Bidentate", "Surface Adsorption Bidentate"),
        ("Cation_Addition_MultipleBond", "Cation Addition Multiple Bond"),
        ("Surface_EleyRideal_Addition_Multiple_Bond",
         "Surface Eley Rideal Addition Multiple Bond"),
        # hyphens separate too
        ("Disproportionation-Y", "Disproportionation Y"),
        ("Baeyer-Villiger_step2", "Baeyer Villiger step2"),
        # a lowercase identifier gets a capital first letter, nothing else
        ("lone_electron_pair_bond", "Lone electron pair bond"),
        ("halocarbene_recombination", "Halocarbene recombination"),
        # a single token with nothing to split is returned as-is
        ("Disproportionation", "Disproportionation"),
    ],
)
def test_mechanical_layer(identifier, expected):
    assert reaction_family_display_name(identifier) == expected


@pytest.mark.parametrize(
    "identifier, expected",
    [
        # A naive camelCase split would produce "vd W", "Li R", "NH 3".
        ("Surface_Abstraction_vdW", "Surface Abstraction vdW"),
        ("1,2_Elimination_LiR", "1,2 Elimination LiR"),
        ("1,2_NH3_elimination", "1,2 NH3 elimination"),
        ("Intra_RH_Add_Endocyclic", "Intra RH Add Endocyclic"),
        ("XY_Addition_MultipleBond", "XY Addition Multiple Bond"),
    ],
)
def test_camel_split_does_not_mangle_formulas(identifier, expected):
    assert reaction_family_display_name(identifier) == expected


# ---------------------------------------------------------------------------
# Layer 2 — the confirmed dictionary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "identifier, expected",
    [
        ("H_Abstraction", "Hydrogen Abstraction"),
        ("Birad_R_Recombination", "Biradical Radical Recombination"),
        ("R_Addition_MultipleBond", "Radical Addition Multiple Bond"),
        ("Birad_recombination", "Biradical recombination"),
        ("HO2_Elimination_from_PeroxyRadical",
         "Hydroperoxyl Elimination from Peroxy Radical"),
        ("Singlet_Val6_to_triplet",
         "Singlet atom with six valence electrons to triplet"),
        ("Intra_2+2_cycloaddition_Cd",
         "Intra 2+2 cycloaddition carbon with one double bond"),
        ("1,2_Insertion_CO", "1,2 Insertion CO"),
        # element symbols, settled by their abstraction siblings
        ("F_Abstraction", "Fluorine Abstraction"),
        ("Br_Abstraction", "Bromine Abstraction"),
        ("Cl_Abstraction", "Chlorine Abstraction"),
        ("Li_Abstraction", "Lithium Abstraction"),
    ],
)
def test_confirmed_expansions_apply(identifier, expected):
    assert reaction_family_display_name(identifier) == expected


def test_dictionary_is_exactly_the_confirmed_ledger():
    """The dictionary is a ledger of confirmed chemistry claims.

    Every entry was checked by a chemist in *family-name* scope. Changing
    this test is the deliberate friction: an entry must not appear because
    someone wanted a gap filled.
    """
    assert TOKEN_EXPANSIONS == {
        "H": "Hydrogen",
        "R": "Radical",
        "Birad": "Biradical",
        "HO2": "hydroperoxyl",
        "Val6": "atom with six valence electrons",
        "Cd": "carbon with one double bond",
        # No entry for "1,2". Comma locants render bare -- see the note beside
        # _LOCANTS. They are still recognised, so they can never be swept into
        # another rule, but they carry no expansion because the notation needs
        # no explaining to the audience that reads it.
        "F": "Fluorine",
        "Br": "Bromine",
        "Cl": "Chlorine",
        "Li": "Lithium",
    }


@pytest.mark.parametrize("token", ["COm", "CSm", "2F", "ExoTetCyclic"])
def test_unexplained_tokens_never_gain_an_expansion(token):
    """A future well-meaning guess must fail loudly here.

    ``COm``/``CSm`` (unexplained trailing ``m``), ``2F`` (fluorine? Faradays?
    free sites?) and ``ExoTetCyclic`` (probably Baldwin's rules, unconfirmed)
    are not understood. Inventing chemistry on a public page is the one thing
    this project refuses.
    """
    assert token not in TOKEN_EXPANSIONS
    assert token in UNRESOLVED_TOKENS


def test_wildcard_and_molecule_tokens_are_not_imported_from_rmg_atom_types():
    """``CO`` is carbon monoxide in a family name, not a carbonyl carbon.

    RMG's atom-type table is scoped to group definitions. Importing it
    wholesale would corrupt family names, so ``CO`` has no entry at all and
    falls through untouched.
    """
    assert "CO" not in TOKEN_EXPANSIONS
    assert reaction_family_display_name("CO_Disproportionation") == "CO Disproportionation"
    # ``R``, by contrast, *is* mapped — but to the family-name reading.
    assert TOKEN_EXPANSIONS["R"] == "Radical"


# ---------------------------------------------------------------------------
# The fall-through property — what makes an incomplete dictionary safe
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "identifier, expected",
    [
        # None of these tokens is in the dictionary. All survive verbatim,
        # spaced — not dropped, not guessed.
        ("Surface_Adsorption_Bidentate", "Surface Adsorption Bidentate"),
        ("H2_Loss", "H2 Loss"),
        ("intra_NO2_ONO_conversion", "Intra NO2 ONO conversion"),
        ("Korcek_step1_cat", "Korcek step1 cat"),
        # tokens that will never be in any dictionary
        ("Zzz_Abstraction", "Zzz Abstraction"),
        ("Qq_Ww_Ee", "Qq Ww Ee"),
    ],
)
def test_unmapped_tokens_fall_through_untouched(identifier, expected):
    """An unmapped token renders as its Layer-1 form and nothing else.

    This is the property that lets the dictionary be one entry or seventy
    without anything ever being wrong.
    """
    assert reaction_family_display_name(identifier) == expected


def test_a_family_of_only_unmapped_words_still_translates():
    """The refusal rule must not widen to "any unmapped token".

    ``Surface``, ``Adsorption`` and ``Bidentate`` are ordinary English and
    read correctly with no dictionary entry. If "unknown" were implemented as
    "not in the dictionary", this family would wrongly be shown raw.
    """
    identifier = "Surface_Adsorption_Bidentate"
    assert not any(token in TOKEN_EXPANSIONS for token in identifier.split("_"))
    assert not is_unresolved_reaction_family(identifier)
    assert reaction_family_display_name(identifier) == "Surface Adsorption Bidentate"


# ---------------------------------------------------------------------------
# The refusal — an unresolved family keeps its identifier
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("identifier", sorted(EXPECTED_RAW_FAMILIES))
def test_unresolved_family_is_returned_verbatim(identifier):
    """Byte-identical to the stored identifier — not spaced, not expanded.

    A half-translated "Surface Carbonate 2F Decomposition" reads like a human
    name while carrying an untranslated chemistry token. The raw identifier is
    visibly a machine name, so a reader knows no translation was given.
    """
    assert is_unresolved_reaction_family(identifier)
    assert reaction_family_display_name(identifier) == identifier


def test_exactly_these_canonical_families_are_shown_raw():
    """Measured over the real vocabulary: six of 125, and no others."""
    raw = {
        name
        for name in CANONICAL_REACTION_FAMILIES
        if reaction_family_display_name(name) == name and "_" in name
    }
    assert raw == EXPECTED_RAW_FAMILIES


def test_f_is_scoped_to_the_family_not_to_the_token():
    """The same token, two meanings, decided per family.

    ``F`` is fluorine in ``F_Abstraction`` (siblings ``Br_``/``Cl_``/``H_``/
    ``Li_Abstraction`` settle it) and unresolved in
    ``Surface_Carbonate_F_CO_Decomposition``. A token-level table cannot say
    that; the family-level exclusion can.
    """
    assert reaction_family_display_name("F_Abstraction") == "Fluorine Abstraction"
    assert "Surface_Carbonate_F_CO_Decomposition" in UNRESOLVED_FAMILIES
    assert (
        reaction_family_display_name("Surface_Carbonate_F_CO_Decomposition")
        == "Surface_Carbonate_F_CO_Decomposition"
    )


# ---------------------------------------------------------------------------
# Locants
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "identifier, expected",
    [
        ("1,2_XY_interchange", "1,2 XY interchange"),
        ("1,3_NH3_elimination", "1,3 NH3 elimination"),
        ("1,4_Linear_birad_scission", "1,4 Linear birad scission"),
    ],
)
def test_comma_locants_survive_bare(identifier, expected):
    """A comma locant reaches the reader unchanged, and unprefixed.

    It was briefly rendered "Positions 1,2 ...". That is not wrong, it is
    merely unhelpful: ``1,3_sigmatropic_rearrangement`` is already how a
    chemist writes a [1,3] shift, so the prefix explains nothing to the
    audience that reads it and lengthens every such name.

    The locant pattern is still matched rather than ignored. That is what
    keeps ``1,2`` out of any other rule, and it is the same guard that stops
    ``2+2`` -- which counts cycloaddition components, not positions -- from
    ever being read as one. The separator is the discriminator: comma is a
    locant, plus is a count.
    """
    assert reaction_family_display_name(identifier) == expected


@pytest.mark.parametrize(
    "identifier, expected",
    [
        ("2+2_cycloaddition", "2+2 cycloaddition"),
        ("1+2_Cycloaddition", "1+2 Cycloaddition"),
    ],
)
def test_plus_forms_are_not_locants(identifier, expected):
    """``2+2`` counts cycloaddition components; "positions 2+2" would be wrong."""
    assert reaction_family_display_name(identifier) == expected
