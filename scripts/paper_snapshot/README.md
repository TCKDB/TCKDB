# Paper snapshot build

One command rebuilds the paper's demonstration snapshot on an empty local TCKDB instance. The snapshot is built
from data we already have, deposited through the same public routes a contributor would use. It is not a hosted
instance, and the script is the deliverable, not the database.

## What it deposits

- **Source 1, E2 conformer study** (`source_e2.py`): 16 molecules, 61 starting structures, optimised with
  frequencies at B3LYP-D3(BJ)/def2-TZVP and wB97X-D/def2-TZVP (Gaussian 16 C.02).
  - Route: the Python client builders. One `ComputedSpeciesUpload` per optimised minimum, then the job's
    `job.log` and `job.gjf` attached in the builders' second phase.
  - Read from the files: the final geometry, last SCF energy, zero-point correction, frequency list, convergence
    and the start coordinates in the input deck. The server reads the route line and SCF settings itself.
- **Source 4, formic acid pyrolysis and oxidation model** (`source_chemkin.py`): Wu and Grinberg Dana,
  *ACS Omega* 2026, 11, 6371 (CC BY 4.0), 123 species and 3002 reactions, deposited unmodified.
  - Route: the CHEMKIN adapter (`clients/python/adapters/chemkin`), which posts to `/uploads/thermo` and
    `/uploads/kinetics`.
  - The adapter's own import warnings (wrong `DUPLICATE` flags) are kept in the report; the importer never
    repairs them.
- **Source 2, ARC runs** and **source 3, N2H3 pressure-dependent network**: stubs (`source_arc.py`,
  `source_pdep.py`). No entry in the sources file means the step is skipped and the build says so. An enabled
  entry stops the build, because the deposit step is not written yet.

## Run it

Needs the compose stack's Postgres (RDKit cartridge), an S3-compatible object store for job files, and the
`tckdb_env` conda environment.

```bash
docker compose up -d db seaweedfs          # or any S3-compatible store at S3_ENDPOINT_URL
make up                                    # migrations on tckdb_dev (an empty instance)
make api                                   # in a second terminal
cp scripts/paper_snapshot/sources.example.yml my_sources.yml   # edit the paths
python scripts/paper_snapshot/build.py --sources my_sources.yml --out-dir snapshot_out
```

The build refuses a non-empty instance. Set `RATE_LIMIT_ENABLED=false` on the API process: one client making a few
thousand calls would otherwise spend its per-minute budget.

`instance.py` makes a throw-away instance for rehearsals (database `tckdb_test_snapshot_<name>`, bucket
`tckdb-test-snapshot-<name>`, an API on a local port):

```bash
python scripts/paper_snapshot/instance.py up --name a --port 8101
python scripts/paper_snapshot/build.py --sources my_sources.yml
python scripts/paper_snapshot/instance.py down --name a --drop
```

When you run from a git worktree, an editable install can point at another checkout. `build.py` and `instance.py`
put this checkout's packages first on `PYTHONPATH`; `snapshot_record.json` says whether the imports came from here.

## What comes out

In `--out-dir`:

- `tckdb.archive`: the whole instance, from `backend/scripts/tckdb_archive.py`. Job files travel as blobs.
- `expected_outputs/<name>.json` and `.md`: the paper's numbers, from `backend/scripts/paper/generate_expected_outputs.py`
  (the generators registered in `backend/scripts/paper/registry.py`).
- `build_report.json`: per source what was deposited, and every refusal and warning grouped by code.
- `snapshot_record.json`: source commit, package versions, the approval statement, archive digest.

Generators run for the snapshot: `corpus_counts`, `conformer_basins`, `calculation_evidence`,
`deposit_warnings`, `hessian_reanalysis`, `selected_thermo_by_species`, `thermo_selection_outcomes`,
`candidate_lineage`, `transition_state_evidence`, `mechanism_roundtrip_counts`, `mechanism_fixture_provenance`.
The use-case generator (CH4 + OH, the methyl-radical alternatives) and the network generator are not written:
they need source 2 and source 3, and a generator cannot be tested without its data.

## Approval is a demonstration

After the deposits, the build creates a curator account and approves every submission through
`POST /submissions/{id}/approve`. The summary on every approval says the build script did it for a demonstration and
that it is not an independent review. The curator account is created with the operator CLI
(`backend/scripts/bootstrap_admin.py --role curator`) because no public route grants that role.

## Check it

```bash
# Two builds from empty instances: same content?
python scripts/paper_snapshot/compare.py build_one_out build_two_out

# Restore the archive into a third empty instance and read samples back, including a job file by digest
python scripts/paper_snapshot/instance.py up --name c --port 8103
python scripts/paper_snapshot/instance.py restore --name c --archive snapshot_out/tckdb.archive
python scripts/paper_snapshot/readback.py --api-url http://127.0.0.1:8103 --sources my_sources.yml
```

**Known blocker for the full snapshot.** `tckdb_archive.py restore` refuses an archive in which one species is both a
reactant and a product of the same reaction (the formic acid model has 166 such participants): the writer orders
`reaction_participant` rows by the database's enum order (`reactant`, `product`) and the reader checks the
primary key in string order (`product`, `reactant`), so it reports "Primary keys are duplicated or unsorted". An
archive of the E2 source alone restores, regenerates byte-identical expected outputs and reads back; the archive with
the formic acid model does not restore until that check is fixed.

`compare.py` does not expect identical bytes: public refs are random and timestamps differ. It masks both, says so,
and reports which files needed it.

## What is left out, and why

- **GFN2-xTB level of E2.** The server refuses `xtb.out` as an `output_log` (it knows no xtb header) and `.out` is
  not an `ancillary` extension, so the level's log has no admissible slot. `levels` in the sources file leaves it
  out. `gfn2_log_as: ancillary` deposits the log as a byte-identical `.txt` copy; use it only if that is acceptable.
- **`wb97xd/1_propyl/k00`.** The optimisation ends at a saddle point (one imaginary mode); the study excludes it
  and so does the build.
- **Gaussian punch files (`fort.7`), xtb sidecar files.** No artifact kind admits those names.
- Every task of the campaign is checked for its output first; the result is in `build_report.json` under
  `e2_conformers.provenance`.
