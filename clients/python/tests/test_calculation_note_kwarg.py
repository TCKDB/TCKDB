"""``note=`` kwarg on the public ``Calculation.opt`` / ``.freq`` / ``.sp``
factories.

The kwarg surfaced from the ARC-style dry-run, which needed a place
to attach workflow-side context to a converged calculation (e.g.
"lowest-energy converged structure; conformer search history retained
as artifacts"). The wire schema's ``CalculationInBundle`` does not
carry a per-calc note today, so the value is **stored on the builder
but not emitted on the wire**. This module pins both behaviours.
"""

from __future__ import annotations

import json

import pytest

from tckdb_client.builders import (
    Calculation,
    ChemReaction,
    ComputedReactionUpload,
    ComputedSpeciesUpload,
    Geometry,
    Kinetics,
    LevelOfTheory,
    Species,
    SoftwareRelease,
    TCKDBBuilderValidationError,
    TransitionState,
)


SR = SoftwareRelease(software="Gaussian", version="16")
LOT = LevelOfTheory(method="wb97xd", basis="def2tzvp")

WATER_XYZ = (
    "3\nh2o\n"
    "O 0.0 0.0 0.117\n"
    "H 0.0 0.757 -0.469\n"
    "H 0.0 -0.757 -0.469"
)


@pytest.fixture
def geom() -> Geometry:
    return Geometry.from_xyz(WATER_XYZ)


# ---------------------------------------------------------------------
# Acceptance — each factory threads ``note`` onto the builder.
# ---------------------------------------------------------------------


def test_opt_accepts_note(geom):
    note = "lowest-energy converged structure; CREST-pruned"
    opt = Calculation.opt(
        SR, LOT, output_geometry=geom,
        final_energy_hartree=-76.4, converged=True, label="opt",
        note=note,
    )
    assert opt.note == note


def test_freq_accepts_note(geom):
    opt = Calculation.opt(
        SR, LOT, output_geometry=geom, final_energy_hartree=-76.4,
        converged=True, label="opt",
    )
    freq = Calculation.freq(
        SR, LOT, input_geometry=geom, n_imag=0, zpe_hartree=0.02,
        depends_on=opt, label="freq",
        note="harmonic on the converged opt structure",
    )
    assert freq.note == "harmonic on the converged opt structure"


def test_sp_accepts_note(geom):
    opt = Calculation.opt(
        SR, LOT, output_geometry=geom, final_energy_hartree=-76.4,
        converged=True, label="opt",
    )
    sp = Calculation.sp(
        SR, LOT, input_geometry=geom, electronic_energy_hartree=-76.45,
        depends_on=opt, label="sp",
        note="high-LoT refinement on the opt geometry",
    )
    assert sp.note == "high-LoT refinement on the opt geometry"


def test_note_defaults_to_none(geom):
    """Existing factory behaviour without ``note`` remains unchanged."""
    opt = Calculation.opt(
        SR, LOT, output_geometry=geom, final_energy_hartree=-76.4,
        converged=True, label="opt",
    )
    freq = Calculation.freq(
        SR, LOT, input_geometry=geom, n_imag=0, zpe_hartree=0.02,
        depends_on=opt, label="freq",
    )
    sp = Calculation.sp(
        SR, LOT, input_geometry=geom, electronic_energy_hartree=-76.45,
        depends_on=opt, label="sp",
    )
    assert opt.note is None
    assert freq.note is None
    assert sp.note is None


# ---------------------------------------------------------------------
# Validation — empty / non-string is rejected up front.
# ---------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["", "   ", "\t\n"])
def test_empty_note_is_rejected(geom, bad):
    with pytest.raises(TCKDBBuilderValidationError) as exc:
        Calculation.opt(
            SR, LOT, output_geometry=geom, final_energy_hartree=-76.4,
            converged=True, label="opt", note=bad,
        )
    assert "note" in str(exc.value).lower()


@pytest.mark.parametrize("bad", [42, 1.0, True, ["x"], {"x": 1}, b"bytes"])
def test_non_string_note_is_rejected(geom, bad):
    with pytest.raises(TCKDBBuilderValidationError) as exc:
        Calculation.opt(
            SR, LOT, output_geometry=geom, final_energy_hartree=-76.4,
            converged=True, label="opt", note=bad,
        )
    assert "note" in str(exc.value).lower()


def test_freq_and_sp_share_validation(geom):
    opt = Calculation.opt(
        SR, LOT, output_geometry=geom, final_energy_hartree=-76.4,
        converged=True, label="opt",
    )
    with pytest.raises(TCKDBBuilderValidationError):
        Calculation.freq(
            SR, LOT, input_geometry=geom, n_imag=0, zpe_hartree=0.02,
            depends_on=opt, label="freq", note="",
        )
    with pytest.raises(TCKDBBuilderValidationError):
        Calculation.sp(
            SR, LOT, input_geometry=geom, electronic_energy_hartree=-1.0,
            depends_on=opt, label="sp", note=123,  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------
# Wire emission — note is local-only; today's CalculationInBundle has
# no note field on either the species or the reaction upload path.
# ---------------------------------------------------------------------


_SENTINEL = "CALC_NOTE_SENTINEL_xyz123"


def _make_species_upload(geom: Geometry) -> ComputedSpeciesUpload:
    opt = Calculation.opt(
        SR, LOT, output_geometry=geom, final_energy_hartree=-76.4,
        converged=True, label="opt", note=_SENTINEL,
    )
    return ComputedSpeciesUpload(
        species=Species(smiles="O", charge=0, multiplicity=1, label="water"),
        calculations=[opt],
        primary_calculation=opt,
    )


def test_note_not_emitted_in_computed_species_payload(geom):
    """The Phase-1 ``CalculationInBundle`` shape has no ``note``
    field; the value must therefore not slip into the wire payload."""
    upload = _make_species_upload(geom)
    blob = json.dumps(upload.to_payload())
    assert _SENTINEL not in blob
    # Defensive: no flat ``"note":`` key appears on any conformer's
    # primary/additional calc dict.
    for conf in upload.to_payload()["conformers"]:
        primary = conf["primary_calculation"]
        assert "note" not in primary
        for add in conf.get("additional_calculations", []):
            assert "note" not in add


def test_note_preserved_on_builder_even_when_not_emitted(geom):
    upload = _make_species_upload(geom)
    # Walk via the public iter helper and confirm the value is still
    # on the builder object — emission is the wire concern, the
    # builder still carries the field for adapter / preview use.
    seen = [calc.note for calc in upload.iter_calculations()]
    assert _SENTINEL in seen


def test_note_not_emitted_in_computed_reaction_payload(geom):
    ts_geom = Geometry.from_xyz(
        "3\nts\nC 0 0 0\nH 0 0 0.8\nH 0 0 -1.0"
    )
    # Build a tiny reaction upload with notes scattered across TS-bucket
    # and species-bucket calcs.
    a_opt = Calculation.opt(
        SR, LOT, output_geometry=geom, final_energy_hartree=-76.4,
        converged=True, label="a opt", note=f"{_SENTINEL}_a_opt",
    )
    b_opt = Calculation.opt(
        SR, LOT, output_geometry=geom, final_energy_hartree=-76.4,
        converged=True, label="b opt", note=f"{_SENTINEL}_b_opt",
    )
    ts_opt = Calculation.opt(
        SR, LOT, output_geometry=ts_geom, final_energy_hartree=-1.0,
        converged=True, label="ts opt", note=f"{_SENTINEL}_ts_opt",
    )
    ts_freq = Calculation.freq(
        SR, LOT, n_imag=1, imag_freq_cm1=-1000.0, zpe_hartree=0.04,
        depends_on=ts_opt, label="ts freq",
        note=f"{_SENTINEL}_ts_freq",
    )
    kin = Kinetics.modified_arrhenius(
        A=1e13, A_units="cm3/mol/s", n=0.5, Ea=10.0, Ea_units="kJ/mol",
        Tmin=300, Tmax=2500,
    )
    a = Species(smiles="O", charge=0, multiplicity=1, label="A")
    b = Species(smiles="N", charge=0, multiplicity=1, label="B")
    rxn = ChemReaction(
        reactants=[a], products=[b],
        transition_state=TransitionState(
            charge=0, multiplicity=1, geometry=ts_geom, label="ts",
        ),
        kinetics=[kin],
    )
    upload = ComputedReactionUpload(
        reaction=rxn,
        calculations=[ts_opt, ts_freq],
        species_calculations={a: [a_opt], b: [b_opt]},
    )
    blob = json.dumps(upload.to_payload())
    # No note sentinel — TS- or species-bucket — appears on the wire.
    assert _SENTINEL not in blob, (
        "Calculation.note must not be emitted; today's bundle schema "
        "has no per-calc note field."
    )


def test_summary_does_not_leak_note_string(geom):
    """The summary surface is also a wire-shape viewer in the sense
    that it should not surface free-text builder annotations.
    ``note`` is *local* to the builder — producers can read it via
    ``upload.iter_calculations()`` if they want, but it must not
    appear in the structured summary dict."""
    upload = _make_species_upload(geom)
    data = upload.summary().to_dict()
    blob = json.dumps(data)
    assert _SENTINEL not in blob
