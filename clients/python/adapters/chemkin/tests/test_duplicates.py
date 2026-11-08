"""Import-report warnings for wrong DUPLICATE flags (never a change to the model)."""

from tckdb_chemkin.duplicates import duplicate_flag_warnings
from tckdb_chemkin.identity import IdentityResolver
from tckdb_chemkin.parser import parse_mechanism
from tckdb_chemkin.payloads import ImportConfig, build_all_payloads

# Reactions start on line 9 of the text below.
_HEAD = "ELEMENTS\nH O\nEND\nSPECIES\nH2 O2 H2O2 OH HO2 H O\nEND\n\nREACTIONS\n"


def _warn(body):
    mech = parse_mechanism(_HEAD + body + "END\n")
    return mech, duplicate_flag_warnings(mech.reactions)


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
    assert len(w) == 1 and "line(s) 11 carry no DUPLICATE" in w[0]


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
