# tckdb-qcschema

Moves MolSSI QCSchema documents (the JSON that QCEngine, Psi4 and QCArchive
write) into TCKDB, and TCKDB calculations back out as QCSchema.

```
pip install -e clients/python/adapters/qcschema   # pins qcelemental==0.51.2
```

## Commands

```
tckdb-qcschema report  result.json [--smiles O]
tckdb-qcschema import  result.json [--smiles O] [--upload | --dry-run] [--allow-duplicate]
tckdb-qcschema import  torsiondrive.json --parent-opt opt.json [--smiles OO] [--upload | --dry-run]
tckdb-qcschema export  <calc_ref or id> [--out FILE]
```

`--upload` and `export` need the API root in `--base-url` or `$TCKDB_BASE_URL`
(for example `https://<host>/api/v1`) and, for writes, `$TCKDB_API_KEY`.
`report` never touches the network.

Exit status: 0 success, 2 the adapter refused the data (`REFUSED [code]: ...`),
1 the API could not be used (`ERROR [...]: ...`).

## What maps to what

| QCSchema document | TCKDB |
|---|---|
| `AtomicResult`, driver `energy` or `gradient` | `sp` calculation |
| `AtomicResult`, driver `hessian` | `freq` calculation with the uploaded Hessian |
| `OptimizationResult` | `opt` calculation |
| `TorsionDriveResult` + the `OptimizationResult` it started from | a conformer whose `opt` carries a `scan` calculation |

Both QCSchema families (qcelemental `models.v1` and `models.v2`) are read. The
family is decided by the document's shape, never by its `schema_version`.

Every import keeps the original file as an `ancillary` artifact and records a
mapping report (`transformed`, `retained_only`, `unsupported`, `rejected`) in
the calculation's `parameters_json`. Identity comes only from `--smiles` or
the molecule's `identifiers.smiles`; a structure is never perceived from its
coordinates.

## Scans

QCSchema's only scan model is the TorsionDrive: one or more proper dihedrals
on an integer grid, with a constrained optimization at every grid point.

- **Import.** Give the drive and, with `--parent-opt`, the optimization it
  started from. TCKDB attaches a scan to a conformer, and the conformer is
  anchored by that optimization; the drive's own optimizations are all
  constrained, so none of them can stand in for it. The adapter checks that
  the optimization is the drive's starting point (same atoms, charge and
  multiplicity, and its final geometry is one of the drive's initial
  molecules), and that every grid point's geometry holds the angle its grid
  key names. Energies are stored exactly as written. The per-point
  optimization trajectories have no TCKDB table; they stay in the raw
  artifact and are listed as `retained_only`. Extra optimizer constraints are
  refused.
- **Export.** A stored scan exports as a v2 `TorsionDriveResult` when it is
  one: relaxed, every coordinate a proper dihedral, whole-degree values that
  agree with each point's stored geometry. Bond, angle and improper scans,
  rigid scans, and scans whose values are a sweep relative to the first point
  (the convention of scans deposited before ADR 0020) are refused with a
  code, never relabelled. What the document does not carry is listed in
  `extras.tckdb.export_report`.

A document exported by TCKDB (`provenance.creator = "TCKDB"`) is refused on
import: it is TCKDB's own numbers, not a new calculation.

## IRCs

qcelemental 0.51.2 has no IRC, NEB or reaction-path model in either family,
so IRC calculations are neither imported nor exported.
