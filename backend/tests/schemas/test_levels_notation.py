"""``ScientificLevelsSummary.notation`` for every shape (ADR 0021, plan section 2.6).

The notation is derived from ``energy`` and ``geometry`` at read time, never stored. The rules,
each pinned by a test below that fails when its rule is bent:

* either level absent -> ``None`` (never a partial notation);
* the two levels the same level (by ref) -> that one level, written once;
* two levels -> ``energy//geometry``;
* a composite energy -> the composite's own label, and ``//geometry`` only when the geometry
  is not the recipe's own (by ref); a recipe that states no geometry has no "own" geometry.

These are pure-function and pure-model tests: they call ``levels_notation`` and build
``ScientificLevelsSummary`` directly, including through ``model_construct`` (an unvalidated
payload), so the derivation does not depend on a read having produced the fields.
"""

from __future__ import annotations

import pytest

from app.db.models.common import CompositeSchemeKind, CoreTreatment
from app.schemas.reads.scientific_common import (
    CompositeSchemeSummary,
    LevelOfTheorySummary,
    ScientificLevelsSummary,
    levels_notation,
)


def _level(
    ref: str,
    method: str,
    basis: str | None = None,
    *,
    scheme: CompositeSchemeSummary | None = None,
) -> LevelOfTheorySummary:
    return LevelOfTheorySummary(
        level_of_theory_id=abs(hash(ref)) % 10_000,
        level_of_theory_ref=ref,
        method=method,
        basis=basis,
        core_treatment=None,
        composite_scheme=scheme,
    )


F12 = _level("lot_f12", "CCSD(T)-F12", "cc-pVTZ-F12")
WB97XD = _level("lot_wb97xd", "wB97X-D", "def2-TZVP")
B3LYP_CBSB7 = _level("lot_b3lyp_cbsb7", "B3LYP", "CBSB7")


def _cbs_qb3(geometry_ref: str | None = "lot_b3lyp_cbsb7") -> LevelOfTheorySummary:
    scheme = CompositeSchemeSummary(
        composite_scheme_ref="csch_qb3",
        kind=CompositeSchemeKind.named_method,
        name="CBS-QB3",
        geometry_level_of_theory_ref=geometry_ref,
    )
    return _level("lot_cbsqb3", "CBS-QB3", None, scheme=scheme)


def test_two_levels_are_written_energy_slash_slash_geometry():
    assert levels_notation(energy=F12, geometry=WB97XD) == "CCSD(T)-F12/cc-pVTZ-F12//wB97X-D/def2-TZVP"


def test_one_level_for_both_is_written_once():
    assert levels_notation(energy=WB97XD, geometry=WB97XD) == "wB97X-D/def2-TZVP"


def test_equality_is_by_ref_not_by_text():
    """Two rows can render alike (they differ in dispersion or solvent) and are still two levels."""
    twin = _level("lot_wb97xd_solvated", "wB97X-D", "def2-TZVP")
    assert levels_notation(energy=WB97XD, geometry=twin) == "wB97X-D/def2-TZVP//wB97X-D/def2-TZVP"
    # The converse: the same ref spelled with different text (a stale summary) is still one level.
    assert levels_notation(energy=WB97XD, geometry=_level("lot_wb97xd", "wb97xd", "def2tzvp")) == "wB97X-D/def2-TZVP"


def test_two_levels_differing_only_in_dispersion_or_core_treatment_get_different_notation():
    """``display`` is method/basis only; the notation spells out the rest of the level's identity."""
    plain = _level("lot_b3lyp", "B3LYP", "def2-TZVP")
    d3 = plain.model_copy(update={"level_of_theory_ref": "lot_b3lyp_d3", "dispersion": "D3BJ"})
    fc = F12.model_copy(update={"level_of_theory_ref": "lot_f12_fc", "core_treatment": CoreTreatment.frozen_core})
    ae = F12.model_copy(update={"level_of_theory_ref": "lot_f12_ae", "core_treatment": CoreTreatment.all_electron})
    assert levels_notation(energy=d3, geometry=d3) == "B3LYP/def2-TZVP (disp=D3BJ)"
    assert levels_notation(energy=plain, geometry=plain) == "B3LYP/def2-TZVP"
    assert levels_notation(energy=F12, geometry=d3) == "CCSD(T)-F12/cc-pVTZ-F12//B3LYP/def2-TZVP (disp=D3BJ)"
    assert levels_notation(energy=fc, geometry=WB97XD) != levels_notation(energy=ae, geometry=WB97XD)
    assert levels_notation(energy=fc, geometry=WB97XD) == "CCSD(T)-F12/cc-pVTZ-F12 (core=frozen_core)//wB97X-D/def2-TZVP"
    solvated = plain.model_copy(update={"level_of_theory_ref": "lot_b3lyp_s", "solvent": "water", "dispersion": "D3BJ"})
    assert levels_notation(energy=solvated, geometry=solvated) == "B3LYP/def2-TZVP (disp=D3BJ, solvent=water)"


def test_a_method_without_a_basis_is_written_as_the_method_alone():
    am1 = _level("lot_am1", "AM1")
    assert levels_notation(energy=am1, geometry=am1) == "AM1"
    assert levels_notation(energy=F12, geometry=am1) == "CCSD(T)-F12/cc-pVTZ-F12//AM1"


def test_a_composite_on_its_own_recipe_geometry_is_the_label_alone():
    assert levels_notation(energy=_cbs_qb3(), geometry=B3LYP_CBSB7) == "CBS-QB3"


def test_a_composite_level_that_is_also_the_geometry_is_written_once_as_its_label():
    """The legacy shape: an opt that ran at the CBS-QB3 level. Energy and geometry are one level, not ``X//X``."""
    qb3 = _cbs_qb3()
    assert levels_notation(energy=qb3, geometry=qb3) == "CBS-QB3"
    # ... even when the recipe's own geometry is a different level, because the geometry here IS the energy level.
    assert levels_notation(energy=_cbs_qb3(geometry_ref=None), geometry=qb3) == "CBS-QB3"


def test_a_composite_on_an_external_geometry_names_it():
    assert levels_notation(energy=_cbs_qb3(), geometry=WB97XD) == "CBS-QB3//wB97X-D/def2-TZVP"


def test_a_recipe_that_states_no_geometry_has_no_own_geometry_so_the_geometry_is_written():
    assert levels_notation(energy=_cbs_qb3(geometry_ref=None), geometry=B3LYP_CBSB7) == "CBS-QB3//B3LYP/CBSB7"


def test_the_composite_label_is_the_scheme_name_not_the_levels_method_spelling():
    energy = _cbs_qb3()
    energy = energy.model_copy(update={"method": "cbs-qb3"})
    assert levels_notation(energy=energy, geometry=B3LYP_CBSB7) == "CBS-QB3"


@pytest.mark.parametrize(
    ("energy", "geometry"),
    [(None, None), (F12, None), (None, WB97XD), (_cbs_qb3(), None), (None, B3LYP_CBSB7)],
    ids=["neither", "no-geometry", "no-energy", "composite-no-geometry", "composite-none-energy"],
)
def test_an_absent_level_gives_no_notation_never_a_partial_one(energy, geometry):
    assert levels_notation(energy=energy, geometry=geometry) is None


def test_the_model_derives_it_from_its_own_fields():
    levels = ScientificLevelsSummary(
        geometry=WB97XD,
        energy=F12,
        energy_source="sp",
        composite_energy_verification=None,
        legacy_composite_shape=None,
    )
    assert levels.notation == "CCSD(T)-F12/cc-pVTZ-F12//wB97X-D/def2-TZVP"
    # It is serialised (the read contract) and follows a later change of the fields it is derived from.
    assert levels.model_dump(mode="json")["notation"] == levels.notation
    assert levels.model_copy(update={"geometry": F12}).notation == "CCSD(T)-F12/cc-pVTZ-F12"


def test_an_empty_summary_has_a_null_notation_in_the_contract():
    dumped = ScientificLevelsSummary(composite_energy_verification=None, legacy_composite_shape=None).model_dump(
        mode="json"
    )
    assert "notation" in dumped and dumped["notation"] is None


def test_the_derivation_does_not_depend_on_validation():
    """An unvalidated payload (``model_construct``) derives the same notation from whatever it holds."""
    levels = ScientificLevelsSummary.model_construct(geometry=B3LYP_CBSB7, energy=_cbs_qb3())
    assert levels.notation == "CBS-QB3"
    assert ScientificLevelsSummary.model_construct(energy=F12).notation is None


def test_notation_is_never_accepted_on_input():
    """Passing it is ignored, not stored: the value always comes from the levels."""
    levels = ScientificLevelsSummary(
        geometry=WB97XD,
        energy=F12,
        notation="something else",  # type: ignore[call-arg]
        composite_energy_verification=None,
        legacy_composite_shape=None,
    )
    assert levels.notation == "CCSD(T)-F12/cc-pVTZ-F12//wB97X-D/def2-TZVP"
