#!/usr/bin/env python
"""Regenerate the torsion-drive fixtures' raw documents (Psi4 via qcengine).

Run in the ``tckdb_qcschema_psi4`` environment (``tests/fixtures/ENVIRONMENT.lock.txt``,
which includes ``torsiondrive`` 1.2.0):

    conda run -n tckdb_qcschema_psi4 python \\
        clients/python/adapters/qcschema/scripts/generate_torsiondrive_fixtures.py OUT_DIR [--only NAME ...]

It writes the raw ``*.json`` documents to ``OUT_DIR``; the fixture
directories (``tests/fixtures/torsiondrive_*/``) hold them byte-for-byte, with
``meta.json`` pins taken from the same documents. A rerun reproduces the
calculation -- same molecules, method, keywords and grids -- but not the
bytes: ``provenance.wall_time``/``hostname``/``username`` differ per run, and
the last digits of an energy can move with the thread count.

Everything here is qcengine's own code except :func:`_spawn_optimization_comma_split`,
which the 2-D drive needs (see its docstring).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import qcengine
from qcelemental.models import v2 as m2
from qcelemental.models.v2 import Molecule, OptimizationInput
from qcengine.procedures import torsiondrive as td_mod

MODEL = {"method": "hf", "basis": "sto-3g"}
ATOMIC = {"driver": "gradient", "model": MODEL, "program": "psi4", "protocols": {"stdout": False}}


def _spawn_optimization_comma_split(grid_point, job, input_model, config):
    """qcengine 0.51.0's ``TorsionDriveProcedure._spawn_optimization``, one line changed.

    The shipped line is ``angles = grid_point.split()``. torsiondrive 1.2.0
    names a 2-D grid point ``"180,-60"``, so the shipped harness raises
    ``ValueError: invalid literal for int() with base 10: '180,-60'`` on any
    multi-dimensional drive. The replacement treats commas as separators too;
    for a 1-D drive (``"90"``) it is identical to the original.
    """
    from qcengine import compute

    input_molecule = input_model.initial_molecule[0].copy(deep=True).dict()
    input_molecule["geometry"] = np.array(job).reshape(len(input_molecule["symbols"]), 3)
    input_molecule = Molecule.from_data(input_molecule)
    dihedrals = input_model.specification.keywords.dihedrals
    angles = grid_point.replace(",", " ").split()  # was: grid_point.split()
    optspec_v2 = input_model.specification.specification.model_dump()
    optspec_v2["keywords"].setdefault("constraints", {})
    optspec_v2["keywords"]["constraints"].setdefault("set", [])
    optspec_v2["keywords"]["constraints"]["set"].extend(
        [{"type": "dihedral", "indices": dihedral, "value": int(angle)} for dihedral, angle in zip(dihedrals, angles)]
    )
    input_data = OptimizationInput(initial_molecule=input_molecule, specification=optspec_v2)
    return compute(input_data, program=input_model.specification.specification.program, task_config=config.dict())


td_mod.TorsionDriveProcedure._spawn_optimization = staticmethod(_spawn_optimization_comma_split)


H2O2 = """
0 1
O  0.000000  0.700000  0.000000
O  0.000000 -0.700000  0.000000
H  0.900000  0.900000  0.300000
H -0.900000 -0.900000  0.300000
units angstrom
"""

HOOOH = """
0 1
H   1.200000  0.900000  0.400000
O   1.100000  0.000000  0.000000
O   0.000000 -0.700000  0.000000
O  -1.100000  0.000000  0.000000
H  -1.200000  0.900000 -0.400000
units angstrom
"""


def _optimize(xyz: str):
    optin = m2.OptimizationInput(
        initial_molecule=m2.Molecule.from_data(xyz),
        specification={
            "program": "geometric",
            "keywords": {"coordsys": "tric"},
            "protocols": {"trajectory_results": "all"},
            "specification": ATOMIC,
        },
    )
    return qcengine.compute(optin, "geometric", raise_error=True)


def _drive(start, dihedrals, spacing, *, scan_results, trajectory):
    tdin = m2.TorsionDriveInput(
        initial_molecule=[start],
        specification={
            "program": "torsiondrive",
            "keywords": {"dihedrals": dihedrals, "grid_spacing": spacing},
            "protocols": {"scan_results": scan_results},
            "specification": {
                "program": "geometric",
                "keywords": {"coordsys": "tric"},
                "protocols": {"trajectory_results": trajectory},
                "specification": ATOMIC,
            },
        },
    )
    return qcengine.compute(tdin, "torsiondrive", raise_error=True)


def _dump(out: Path, name: str, model) -> None:
    (out / f"{name}.json").write_text(model.model_dump_json() if hasattr(model, "model_dump_json") else model.json())
    print("wrote", name)


def h2o2(out: Path) -> None:
    """torsiondrive_parent_opt_v1/_v2 and torsiondrive_v1/_v2."""
    opt = _optimize(H2O2)
    _dump(out, "h2o2_opt_v2", opt)
    _dump(out, "h2o2_opt_v1", opt.convert_v(1))
    td = _drive(opt.final_molecule, [(2, 0, 1, 3)], [90], scan_results="all", trajectory="all")
    _dump(out, "h2o2_td_v2", td)
    _dump(out, "h2o2_td_v1", td.convert_v(1))


def hoooh(out: Path) -> None:
    """torsiondrive_2d_parent_opt_v2 and torsiondrive_2d_v2.

    90 degrees, not 120: with a spacing whose grid lacks 0, torsiondrive 1.2.0
    files each result under the wrong grid id and never finishes (see
    tests/fixtures/README.md).
    """
    opt = _optimize(HOOOH)
    _dump(out, "hoooh_opt_v2", opt)
    td = _drive(opt.final_molecule, [(0, 1, 2, 3), (1, 2, 3, 4)], [90, 90], scan_results="lowest", trajectory="final")
    _dump(out, "hoooh_td2d_v2", td)


def hoooh_rotors(out: Path, parent: Path) -> None:
    """torsiondrive_rotor1_v2 and torsiondrive_rotor2_v2: two 1-D drives,
    one per O-O torsion, both from the committed hydrogen-trioxide
    optimization (``parent``: torsiondrive_2d_parent_opt_v2/document.json),
    so the two drives share one parent."""
    start = m2.OptimizationResult.model_validate_json(parent.read_text()).final_molecule
    for name, dihedral in (("hoooh_rotor1_td_v2", (0, 1, 2, 3)), ("hoooh_rotor2_td_v2", (1, 2, 3, 4))):
        td = _drive(start, [dihedral], [90], scan_results="lowest", trajectory="final")
        _dump(out, name, td)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("out_dir")
    parser.add_argument("--only", nargs="*", choices=["h2o2", "hoooh", "hoooh_rotors"])
    parser.add_argument(
        "--parent",
        default=str(Path(__file__).resolve().parents[1] / "tests/fixtures/torsiondrive_2d_parent_opt_v2/document.json"),
        help="the optimization the two rotor drives start from",
    )
    args = parser.parse_args()
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    only = set(args.only or ["h2o2", "hoooh", "hoooh_rotors"])
    if "h2o2" in only:
        h2o2(out)
    if "hoooh" in only:
        hoooh(out)
    if "hoooh_rotors" in only:
        hoooh_rotors(out, Path(args.parent))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
