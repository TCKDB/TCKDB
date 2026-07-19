"""Serializer-level CHEMKIN kinetics-block tests.

These build an :class:`ExportRecordSet` directly from lightweight stand-ins for
the ORM rows (no DB fixture) and drive ``serialize_chemkin`` +
``validate_chemkin_mechanism``. They pin down two correctness bugs that the
full parser->parser round trip could not surface (Cantera silently accepts a
``k≡0`` line, and the old lenient re-parser round-tripped a mis-shaped
third-body equation):

* **multi_arrhenius** (sum-of-Arrhenius / Chemkin ``DUPLICATE`` channel): the
  scalar ``a/n/ea`` are NULL by design, so the record must expand to one
  Arrhenius line *per term*, each marked ``DUPLICATE`` — never a single
  ``0.0`` line that drops every term.
* **simple third-body** (``is_third_body`` with no falloff): must be written
  with a BARE ``+ M`` on both sides (order+1), distinct from the falloff
  ``(+M)`` paren form, and may legally carry collider efficiencies.

Regression stand-ins for Troe falloff, PLOG, and Chebyshev assert the
single-block forms still serialize byte-for-byte as before and still load in
Cantera.
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.db.models.common import (
    ArrheniusAUnits,
    KineticsModelKind,
    RecordReviewStatus,
)
from app.services.scientific_read.chemkin_serialize import (
    ChemkinOptions,
    _assign_names,
    _composition,
    serialize_chemkin,
    validate_chemkin_mechanism,
)
from app.services.scientific_read.export import (
    CollapseMode,
    ExportRecordSet,
    ReactionExportRecord,
    SeedSelection,
    SelectedKinetics,
    SelectionPolicy,
    SpeciesExportRecord,
)

# ---------------------------------------------------------------------------
# Lightweight ORM stand-ins (attribute-compatible with what the serializer
# reads). Kept minimal on purpose: a full DB fixture would obscure the exact
# emitted-line assertions these tests are about.
# ---------------------------------------------------------------------------


def _nasa():
    """A trivially-valid constant-Cp NASA-7 (a=LOW, b=HIGH per TCKDB convention)."""
    c = [3.5, 0.0, 0.0, 0.0, 0.0, -1000.0, 5.0]
    return SimpleNamespace(
        t_low=200.0,
        t_high=3500.0,
        t_mid=1000.0,
        a1=c[0], a2=c[1], a3=c[2], a4=c[3], a5=c[4], a6=c[5], a7=c[6],
        b1=c[0], b2=c[1], b3=c[2], b4=c[3], b5=c[4], b6=c[5], b7=c[6],
    )


def _species(sid: int, smiles: str, ref: str) -> SpeciesExportRecord:
    sp = SimpleNamespace(id=sid, smiles=smiles, public_ref=ref)
    se = SimpleNamespace(id=sid, public_ref=f"{ref}-e")
    return SpeciesExportRecord(
        species_entry=se,
        species=sp,
        is_linear=None,
        thermos=[SimpleNamespace(model_kind="nasa", nasa=_nasa())],
        transports=[],
    )


def _kinetics(**kw) -> SimpleNamespace:
    base = {
        "model_kind": None,
        "is_third_body": False,
        "third_body_efficiencies": [],
        "arrhenius_entries": [],
        "a": None,
        "a_units": None,
        "n": None,
        "ea_kj_mol": None,
        "falloff": None,
        "plog_entries": [],
        "chebyshev": None,
        "public_ref": "k",
        "scientific_origin": SimpleNamespace(value="experimental"),
        "tmin_k": None,
        "tmax_k": None,
        "degeneracy": None,
    }
    base.update(kw)
    return SimpleNamespace(**base)


def _arr_entry(idx: int, a: float, n: float, ea_kj_mol: float) -> SimpleNamespace:
    return SimpleNamespace(
        entry_index=idx,
        a=a,
        a_units=ArrheniusAUnits.cm3_mol_s,
        n=n,
        ea_kj_mol=ea_kj_mol,
    )


def _reaction(reactant_refs, product_refs, k, *, ref="R", reversible=True):
    return ReactionExportRecord(
        reaction_entry=SimpleNamespace(public_ref=f"{ref}-e"),
        reaction=SimpleNamespace(reversible=reversible, public_ref=ref),
        reaction_family=None,
        reactant_refs=list(reactant_refs),
        product_refs=list(product_refs),
        kinetics=[
            SelectedKinetics(kinetics=k, review_status=RecordReviewStatus.approved)
        ],
    )


def _record_set(species_records, reaction_records) -> ExportRecordSet:
    return ExportRecordSet(
        seed=SeedSelection(species_refs=[], reaction_refs=[]),
        min_review_status=None,
        collapse=CollapseMode.first,
        selection_policy=SelectionPolicy.latest,
        generated_at=datetime.now(timezone.utc),
        species_records=list(species_records),
        reaction_records=list(reaction_records),
    )


def _serialize(species_records, reaction_records):
    """Serialize with formula naming + matching collider names, no transport."""
    rs = _record_set(species_records, reaction_records)
    comps = {
        sr.species_entry.id: _composition(sr.species.smiles)
        for sr in rs.species_records
    }
    names = _assign_names(rs.species_records, naming_policy="formula", compositions=comps)
    collider_names = {
        sr.species.id: names[sr.species_entry.id] for sr in rs.species_records
    }
    export = serialize_chemkin(
        rs,
        options=ChemkinOptions(energy_units="cal/mol", include_transport=False),
        collider_names=collider_names,
    )
    return export, names


def _reaction_lines(chem_inp: str) -> list[str]:
    """Reaction body lines (between the REACTIONS header and END)."""
    lines = chem_inp.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("REACTIONS"))
    end = next(i for i, ln in enumerate(lines) if ln.strip() == "END" and i > start)
    return lines[start + 1 : end]


def _forward_rate_sum(export_files, equation_species, *, temperature=1000.0):
    """Load the mechanism in Cantera and sum forward rate constants over the
    reactions whose reactants match ``equation_species`` (a set of names)."""
    ct = pytest.importorskip("cantera")
    from cantera import ck2yaml

    with tempfile.TemporaryDirectory() as tmp:
        for name, content in export_files.items():
            with open(os.path.join(tmp, name), "w") as fh:
                fh.write(content)
        out = os.path.join(tmp, "mech.yaml")
        ck2yaml.convert(
            input_file=os.path.join(tmp, "chem.inp"),
            thermo_file=os.path.join(tmp, "therm.dat"),
            out_name=out,
            quiet=True,
            permissive=False,
        )
        gas = ct.Solution(out)
        gas.TP = temperature, ct.one_atm
        total = 0.0
        for i, rxn in enumerate(gas.reactions()):
            if set(rxn.reactants) == set(equation_species):
                total += gas.forward_rate_constants[i]
        return gas, total


# ---------------------------------------------------------------------------
# Bug A — multi_arrhenius
# ---------------------------------------------------------------------------


def test_multi_arrhenius_emits_one_duplicate_arrhenius_line_per_term():
    """A multi_arrhenius record must expand to one Arrhenius line per term (each
    DUPLICATE-marked), NOT a single ``k≡0`` line dropping every term."""
    H = _species(1, "[H]", "H")
    O2 = _species(2, "[O][O]", "O2")
    HO2 = _species(3, "[O]O", "HO2")
    k = _kinetics(
        model_kind=KineticsModelKind.multi_arrhenius,
        arrhenius_entries=[
            _arr_entry(1, 1.0e12, 0.0, 0.0),
            _arr_entry(2, 5.0e11, 0.0, 0.0),
        ],
    )
    rxn = _reaction(["H-e", "O2-e"], ["HO2-e"], k, ref="R1")
    export, _ = _serialize([H, O2, HO2], [rxn])

    body = _reaction_lines(export.files["chem.inp"])
    eq = "H1 + O2 <=> H1O2"
    main_lines = [ln for ln in body if ln.startswith(eq)]
    dup_lines = [ln for ln in body if ln.strip() == "DUPLICATE"]

    # Exactly the two terms, each its own DUPLICATE-marked line.
    assert len(main_lines) == 2
    assert len(dup_lines) == 2
    assert any("1.0000E+12" in ln for ln in main_lines)
    assert any("5.0000E+11" in ln for ln in main_lines)
    # The old bug wrote a single identically-zero rate; assert it is gone.
    assert "0.0 0.000 0.0000" not in export.files["chem.inp"]
    for ln in main_lines:
        assert not ln.strip().endswith("0.0 0.000 0.0000")


def test_multi_arrhenius_cantera_summed_rate_is_sum_of_terms():
    """Cantera loads the expanded mechanism and its net forward rate equals the
    (non-zero) sum of the two Arrhenius terms."""
    pytest.importorskip("cantera")
    H = _species(1, "[H]", "H")
    O2 = _species(2, "[O][O]", "O2")
    HO2 = _species(3, "[O]O", "HO2")
    k = _kinetics(
        model_kind=KineticsModelKind.multi_arrhenius,
        arrhenius_entries=[
            _arr_entry(1, 1.0e12, 0.0, 0.0),
            _arr_entry(2, 5.0e11, 0.0, 0.0),
        ],
    )
    rxn = _reaction(["H-e", "O2-e"], ["HO2-e"], k, ref="R1")
    export, _ = _serialize([H, O2, HO2], [rxn])

    validate_chemkin_mechanism(export.files)  # ground-truth Cantera load

    gas, total = _forward_rate_sum(export.files, {"H1", "O2"})
    assert gas.n_reactions == 2  # both terms survive as a duplicate pair
    # cm3/mol/s -> m3/kmol/s is a factor 1e-3 for a bimolecular rate.
    expected = (1.0e12 + 5.0e11) * 1.0e-3
    assert total == pytest.approx(expected, rel=1e-9)
    assert total > 0.0


def test_multi_arrhenius_collision_with_separate_record_marks_all():
    """A multi_arrhenius (2 terms) colliding with a separate plain-Arrhenius
    record of the same equation yields 3 duplicate lines, all marked — the
    per-term expansion folds into the same duplicate tally the assembly uses."""
    H = _species(1, "[H]", "H")
    O2 = _species(2, "[O][O]", "O2")
    HO2 = _species(3, "[O]O", "HO2")
    k_multi = _kinetics(
        model_kind=KineticsModelKind.multi_arrhenius,
        arrhenius_entries=[
            _arr_entry(1, 1.0e12, 0.0, 0.0),
            _arr_entry(2, 5.0e11, 0.0, 0.0),
        ],
    )
    k_plain = _kinetics(
        model_kind=KineticsModelKind.modified_arrhenius,
        a=3.0e11,
        a_units=ArrheniusAUnits.cm3_mol_s,
        n=0.0,
        ea_kj_mol=0.0,
    )
    rxn_multi = _reaction(["H-e", "O2-e"], ["HO2-e"], k_multi, ref="R1")
    rxn_plain = _reaction(["H-e", "O2-e"], ["HO2-e"], k_plain, ref="R2")
    export, _ = _serialize([H, O2, HO2], [rxn_multi, rxn_plain])

    body = _reaction_lines(export.files["chem.inp"])
    eq = "H1 + O2 <=> H1O2"
    assert len([ln for ln in body if ln.startswith(eq)]) == 3
    assert len([ln for ln in body if ln.strip() == "DUPLICATE"]) == 3
    validate_chemkin_mechanism(export.files)


# ---------------------------------------------------------------------------
# Bug B — simple third-body
# ---------------------------------------------------------------------------


def _third_body_species():
    return [
        _species(1, "[H]", "H"),
        _species(2, "[O][O]", "O2"),
        _species(3, "[O]O", "HO2"),
        _species(4, "O", "H2O"),
        _species(5, "[Ar]", "Ar"),
    ]


def test_simple_third_body_uses_bare_plus_M_with_efficiencies():
    """``is_third_body`` (no falloff) -> bare ``+ M`` on both sides (not the
    ``(+M)`` paren form) and the efficiency block is emitted."""
    sp = _third_body_species()
    k = _kinetics(
        model_kind=KineticsModelKind.modified_arrhenius,
        is_third_body=True,
        a=1.0e18,
        a_units=ArrheniusAUnits.cm6_mol2_s,
        n=-1.0,
        ea_kj_mol=0.0,
        third_body_efficiencies=[
            SimpleNamespace(collider_species_id=4, efficiency=6.0),
            SimpleNamespace(collider_species_id=5, efficiency=0.7),
        ],
    )
    rxn = _reaction(["H-e", "O2-e"], ["HO2-e"], k, ref="R1")
    export, _ = _serialize(sp, [rxn])
    body = _reaction_lines(export.files["chem.inp"])

    main = next(ln for ln in body if "<=>" in ln)
    assert main.startswith("H1 + O2 + M <=> H1O2 + M")
    assert "(+M)" not in main  # bare +M, not the falloff paren form
    # efficiency block present with both colliders.
    assert any("H2O1/6/" in ln and "Ar1/0.7/" in ln for ln in body)

    validate_chemkin_mechanism(export.files)
    ct = pytest.importorskip("cantera")

    with tempfile.TemporaryDirectory() as tmp:
        for name, content in export.files.items():
            with open(os.path.join(tmp, name), "w") as fh:
                fh.write(content)
        from cantera import ck2yaml

        out = os.path.join(tmp, "mech.yaml")
        ck2yaml.convert(
            input_file=os.path.join(tmp, "chem.inp"),
            thermo_file=os.path.join(tmp, "therm.dat"),
            out_name=out,
            quiet=True,
            permissive=False,
        )
        gas = ct.Solution(out)
        r0 = gas.reactions()[0]
        assert "three-body" in r0.reaction_type
        assert r0.third_body is not None
        assert r0.third_body.efficiencies == pytest.approx({"H2O1": 6.0, "Ar1": 0.7})


def test_simple_third_body_without_efficiencies_still_bare_M_and_loads():
    """A bare-``+M`` third-body reaction with no explicit efficiencies still
    round-trips as a three-body reaction (all colliders default 1)."""
    sp = _third_body_species()
    k = _kinetics(
        model_kind=KineticsModelKind.modified_arrhenius,
        is_third_body=True,
        a=1.0e18,
        a_units=ArrheniusAUnits.cm6_mol2_s,
        n=-1.0,
        ea_kj_mol=0.0,
    )
    rxn = _reaction(["H-e", "O2-e"], ["HO2-e"], k, ref="R1")
    export, _ = _serialize(sp, [rxn])
    body = _reaction_lines(export.files["chem.inp"])

    main = next(ln for ln in body if "<=>" in ln)
    assert main.startswith("H1 + O2 + M <=> H1O2 + M")
    assert "(+M)" not in main
    # No efficiency line for this variant.
    assert not any("/" in ln and "<=>" not in ln for ln in body)

    validate_chemkin_mechanism(export.files)
    ct = pytest.importorskip("cantera")
    with tempfile.TemporaryDirectory() as tmp:
        for name, content in export.files.items():
            with open(os.path.join(tmp, name), "w") as fh:
                fh.write(content)
        from cantera import ck2yaml

        out = os.path.join(tmp, "mech.yaml")
        ck2yaml.convert(
            input_file=os.path.join(tmp, "chem.inp"),
            thermo_file=os.path.join(tmp, "therm.dat"),
            out_name=out,
            quiet=True,
            permissive=False,
        )
        gas = ct.Solution(out)
        assert "three-body" in gas.reactions()[0].reaction_type


# ---------------------------------------------------------------------------
# Bug A x Bug B — multi_arrhenius that is ALSO a third-body reaction. The
# efficiency line must ride EVERY per-term block, or the deposited colliders
# silently vanish (Cantera loads it as three-body with default =1.0 effs).
# ---------------------------------------------------------------------------


def test_multi_arrhenius_third_body_efficiencies_on_every_term():
    sp = _third_body_species()
    k = _kinetics(
        model_kind=KineticsModelKind.multi_arrhenius,
        is_third_body=True,
        arrhenius_entries=[
            _arr_entry(1, 1.0e18, -1.0, 0.0),
            _arr_entry(2, 5.0e17, -1.0, 0.0),
        ],
        third_body_efficiencies=[
            SimpleNamespace(collider_species_id=4, efficiency=6.0),
            SimpleNamespace(collider_species_id=5, efficiency=0.7),
        ],
    )
    rxn = _reaction(["H-e", "O2-e"], ["HO2-e"], k, ref="R1")
    export, _ = _serialize(sp, [rxn])
    body = _reaction_lines(export.files["chem.inp"])

    main_lines = [ln for ln in body if "<=>" in ln]
    eff_lines = [ln for ln in body if "H2O1/6/" in ln and "Ar1/0.7/" in ln]
    dup_lines = [ln for ln in body if ln.strip() == "DUPLICATE"]
    # Each of the two terms: bare +M main line + its own efficiency line + DUP.
    assert len(main_lines) == 2
    assert all(m.startswith("H1 + O2 + M <=> H1O2 + M") for m in main_lines)
    assert all("(+M)" not in m for m in main_lines)
    assert len(eff_lines) == 2
    assert len(dup_lines) == 2

    validate_chemkin_mechanism(export.files)
    ct = pytest.importorskip("cantera")
    with tempfile.TemporaryDirectory() as tmp:
        for name, content in export.files.items():
            with open(os.path.join(tmp, name), "w") as fh:
                fh.write(content)
        from cantera import ck2yaml

        out = os.path.join(tmp, "mech.yaml")
        ck2yaml.convert(
            input_file=os.path.join(tmp, "chem.inp"),
            thermo_file=os.path.join(tmp, "therm.dat"),
            out_name=out,
            quiet=True,
            permissive=False,
        )
        gas = ct.Solution(out)
        assert gas.n_reactions == 2  # duplicate pair
        for rxn_obj in gas.reactions():
            assert "three-body" in rxn_obj.reaction_type
            assert rxn_obj.third_body is not None
            # The deposited colliders survive — NOT an empty default dict.
            assert rxn_obj.third_body.efficiencies == pytest.approx(
                {"H2O1": 6.0, "Ar1": 0.7}
            )


def test_multi_arrhenius_with_no_terms_records_gap_not_silent_drop():
    """A degenerate multi_arrhenius (0 entries, only reachable as a raw DB row —
    the upload validator requires >=2) degrades to an ExportGap instead of being
    silently omitted from chem.inp."""
    H = _species(1, "[H]", "H")
    O2 = _species(2, "[O][O]", "O2")
    HO2 = _species(3, "[O]O", "HO2")
    k = _kinetics(model_kind=KineticsModelKind.multi_arrhenius, arrhenius_entries=[])
    rxn = _reaction(["H-e", "O2-e"], ["HO2-e"], k, ref="R1")
    export, _ = _serialize([H, O2, HO2], [rxn])

    assert _reaction_lines(export.files["chem.inp"]) == []
    gap = next(
        g for g in export.gaps if g.kind == "kinetics" and g.ref == "R1-e"
    )
    assert "no Arrhenius terms" in gap.detail


# ---------------------------------------------------------------------------
# Regression — single-block forms must serialize identically and load.
# ---------------------------------------------------------------------------


def test_regression_scalar_and_troe_falloff_lines_unchanged():
    sp = _third_body_species()
    falloff = SimpleNamespace(
        low_a=1.0e20,
        low_a_units=ArrheniusAUnits.cm6_mol2_s,
        low_n=-1.5,
        low_ea_kj_mol=2.0,
        troe_alpha=0.5,
        troe_t3=100.0,
        troe_t1=1000.0,
        troe_t2=2000.0,
        sri_a=None, sri_b=None, sri_c=None, sri_d=None, sri_e=None,
    )
    k = _kinetics(
        model_kind=KineticsModelKind.troe,
        a=1.0e13,
        a_units=ArrheniusAUnits.cm3_mol_s,
        n=0.0,
        ea_kj_mol=0.0,
        falloff=falloff,
        third_body_efficiencies=[
            SimpleNamespace(collider_species_id=4, efficiency=6.0),
        ],
    )
    rxn = _reaction(["H-e", "O2-e"], ["HO2-e"], k, ref="R1")
    export, _ = _serialize(sp, [rxn])
    body = _reaction_lines(export.files["chem.inp"])

    assert body[0].startswith("H1 + O2 (+M) <=> H1O2 (+M)")
    assert any(ln.startswith("    LOW /") for ln in body)
    assert any(ln.startswith("    TROE / 0.5 100 1000 2000 /") for ln in body)
    assert any("H2O1/6/" in ln for ln in body)
    # No DUPLICATE for a lone reaction.
    assert not any(ln.strip() == "DUPLICATE" for ln in body)
    validate_chemkin_mechanism(export.files)


def test_regression_plog_lines_unchanged():
    H = _species(1, "[H]", "H")
    O2 = _species(2, "[O][O]", "O2")
    HO2 = _species(3, "[O]O", "HO2")
    k = _kinetics(
        model_kind=KineticsModelKind.plog,
        a=1.0e13,
        a_units=ArrheniusAUnits.cm3_mol_s,
        n=0.0,
        ea_kj_mol=0.0,
        plog_entries=[
            SimpleNamespace(
                entry_index=1, pressure_bar=1.01325, a=1.0e12,
                a_units=ArrheniusAUnits.cm3_mol_s, n=0.0, ea_kj_mol=0.0,
            ),
            SimpleNamespace(
                entry_index=2, pressure_bar=10.1325, a=2.0e12,
                a_units=ArrheniusAUnits.cm3_mol_s, n=0.0, ea_kj_mol=0.0,
            ),
        ],
    )
    rxn = _reaction(["H-e", "O2-e"], ["HO2-e"], k, ref="R1")
    export, _ = _serialize([H, O2, HO2], [rxn])
    body = _reaction_lines(export.files["chem.inp"])

    plog = [ln for ln in body if ln.startswith("    PLOG /")]
    assert len(plog) == 2
    assert not any(ln.strip() == "DUPLICATE" for ln in body)
    validate_chemkin_mechanism(export.files)


def test_regression_chebyshev_lines_unchanged():
    H = _species(1, "[H]", "H")
    O2 = _species(2, "[O][O]", "O2")
    HO2 = _species(3, "[O]O", "HO2")
    cheb = SimpleNamespace(
        n_temperature=2,
        n_pressure=2,
        tmin_k=300.0,
        tmax_k=2000.0,
        pmin_bar=0.1,
        pmax_bar=100.0,
        coefficients=[[8.0, 0.1], [0.2, 0.01]],
    )
    k = _kinetics(model_kind=KineticsModelKind.chebyshev, chebyshev=cheb)
    rxn = _reaction(["H-e", "O2-e"], ["HO2-e"], k, ref="R1")
    export, _ = _serialize([H, O2, HO2], [rxn])
    body = _reaction_lines(export.files["chem.inp"])

    # Placeholder main line + CHEB block, (+M) paren form, no bogus rate.
    assert body[0].startswith("H1 + O2 (+M) <=> H1O2 (+M)   1.0000E+00 0.000 0.0000")
    assert any(ln.startswith("    TCHEB /") for ln in body)
    assert any(ln.startswith("    PCHEB /") for ln in body)
    assert any(ln.startswith("    CHEB / 2 2 ") for ln in body)
    validate_chemkin_mechanism(export.files)
