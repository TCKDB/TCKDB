# TCKDB — Article Outline (draft for expansion)

**Status:** skeleton with bullet points to be written into prose.
**Working title options:**
- "TCKDB: A Provenance-First, Community Thermochemical and Kinetics Database for Computational Chemistry"
- "Separating Identity, Provenance, Result, and Trust: An Architecture for a Reproducible Community Kinetics Database"

**Target venues (in rough priority):** *J. Chem. Inf. Model.* (JCIM);
*Scientific Data* (data-descriptor format fits well); *J. Chem. Theory
Comput.* (if we emphasize the reproducibility/statmech angle);
*Combustion and Flame* / *Int. J. Chem. Kinet.* (if we emphasize the
kinetics community angle). Recommendation: JCIM or Scientific Data — the
novelty is the **data model + governance**, not a single mechanism.

**One-sentence thesis:** TCKDB is the first community computational-
chemistry database that stores not just thermochemical and kinetic
*values* but the complete, machine-readable *argument* for each value —
its identity, provenance, and trust kept rigorously separate — so that
disagreement is representable, re-computation is possible from inside the
database, and "the best value" is a transparent, reproducible query-time
decision rather than a curator's frozen flag.

---

## Abstract (write last; ~200 words)

- Problem: computational thermochemistry/kinetics values look final but
  are the output of long chains of methodological choices; existing
  collections store the number + a citation, so disagreements cannot be
  arbitrated, better methods silently overwrite old entries, and errors
  cannot be traced.
- Contribution: a provenance-first relational data model built on a
  single organizing principle — every table is **identity**,
  **provenance**, **result**, or **trust/curation** — deployed as an
  open, versioned, API-accessible community database.
- Key features: full traceability from a thermo/kinetic value to the ESS
  jobs, geometries, levels of theory, and (byte-exact) log files that
  produced it; append-only results so history is never lost;
  deterministic, policy-driven read-time selection of the "best" value;
  content-only uploads that make contributions portable across
  deployments.
- Result / availability: N species, M reactions, K calculations at
  launch (fill in); open API; self-hostable; client library.
- Impact: a template for reproducible scientific databases beyond
  chemistry.

---

## 1. Introduction

- **The reproducibility gap in computational thermochemistry/kinetics.**
  - A "heat of formation of species X" or "rate coefficient k(T) of
    reaction Y" is not a fact but the output of choices: conformer,
    level of theory, basis set, dispersion/solvation treatment, frequency
    scale factor, bond-additivity/atomization corrections, hindered-rotor
    vs harmonic treatment, tunneling model.
  - Two careful groups can publish values differing by several kJ/mol (or
    factors in k), both correct *given their choices*.
- **Why current resources are insufficient** (cite/contrast, don't
  disparage): group-additivity databases, Active Thermochemical Tables
  (ATcT), Burcat tables, the RMG database, NIST resources.
  - Most store final numbers + citations; provenance is prose, not data.
  - Overwriting on update; no representation of legitimate disagreement;
    no path from a stored value back to a re-runnable calculation.
- **What a community database for this era needs** (motivates our design
  requirements — forward-reference the four buckets):
  - Deduplicated *identity* so the same molecule/reaction isn't stored
    many times.
  - Append-only *provenance* and *results* so re-running never erases
    history.
  - Trust as an *overlay* that changes belief, not values.
  - Machine-readable end-to-end traceability enabling re-computation.
  - Portability of contributions across a laptop, a lab server, and the
    hosted community instance.
- **Contributions of this paper** (bulleted list):
  1. A four-role data-model taxonomy (identity/provenance/result/trust)
     and the design invariants that follow from it.
  2. A complete provenance chain for computed thermochemistry and
     kinetics, including itemized energy-correction decompositions and
     literature-sourced frequency scale factors — to our knowledge not
     captured as data by any prior database.
  3. Read-time, policy-based "best value" selection: no stored
     preferred-value flag anywhere.
  4. A content-only upload/idempotency model that makes contributions
     portable and safe to replay.
  5. An open, versioned implementation (API, client, self-host) with a
     test-and-migration discipline suitable for long-lived scientific
     data.

---

## 2. Design principles (the conceptual core — likely the most novel section)

### 2.1 The four roles every table plays
- Table (reuse from `system_flow.md`): Identity / Provenance / Result /
  Trust — question answered, deduped?, examples.
- **Identity** (deduped): `species`, `species_entry`, `chem_reaction`,
  `reaction_entry`, `transition_state`, `conformer_group`.
- **Provenance** (append-only): `calculation` + typed result tables,
  `conformer_observation`, `software_release`, `level_of_theory`.
- **Result** (append-only): `thermo`, `statmech`, `kinetics`,
  `transport`, `network`.
- **Trust/curation** (overlay): `submission`, `record_review`,
  `record_machine_review`.
- **Claim:** keeping these separate makes most design questions answer
  themselves; illustrate with 2–3 "where does X go?" examples.

### 2.2 Identity vs. entry — what counts as "the same thing"
- `species` = molecular graph identity; `species_entry` = a resolved
  scientific form (stereochemistry, electronic/spin state, isotopologue,
  stationary-point kind).
- **Chemistry emphasis:** why identity is (canonical SMILES, charge,
  multiplicity) rather than InChIKey alone (DR-0031):
  - Standard InChI does not encode spin state → singlet vs triplet CH₂,
    singlet vs triplet O₂, open-shell singlets must be distinguishable.
  - Standard InChIKey merges some tautomers (2-pyridone / 2-hydroxy-
    pyridine) that have different structures and very different thermo.
  - InChIKey retained as a non-unique search index for interoperability.
- Same split for reactions (graph-level stoichiometry vs resolved
  entry) and transition states (saddle-point concept vs candidate
  geometry).

### 2.3 Append-only provenance and results — history as a first-class citizen
- Re-running a calculation creates new rows; nothing is overwritten.
- No "preferred"/"selected" columns on result tables (contrast with the
  common anti-pattern).

### 2.4 Trust as a separate overlay
- Human review state machine (not_reviewed → under_review → approved /
  rejected / deprecated); creators cannot approve their own records.
- Deterministic, read-time trust computed from evidence (review status +
  completeness) rather than a stored score.
- Optional machine review / LLM precheck are advisory, append-only, and
  firewalled from public trust output.

### 2.5 Read-time, policy-based selection (a novel governance idea)
- No stored "best value." A read gathers candidates and *sorts* them by
  an explicit, echoed policy (e.g. thermo: covers requested T-range →
  smaller extrapolation → higher review status → higher evidence
  completeness → newer).
- Why: "best" is a policy, not a property; stored flags rot and demand
  re-curation as data grows; a read-time sort is always current,
  explainable, and reproducible.

### 2.6 Content-only, portable contributions
- Upload payloads carry scientific content (SMILES, XYZ, energies,
  parameters), never database FK IDs.
- Identity resolution/dedup happens server-side; the same payload means
  the same thing on any deployment.
- Idempotency keys make re-submission safe (network retries, replays).

---

## 3. Scientific data model (the chemistry-facing section)

### 3.1 Molecular and reaction identity
- Species identity, stereochemistry classification on the graph
  (DR-0018), charged/open-shell handling, isotopologues.
- Reactions: elemental-balance enforcement at ingestion, stoichiometry
  hashing, reaction families, reversibility, degeneracy.
- Transition states: identity without InChI; status lifecycle (guess →
  optimized → validated → rejected); IRC/NEB validation evidence.

### 3.2 Conformers (identity vs. observation)
- `conformer_group` (identity) vs `conformer_observation` (provenance).
- **Chemistry emphasis:** torsional-basin fingerprinting for grouping
  (DR-0005); cross-method consistency lives at the group level; versioned
  assignment schemes allow re-grouping without falsifying history.

### 3.3 Calculations and raw quantum-chemistry data
- The `calculation` hub + typed result tables (sp/opt/freq/scan/irc/
  path-search/composite).
- Content stored: electronic energies (hartree), per-mode vibrational
  frequencies (signed for imaginary modes), ZPE, scan PES point-by-point,
  IRC/NEB profiles.
- **Reproducibility diagnostics:** SCF stability (no-row = not-checked
  semantics), T1/D1 wavefunction diagnostics, geometry-graph-isomorphism
  validation (guards the "optimized into a different isomer" failure).
- **Hessian storage (this work, DR-0030):** the Cartesian second-
  derivative matrix as a geometry-bound side table; why the geometry
  binding is mandatory (orientation/atom-order dependence); enables
  rotor re-projection, isotopologue re-analysis, VPT2 restarts,
  ML-ready force-constant data; raw `.hess`/`.fchk` also retained as
  byte-exact artifacts.
- **Calculation DAG:** downstream results cite the upstream calculations
  they consumed; opportunistic but what makes a result reproducible from
  inside the database.
- **Artifacts:** raw ESS logs/checkpoints in content-addressed object
  storage, SHA-256-verified.

### 3.4 Provenance vocabularies
- Level of theory (method + basis + dispersion + solvation + auxiliary),
  with (this work) a normalization layer and an explicit spin-treatment
  (R/U/RO) axis for reproducibility (DR-0033 — LOT integrity).
- Software/software-release and workflow-tool/workflow-tool-release
  (TCKDB is workflow-tool agnostic; ARC is one producer).
- Literature with DOI/ISBN metadata resolution.

### 3.5 Scientific products
- **Statmech:** partition-function inputs — symmetry number, point group,
  rotor treatments (RRHO through 1D/ND hindered rotors), frequency scale
  factor pinned to a literature-sourced registry (NULL=unknown,
  1.0=explicitly unscaled), (this work) optical-isomer count and
  electronic energy levels (DR — statmech completeness).
- **Thermo:** ΔfH°298, S°298, Cp/H/S/G points, NASA polynomials; energy
  corrections as a two-layer design (reference parameter library vs
  applied-correction results with per-bond/atom itemization).
- **Kinetics:** (modified) Arrhenius with enum-backed units, temperature
  validity ranges, uncertainty semantics (multiplicative vs additive),
  (this work) tunneling model as an enum, pressure context (k∞ vs
  apparent-at-P), falloff/Troe + third-body efficiencies, and standalone
  PLOG/Chebyshev fits (DR-0032).
- **Transport:** Lennard-Jones and related parameters.
- **Pressure-dependent networks:** wells, channels, master-equation
  solves, and the fitted k(T,P) tied to the physical model that produced
  them (DR-0001).

### 3.6 Units policy
- Fixed-unit columns for canonical quantities; enum-backed units only
  where dimensionality genuinely varies; free-text units banned for
  scientific values.

---

## 4. System architecture and lifecycle

- End-to-end path (reuse the `system_flow.md` narrative and figure):
  upload → submission → identity/provenance resolution → append-only
  results → curation overlay → read-time product selection.
- Submissions as the reviewable unit; sync vs async (worker) ingestion.
- Public refs (stable handles) vs internal PKs (hidden by default).
- Auth/roles (machine API keys + human sessions), deployment modes
  (laptop / self-hosted lab node / hosted community), hosted-mode
  safety guards.
- Figure: the four-bucket lifecycle diagram (already in repo).

## 5. Implementation and engineering for longevity

- Stack: PostgreSQL + RDKit cartridge, SQLAlchemy 2, Alembic, Pydantic v2,
  FastAPI.
- Migration discipline (append-only for deployed tables); test discipline
  (thousands of tests; migration-built test DB; CI with lint + type +
  OpenAPI-contract snapshot).
- Client library and ARC integration (consumes producer output files,
  not live objects); offline contribution bundles + replay.
- **Interoperability adapters (formats at the edges, science in the
  core).** A content-only upload API means each producer format is a
  thin edge adapter that translates native output → validated content:
  - CHEMKIN import: parse `chem.inp` + RMG adjacency-list dictionary +
    `tran.dat` client-side, resolve species identity to canonical SMILES,
    and POST content (never the raw file) — one adapter covers any tool
    that emits CHEMKIN (RMG, EStokTP, …).
  - Bulk export: NDJSON (lossless, re-ingestible) and CHEMKIN
    (`chem.inp`/`therm.dat`/`tran.dat`) regenerated from stored content,
    with read-time selection collapsing append-only candidates to one
    value per record.
  - **Export validity as a guarantee, not a hope:** exported mechanisms
    are validated by loading them through Cantera (strict `ck2yaml` +
    `Solution`), so the database never hands a user a file a downstream
    interpreter would reject (undeclared duplicates, malformed NASA cards).
- Open source; versioned API; self-hosting runbook.

## 6. Case studies / demonstrations (pick 2–3 that showcase the thesis)

- **(a) Traceability:** take one published thermo value in TCKDB and walk
  the full chain to the SHA-256 of the originating log file; show the
  itemized bond-additivity correction sum.
- **(b) Representable disagreement:** two independent computations of the
  same species' ΔfH at different levels of theory coexisting; show the
  read-time selection policy choosing between them and the client
  retrieving both.
- **(c) A spin-state / tautomer pair** that a legacy InChIKey-keyed store
  would merge, kept correctly distinct (validates the identity design).
- **(d, optional) Round-trip an ARC project** end to end (upload →
  curate → query) to demonstrate the producer workflow.
- **(e) CHEMKIN interoperability round-trip (validates the
  "database as an interoperability layer" claim):** take a real,
  unmodified RMG mechanism (an ammonia–methane oxidation model emulating
  García-Ruiz et al. 2024), import it (species resolved from RMG
  adjacency lists to canonical SMILES; modified-Arrhenius, Troe falloff +
  third-body efficiencies, Chebyshev P-dependence, duplicates, NASA-7
  thermo, transport), store it, then export a mechanism that loads
  cleanly in Cantera. Note honestly that building this surfaced real
  format-dialect bugs (the `THERM ALL` keyword, `(+M)`-notation
  Chebyshev, fixed-column NASA cards, undeclared duplicates) that only a
  full store-and-export-and-reload cycle — not a parser round trip —
  exposes; the point is that TCKDB can sit as a curation/provenance layer
  over the CHEMKIN-based tooling the community already uses.

## 7. Comparison to existing resources (table)

- Columns: provenance-as-data? / append-only? / representable
  disagreement? / re-computable from DB? / open API? / self-hostable? /
  covers thermo+kinetics+transport+PDep? / raw-data (Hessian/log) links?
- Rows: TCKDB, ATcT, RMG-database, Burcat, NIST CCCBDB, group-additivity
  tools. (Be fair and accurate; cite each.)

## 8. Limitations and future work (candid — referees respect this)

- Current scientific gaps and roadmap (from the internal assessment):
  - Falloff/PLOG breadth (being addressed; note what's landed vs planned).
  - Anharmonic/VPT2 data; spin contamination ⟨S²⟩; excited states;
    solvation thermochemistry; reference-state metadata (0 K vs 298 K,
    standard-state pressure) — enumerate honestly.
  - Uncertainty quantification and cross-record propagation.
- Community/adoption surface (state what's shipped vs planned):
  bulk export **shipped** (NDJSON + Cantera-validated CHEMKIN) and
  vocabulary-discovery endpoints **shipped**; a contributor/web UI and a
  server-side file-ingestion endpoint remain planned; broader producer
  adapters beyond ARC/CHEMKIN (e.g. a native EStokTP path) planned.
- Operational: distributed rate limiting, packaging, automated backups.

## 9. Availability and governance

- License; repository; API base URL; client install; DOI for the dataset
  snapshot (mint a Zenodo DOI for the release).
- Contribution model, review/curation policy, versioning of the schema
  and the data.

## 10. Conclusion

- Restate the thesis: storing the *argument*, not just the number, and
  keeping identity/provenance/result/trust separate, yields a database
  that can host contested, evolving, expensive scientific claims — a
  template beyond chemistry.

---

## Figures / tables to prepare

1. The four-role taxonomy table (identity/provenance/result/trust).
2. Lifecycle diagram (upload → … → read-time selection) — exists in repo.
3. Provenance-chain figure: thermo value → calculations → LOT/software/
   artifacts → geometry → conformer (one real example).
4. Energy-correction itemization example ("6 × C–H × −0.11 = −0.66").
5. Comparison table (§7).
6. ER-style schematic of the core tables grouped by role (optional).

## Notes on framing / novelty (for the cover letter)

- The novelty is **architectural and governance**, not a new method or a
  new dataset per se: provenance-as-data, append-only history,
  representable disagreement, read-time policy selection, content-only
  portable contributions.
- Position as infrastructure the whole community can build on and
  contribute to — "first of its kind" specifically in *keeping the four
  roles separate* and making *re-computation from inside the database*
  possible.
