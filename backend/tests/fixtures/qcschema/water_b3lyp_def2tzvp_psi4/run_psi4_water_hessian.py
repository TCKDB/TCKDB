"""Psi4 Hessian on water at the Gaussian geometry, written as a QCSchema document.

Phase C C-Q4 demonstration input. Run in the pinned environment the adapter's
test corpus was generated in (Psi4 1.11, qcengine 0.51.0, qcelemental 0.51.2):

    conda run -n tckdb_qcschema_psi4 python paper/qcschema_demo/run_psi4_water_hessian.py

The environment is reproducible from
clients/python/adapters/qcschema/tests/fixtures/ENVIRONMENT.lock.txt.

Geometry: the input geometry of the Gaussian 16 B3LYP/def2-TZVP freq record
calc_cg3uwkx4ud4o7owh73orzxosfe (geometry geom_yfbeuczxncz7kxfjnuaivx72ne),
read from the TCKDB scientific API on 2026-09-29. The frame is fixed so the
Psi4 Hessian is expressed in the same Cartesian axes as the Gaussian one.

Level: B3LYP/def2-TZVP chosen to match the Gaussian record as closely as the
codes allow:
- Psi4 `b3lyp` uses the VWN(III)/RPA correlation piece, the same definition as
  Gaussian's B3LYP (Psi4 `b3lyp5` would be the VWN5 variant).
- `scf_type pk`: conventional integrals, as Gaussian uses; Psi4's default is
  density fitting.
- (99, 590) grid: Gaussian 16's default UltraFine. Assumed, not read from the
  Gaussian record. Radial/angular schemes and pruning still differ between the
  codes, so energies and frequencies are compared as measurements, not held to
  a printed-precision bound.

Outputs, next to this script:
- water_b3lyp_def2tzvp_hessian_v1.json  (qcengine's AtomicResult, family v1)
- water_b3lyp_def2tzvp_hessian_v2.json  (the same result via convert_v(2))
"""

from __future__ import annotations

import json
from pathlib import Path

import qcelemental as qcel
import qcengine

HERE = Path(__file__).resolve().parent

# Angstrom, exactly as stored for geom_yfbeuczxncz7kxfjnuaivx72ne.
SYMBOLS = ["O", "H", "H"]
COORDS_ANGSTROM = [
    [0.0, 0.0, 0.116888],
    [0.0, 0.765017, -0.467553],
    [0.0, -0.765017, -0.467553],
]


def build_input() -> qcel.models.AtomicInput:
    bohr_per_angstrom = 1.0 / qcel.constants.bohr2angstroms
    molecule = qcel.models.Molecule(
        symbols=SYMBOLS,
        geometry=[c * bohr_per_angstrom for xyz in COORDS_ANGSTROM for c in xyz],
        molecular_charge=0,
        molecular_multiplicity=1,
        fix_com=True,
        fix_orientation=True,
        identifiers={"smiles": "O"},
    )
    return qcel.models.AtomicInput(
        molecule=molecule,
        driver="hessian",
        model={"method": "b3lyp", "basis": "def2-tzvp"},
        keywords={
            "scf_type": "pk",
            "dft_radial_points": 99,
            "dft_spherical_points": 590,
            "e_convergence": 1e-10,
            "d_convergence": 1e-10,
        },
    )


def main() -> int:
    result = qcengine.compute(
        build_input(),
        "psi4",
        task_config={"ncores": 4, "memory": 8},
        raise_error=False,
    )
    if not getattr(result, "success", False):
        error = getattr(result, "error", None)
        print("Psi4 run FAILED:", error.error_message if error else result)
        return 1

    v1_path = HERE / "water_b3lyp_def2tzvp_hessian_v1.json"
    v2_path = HERE / "water_b3lyp_def2tzvp_hessian_v2.json"
    v1_path.write_text(result.json())
    v2_path.write_text(result.convert_v(2).json())

    print("energy (Eh):", result.properties.return_energy)
    print("wrote", v1_path.name, "and", v2_path.name)
    print(json.dumps(result.provenance.dict(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
