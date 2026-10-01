# Core scientific concepts

A short reference for the recurring nouns in the TCKDB schema and API.
The schema separates **identity** (what something is), **structure**
(its geometry), **calculation** (how a number was produced),
**scientific product** (the resulting quantity), and **provenance and
trust** (who/what/when, and review state).

For full field-level detail see [`backend/schema_spec.md`](../../backend/schema_spec.md).
For the design rationale behind splitting identity from results, see
the design docs under `backend/docs/`.

---

## Identity vs entry

TCKDB consistently distinguishes a chemical "thing" from a specific
scientific form of that thing.

### `species` vs `species_entry`

- **`species`** is the graph-level molecular identity — the
  connectivity that defines "ethanol" or "the OH radical", keyed by
  `(canonical_smiles, charge, multiplicity)`. InChIKey is derived and
  stored for cross-notation search but is not part of the dedup key —
  standard InChI can't represent spin state and over-merges some
  tautomers (DR-0031). Two equally valid Gaussian and Orca
  optimizations of ethanol refer to the same `species`.
- **`species_entry`** is a resolved scientific form of a species: a
  particular stereochemistry, electronic state, isotopologue label, or
  stationary-point kind (minimum, transition state, conformer
  observation, etc.). A `species` may have many `species_entry` rows.

### `chem_reaction` vs `reaction_entry`

- **`chem_reaction`** is the bare reactants → products identity (the
  participant species plus reaction family and direction);
  stoichiometric coefficients live here. Atom mapping is *not* stored at
  the reaction level — reaction identity is participant species +
  stoichiometry + direction.
- **`reaction_entry`** is a scientific instance of that reaction —
  reactants and products attached as specific `species_entry` rows
  (chirality, electronic state, …), with attached kinetics, transport,
  and review state.

### `transition_state` vs `transition_state_entry`

The same split applies to transition states: `transition_state`
captures the saddle-point identity for a reaction, and
`transition_state_entry` is a specific scientific instance with
geometry, frequencies, IRC linkage, and review state.

---

## Structure

### `geometry`

A coordinate set — element symbols and Cartesians (Angstrom),
optionally with charges/multiplicities and atom labels. Geometries are
referenced from `species_entry`, `transition_state_entry`, conformer
observations, and as inputs/outputs of calculations.

Geometries are addressed by **public handle** (a `geom_…` ref).
Coordinate payloads are fetched explicitly via
`/api/v1/scientific/geometries/{geometry_handle}`.

### `conformer_group` vs `conformer_observation`

- **`conformer_group`** clusters observations of the same conformer
  identity — typically keyed by topology and a canonical pose.
- **`conformer_observation`** is a single observed conformer: a
  geometry produced by a specific calculation, attached to a
  `species_entry`. Multiple observations of one conformer share a
  group; cross-method consistency lives at the group level, not in
  individual observations.

---

## Calculations

### `calculation`

The hub table for any computed result — `sp`, `opt`, `freq`, `scan`,
`irc`, `path_search`, `conf`. It records what was run, by which
software at which level of theory, on which input geometry, with which
parameters. Specific result rows (`calc_sp_result`, `calc_opt_result`,
`calc_freq_result`, …) attach to the hub.

A `composite` type (CBS-QB3, G4, W1BD and user-built extrapolations) is
**planned, not built**: today a composite energy has no honest home and is
deposited as an `sp` or `opt`. The decided model is in
[ADR 0021](../adr/0021-composite-levels-of-theory-are-a-recipe-bound-to-a-level-not-a-weighted-level.md).

### Calculation dependencies

Calculations are linked into a small **DAG** so a downstream result
can point back at the upstream calculations it consumed. A frequency
calculation cites the optimization that produced its geometry; a
single-point cites the optimization whose geometry it ran on. (Once the
planned `composite` type lands, a composite cites the single points it was
assembled from; see ADR 0021.)
Dependencies are opportunistic enrichment — they are not required for
a calculation to be valid, but they are what makes a result
**reproducible from inside the database**.

### Calculation parameters and artifacts

- **Parameters** record ESS execution settings (basis set keywords,
  solvent, SCF thresholds, integrator, …) under a controlled
  vocabulary. Parameters are observations from parsed jobs, not a
  general dictionary of every possible flag.
- **Artifacts** are raw output blobs (output files, logs) stored in
  S3-compatible object storage. They are reachable through the API by
  artifact handle, not by direct URL.

---

## Scientific products

### `statmech`

Per-species partition-function data — rotational constants, vibrational
frequencies, electronic state energies, hindered-rotor scans, symmetry.
Statmech rows are computed from `freq` and `scan` calculations and feed
thermo and kinetics generation.

### `thermo`

Temperature-dependent thermodynamic quantities: Cp(T), H(T), S(T), and
fitted NASA polynomials (NASA-7 and NASA-9) or a Wilhoit form; the
representation is tagged explicitly by ``thermo.model_kind``.
Each row carries provenance back to the underlying statmech (via the
``statmech_id`` FK, for computed thermo) or the literature it was
lifted from.

### `kinetics`

Rate-coefficient records — Arrhenius, modified Arrhenius, and
pressure-dependent models (Chebyshev, PLOG, …). Each model kind stores
its parameters with units expressed by enum (`ArrheniusAUnits`,
`ArrheniusEaUnits`) and applicability range (T, P). Provenance points
back at the reaction entry, level of theory, and any underlying
network or transition state.

### `transport`

Lennard-Jones and related transport parameters per species/species
entry.

### `network`

Pressure-dependent kinetic networks — sets of wells and transition
states feeding a master-equation calculation. Network kinetics
(typically Chebyshev/PLOG) link back to the network they were derived
from.

---

## Provenance and trust

### Public refs vs internal IDs

Every read-API response uses **public refs** as the addressable
handle for a record — `species_…`, `species_entry_…`, `geom_…`,
`reaction_…`, `reaction_entry_…`, `lot_…`, … Public refs are stable
across reseeds, safe to log, and the only form clients should depend
on. Integer primary keys (`id` columns) are an internal database
concern and are hidden from responses by default; they can only be
surfaced under an explicit `include=internal_ids` request on
deployments that permit it.

See [`docs/specs/public_identifier_policy.md`](../specs/public_identifier_policy.md)
and [`docs/specs/internal_ids_visibility_policy.md`](../specs/internal_ids_visibility_policy.md).

### Levels of theory, software, workflow tools

- **`level_of_theory`** captures method + basis + dispersion + solvent
  + auxiliary settings under one stable ref (`lot_…`).
- **`software` / `software_release`** identifies a program and its
  exact release. On a **calculation** it is always the electronic
  structure program that ran the job (Gaussian 16 C.02, ORCA 6.1.0, …);
  naming a workflow tool there (Arkane, ARC, RMG) is refused with
  `calculation_software_is_workflow_tool`. On a thermo, statmech or
  kinetics record it is the *analysis* code that produced the numbers
  (the bundle's `analysis_software_release` — Arkane, MESS, MultiWell),
  which is why Arkane can appear in the `software` table without any
  calculation citing it. A NULL `version` means "the program was
  recorded, its version was not"; it is never filled in place. When an
  uploaded output log's banner names the same program with a version,
  the calculation is re-pointed at that versioned release at ingest
  (`backend/app/services/software_banner_extraction.py`), unless an
  execution-environment manifest fixes its release; stored logs are
  handled by `backend/app/services/software_release_version_fill.py`.
  Where no log can speak, the person who ran the calculations can: an
  owner's statement of the version is recorded verbatim in the append-only
  `software_version_attestation` table, with one
  `software_version_attestation_calculation` row per calculation it
  re-pointed (before and after release), by the same tool's
  `--attest-version` mode. The statement names whose deposits it covers
  (`covers_depositor`: the attester, or another account when an admin
  attests), and only that account's calculations are moved.
- **`workflow_tool` / `workflow_tool_release`** identifies the
  high-level pipeline that produced the records (ARC, a custom RMG
  pipeline, …). TCKDB itself is workflow-tool agnostic.
- The `/meta/software*` and `/meta/workflow-tool*` vocabularies list only
  programs and versions some **calculation** cites — observations, not
  the registry. Registry rows nothing cites at all can be listed and
  removed with `backend/scripts/ops/prune_orphan_provenance.py`.

### Literature

Records may cite one or more `literature` entries (with `author`
links) when their values were lifted from a paper. DOI/ISBN metadata
is fetched and cached; see
`docs/literature_policy.md`
if present.

### Submissions and reviews

- **`submission`** groups records uploaded together as one logical
  contribution. Uploads are submission-scoped and self-contained.
- **`record_review`** captures human moderation state per record —
  `not_reviewed`, `under_review`, `approved`, `rejected`, `deprecated`
  (the `RecordReviewStatus` enum). Read endpoints
  default to filtering by `min_review_status`, so anonymous readers
  see curated data unless they explicitly opt into raw drafts.

### Read profiles: `exploratory` vs `curated`

Every `/api/v1/scientific/*` read answers under an explicit profile,
echoed in the response's `request` block.

- **`exploratory`** (the default) — every visible candidate, with its
  review state. TCKDB makes **no recommendation**; you are reading the
  archive. `request.profile_recommendation` says `none`.
- **`curated`** — records at or above the `approved` review floor,
  reported alongside the dataset release backing them.

### `dataset_release` and `release_selection`

- **`dataset_release`** is a *citable* snapshot of curated values: a tag
  (`2026.07.0`), an immutable SHA-256-checksummed manifest, licenses, a
  citation string, and a DOI once one has actually been deposited.
- **`release_selection`** is one attributed, append-only decision inside
  a release: which candidate a named curator chose for a subject, under
  which policy version, and why. It points at the scientific record and
  never modifies it, so the underlying candidates stay equally
  retrievable. Changing a decision appends a superseding row rather than
  editing the original.
- **`curation_policy`** is the named, versioned rubric a selection cites.
  It is the persisted counterpart to the read-time `selection_policy`
  knob, which ranks candidates deterministically and persists nothing.

A release publishes its selections *and* the full candidate set and
review history behind them, so a reader can disagree with a TCKDB
recommendation without privileged access. See
[`dataset_release_and_profiles.md`](../../backend/docs/specs/dataset_release_and_profiles.md).
