# QCSchema interchange: a Psi4 water Hessian through TCKDB and back (Phase C, C-Q4)

Measured 2026-09-29. This is the demonstration for the QCSchema half of the
[Phase C plan](../research/tckdb-phase-c-implementation-plan.md) (section C1,
"Demonstration"). Its evidence entry is in the
[Phase C verification record](../research/tckdb-phase-c-verification.md).

- **Script:**
  [`backend/scripts/validation/qcschema_interchange_report.py`](../../backend/scripts/validation/qcschema_interchange_report.py).
- **Fixtures:**
  [`backend/tests/fixtures/qcschema/water_b3lyp_def2tzvp_psi4/`](../../backend/tests/fixtures/qcschema/water_b3lyp_def2tzvp_psi4/README.md).
- **Paper generator:** `qcschema_interchange` in
  `backend/scripts/paper/registry.py`.
- **Test:** `backend/tests/scripts/test_qcschema_interchange_report.py`, in
  the backend complement gate.

The script reads only the committed fixtures. It contacts no network. It
writes to the database only inside a transaction that it always rolls back.

The design decisions for this demonstration are recorded below, under
"Decisions". There is no separate decision record.

## In plain terms

We took one Psi4 frequency calculation on water and saved it in the QCSchema
format, the JSON exchange format of the MolSSI tools. We loaded it into TCKDB
through the QCSchema adapter and read it back out as QCSchema. Then we checked
that nothing that matters had changed. The force-constant matrix came back
exactly, number for number. The coordinates came back to ten decimal places.
The molecule's identity was unchanged. Every field that did not survive is
listed, with the reason.

We then set the Psi4 result beside the Gaussian 16 calculation of the same
molecule, at the same level of theory and the same geometry, that TCKDB
already holds. The two programs disagree by:

- 3.2e-8 hartree in energy;
- less than 0.1 cm^-1 in each of water's three vibrational frequencies;
- 6e-7 hartree in zero-point energy.

These are **measurements, not a pass mark.** The two programs integrate the
B3LYP functional on different grids, and they obtain the Hessian differently:
Gaussian analytically, Psi4 by finite differences of gradients. No tolerance
was set in advance, and none is applied.

## Reproducing it

```bash
# Any migrated database will do. Nothing is committed to it.
PGPASSWORD=tckdb createdb -h 127.0.0.1 -U tckdb tckdb_test_qcschema_demo
cd backend && DB_NAME=tckdb_test_qcschema_demo alembic upgrade head && cd ..
DB_NAME=tckdb_test_qcschema_demo python backend/scripts/validation/qcschema_interchange_report.py \
    --json-out qcschema_interchange.json --markdown-out qcschema_interchange.md
PGPASSWORD=tckdb dropdb -h 127.0.0.1 -U tckdb tckdb_test_qcschema_demo
```

The script needs `qcelemental==0.51.2`, which is now in the backend's `[dev]`
extra. It imports the adapter and `tckdb-client` from this checkout's
`clients/python` tree.

Exit status:

- `0`: every round-trip check passed.
- `1`: a round-trip check failed; the output names it.
- `2`: the fixtures or the environment could not be used.

Two runs give byte-identical JSON. The paper generator returns the same report
without the checkout's commit hash.

## What was run and why

The C1 plan asked for one real document from a second electronic-structure
program to go through the adapter and back. It also asked for that result to
be set beside a record TCKDB already holds. The development instance holds
water at B3LYP/def2-TZVP from Gaussian 16 C.02. That record has the three
things needed:

- a stored Hessian (packed lower triangle, `source=parsed_log`, parser
  `arc-hessian-1`);
- a single-point energy at the same geometry;
- a stored zero-point energy.

The repository owner ran Psi4 1.11 through qcengine 0.51.0 at that record's
input geometry on 2026-09-29. The run used the pinned `tckdb_qcschema_psi4`
environment, the same one as the adapter's HF/STO-3G corpus. Both QCSchema
families were written (v1 as produced, v2 by qcelemental's `convert_v(2)`).
The fixture README gives the full provenance, including the two edits made to
the Gaussian reference file (a wording change and an added geometry block; no
number was changed).

For the round trip, the script imports the v2 document with `--smiles O`
using the real adapter:

1. `reader.read_document`;
2. `mapping.build_conformer_upload_payload`;
3. a POST through the real `tckdb-client` to the real
   `POST /api/v1/uploads/conformers` route of an in-process application,
   authenticated with an API key minted for a scratch user.

It then exports the calculation with `exporter.export_calculation`, passing
the integer calculation id: the Hessian read needs the id, which the
scientific read does not return. Everything the import writes is rolled back.

## Decisions

1. **Water at the Gaussian record's own input geometry, in its frame.** The
   Psi4 input fixes the centre of mass and the orientation, so both Hessians
   are in the same Cartesian axes. qcelemental rounds coordinates to 8
   decimals of bohr, so the two geometries differ by at most 1.4e-9 Angstrom.
2. **Psi4 `b3lyp`, not `b3lyp5`.** Psi4's stdout names the correlation
   component "Vosko, Wilk & Nusair (VWN5_RPA)". This is the RPA-fitted VWN
   component that libxc's B3LYP uses to follow Gaussian's definition
   (Gaussian documents it as VWN functional III). `b3lyp5` would use VWN5.
   This matches the definition, not the implementation.
3. **`scf_type pk`.** Conventional integrals, as Gaussian uses. Psi4's default
   is density fitting, which would add an approximation Gaussian does not
   make.
4. **A (99, 590) grid, which assumes Gaussian's grid.** Gaussian 16's default
   is UltraFine, (99, 590) pruned. The Gaussian record does not say which grid
   the job used, so this is **assumed**. Matching the point counts does not
   make the grids the same: Psi4 reports a Treutler radial scheme and no
   pruning, and UltraFine is pruned.
5. **The Hessians are computed differently, and this was left as it is.** Psi4
   differentiated analytic gradients by finite difference (9 displacements,
   `FINDIF NUMBER` 9 in `extras.qcvars`). Gaussian's freq job gives analytic
   second derivatives. The frequency differences below include this.
6. **Identity is declared.** Psi4 drops `molecule.identifiers`, and TCKDB does
   not derive a SMILES from 3D coordinates. The import therefore uses
   `--smiles O` and records `identity_source=depositor_declared`.
7. **One frequency implementation for both Hessians: TCKDB's own**
   (`app.chemistry.normal_modes`, the code the Phase B Hessian reanalysis
   uses). For each Hessian:
   - Unpack the packed lower triangle.
   - Mass-weight with the most abundant isotope's mass (RDKit's table, via
     `atomic_mass`): O 15.99491462 amu, H 1.007825032 amu.
   - Project translations and rotations out exactly (`rigid_body_subspace`,
     then `solve_vibrational_modes`), each Hessian at its own stored geometry.
   - Take the zero-point energy as the harmonic, unscaled sum of nu/2, with
     1 hartree = 219474.6313632 cm^-1 (CODATA 2018).
   - Read the Psi4 side from the matrix TCKDB stored, not from the document.
8. **No tolerance on the comparison between programs.** The C1 plan says a
   printed-precision bound does not apply across programs, so no threshold
   is set. The report carries numbers and no verdict.
9. **The rigid-body residue is reported in the units of the Phase B bound.**
   The only existing bound on rigid-body curvature is
   `FRAME_CONSISTENCY_TOLERANCE_CM1 = 100` cm^-1 in
   `backend/app/chemistry/normal_modes.py`. It is a signed wavenumber of the
   curvature along each translation and rotation direction, and it exists to
   catch a Hessian stored in the wrong frame. Two quantities are reported,
   both as signed wavenumbers: that curvature, and the six lowest eigenvalues
   of the unprojected mass-weighted Hessian.
10. **The energy is not carried by the round trip, and the check says so.**
    Profile v1 maps a document with driver `hessian` to a `freq` record that
    holds only the matrix, so `properties.return_energy` is not stored. The
    exporter fills `properties.return_energy` only from a `sp` record at the
    same level on the same conformer, and there is none. The check
    `energy_not_carried` asserts that the export carries **no** energy, rather
    than a wrong one. The energy for the comparison between programs is read
    directly from the fixture.
11. **The Hessian is compared exactly after TCKDB's packing and unpacking.**
    The Psi4 matrix is not bit-symmetric: four upper-triangle elements differ
    from their mirror by at most 2.1e-17 hartree/bohr^2. Packed lower-triangle
    storage cannot represent that. So the checks are:
    - the stored triangle equals the document's lower triangle exactly;
    - the exported matrix equals that triangle mirrored exactly.

    The expected triangle is computed by the script itself, in
    `hessian_parsing`'s row-major order, not by the adapter's packing code.
12. **The loss list is measured, then pinned.** The original and the export
    are both parsed by qcelemental's v2 `AtomicResult`, and every field path
    is classified. A field qcelemental fills on its own when parsing (`masses`
    from `mass_numbers`, `real`, `fragments`) therefore counts as carried. The
    measured lists of lost and changed fields must equal the declared ones
    exactly.
13. **The raw-artifact POST is not repeated.** `tckdb-qcschema import
    --upload` also posts the untouched document as an artifact, which goes to
    the object store. This script does not contact the object store. The C-Q2
    corpus test (`backend/tests/api/test_api_qcschema_fixtures.py`) covers
    that POST.
14. **`qcelemental==0.51.2` is added to the backend's `[dev]` extra** (and to
    `backend/uv.lock`), with the adapter's exact pin. Before this, the backend
    test environment could not run the adapter. The deployed image installs
    with `--no-deps` from `environment.yml`, so this does not reach it. A
    workstation without the extra skips the new test. CI fails instead of
    skipping.
15. **The paper generator leaves out the commit hash.** It is the same report
    without `tckdb_commit`. A reproducer's checkout could otherwise make the
    deposit's byte comparison fail on a value the fixtures do not determine.

## Round trip

All 11 checks pass:

| check | result | measured |
| --- | --- | --- |
| `fixture_digests_match_meta` | pass | every fixture's sha256 equals `meta.json` |
| `mapped_as_freq` | pass | payload and stored record are both `freq` |
| `v1_and_v2_map_to_the_same_payload` | pass | identical outside `parameters_json` bookkeeping |
| `stored_triangle_exact` | pass | 45 of 45 packed elements equal |
| `exported_hessian_exact_after_pack_unpack` | pass | 81 of 81 equal the mirrored triangle; 4 differ from the raw document by at most 2.08e-17, the document's own asymmetry |
| `stored_geometry_within_bound` | pass | 4.29e-11 Angstrom against a 5e-11 bound, using the backend's `BOHR_TO_ANGSTROM` = 0.52917721067 |
| `exported_geometry_within_bound` | pass | 8.11e-11 bohr against a 9.45e-11 bound |
| `identity_preserved` | pass | O/H/H; charge 0; multiplicity 1; mass numbers 16/1/1; one fragment |
| `energy_not_carried` | pass | no `sp_result` in the payload; no `properties.return_energy` in the export |
| `loss_list_complete` | pass | measured lost and changed lists equal the declared ones |
| `export_reimport_refused` | pass | `tckdb_export_reimport_refused` |

The imported record is stored with Hessian source `uploaded`. The Phase B
Hessian reanalysis reports it as `frequency_list_missing`, as the C1 plan
expected. A QCSchema Hessian arrives with no frequency list, and TCKDB does
not create one from the matrix.

### What the round trip does not carry

Fields are grouped by what the import said about them.

| group | fields | why |
| --- | --- | --- |
| Stored, not exported | `input_data.specification.keywords` (5 keywords); `provenance.hostname`, `.memory`, `.nthreads`, `.wall_time` | Keywords become parameter observations, and the four provenance keys go into `parameters_json`. The exporter has no QCSchema field to write them back to. |
| Reported unsupported at import | `stdout`; `provenance.cpu`, `.module`, `.qcengine_version`, `.username` | The mapping report names these. They survive only in the raw document. |
| Dropped at import without being named | `extras.qcvars`; `properties.return_energy`, `.return_gradient`, `.return_hessian`, `.nuclear_repulsion_energy`, `.calcinfo_nbasis`, `.calcinfo_nmo`, `.calcinfo_nalpha`, `.calcinfo_nbeta`, `.calcinfo_natom` | Profile v1 maps none of these. The import's mapping report does not name them either; see "Findings". |
| Changed | `provenance.creator`, `.routine`, `.version` | Replaced by TCKDB's own export provenance, which is what makes the export refuse re-import. |
| Changed | `molecule.fix_com`, `.fix_orientation`, and the same two under `input_data.molecule` | `true` in the Psi4 input; they read back `false` because the exporter does not set them. |

Everything else in the document compares equal after parsing: 54 field
paths. The Hessian and the two geometries are checked separately above.

## Measurements

All differences are **Psi4 minus Gaussian**, at geometries that agree to
1.4e-9 Angstrom. No tolerance is applied.

| quantity | Gaussian 16 C.02 | Psi4 1.11 | difference |
| --- | ---: | ---: | ---: |
| electronic energy (hartree) | -76.4629956143 | -76.4629955819491 | +3.23509e-8 (+8.494e-5 kJ/mol) |
| nu1, bend (cm^-1) | 1617.0428 | 1616.9449 | -0.0979 |
| nu2, symmetric stretch (cm^-1) | 3785.7692 | 3785.6829 | -0.0863 |
| nu3, antisymmetric stretch (cm^-1) | 3890.9730 | 3890.9035 | -0.0695 |
| imaginary modes | 0 | 0 | |
| ZPE from the recovered frequencies (hartree) | 0.021172800165 | 0.021172222231 | -5.779e-7 |
| ZPE against the stored Gaussian 0.0211728 (hartree) | +1.65e-10 | -5.778e-7 (-1.517e-3 kJ/mol) | |
| max \|H_ij\| difference (hartree/bohr^2) | | | 3.56e-5 (rms 8.41e-6) |
| Hessian asymmetry (hartree/bohr^2) | not observable (stored packed) | 2.08e-17 in the document | |
| rigid-body curvature along each direction (cm^-1) | 0.938, 1.327, 0.673, 13.866, 12.382, 13.102 | 0.000, 0.000, 0.000, -10.855, 2.886, 3.818 | |
| six lowest unprojected eigenvalues (cm^-1) | 0.666, 0.932, 1.327, 12.382, 13.102, 13.866 | -10.855, 0.000, 0.000, 0.000, 2.886, 3.818 | |
| largest rigid-body curvature, against the 100 cm^-1 Phase B bound | 13.866 | 10.855 | both well inside |

Reading the table:

- **Printed precision.** The Gaussian energy is printed to 10 decimals, the
  stored ZPE to 7, and the Gaussian Hessian to 6 significant figures.
  - The recovered Gaussian ZPE matches its stored value to 1.65e-10 hartree,
    inside the 5e-8 half-unit of the stored value's last printed digit.
  - The largest element-wise Hessian difference, 3.6e-5, is about 70 times
    the print rounding (5e-7) of the largest Gaussian elements.
- **Psi4's translations.** Its three translational curvatures are exactly
  zero. This is consistent with Psi4's finite-difference procedure removing
  translations.
- **Psi4's rotations.** Its rotational curvatures are not zero, and one is
  negative (-10.9 cm^-1). The geometry is the Gaussian freq job's, and it is
  not exactly stationary for Psi4: the Psi4 gradient there has a largest
  component of 1.40e-6 hartree/bohr.
- **The Phase B bound.** Both residues are an order of magnitude below the
  100 cm^-1 frame bound. A Hessian in the wrong frame measured 400 to
  800 cm^-1 there. These numbers say the two matrices are in the frame of
  their stored geometries. They say nothing more.

## Mutations

- **Adapter source edited, then reverted.** `pack_lower_triangle` in
  `clients/python/adapters/qcschema/tckdb_qcschema/hessian.py` was changed to
  walk the lower triangle column by column (the transposed packing order).
  - The gate test `test_round_trip_is_exact_and_every_measurement_is_reported`
    went red, with `stored_triangle_exact` and
    `exported_hessian_exact_after_pack_unpack` failing.
  - The monkeypatched exporter-side test also went red. With both sides
    transposed, the export came back right, and **only** the
    `stored_triangle_exact` check caught it. A round-trip comparison alone
    cannot see this: a matching error in packing and unpacking cancels out.
    The stored triangle is therefore checked against an order computed
    independently.
- **Kept as tests.**
  - The importer packing in transposed order: both Hessian checks fail;
    geometry and identity checks do not.
  - The exporter unpacking in transposed order: exactly one check fails,
    `exported_hessian_exact_after_pack_unpack`.
  - A fixture edited after `meta.json` was written is reported as a digest
    mismatch.

## What can and cannot be claimed

**Can claim:**

- One real Psi4 QCSchema Hessian document, v2, was imported through the
  adapter and the real upload route and exported back. Against the document:
  - the Hessian is exact element for element, after TCKDB's
    packed-lower-triangle storage. The only differences are 4 elements of
    2e-17 where the document itself is not bit-symmetric.
  - the geometry is within the stated ten-decimal bound;
  - composition, charge, multiplicity and mass numbers are identical;
  - the export cannot be re-imported as new evidence.
- Every field that does not survive is listed, with the reason, and the list
  is asserted. Its v1 twin maps to the same payload.
- Beside the Gaussian 16 record at the same geometry and nominal level, the
  measured differences are:
  - energy: 3.2e-8 hartree;
  - frequencies: -0.070 to -0.098 cm^-1;
  - ZPE: -5.8e-7 hartree;
  - imaginary modes: zero on both sides;
  - rigid-body residue: under 14 cm^-1 on both sides, against the 100 cm^-1
    Phase B frame bound.

**Cannot claim:**

- Agreement or accuracy. No tolerance was set, and the two programs differ in
  grid, pruning and Hessian method.
- That the grids match. Gaussian's is assumed from its default and was not
  read from the record.
- That the energy of a Hessian document round-trips. It is not stored.
- QCSchema support beyond profile v1: one fragment, real atoms only, drivers
  energy, gradient and hessian, plus `OptimizationResult`.
- Anything about other molecules, levels of theory or programs.
- That the export carries a fixed frame. It does not declare
  `fix_com`/`fix_orientation`, so a consumer that lets qcelemental reorient
  the molecule must rotate the Hessian with it.

## Findings for later (not fixed here)

- **The import's mapping report does not name every field it drops.**
  `extras.qcvars` and nine `properties.*` fields, including
  `properties.return_energy`, appear in none of its four lists. The raw
  document keeps them, but the report is incomplete. A fix belongs in
  `tckdb_qcschema.mapping` and needs a version bump.
- **The exporter does not set `fix_com`/`fix_orientation`.** A Hessian
  depends on the frame, so the export should arguably declare it.
- **The level of theory is spelled differently by the two programs.** Psi4
  writes `b3lyp`/`def2-tzvp`; the Gaussian record's level is `b3lyp/def2tzvp`.
  The level-of-theory hash is byte-exact, so these are two different rows.
  This demonstration pairs the two records by fixture, not by level identity.
  The C1 plan already flags this risk.
