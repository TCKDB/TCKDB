"""Import-report warnings for wrong DUPLICATE flags (never a change to the model).

The rule is Cantera 3.2's ``checkDuplicates``; where Cantera is installed each
case is also confirmed against a strict load.
"""

import pytest

from tckdb_chemkin.duplicates import duplicate_flag_warnings
from tckdb_chemkin.identity import IdentityResolver
from tckdb_chemkin.parser import parse_mechanism
from tckdb_chemkin.payloads import ImportConfig, build_all_payloads

# Reactions start on line 9 of the text below.
_HEAD = "ELEMENTS\nH O\nEND\nSPECIES\nH2 O2 H2O2 OH HO2 H O\nEND\n\nREACTIONS\n"
_SPECIES = ("A", "B", "C")


def _warn(body):
    mech = parse_mechanism(_HEAD + body + "END\n")
    return mech, duplicate_flag_warnings(mech.reactions)


def _abc(body):
    text = "ELEMENTS\nH\nEND\nSPECIES\nA B C\nEND\nREACTIONS\n" + body + "END\n"
    mech = parse_mechanism(text)
    return text, duplicate_flag_warnings(mech.reactions)


def _cantera_accepts(body: str) -> bool:
    """Strict Cantera load of ``body`` over three H2 species A, B, C."""
    ck2yaml = pytest.importorskip("cantera.ck2yaml")
    import os
    import tempfile

    from cantera import Solution

    def card(name):
        zero = f"{0.0:15.8E}"
        cp = f"{3.5:15.8E}"
        l1 = name.ljust(24) + "H   2" + " " * 15 + "G" + f"{300.0:>10.3f}{3000.0:>10.3f}{1000.0:>8.2f}"
        l1 = l1.ljust(79) + "1"
        row = cp + zero * 4
        l2 = row.ljust(79) + "2"
        l3 = (zero * 2 + cp + zero * 2).ljust(79) + "3"
        l4 = (zero * 4).ljust(79) + "4"
        return "\n".join((l1, l2, l3, l4))

    thermo = "THERMO ALL\n   300.000  1000.000  3000.000\n" + "\n".join(card(n) for n in _SPECIES) + "\nEND\n"
    text = "ELEMENTS\nH\nEND\nSPECIES\nA B C\nEND\nREACTIONS\n" + body + "END\n"
    with tempfile.TemporaryDirectory() as tmp:
        inp, th, out = (os.path.join(tmp, n) for n in ("c.inp", "t.dat", "o.yaml"))
        open(inp, "w").write(text)
        open(th, "w").write(thermo)
        try:
            ck2yaml.convert(input_file=inp, thermo_file=th, out_name=out, quiet=True, permissive=False)
            Solution(out)
        except Exception:
            return False
    return True


def test_a_declared_pair_is_silent():
    _, w = _warn("H+HO2=H2+O2   1.0 0.0 0.0\nDUPLICATE\nH+HO2=H2+O2   2.0 0.0 0.0\nDUPLICATE\n")
    assert w == []


def test_a_repeat_without_duplicate_is_reported_with_its_lines():
    _, w = _warn("H+HO2=H2+O2   1.0 0.0 0.0\nH+HO2=H2+O2   2.0 0.0 0.0\n")
    assert len(w) == 1
    assert "lines 9, 10" in w[0] and "line(s) 9, 10 carry no DUPLICATE" in w[0]
    assert "Imported as written" in w[0]


def test_one_marked_and_one_unmarked_repeat_names_the_unmarked_line():
    _, w = _warn("H+HO2=H2+O2   1.0 0.0 0.0\nDUPLICATE\nH+HO2=H2+O2   2.0 0.0 0.0\n")
    # Cantera reports both: the lone marker has no marked partner, and the repeat is undeclared.
    assert len(w) == 2
    assert any("line(s) 11 carry no DUPLICATE" in x for x in w)
    assert any("no partner" in x for x in w)


def test_a_marker_with_no_partner_is_reported_with_both_lines():
    _, w = _warn("H+HO2=H2+O2   1.0 0.0 0.0\nDUPLICATE\n")
    assert len(w) == 1
    assert "no partner" in w[0] and "line 9, DUPLICATE on line 10" in w[0]


def test_different_participants_are_not_repeats():
    _, w = _warn("H+HO2=H2+O2   1.0 0.0 0.0\nH+HO2=OH+OH   2.0 0.0 0.0\n")
    assert w == []


def test_reporting_changes_nothing_in_the_model_or_the_payloads():
    mech, w = _warn(
        "H+HO2=H2+O2   1.0 0.0 0.0\nH+HO2=H2+O2   2.0 0.0 0.0\nH+O2=OH+O   1.0 0.0 0.0\nDUPLICATE\n"
    )
    assert len(w) == 2
    assert [r.duplicate for r in mech.reactions] == [False, False, True]
    assert len(mech.reactions) == 3
    built = build_all_payloads(
        mech, IdentityResolver(allow_pseudo=True), ImportConfig(scientific_origin="experimental")
    )
    assert len(built.kinetics) == 3
    assert [x for x in built.warnings if "DUPLICATE" in x] == w


# --- Cantera 3.2's rule, case by case ---------------------------------------

_CHEB = (
    "A (+M) <=> B (+M)   1.0 0.0 0.0\n"
    "    TCHEB / 300.0 2500.0 /\n    PCHEB / 0.1 100.0 /\n"
    "    CHEB / 1 1 1.0 /\n"
)


def test_marked_arrhenius_beside_unmarked_chebyshev_is_an_orphan():
    body = "A <=> B   1.0E10 0.0 0.0\n    DUPLICATE\n" + _CHEB
    _, w = _abc(body)
    assert len(w) == 1 and "no partner" in w[0]
    assert _cantera_accepts(body) is False


def test_marked_arrhenius_beside_marked_chebyshev_is_silent():
    body = "A <=> B   1.0E10 0.0 0.0\n    DUPLICATE\n" + _CHEB + "    DUPLICATE\n"
    _, w = _abc(body)
    assert w == []
    assert _cantera_accepts(body) is True


def test_marked_three_body_beside_unmarked_plain_reaction_is_an_orphan():
    body = "A + M <=> B + M   1.0E10 0.0 0.0\n    DUPLICATE\nA <=> B   1.0E10 0.0 0.0\n"
    _, w = _abc(body)
    assert len(w) == 1 and "no partner" in w[0]
    assert "A + M <=> B + M" in w[0]  # the equation is kept as written
    assert _cantera_accepts(body) is False


def test_reversed_repeat_of_the_same_type_is_undeclared():
    body = "A <=> B   1.0E10 0.0 0.0\nB <=> A   1.0E10 0.0 0.0\n"
    _, w = _abc(body)
    assert len(w) == 1 and "undeclared" in w[0]
    assert _cantera_accepts(body) is False


def test_reversed_repeat_with_both_marked_is_silent():
    body = "A <=> B   1.0E10 0.0 0.0\n    DUPLICATE\nB <=> A   1.0E10 0.0 0.0\n    DUPLICATE\n"
    _, w = _abc(body)
    assert w == []
    assert _cantera_accepts(body) is True


def test_reversed_repeat_of_irreversible_reactions_is_not_a_repeat():
    body = "A => B   1.0E10 0.0 0.0\nB => A   1.0E10 0.0 0.0\n"
    _, w = _abc(body)
    assert w == []
    assert _cantera_accepts(body) is True


def test_a_three_row_cluster_is_one_warning():
    _, w = _abc("A <=> B   1.0 0.0 0.0\nA <=> B   2.0 0.0 0.0\nA <=> B   3.0 0.0 0.0\n")
    assert len(w) == 1 and "3 times" in w[0]
