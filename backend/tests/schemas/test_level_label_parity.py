"""One renderer for a level of theory, behind both the notation and the ML export's label (ADR 0021, P7a).

The notation of a record's levels is built from ``LevelOfTheorySummary``; the ML-dataset export's ``label`` is
built from the ``LevelOfTheory`` row. Both call :func:`app.chemistry.level_label.render_level_label`, and these
tests hold them to it: the same level renders identically through both, every part of the identity the renderer
writes changes the label, and raw ``keywords`` are deliberately not part of it.
"""

from __future__ import annotations

import pytest

from app.chemistry.level_label import render_level_label
from app.db.models.common import CoreTreatment, SpinTreatment
from app.db.models.level_of_theory import LevelOfTheory
from app.schemas.reads.scientific_common import LevelOfTheorySummary, level_label
from app.services.scientific_read.ml_dataset import _lot_label

BASE = {"method": "B3LYP", "basis": "def2-TZVP"}

#: A spread of levels: nothing stated, each part alone, and several together.
SPREAD = [
    {"method": "AM1"},
    BASE,
    {**BASE, "dispersion": "D3BJ"},
    {**BASE, "solvent": "water"},
    {**BASE, "solvent": "water", "solvent_model": "smd"},
    {**BASE, "solvent_model": "smd"},  # a model with no solvent: nothing to write
    {**BASE, "spin_treatment": SpinTreatment.unrestricted},
    {**BASE, "spin_treatment": SpinTreatment.restricted},
    {**BASE, "core_treatment": CoreTreatment.frozen_core},
    {**BASE, "core_treatment": CoreTreatment.all_electron},
    {"method": "CCSD(T)-F12", "basis": "cc-pVTZ-F12", "cabs_basis": "cc-pVTZ-F12-CABS"},
    {"method": "CCSD(T)-F12", "basis": "cc-pVTZ-F12", "aux_basis": "cc-pVTZ-F12/C"},
    {
        "method": "DLPNO-CCSD(T)",
        "basis": "cc-pVTZ",
        "aux_basis": "cc-pVTZ/C",
        "cabs_basis": "cabs",
        "dispersion": "D3BJ",
        "solvent": "water",
        "solvent_model": "smd",
        "spin_treatment": SpinTreatment.restricted_open,
        "core_treatment": CoreTreatment.frozen_core,
    },
]


def _row(fields: dict) -> LevelOfTheory:
    return LevelOfTheory(lot_hash="0" * 64, **fields)


def _summary(fields: dict) -> LevelOfTheorySummary:
    return LevelOfTheorySummary(level_of_theory_id=1, level_of_theory_ref="lot_x", composite_scheme=None, **fields)


@pytest.mark.parametrize("fields", SPREAD, ids=lambda f: "-".join(sorted(f)))
def test_the_same_level_renders_identically_through_both_paths(fields):
    assert level_label(_summary(fields)) == _lot_label(_row(fields))


def test_the_rendering_is_exactly_this():
    """Pinned: part order and spelling, so a change to either path or the renderer is a visible change."""
    full = SPREAD[-1]
    expected = (
        "DLPNO-CCSD(T)/cc-pVTZ (aux=cc-pVTZ/C, cabs=cabs, disp=D3BJ, solvent=smd:water, "
        "spin=restricted_open, core=frozen_core)"
    )
    assert level_label(_summary(full)) == expected
    assert _lot_label(_row(full)) == expected
    assert level_label(_summary(BASE)) == "B3LYP/def2-TZVP"
    assert level_label(_summary({"method": "AM1"})) == "AM1"


@pytest.mark.parametrize(
    "change",
    [
        {"spin_treatment": SpinTreatment.unrestricted},
        {"spin_treatment": SpinTreatment.restricted},
        {"solvent": "water", "solvent_model": "smd"},
        {"solvent": "water", "solvent_model": "pcm"},
        {"solvent": "water"},
        {"core_treatment": CoreTreatment.frozen_core},
        {"core_treatment": CoreTreatment.all_electron},
        {"cabs_basis": "cabs-a"},
        {"cabs_basis": "cabs-b"},
        {"aux_basis": "aux-a"},
        {"dispersion": "D3BJ"},
    ],
    ids=lambda c: "-".join(f"{k}={getattr(v, 'value', v)}" for k, v in c.items()),
)
def test_levels_differing_in_one_part_alone_render_differently(change):
    """Each part of the identity the renderer writes moves the label, through both paths."""
    plain = {"method": "B3LYP", "basis": "def2-TZVP"}
    other = {**plain, **change}
    assert level_label(_summary(plain)) != level_label(_summary(other))
    assert _lot_label(_row(plain)) != _lot_label(_row(other))


def test_variants_of_one_part_differ_from_each_other():
    plain = {"method": "B3LYP", "basis": "def2-TZVP"}
    for part, a, b in (
        ("spin_treatment", SpinTreatment.restricted, SpinTreatment.unrestricted),
        ("core_treatment", CoreTreatment.frozen_core, CoreTreatment.all_electron),
    ):
        assert level_label(_summary({**plain, part: a})) != level_label(_summary({**plain, part: b}))
    smd = {**plain, "solvent": "water", "solvent_model": "smd"}
    pcm = {**plain, "solvent": "water", "solvent_model": "pcm"}
    assert level_label(_summary(smd)) != level_label(_summary(pcm))
    assert _lot_label(_row(smd)) != _lot_label(_row(pcm))


def test_unrestricted_and_restricted_levels_of_one_method_read_differently_in_a_notation():
    from app.schemas.reads.scientific_common import levels_notation

    restricted = _summary({**BASE, "spin_treatment": SpinTreatment.restricted})
    unrestricted = _summary({**BASE, "spin_treatment": SpinTreatment.unrestricted}).model_copy(
        update={"level_of_theory_ref": "lot_u"}
    )
    same_text = levels_notation(energy=restricted, geometry=restricted)
    assert same_text == "B3LYP/def2-TZVP (spin=restricted)"
    assert levels_notation(energy=unrestricted, geometry=restricted) == (
        "B3LYP/def2-TZVP (spin=unrestricted)//B3LYP/def2-TZVP (spin=restricted)"
    )


def test_raw_keywords_are_not_part_of_the_label():
    with_keywords = {**BASE, "keywords": "tightscf grid=ultrafine"}
    assert _lot_label(_row(with_keywords)) == _lot_label(_row(BASE)) == "B3LYP/def2-TZVP"


def test_an_unstated_part_is_omitted_not_defaulted_and_empty_strings_count_as_unstated():
    assert render_level_label(method="M", basis="", dispersion="", solvent="", aux_basis="", cabs_basis="") == "M"
    assert render_level_label(method="M", basis="B", spin_treatment=None, core_treatment=None) == "M/B"
    # The renderer takes an enum or its value.
    assert render_level_label(method="M", spin_treatment="unrestricted", core_treatment=CoreTreatment.frozen_core) == (
        "M (spin=unrestricted, core=frozen_core)"
    )
    # A solvent model with no solvent writes nothing.
    assert render_level_label(method="M", solvent_model="smd") == "M"
