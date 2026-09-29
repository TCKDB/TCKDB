# Water B3LYP/def2-TZVP: a Psi4 Hessian and its Gaussian reference

Inputs to the QCSchema interchange demonstration (Phase C work package C-Q4).
`backend/scripts/validation/qcschema_interchange_report.py` reads them, and
nothing else. The write-up is `docs/validation/qcschema_interchange.md`.

This directory has no `payload.json`, so the QCSchema backend corpus test
(`backend/tests/api/test_api_qcschema_fixtures.py`) does not pick it up as a
ninth route case.

## Files

| File | What it is |
| --- | --- |
| `water_b3lyp_def2tzvp_hessian_v1.json` | qcengine's `AtomicResult` (QCSchema family v1), driver `hessian`, exactly as written |
| `water_b3lyp_def2tzvp_hessian_v2.json` | the same result converted with qcelemental's `convert_v(2)` (family v2) |
| `run_psi4_water_hessian.py` | the exact script that produced both documents, byte for byte |
| `gaussian_reference.json` | the Gaussian 16 C.02 record the Psi4 run is compared with |
| `meta.json` | the expected route for both documents, the generator versions, and the sha256 of every file above |

## Provenance

- **Who and when.** The repository owner ran the script on 2026-09-29, from
  an untracked working directory. The script writes its two outputs next to
  itself, so running this committed copy writes them here.
- **Environment.** Psi4 1.11 through qcengine 0.51.0, with qcelemental 0.51.2,
  in the conda environment `tckdb_qcschema_psi4`. That is the same pinned
  environment as the adapter's HF/STO-3G corpus. Its recipe is
  `clients/python/adapters/qcschema/tests/fixtures/ENVIRONMENT.lock.txt`, and
  `meta.json` records the lockfile's sha256.
- **Geometry.** The input geometry of the Gaussian record
  (`geom_yfbeuczxncz7kxfjnuaivx72ne`), in Angstrom, as the script's
  `COORDS_ANGSTROM`. `fix_com` and `fix_orientation` are set, so the Psi4
  Hessian uses the same Cartesian axes as the Gaussian one. qcelemental rounds
  the geometry to eight decimals of bohr, so the two geometries differ by at
  most 1.4e-9 Angstrom.
- **Identity.** Psi4 drops `molecule.identifiers`, and TCKDB never derives a
  SMILES from 3D coordinates. The documents are therefore imported with
  `--smiles O`, and the identity is recorded as `depositor_declared`.
- **Gaussian reference.** Read read-only from the development instance on
  2026-09-29. It holds:
  - the freq record's stored Hessian (`calc_cg3uwkx4ud4o7owh73orzxosfe`):
    a 45-element packed lower triangle in hartree/bohr^2, `source=parsed_log`,
    parser `arc-hessian-1`;
  - the single-point energy at the same geometry
    (`calc_y2snebt5z2ywvgaoj7omeffywy`);
  - the stored zero-point energy.

  The refs are that instance's and are kept as provenance. They resolve
  nowhere else.

  The committed file differs from the file as read in two ways. First, the
  `read_from` text now names "the development instance" rather than the
  instance itself. Second, `geometry_angstrom` was added, copied from the run
  script's `COORDS_ANGSTROM`, which was read from the same record. No number
  was changed.

## What is assumed or differs

- **Functional.** Psi4's `b3lyp` is used, not `b3lyp5`. Its stdout names the
  correlation component "Vosko, Wilk & Nusair (VWN5_RPA)", the RPA-fitted VWN
  component that libxc's B3LYP uses to follow Gaussian's definition. Gaussian
  documents that component as VWN functional III. `b3lyp5` would use VWN5
  instead. This is the choice the run script's docstring records. It matches
  the definition, not the implementation.
- **Integrals.** `scf_type pk` gives conventional integrals, as Gaussian uses.
  Psi4's default is density fitting.
- **Grid.** The (99, 590) grid matches Gaussian 16's default UltraFine, and
  that is **assumed**: the grid was not read from the Gaussian record. Even
  with the same point counts, the grids are not the same. Psi4's stdout
  reports a Treutler radial scheme and no pruning, while UltraFine is a pruned
  grid.
- **How each Hessian was computed.** Psi4 differentiated its analytic
  gradients by finite difference (stdout: "Using finite-differences of
  gradients"; `FINDIF NUMBER` 9). Gaussian's freq job gives analytic second
  derivatives.
- **Convergence.** `e_convergence` and `d_convergence` were set to 1e-10.

Because of all this, the two programs' numbers are compared as measurements
with no tolerance. See the validation doc.

## Personal fields in the raw documents

The documents are committed byte-exact because their sha256 is the raw
artifact digest. qcengine's `provenance` block therefore still carries the
run's `username`, `hostname` and `cpu`, as the HF/STO-3G corpus documents do.
The adapter's mapper drops `username` and `cpu` on import and keeps
`hostname`, as it does for every document.
