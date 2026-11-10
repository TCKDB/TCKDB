"""The adapter's DUPLICATE detector agrees with a strict Cantera 3.2 load.

The adapter tests (``clients/python/adapters/chemkin/tests/test_duplicates.py``) skip their
Cantera half where Cantera is absent. This module runs in the backend environment, which has
Cantera 3.2, and checks the same kind of mechanism both ways: the detector reports a problem
exactly when Cantera refuses the file. Also here: an isotopologue exports and re-imports as
itself, not merged with the plain species.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from rdkit import Chem

from tests._ci_dependency import require_module

# Same adapter wiring as test_chemkin_round_trip_real.py (see the note there).
_CHEMKIN_ADAPTER = (
    Path(__file__).resolve().parents[3] / "clients" / "python" / "adapters" / "chemkin"
)
if _CHEMKIN_ADAPTER.is_dir() and str(_CHEMKIN_ADAPTER) not in sys.path:
    sys.path.append(str(_CHEMKIN_ADAPTER))

from tckdb_chemkin.duplicates import duplicate_flag_warnings
from tckdb_chemkin.identity import IdentityResolver
from tckdb_chemkin.parser import parse_mechanism

from app.services.scientific_read.chemkin_serialize import validate_chemkin_mechanism
from tests.services.scientific_read.test_chemkin_serialize_kinetics import _serialize, _species

require_module("cantera", install="conda install -c conda-forge cantera=3.2.0 (backend/environment.yml)")

_NAMES = ("A", "B", "C")  # three H2 species, so every equation below balances


def _card(name: str) -> str:
    zero, cp = f"{0.0:15.8E}", f"{3.5:15.8E}"
    l1 = name.ljust(24) + "H   2" + " " * 15 + "G" + f"{300.0:>10.3f}{3000.0:>10.3f}{1000.0:>8.2f}"
    return "\n".join(
        (
            l1.ljust(79) + "1",
            (cp + zero * 4).ljust(79) + "2",
            (zero * 2 + cp + zero * 2).ljust(79) + "3",
            (zero * 4).ljust(79) + "4",
        )
    )


_THERMO = "THERMO ALL\n   300.000  1000.000  3000.000\n" + "\n".join(_card(n) for n in _NAMES) + "\nEND\n"
_CHEB = (
    "A (+M) <=> B (+M)   1.0 0.0 0.0\n"
    "    TCHEB / 300.0 2500.0 /\n    PCHEB / 0.1 100.0 /\n"
    "    CHEB / 1 1 1.0 /\n"
)

# name -> mechanism body
_CASES = {
    "declared pair": "A <=> B   1.0E10 0.0 0.0\n DUPLICATE\nA <=> B   2.0E10 0.0 0.0\n DUPLICATE\n",
    "undeclared pair": "A <=> B   1.0E10 0.0 0.0\nA <=> B   2.0E10 0.0 0.0\n",
    "one marked, one not": "A <=> B   1.0E10 0.0 0.0\n DUPLICATE\nA <=> B   2.0E10 0.0 0.0\n",
    "lone marker": "A <=> B   1.0E10 0.0 0.0\n DUPLICATE\n",
    "different participants": "A <=> B   1.0E10 0.0 0.0\nA <=> C   1.0E10 0.0 0.0\n",
    "marked arrhenius, unmarked chebyshev": "A <=> B   1.0E10 0.0 0.0\n DUPLICATE\n" + _CHEB,
    "marked arrhenius, marked chebyshev": "A <=> B   1.0E10 0.0 0.0\n DUPLICATE\n" + _CHEB + " DUPLICATE\n",
    "unmarked arrhenius, unmarked chebyshev": "A <=> B   1.0E10 0.0 0.0\n" + _CHEB,
    "marked three-body, unmarked plain": "A + M <=> B + M   1.0E10 0.0 0.0\n DUPLICATE\nA <=> B   1.0E10 0.0 0.0\n",
    "reversed, unmarked": "A <=> B   1.0E10 0.0 0.0\nB <=> A   1.0E10 0.0 0.0\n",
    "reversed, both marked": "A <=> B   1.0E10 0.0 0.0\n DUPLICATE\nB <=> A   1.0E10 0.0 0.0\n DUPLICATE\n",
    "reversed, irreversible": "A => B   1.0E10 0.0 0.0\nB => A   1.0E10 0.0 0.0\n",
    "reversed, one reversible": "A => B   1.0E10 0.0 0.0\nB <=> A   1.0E10 0.0 0.0\n",
    "stoichiometric multiple, unmarked": "A <=> B   1.0E10 0.0 0.0\n2A <=> 2B   1.0E10 0.0 0.0\n",
    "stoichiometric multiple, marked": "A <=> B   1.0E10 0.0 0.0\n DUPLICATE\n2A <=> 2B   1.0E10 0.0 0.0\n DUPLICATE\n",
    "2C written as C + C, one marked": "A + B <=> 2C   1.0E10 0.0 0.0\nA + B <=> C + C   1.0E10 0.0 0.0\n DUPLICATE\n",
}


def _text(body: str) -> str:
    return "ELEMENTS\nH\nEND\nSPECIES\nA B C\nEND\nREACTIONS\n" + body + "END\n"


def _cantera_refuses(body: str) -> bool:
    try:
        validate_chemkin_mechanism({"chem.inp": _text(body), "therm.dat": _THERMO})
    except ValueError:
        return True
    return False


@pytest.mark.parametrize("case", sorted(_CASES))
def test_detector_agrees_with_cantera(case):
    body = _CASES[case]
    warnings = duplicate_flag_warnings(parse_mechanism(_text(body)).reactions)
    assert bool(warnings) == _cantera_refuses(body), (case, warnings)


def test_one_defect_is_one_warning():
    """A marker beside an unmarked repeat is one undeclared repeat, not also an orphan."""
    warnings = duplicate_flag_warnings(parse_mechanism(_text(_CASES["one marked, one not"])).reactions)
    assert len(warnings) == 1 and "undeclared" in warnings[0]


# --- an isotopologue exports and re-imports as itself ----------------------------


def test_an_isotopologue_exports_and_reimports_as_itself_not_the_plain_species():
    plain = _species(1, "C", "S1")
    deuterated = _species(2, "C", "S2")  # the species SMILES is isotope-blind
    deuterated.species_entry.isotope_key = "[2H]C"
    export, names = _serialize([plain, deuterated], [])

    comments = [ln.split("!", 1)[1] for ln in export.files["chem.inp"].splitlines() if "SMILES=" in ln]
    assert comments[0].split()[0] == "SMILES=C"  # non-isotopic species unchanged
    assert comments[1].split()[0] == "SMILES=[2H]C"

    mech = parse_mechanism(export.files["chem.inp"], thermo_text=export.files["therm.dat"])
    resolved = IdentityResolver().resolve_mechanism(mech)
    assert len(resolved) == 2
    assert {r.smiles for r in resolved.values()} == {
        Chem.MolToSmiles(Chem.MolFromSmiles("C")),
        Chem.MolToSmiles(Chem.MolFromSmiles("[2H]C")),
    }
