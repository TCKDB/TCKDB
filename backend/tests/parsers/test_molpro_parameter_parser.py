"""Tests for the Molpro log-file parser.

Validated against 12 real Molpro fixtures under ``tests/fixtures/molpro/``:

* 6 CCSD(T)-F12 / cc-pVTZ-F12 jobs (4 closed-shell ``ccsd(t)-f12`` + 2
  open-shell ``uccsd(t)-f12``), one of which (``c4h6o``) errored out before
  the CCSD(T) energy — a valid "no SP energy" case.
* 6 plain MRCI / cc-pVTZ jobs (Michal-Keslin N2H4 system) whose SP energy is
  the Davidson relaxed-reference cluster-corrected energy.

The parser is DB-free: these tests import and call it directly, no session.
"""

from __future__ import annotations

import os

import pytest

from app.services.molpro_parameter_parser import (
    parse_charge_multiplicity,
    parse_method_basis,
    parse_molpro_log,
    parse_software_version,
    parse_sp_energy,
)

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "..", "fixtures", "molpro")


def _read(name: str) -> str:
    with open(os.path.join(FIXTURES_DIR, name, "input.out")) as f:
        return f.read()


def _find_param(params: list[dict], canonical_key: str) -> dict | None:
    for p in params:
        if p.get("canonical_key") == canonical_key:
            return p
    return None


# ---------------------------------------------------------------------------
# CCSD(T)-F12 — closed shell
# ---------------------------------------------------------------------------


def test_ch4_closed_shell_full_parse() -> None:
    r = parse_molpro_log(text=_read("ch4_closed_shell"))

    assert r["method_family"] == "ccsd_f12"
    assert r["parser_version"] == "molpro_v1"

    mb = r["method_basis"]
    assert mb["method"] == "ccsd(t)-f12"
    assert mb["basis"] == "cc-pvtz-f12"

    cm = r["charge_multiplicity"]
    assert cm == {"charge": 0, "multiplicity": 1}

    assert r["software"] == {
        "name": "molpro",
        "version": "2026.1",
        "build": None,
        "release_date_raw": None,
    }

    # F12a energy (cc-pVTZ-F12 → F12a), Hartree, no conversion.
    assert r["sp_electronic_energy_hartree"] == pytest.approx(-40.457885930635)

    params = r["parameters"]
    mem = _find_param(params, "memory.raw")
    assert mem is not None and mem["raw_value"] == "5250" and mem["unit"] == "MW"
    maxit = _find_param(params, "scf.max_cycles")
    assert maxit is not None and maxit["raw_value"] == "999"
    ansatz = _find_param(params, "f12.ansatz")
    assert ansatz is not None and ansatz["canonical_value"] == "3C(FIX)"


def test_thf_and_ketene_closed_shell_sp_energy() -> None:
    assert parse_sp_energy(_read("thf_ring")) == pytest.approx(-232.167597884334)
    assert parse_sp_energy(_read("ch2co_ketene")) == pytest.approx(
        -152.427692581671
    )
    # Both are closed-shell ccsd(t)-f12, singlet.
    assert parse_method_basis(_read("thf_ring"))["method"] == "ccsd(t)-f12"
    assert parse_charge_multiplicity(_read("thf_ring")) == {
        "charge": 0,
        "multiplicity": 1,
    }


# ---------------------------------------------------------------------------
# CCSD(T)-F12 — open shell (uccsd distinction + F12a from paired ! lines)
# ---------------------------------------------------------------------------


def test_ch3_radical_open_shell() -> None:
    r = parse_molpro_log(text=_read("ch3_radical"))

    # Open-shell method token is the U-variant.
    assert r["method_basis"]["method"] == "uccsd(t)-f12"
    assert r["charge_multiplicity"] == {"charge": 0, "multiplicity": 2}
    # First (F12a) !RHF-UCCSD(T)-F12 energy line, not the later F12b one.
    assert r["sp_electronic_energy_hartree"] == pytest.approx(-39.778648884742)


def test_hcco_radical_open_shell() -> None:
    r = parse_molpro_log(text=_read("hcco_radical"))
    assert r["method_basis"]["method"] == "uccsd(t)-f12"
    assert r["charge_multiplicity"] == {"charge": 0, "multiplicity": 2}
    assert r["sp_electronic_energy_hartree"] == pytest.approx(-151.744954252794)


def test_failed_job_has_no_sp_energy_but_still_parses_params() -> None:
    # c4h6o ran out of memory before the CCSD(T) energy (GLOBAL ERROR).
    r = parse_molpro_log(text=_read("c4h6o"))
    assert r["sp_electronic_energy_hartree"] is None
    # Deck-derived parameters and identity are still recovered.
    assert r["method_basis"]["method"] == "ccsd(t)-f12"
    assert r["charge_multiplicity"] == {"charge": 0, "multiplicity": 1}
    assert _find_param(r["parameters"], "scf.max_cycles")["raw_value"] == "999"


# ---------------------------------------------------------------------------
# MRCI — Davidson relaxed reference, positional wf, charge derivation
# ---------------------------------------------------------------------------


def test_mrci_hydrazine_closed_shell() -> None:
    r = parse_molpro_log(text=_read("mrci/hydrazine_closed"))

    assert r["method_family"] == "mrci"
    mb = r["method_basis"]
    assert mb["method"] == "mrci"
    assert mb["basis"] == "cc-pvtz"
    # Reference chain captured from the {rhf} → {casscf} → {mrci} blocks.
    assert mb["reference_methods"] == ["rhf", "casscf"]

    # Positional wf (18,1,0): charge = sum(Z=18) - nelec(18) = 0, spin 0.
    assert r["charge_multiplicity"] == {"charge": 0, "multiplicity": 1}
    assert r["software"]["version"] == "2022.3"

    # Davidson relaxed-reference energy, Hartree.
    assert r["sp_electronic_energy_hartree"] == pytest.approx(-111.69430030)

    # No F12 ansatz parameter for a non-F12 job.
    assert _find_param(r["parameters"], "f12.ansatz") is None
    assert _find_param(r["parameters"], "scf.max_cycles")["raw_value"] == "1000"
    assert _find_param(r["parameters"], "memory.raw")["raw_value"] == "752"


def test_mrci_n2h2c_closed_sp_energy() -> None:
    r = parse_molpro_log(text=_read("mrci/n2h2c_closed"))
    assert r["charge_multiplicity"] == {"charge": 0, "multiplicity": 1}
    assert r["sp_electronic_energy_hartree"] == pytest.approx(-110.46853747)


def test_mrci_ts2_sp_energy() -> None:
    assert parse_sp_energy(_read("mrci/ts2")) == pytest.approx(-111.56477637)


def test_mrci_n2h3_open_shell() -> None:
    r = parse_molpro_log(text=_read("mrci/n2h3_radical"))
    # Positional wf (17,1,1): charge 0, spin 1 → doublet.
    assert r["charge_multiplicity"] == {"charge": 0, "multiplicity": 2}
    assert r["sp_electronic_energy_hartree"] == pytest.approx(-111.05606887)


def test_mrci_nh2_open_shell() -> None:
    r = parse_molpro_log(text=_read("mrci/nh2_radical"))
    # wf,9,2,1 → NH2 (Z sum 9), charge 0, spin 1 → doublet.
    assert r["charge_multiplicity"] == {"charge": 0, "multiplicity": 2}
    assert r["sp_electronic_energy_hartree"] == pytest.approx(-55.79346542)


def test_mrci_h_atom_edge_case() -> None:
    r = parse_molpro_log(text=_read("mrci/h_atom"))
    # Single atom, wf,1,1,1 → charge 0, doublet.
    assert r["charge_multiplicity"] == {"charge": 0, "multiplicity": 2}
    assert r["sp_electronic_energy_hartree"] == pytest.approx(-0.49980981)


# ---------------------------------------------------------------------------
# wf-syntax handling and software version
# ---------------------------------------------------------------------------


def test_wf_keyword_vs_positional_forms() -> None:
    # Keyword form: wf,spin=1,charge=0 (CCSD deck)
    assert parse_charge_multiplicity(_read("ch3_radical")) == {
        "charge": 0,
        "multiplicity": 2,
    }
    # Positional form: wf,17,1,1 (MRCI deck)
    assert parse_charge_multiplicity(_read("mrci/n2h3_radical")) == {
        "charge": 0,
        "multiplicity": 2,
    }


def test_software_version_parses_both_release_lines() -> None:
    assert parse_software_version(_read("ch4_closed_shell"))["version"] == "2026.1"
    assert (
        parse_software_version(_read("mrci/hydrazine_closed"))["version"]
        == "2022.3"
    )


# ---------------------------------------------------------------------------
# MRCI-F12 deferral guard (synthetic — no real fixture in scope)
# ---------------------------------------------------------------------------


def test_mrci_f12_variant_is_not_misreported() -> None:
    """A deferred MRCI-F12 deck must not grab a wrong energy.

    No MRCI-F12 fixture is in scope this pass; this synthetic deck confirms
    the family is detected and the SP energy is reported as ``None`` (rather
    than falling through to a plain-MRCI or F12a/F12b line).
    """
    synthetic = (
        " Variables initialized (1), CPU time= 0.00 sec\n"
        " ***,dummy\n"
        " memory,752,m;\n"
        " basis=aug-cc-pvtz-f12\n"
        " {rhf; wf,18,1,0}\n"
        " {mrci-f12; wf,18,1,0}\n"
        " Commands initialized (1), CPU time= 0.00 sec\n"
        " Cluster corrected energies  -111.11111111 (Davidson, relaxed reference)\n"
    )
    r = parse_molpro_log(text=synthetic)
    assert r["method_family"] == "mrci_f12"
    assert r["sp_electronic_energy_hartree"] is None


# ---------------------------------------------------------------------------
# Divergence guards vs ARC (wrong-but-plausible energies)
# ---------------------------------------------------------------------------


def test_spurious_vqz_in_body_does_not_flip_ansatz_to_f12b() -> None:
    """A stray ``vqz`` token in the output body must not select F12b.

    F12a/F12b is chosen from the deck's ``basis=`` directive only (as ARC
    does).  A cc-pVTZ-F12 job whose body mentions ``vqz`` anywhere (e.g. a
    ``gprint,basis`` library echo) must still return the F12a energy.
    """
    text = _read("ch4_closed_shell")
    poisoned = text.replace(
        "Checking input...",
        "Checking input...\n Library entry C  aug-cc-pVQZ echo (vqz) ignored",
        1,
    )
    assert "vqz" in poisoned.lower()
    r = parse_molpro_log(text=poisoned)
    # Still F12a — not the later F12b value (-40.454357142403).
    assert r["sp_electronic_energy_hartree"] == pytest.approx(-40.457885930635)


def test_mrci_in_title_does_not_poison_ccsd_family() -> None:
    """A title containing ``mrci`` must not misclassify a CCSD(T)-F12 job.

    Comment/title lines (``***,...``) are excluded from family detection,
    so a genuine CCSD(T)-F12 job titled ``... vs mrci benchmark`` stays
    ``ccsd_f12`` and keeps its F12a energy (rather than silently → None).
    """
    text = _read("ch4_closed_shell")
    poisoned = text.replace("***,CH4[23]", "***,CH4 vs mrci benchmark", 1)
    r = parse_molpro_log(text=poisoned)
    assert r["method_family"] == "ccsd_f12"
    assert r["sp_electronic_energy_hartree"] == pytest.approx(-40.457885930635)


# ---------------------------------------------------------------------------
# ESSSPResult wrapper (ess_result.parse_molpro_sp)
# ---------------------------------------------------------------------------


def test_parse_molpro_sp_returns_ess_sp_result() -> None:
    from app.services.ess_result import ESSSPResult, parse_molpro_sp

    sp = parse_molpro_sp(_read("ch4_closed_shell"))
    assert isinstance(sp, ESSSPResult)
    assert sp.electronic_energy_hartree == pytest.approx(-40.457885930635)

    mrci_sp = parse_molpro_sp(_read("mrci/hydrazine_closed"))
    assert mrci_sp.electronic_energy_hartree == pytest.approx(-111.69430030)

    # Failed/truncated job → no SP result.
    assert parse_molpro_sp(_read("c4h6o")) is None

