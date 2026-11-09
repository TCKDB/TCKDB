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
  - Where to get it: the E2 tree is not in this repository. TODO(owner): say whether it ships with the Zenodo
    archive or is available on request. It must hold `raw_outputs/manifest_union.json`, `raw_outputs/tasks.txt` and
    `raw_outputs/tasks/<level>/<molecule>/<start>/{job.log,job.gjf}`.
- **Source 4, formic acid pyrolysis and oxidation model** (`source_chemkin.py`): Wu and Grinberg Dana,
  *ACS Omega* 2026, 11, 6371 (CC BY 4.0), 123 species and 3002 reactions, deposited unmodified.
  - Route: the CHEMKIN adapter (`clients/python/adapters/chemkin`), which posts to `/uploads/thermo` and
    `/uploads/kinetics`.
  - Where to get it: the files are in this repository, `backend/tests/integration/fixtures/rmg_formic_acid_wu2026/`.
  - The adapter's own import warnings (wrong `DUPLICATE` flags) are kept in the report; the importer never
    repairs them.
- **Source 2, ARC runs** and **source 3, N2H3 pressure-dependent network**: stubs (`source_arc.py`,
  `source_pdep.py`). No entry in the sources file means the step is skipped and the build says so. An enabled
  entry is a configuration error: the deposit step is not written yet.

## Run it

Needs the compose stack's Postgres (RDKit cartridge), an S3-compatible object store for job files, and the
`tckdb_env` conda environment. The steps below and `sources.example.yml` agree: database `tckdb_dev`, API on
port 8010, store at `localhost:9000`.

```bash
docker compose up -d db seaweedfs          # or any S3-compatible store at S3_ENDPOINT_URL
make up                                    # migrations on tckdb_dev (an empty instance)
RATE_LIMIT_ENABLED=false make api          # in a second terminal
cp scripts/paper_snapshot/sources.example.yml my_sources.yml   # set the E2 root
python scripts/paper_snapshot/build.py --sources my_sources.yml --out-dir snapshot_out
```

The build refuses a non-empty instance, and checks the whole sources file (every enabled source, stubs included)
before it deposits anything. `RATE_LIMIT_ENABLED=false` matters: one client making a few thousand calls would
otherwise spend the per-minute budget.

`instance.py` makes a throw-away instance for rehearsals (database `tckdb_test_snapshot_<name>`, bucket
`tckdb-test-snapshot-<name>`, an API on a local port). Point `instance`, `database` and `s3` in your sources file at it:

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
- `snapshot_record.json`: source commit, package versions, the approval statement, archive digest, and per source
  its options and a count and digest of the files it read. No path on the builder's machine is written to it.

Generators run for the snapshot: `corpus_counts`, `conformer_basins`, `calculation_evidence`, `deposit_warnings`,
`hessian_reanalysis`, `thermo_selection_outcomes`, `structure_selection_outcomes`, `mechanism_roundtrip_counts`,
`mechanism_fixture_provenance`.

Registered but not run, because they would be empty: `selected_thermo_by_species` (reads dataset-release selections;
the snapshot publishes no release), `candidate_lineage` (needs an entry with several thermo candidates) and
`transition_state_evidence` (needs transition states). They return when the ARC runs are in. The use-case generator
(CH4 + OH, the methyl-radical alternatives) and the network generator are not written: they need source 2 and
source 3, and a generator cannot be tested without its data.

## The literature row depends on the network

The CHEMKIN deposit cites the paper by DOI, and the server fetches the DOI's metadata from Crossref when it stores
the literature row. The row, and so the archive, therefore depends on whether that lookup was reachable and what the
registry returned at the time. `snapshot_record.json` records what was stored: the title, whether the title came from
the registry or from the sources file (`title_source`), and the number of authors. The literature payload has no
author field, so the authors in the archive are the registry's. Rebuilding offline, or after the registry changes the
record, can change that row and nothing else.

## Approval is a demonstration

After the deposits, the build creates a curator account and approves every submission through
`POST /submissions/{id}/approve`. It pages through the whole pending list, and stops with an error if any approval
fails or anything is still pending at the end. The summary on every approval says the build script did it for a
demonstration and that it is not an independent review. The curator account is created with the operator CLI
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

`compare.py` does not expect identical bytes: public refs are random and timestamps differ. It masks public refs in
the expected outputs and, for the archive, timestamps, refs and the bucket name in artifact URIs. It re-sorts
nothing, so a difference in row order is a difference.

## What is left out, and why

- **GFN2-xTB level of E2.** The server does not yet accept an xtb log as an `output_log`, and this build does not
  deposit a number without its job file. Listing `gfn2` in `levels` is refused until it does.
- **`wb97xd/1_propyl/k00`.** The optimisation ends at a saddle point (one imaginary mode); the study excludes it
  and so does the build.
- **`ethylene_glycol/c02` (B3LYP).** The first attempt stopped at the step limit. The deposited calculation is the
  continuation from `repair1`: its geometry is the restart geometry, and the first attempt's log is not deposited.
- **Gaussian punch files (`fort.7`).** No artifact kind admits the name.
- Every task of the campaign is checked for its output first; the result is in `build_report.json` under
  `e2_conformers.provenance`. A log that cannot be read is recorded as a refusal and the build goes on.
