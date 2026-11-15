# Paper snapshot build

One command rebuilds the paper's demonstration snapshot on an empty local TCKDB instance. The snapshot is built
from data we already have, deposited through the same public routes a contributor would use. It is not a hosted
instance, and the script is the deliverable, not the database.

## What it deposits

- **Source 1, E2 conformer study** (`source_e2.py`): 16 molecules, 61 starting structures, optimised with
  frequencies at B3LYP-D3(BJ)/def2-TZVP and wB97X-D/def2-TZVP (Gaussian 16 C.02) and at GFN2-xTB (xtb 6.7.1).
  - Route: the Python client builders. One `ComputedSpeciesUpload` per optimised minimum, then the job files
    attached in the builders' second phase: `job.log` (`output_log`) and `job.gjf` (`input`) for Gaussian; `xtb.out`
    (`output_log`) on both calculations and `g98.out` (`output_log`) on the frequency calculation for xtb.
  - Read from the files: the final geometry, last energy, zero-point correction, frequency list, convergence and the
    start coordinates (`job.gjf` or `start.xyz`). The server reads the Gaussian route line and SCF settings itself.
  - Each Gaussian calculation carries an `actual_protocol_declaration` read from its route line: `Int=` (integration
    grid), `SCF=` (SCF convergence threshold) and `EmpiricalDispersion=` (dispersion). Nothing else on the line, and
    nothing in an xtb task's input, maps to a declaration field; the xtb calculations carry none. `build_report.json`
    lists each declared field with the token and line it was read from.
  - The E2 tree is not in this repository. It must hold `raw_outputs/manifest_union.json`, `raw_outputs/tasks.txt` and
    `raw_outputs/tasks/<level>/<molecule>/<start>/` with `job.log` and `job.gjf` (Gaussian) or `xtb.out`, `g98.out`,
    `start.xyz`, `xtbopt.xyz` and `vibspectrum` (xtb).
- **Source 4, formic acid pyrolysis and oxidation model** (`source_chemkin.py`): Wu and Grinberg Dana,
  *ACS Omega* 2026, 11, 6371 (CC BY 4.0), 123 species and 3002 reactions, deposited unmodified.
  - Route: the CHEMKIN adapter (`clients/python/adapters/chemkin`), which posts to `/uploads/thermo` and
    `/uploads/kinetics`.
  - Where to get it: the files are in this repository, `backend/tests/integration/fixtures/rmg_formic_acid_wu2026/`.
  - The adapter's own import warnings (wrong `DUPLICATE` flags) are kept in the report; the importer never
    repairs them.
- **Source 2, ARC runs** (`source_arc.py`): a fixed, ordered list of ARC run folders, each deposited through the ARC
  adapter (`tckdb_arc` in `tckdb-adapters`) with its `output/output.yml`.
  - Route: `arc_upload.py`, run per folder by `adapter_command` (a Python that imports `tckdb_arc` and ARC; without
    ARC the adapter leaves out the Hessian, IRC and GSM parts, so the runner refuses), posts each
    `computed_reaction` bundle.
  - The folder's `tckdb` block (`tckdb_upload.yml`, else `input.yml`) is reused. The API URL, the API key, the payload
    directory and the request timeout come from the build; every key setting of the block is dropped. A block naming a
    host outside `recorded_hosts` stops the build before anything is deposited.
  - The folders are read, never written. Payloads, sidecars and the adapter's output go to `<work-dir>/arc_payloads`
    and `<work-dir>/arc_logs`, in a payload directory emptied before each run, so upload state an earlier upload left
    in a folder is never read. A digest of each folder's file list is taken before its run and checked after it; a
    change stops the build. The build also stops if the API key appears in the adapter's output or a sidecar.
  - `snapshot_record.json` records the adapter's and ARC's commits and the adapter's declared versions.
- **Source 3, N2H3 pressure-dependent network** (`source_pdep.py`): Keslin, Kaplan and Grinberg Dana, *Phys. Chem.
  Chem. Phys.* 2025, 27, 2680 (doi:10.1039/d4cp03837a), the Arkane run `Final_Final_CCSDT_F12_Aug_F12`.
  - Route: the PDep ingester (`backend/scripts/pdep_ingestion`, `network_only=True`) builds one
    `NetworkPDepUploadRequest` from `input.py`, `output.py` and `supporting_information.csv`, posted to
    `/uploads/networks/pdep` with the paper as its `literature`.
  - Deposited: 11 species, 5 transition states with geometry (TS7 has none in the run and enters only its
    micro-reaction), 6 micro-reactions, 7 states, 21 channels with their Chebyshev fits.
  - Not deposited: `N` and `NH3`, which `input.py` declares but no isomer, reactant channel, reaction or bath gas names;
    N2H3's rotor treatment (`rrho_1d` in `output.py`), because the run's files do not state the rotor's torsion atoms
    and no scan log is in the directory. The ingester's gap report is in `build_report.json`
    (`pdep_network.ingestion`).
  - The server fetches the DOI's metadata and stores the fetched title (`literature_title_mismatch`: the registry's
    title carries subscript markup).

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
`mechanism_fixture_provenance`, `selected_thermo_by_species`, `candidate_lineage`, `transition_state_evidence`,
`use_case_ch4_oh`, `ch3_enthalpy_of_formation`, `network_n2h3`, `chemkin_reconciliation`, `species_thermo_sources`,
`caught_error` and `dataset_release`.

- `use_case_ch4_oh`: the computed rate coefficient of C + [OH] => O + [CH3], k(300 K) from its stored fit, the
  transition state's imaginary frequency and IRC evidence, the levels of the cited energies, and the deposit's warnings.
- `ch3_enthalpy_of_formation`: every [CH3] thermo record (H298, level, the deposit it came from) and the H298
  selection answer with a level-of-theory filter.
- `network_n2h3`: wells, channels and k(T, P) forms of the stored network, and k(1000 K, 1 bar) of NH3N -> N2H3 from
  the stored Chebyshev fit.
- `chemkin_reconciliation`: the formic acid model file's reactions against the rate records and reactions stored,
  with every line not stored as its own reaction and why.
- `species_thermo_sources`: species with thermo from more than one source, with H298, S298 and Cp(300 K) per record.
- `caught_error`: the rejected records, the structure-evidence finding, the deposit's warnings and the kinetics
  selection answer.
- `dataset_release`: each release's handle, selections, shipped candidates, manifest artifacts and version pins.

Every generator orders its rows by content, never by public ref, and emits no timestamp, so two builds compare equal
once refs are masked.

## The literature row depends on the network

The CHEMKIN deposit cites the paper by DOI, and the server fetches the DOI's metadata from Crossref when it stores
the literature row. The row, and so the archive, therefore depends on whether that lookup was reachable and what the
registry returned at the time. `snapshot_record.json` records what was stored: the title, whether the title came from
the registry or from the sources file (`title_source`), and the number of authors. The literature payload has no
author field, and in the build checked the stored row had no authors (`authors_stored` in the record). Rebuilding offline, or after the registry changes the
record, can change that row and nothing else.

## Curation is a demonstration

After the deposits, the build creates a curator account with the operator CLI
(`backend/scripts/bootstrap_admin.py --role curator`; no public route grants that role). Everything below goes through
the public curator routes as that account, never as the depositor, and every summary, note and rationale it writes says
the build script wrote it for a demonstration and that it is not an independent review. The plan is the `curation`
block of the sources file.

- **Approval.** Every pending submission is approved with `POST /submissions/{id}/approve`, paged to the end, except the
  caught-error run's submission. The build stops if an approval fails or anything else is still pending.
- **The caught error** (`curation.caught_error`): one ARC run whose transition state is wrong.
  - A structure-evidence finding on the transition state's optimised geometry (`POST /curation/structure-evidence-findings`),
    citing its frequency calculation.
  - Its transition state, transition-state entry, kinetics and transition-state calculations are rejected, and its other
    records approved, record by record (`PATCH /record-reviews/{record_type}/{record_id}`). The grain is the record: the
    run's species records are sound, and approving its submission would approve every record it holds, so the
    submission itself stays unapproved.
- **One dataset release** (`curation.release`), following `backend/docs/deployment/cutting_a_dataset_release.md`:
  - a curation policy, a draft release, and one selection per listed record (`kinetics`, `transition_state_entry`, or
    `thermo` with a SMILES), each named by its ARC run;
  - the custody check (`backend/scripts/ops/verify_artifact_integrity.py --release`);
  - publication. No DOI is minted.
  - Rights: the ARC deposits carry the depositor's `rights` fragment (`sources.arc_runs.rights`, tckdb-arc 0.15.6 or
    later). A deposit the release ships that has no rights basis is attested by the curator with `source_terms` when it
    came from a source named in `curation.release.source_terms`; any other stops the build.
- `snapshot_record.json` (`approval`) holds the calls' answers: the finding, every record review, the selections, the
  attestations and the manifest's verification.

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

## What is not deposited

- `wb97xd/1_propyl/k00`: one imaginary mode, excluded as a non-minimum.
- `ethylene_glycol/c02` (B3LYP) and `hydroxyacetone/c00` (GFN2): the continuation from `repair1` is deposited
  (restart geometry); the first attempt's output is not.
- Gaussian punch files (`fort.7`): no artifact kind admits the name.
- xtb's `hessian`, `vibspectrum`, `charges`, `wbo`, `xtbrestart`, `start.xyz`, `xtbopt.xyz`, `xtbhess.xyz`, `task.env`
  and `xtbopt.log`: no artifact kind admits the name, or (`xtbopt.log`) the file carries no program signature.
  `status.txt` is the worker's record, not a job file.

Every task of the campaign is checked for its output first (`build_report.json`, `e2_conformers.provenance`). A log
that cannot be read is recorded as a refusal and the build goes on.
