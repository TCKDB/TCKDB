# CCCBDB → TCKDB Importer (Design Spec)

**Status:** design only — no implementation
**Date:** 2026-05-20
**Scope:** TCKDB backend / schema only. No ARC changes, no `tckdb-client`
changes, no frontend work. Importer code does not yet exist.
**Companion to:**
- [../../schema_spec.md](../../schema_spec.md)
- `unit_policy.md`
- `literature_policy.md`

---

## 1. Purpose

Design a careful, provenance-preserving importer for the NIST
**Computational Chemistry Comparison and Benchmark Database** (CCCBDB,
Standard Reference Database 101, Release 22, May 2022,
DOI `10.18434/T47C7Z`).

CCCBDB is an interactive web UI organized as:

```text
choose a property  ->  choose molecule(s)  ->  choose method + basis
```

It exposes both **experimental** reference data (thermochemistry,
geometries, frequencies, rotational constants, dipoles, point groups,
identifiers) and **calculated** data spanning many method/basis
combinations, plus comparison pages. There is no documented bulk public
API; pages are HTML and access policy is implicit.

The first implementation is a small, careful **pilot** importer for
~10–20 species, not a full-database scrape. The pilot is designed to:

```text
1. Validate that CCCBDB content maps cleanly onto TCKDB's existing
   identity / structure / provenance / scientific-product layers.
2. Surface real schema gaps before they get hacked into the wrong table.
3. Establish the source-snapshot and dedupe story before scale.
4. Produce TCKDB upload/service payloads, not direct ORM writes.
```

Citation requirement: every record produced by this importer carries
provenance back to CCCBDB Release 22 (DOI `10.18434/T47C7Z`) and the
specific source URL/page kind. CCCBDB itself must be represented as a
named external data source, not as anonymous "imported" data.

---

## 2. Design principles

```text
1. Preserve CCCBDB as external provenance, not anonymous imported data.
2. Import records as provenance-bearing observations / scientific
   products, attached to TCKDB's existing identity layer.
3. Keep experimental and computed records distinct (via
   ScientificOriginKind).
4. Keep different (method, basis) results distinct (via
   level_of_theory).
5. Store raw source snapshots or raw parsed payloads for
   reproducibility.
6. Normalize units into TCKDB's fixed-unit columns where possible
   (kJ/mol, J/mol/K, cm^-1, hartree, K, ...). See unit_policy.md.
7. Do not force unrelated properties into existing tables just because
   fields happen to fit (e.g. do NOT use `transport` for dipole or IE).
8. Keep the first importer pilot small and testable.
9. Prefer generating TCKDB upload/service payloads (workflow layer)
   over direct ORM writes.
10. Document legal / rate-limit / robots constraints before any
    broader crawl, and only crawl an explicit allowlist in the pilot.
```

These mirror the existing TCKDB invariants documented in
`schema_spec.md`: identity vs result vs provenance vs curation are
separate; no FK IDs in upload schemas; three-layer resolution (upload
schema → workflow → service).

---

## 3. Out of scope (non-goals)

```text
- Full CCCBDB scrape.
- Live crawler in tests.
- Bypassing TCKDB validation / writing directly to ORM as the primary
  pathway.
- Importing every method/basis combination.
- Importing every CCCBDB property in v1.
- Frontend work, ARC changes, tckdb-client changes.
- LLM parsing of CCCBDB pages.
- Transition-state-specific CCCBDB content.
- Comparison ("expt vs calc") pages — those are derived; we import
  primary records and let TCKDB do the comparison.
```

---

## 4. Source pages

The importer parser layer must understand at least the following
CCCBDB page classes. URLs are illustrative; the actual crawl plan
(§9) pins specific entry-point URLs.

```text
intro / citation:
  - the CCCBDB "How to cite" / release page
    (release number, year, DOI 10.18434/T47C7Z).
  - parsed once per release into a single `external_source` record.

species index / search:
  - formula search
  - name search
  - browse-by-property entry points

experimental species pages (one URL per species):
  - molecular identity table: name, other names, formula, InChI,
    InChIKey, SMILES, charge (often implicit 0), multiplicity
    (when present or inferable from electronic state label),
    state / conformation raw label.
  - experimental thermochemistry: Hf(0 K), Hf(298.15 K),
    S(298.15 K), Cp(T), integrated heat capacity 0->298.15 K,
    per-value uncertainties, per-value source-reference labels.
  - experimental statmech: point group, rotational constants
    (A,B,C or B), vibrational fundamentals, harmonic
    frequencies (when present), ZPE (when present).
  - experimental geometry: cartesian (when available), internal
    coordinates / bond lengths / angles (when available).

calculated pages:
  - per (species, method, basis) cells for:
      energy
      optimized geometry
      vibrational frequencies
      rotational constants
      ZPE
  - method/basis selection pages enumerate the axes for those cells.

later-phase pages (NOT in pilot, documented for completeness):
  - dipole / polarizability / quadrupole pages
  - IE / EA / PA pages
  - atomization energy pages
  - HOMO / LUMO pages
  - comparison pages
```

Example concrete species pages the pilot targets include the
experimental pages for H2, H2O, benzene, and similar small molecules,
which expose identifiers (InChI/InChIKey/SMILES), state/conformation
labels, and thermochemical values directly.

---

## 5. Pilot scope

### 5.1 Pilot species (~14)

Chosen to span chemical families exercised by TCKDB:

```text
H2          # diatomic, closed shell
H2O         # nonlinear closed shell
O2          # diatomic open shell (triplet)
N2          # diatomic closed shell
CO          # heteronuclear diatomic
CO2         # linear closed shell
CH4         # tetrahedral closed shell
CH3         # methyl radical
OH          # diatomic radical
HO2         # bent radical
NH3         # pyramidal closed shell
C2H2        # linear
C2H4        # planar pi system
C2H6        # ethane
CH3OH       # methanol
C6H6        # benzene
C6H5        # phenyl radical
```

This intentionally exercises:

```text
atoms / diatomics (H2, N2, O2, CO, OH)
linear polyatomics (CO2, C2H2)
nonlinear closed-shell (H2O, NH3, CH4, C2H4, C2H6, CH3OH)
radicals (CH3, OH, HO2, C6H5)
aromatics (C6H6, C6H5)
```

### 5.2 Pilot properties (first implementation)

The first importer implementation attempts only:

```text
species identity:
  formula
  preferred name
  other names
  InChI
  InChIKey
  SMILES
  charge
  multiplicity (when CCCBDB states it or it is inferable from the
    electronic-state label; otherwise import as identity-only and let
    a follow-up curation step set multiplicity)
  electronic state / conformation: raw label kept verbatim

experimental thermo:
  Hf(0 K)
  Hf(298.15 K)
  S(298.15 K)
  Cp(298.15 K)
  integrated heat capacity 0 -> 298.15 K
  per-value uncertainty (if given)
  per-value source-reference label (kept verbatim)

experimental statmech:
  point group
  rotational constants
  vibrational fundamentals
  harmonic frequencies (when present)
  ZPE (when present)

experimental geometry:
  cartesian coordinates or internal coordinates when available

computed data (pilot: one or two method/basis combos only,
e.g. B3LYP/6-31G(d) and CCSD(T)/cc-pVTZ where present):
  method
  basis
  optimized geometry
  electronic energy
  ZPE
  calculated vibrational frequencies
  rotational constants
```

### 5.3 Later phases (NOT in pilot)

```text
ionization energy (IE)
electron affinity (EA)
proton affinity (PA)
atomization energy
HOMO / LUMO
quadrupole tensors
full polarizability tensors
comparison pages
all CCCBDB methods / all basis sets
transition-state-specific CCCBDB content
```

---

## 6. TCKDB Mapping

### 6.1 Identity layer

```text
CCCBDB molecule identity
  -> species          (graph identity: InChIKey -> InChI -> SMILES)
  -> species_entry    (resolved scientific meaning, multiplicity,
                       charge, electronic state label)
```

`species_entry.scientific_origin = experimental` is **not** the right
field here — that enum lives on the *product* tables (thermo,
statmech, transport, conformer_observation), not on identity. The
species_entry is a neutral identity object; CCCBDB-experimental vs
CCCBDB-computed only matters on the product attached to it.

### 6.2 Experimental products

```text
CCCBDB experimental thermochemistry
  -> thermo(scientific_origin="experimental")
       Hf(0 K), Hf(298.15 K), S(298.15 K) on the thermo row.
  -> thermo_point rows for additional temperature-specific values
     (e.g. Cp(T), integrated heat-capacity series, S(T) if present).

CCCBDB experimental statmech / frequencies / symmetry
  -> statmech(scientific_origin="experimental")
       point_group, external_symmetry, symmetry_number
         populated directly.
  -> vibrational modes: see §7 — mode-level frequencies for
     experimental data are a real schema gap.

CCCBDB experimental geometry
  -> geometry  (cartesian, hashed)
  -> conformer_observation(scientific_origin="experimental")
       attached to species_entry, with provenance pointing at the
       external_source_record (§8), not at a calculation.
```

### 6.3 Computed products

```text
CCCBDB computed method/basis
  -> level_of_theory   (normalized method + basis + keywords)
       resolved/created by the existing LoT resolution service.

CCCBDB computed geometry / energy / frequencies
  -> calculation(type ∈ {opt, sp, freq})
       species_entry_id resolved via identity layer.
       level_of_theory_id resolved as above.
       software / software_release: see §6.4.
  -> calculation_output_geometry      (per opt)
  -> calc_opt_result                  (per opt)
  -> calc_sp_result                   (per sp; or fold into opt when
                                       SP/Opt LoTs match — see
                                       feedback_sp_vs_opt_energy)
  -> calc_freq_result + calc_freq_mode rows
                                      (per freq; mode-level table
                                       already exists)
  -> thermo_source_calculation
        when CCCBDB exposes computed Hf / S / Cp at this LoT.
  -> statmech_source_calculation
        when CCCBDB exposes computed statmech at this LoT.
```

### 6.4 Software / workflow_tool provenance

CCCBDB does not always state the exact ESS package and version that
produced a row. Where it does, populate software / software_release.
Where it does not:

```text
- Do NOT invent a software_release.
- Set calculation.software_release_id = NULL.
- Record the absence as a `source_note` on the external_source_record
  rather than papering over it.
```

This is consistent with the existing rule that provenance must not be
fabricated.

### 6.5 Literature

CCCBDB cites primary references on most experimental values. The
importer should:

```text
- Capture the source-reference label verbatim on each value
  (string), regardless of resolution success.
- Where the label can be confidently resolved to a DOI/ISBN
  (deferred; not in pilot), create literature rows under the existing
  literature_policy.md workflow.
- Do NOT block import on literature resolution; unresolved labels
  are still useful provenance.
```

---

## 7. Schema gaps

The importer surfaces gaps in the current schema. Each is documented
here as a **gap**, not as part of the pilot. Closing any of these
requires a new Alembic migration revision (and remember the
single-initial-migration rule still in force — fold into
`d861dfd60891` until the schema is finalized).

### Gap 1 — Molecular scalar/vector/tensor properties

CCCBDB exposes a family of properties that do not belong in `thermo`,
`statmech`, or `transport`:

```text
dipole moment             (scalar magnitude + vector)
quadrupole tensor
polarizability tensor (and isotropic average)
HOMO / LUMO energies
ionization energy
electron affinity
proton affinity
atomization energy
```

Recommendation: introduce a general property-observation table,
working name **`molecular_property_observation`**, with shape roughly:

```text
species_entry_id           FK -> species_entry
scientific_origin          ScientificOriginKind
property_kind              enum (dipole_magnitude, dipole_vector,
                                 quadrupole_tensor, polarizability,
                                 polarizability_iso, homo, lumo,
                                 ionization_energy, electron_affinity,
                                 proton_affinity, atomization_energy,
                                 ...)
value_scalar               nullable double
value_vector               nullable double[3]      (e.g. dipole)
value_tensor               nullable double[3][3]   (e.g. quadrupole)
uncertainty                nullable double
units                      fixed per property_kind (debye, hartree,
                           kJ/mol, eV depending on property_kind)
temperature_k              nullable double  (e.g. IE at 298 K)
level_of_theory_id         nullable FK
calculation_id             nullable FK
external_source_record_id  nullable FK
notes                      nullable text
```

Explicitly **do not** use `transport` for these. Transport already has
`scientific_origin` but is semantically Lennard-Jones / collision /
diffusion data, not electrostatics.

This table is deferred past the pilot. The pilot does not import any
of these properties.

### Gap 2 — Mode-level frequencies for experimental data

Computed mode-level frequencies are already supported via
`calc_freq_mode`. Experimental vibrational fundamentals do not have
an equivalent home — `statmech` records point group and summary
fields but not per-mode frequencies.

Options, in increasing cost:

```text
A. Defer mode-level experimental persistence; keep only summary
   fields in statmech for the pilot; preserve full list in the raw
   payload JSON on the external_source_record.
B. Add a `statmech_frequency_mode` table (mode_index, frequency_cm1,
   symmetry_label, is_harmonic vs fundamental).
C. Reuse the molecular_property_observation table with
   property_kind=vibrational_fundamental, indexed by mode_index.
```

Pilot recommendation: **option A**. Re-evaluate after the pilot when
real consumption patterns are known.

### Gap 3 — Rotational constants

Currently statmech does not have first-class A/B/C rotational
constant columns. Options:

```text
A. Add rotational_constant_a_mhz, _b_mhz, _c_mhz nullable columns to
   statmech (one row, three nullable scalars).
B. Add a dedicated `rotational_constants` table keyed by statmech_id
   for richer provenance.
C. Push them into molecular_property_observation under
   property_kind=rotational_constant_a/b/c.
```

Pilot recommendation: **option A** if/when we accept the
molecular_property_observation table; otherwise hold rotational
constants in raw payload JSON during the pilot. They are routinely
needed for partition functions, so this gap should be closed soon
after the pilot lands.

### Gap 4 — External source snapshots

The importer needs first-class source tracking, separate from
`literature`. Propose:

```text
external_source
  id                         PK
  source_name                "CCCBDB"
  source_release             "Release 22, May 2022"
  source_database_doi        "10.18434/T47C7Z"
  citation_text              raw "how to cite" string

external_source_record
  id                         PK
  external_source_id         FK -> external_source
  source_url                 text
  source_record_key          text   (e.g. species + page_kind + slot)
  page_kind                  enum (species_index, experimental_species,
                                   calculated_energy, calculated_geometry,
                                   calculated_frequency, electrostatics,
                                   citation_page, ...)
  retrieved_at               timestamptz
  http_status                int
  content_sha256             text
  raw_html_uri               text   (path / object-store URI to the
                                     raw HTML snapshot)
  parsed_json                jsonb  (normalized, parser-version-stamped)
  parser_name                text
  parser_version             text
  source_notes               text
```

Then thermo / statmech / calculation / future
molecular_property_observation rows can carry an
`external_source_record_id` (nullable, in addition to existing
`literature_id` / `calculation_id` provenance).

This table is **required** before the pilot promotes anything to a
shared TCKDB instance. For the very first parser-only prototype
(Phase 1 below), `parsed_json` can live as files on disk; the table
is introduced before Phase 2.

---

## 8. Provenance requirements

Every imported record must preserve enough provenance to answer:

```text
Where did this value come from?         external_source_record.source_url
Which CCCBDB release?                   external_source.source_release
                                        + .source_database_doi
Which molecule / page?                  external_source_record.source_record_key
                                        + .page_kind
Which property table on the page?       external_source_record.parsed_json
Which method/basis if computed?         level_of_theory + calculation
Which original units?                   parsed_json["raw_units"]
Which source reference label?           parsed_json["source_ref_label"]
                                        + future literature link
When was it retrieved?                  external_source_record.retrieved_at
Which parser version produced it?       parser_name + parser_version
What raw payload produced this row?     content_sha256 + raw_html_uri
                                        + parsed_json
```

No imported row may exist without an `external_source_record_id`
(except `species` / `species_entry` rows, which can be shared with
non-CCCBDB origins and instead carry the provenance on the attached
product rows).

---

## 9. Importer architecture

Importer code lives under:

```text
backend/app/importers/cccbdb/
  __init__.py
  client.py
  crawl_plan.py
  parsers/
    species_index.py
    experimental_species.py
    calculated_energy.py
    calculated_geometry.py
    calculated_frequency.py
    electrostatics.py            # later phase
  normalizers/
    identity.py
    units.py
    level_of_theory.py
    references.py
  builders/
    thermo_payload.py
    statmech_payload.py
    calculation_payload.py
    property_payload.py          # later phase, paired with Gap 1
  tests/
    fixtures/
      h2_experimental.html
      h2o_experimental.html
      benzene_experimental.html
      ...
    test_parsers.py
    test_normalizers.py
    test_builders.py
    test_idempotency.py
```

Responsibilities:

```text
client.py:
  - polite HTTP fetch only (requests / httpx).
  - explicit User-Agent identifying TCKDB + contact email.
  - per-request timeout, capped retries, exponential backoff.
  - global rate limit (e.g. 1 request / 2 seconds) configurable.
  - on-disk response cache keyed by URL + content hash.
  - never used in tests against the live site.

crawl_plan.py:
  - explicit allowlist of pilot species pages.
  - explicit allowlist of (method, basis) cells per species.
  - generates the planned URL set; nothing else is crawled.

parsers/*.py:
  - parse CCCBDB HTML into raw structured records.
  - no normalization here; preserve raw labels and raw units verbatim.
  - emit `parsed_json` shape consumed by normalizers.
  - tolerate missing values without crashing.

normalizers/*.py:
  - units: kJ/mol, J/mol/K, cm^-1, hartree, K, Debye (later).
  - identity: trim/canonicalize InChI strings; compute/verify
    InChIKey; canonicalize SMILES.
  - level_of_theory: map raw CCCBDB method/basis strings to the
    canonical LoT identity (e.g. "B3LYP" + "6-31G*" -> LoT row).
  - references: keep CCCBDB source-reference labels verbatim;
    optional later resolution to DOI.

builders/*.py:
  - construct TCKDB **upload-schema payloads** (workflow layer),
    not ORM rows.
  - emit one payload per logical record (thermo, statmech,
    calculation) attached to the correct species_entry.
  - attach external_source_record_id to each payload.

tests/:
  - fixture-driven; HTML fixtures are sanitized snippets of real
    pages, checked into the repo (see feedback_internalize_external_data).
  - no live network in CI.
```

---

## 10. Crawler constraints

```text
- No full-site crawling in pilot.
- Only allowlisted pilot URLs are fetched.
- Respect robots/legal constraints if any are published; document the
  check in the spec PR.
- Use a clear User-Agent string identifying the project and a
  contact email.
- Use a low request rate (default <= 0.5 req/sec, configurable).
- Cache responses locally; never re-fetch within a parser test.
- Record content_sha256 on every fetched response.
- Parser tests use checked-in / sanitized HTML fixtures, not live
  network.
- Any later crawl expansion lives behind a configuration change and
  documented review.
```

---

## 11. Idempotency and dedupe

Every importer write path must be safely re-runnable. Dedupe keys:

```text
external_source_record:
  (external_source_id, source_url, page_kind, content_sha256)
  -> identical fetch is a no-op; new content_sha256 inserts a new
     record so we keep history.

species identity:
  primary:   InChIKey (when CCCBDB exposes it; almost always for
             pilot species).
  fallback:  normalized InChI.
  last:      (formula, charge, multiplicity) — only after explicit
             human review; never auto-merge on this fallback.

experimental thermo:
  (species_entry_id, property_kind, temperature_k,
   external_source_record_id)
  - same value at same temperature from same source = same row.
  - same value from a new source = new row (do not merge across
    sources; that's a curation decision, not an import-time one).

experimental statmech:
  (species_entry_id, external_source_record_id)
  - one experimental statmech row per (species_entry, source).

geometry:
  geometry hash over normalized XYZ
  -> identical coordinates collapse to one geometry row regardless
     of which CCCBDB page brought them in.

computed calculation:
  (species_entry_id, calculation_type, level_of_theory_id,
   external_source_record_id)
  - CCCBDB rarely reports run-level uniqueness, so source URL +
    page kind is treated as the run identifier.

level_of_theory:
  (normalized_method, normalized_basis, normalized_keywords_hash)
  - resolved through existing LoT service.
```

Re-running the importer over the same fixtures or the same cached
HTML must be a no-op at the DB level (after the first run).

---

## 12. Tests

All implementation work in later phases must satisfy at least these
tests. Tests are **fixture-driven**; no live HTTP in CI.

### 12.1 Parser tests

```text
- parse identifiers from H2O experimental page fixture
  (name, formula, InChI, InChIKey, SMILES, point group).
- parse Hf(0 K) / Hf(298) / S(298) / Cp from H2 fixture, including
  per-value uncertainty and per-value source-reference labels.
- parse benzene fixture: identifiers + thermochemistry +
  rotational constants.
- parse a fixture with missing uncertainty without crashing.
- parse a fixture with blank value cells without inventing a value.
- preserve raw source-reference labels verbatim.
```

### 12.2 Normalizer tests

```text
- kJ mol^-1, kJ/mol, "kJ mol-1" all normalize to the same canonical
  unit ("kJ/mol" internally, value unchanged).
- J K^-1 mol^-1 normalizes to "J/mol/K".
- cm^-1 frequencies normalize to a single canonical token.
- methods: "B3LYP", "b3lyp", "B3LYP/6-31G*", "B3LYP/6-31G(d)" map
  consistently into (method, basis) tuples; unknown methods raise
  a clear error rather than silently dropping data.
- unsupported units raise a typed error with the offending string.
```

### 12.3 Builder tests

```text
- experimental thermo payload: a fixture-derived value yields the
  expected thermo upload payload, with scientific_origin=experimental,
  external_source_record_id set, and no calculation linkage.
- experimental statmech payload: point group + symmetry number
  populated; vibrational modes either deferred (pilot) or attached
  to the chosen mode-level table (post-Gap-2).
- computed calculation payload: yields a workflow payload that the
  existing services can persist as opt + sp + freq with shared
  level_of_theory.
- attaches source metadata to every payload.
- non-transport electrostatics (later phase): never produces a
  transport row; produces a molecular_property_observation payload
  or fails closed if that table doesn't exist yet.
```

### 12.4 Idempotency tests

```text
- same fixture parsed twice -> same dedupe keys, no duplicate rows.
- same XYZ coordinates parsed twice -> same geometry hash.
- same (method, basis) string -> same level_of_theory identity.
- changed content_sha256 -> new external_source_record row.
```

---

## 13. Implementation phases

```text
Phase 0  (this document)
  Spec only. No code. Identify gaps. Identify pilot scope.

Phase 1
  Fixture-based parser prototype for experimental species pages.
  No DB writes. Parsers + normalizers only. Tests run from
  checked-in HTML fixtures. Produces parsed_json on disk.

Phase 2
  external_source / external_source_record tables added (folded
  into the initial migration per the single-migration rule, until
  the schema is finalized).
  Pilot import for experimental thermo / statmech / geometry of the
  ~14 pilot species. Writes go through workflow-layer upload
  payloads, not direct ORM.

Phase 3
  Computed method/basis pilot for 1–2 LoTs (e.g. B3LYP/6-31G(d) and
  CCSD(T)/cc-pVTZ where available), producing calculation +
  calc_opt_result + calc_sp_result + calc_freq_result (+ modes).

Phase 4
  Schema migration to close accepted gaps:
    - molecular_property_observation (Gap 1) if approved
    - mode-level experimental frequencies (Gap 2) if approved
    - rotational constants on statmech (Gap 3) if approved
  Then extend importer to cover the corresponding properties.

Phase 5
  Broader, allowlist-expanded import with explicit monitoring,
  rate-limit metrics, and a process for adding species / LoTs to
  the allowlist. Still no full-site scrape.
```

Each phase is a separate PR and decision point. Phase 0 ships with
this document and no code.

---

## 14. Open questions

```text
Q1. Legal / terms of use: confirm CCCBDB's published use policy and
    cite the relevant page in Phase 1's PR description. If access is
    restricted to a manner the pilot does not satisfy, the pilot is
    blocked at Phase 1.

Q2. Multiplicity inference: CCCBDB sometimes states multiplicity only
    via an electronic-state label (e.g. "X 2Pi"). Should the pilot
    parse those labels into multiplicity automatically, or persist
    the raw label and leave multiplicity NULL? Recommendation:
    persist raw label, leave multiplicity NULL, defer to curation.

Q3. Where do raw HTML snapshots live? On-disk per environment vs
    object storage. Recommendation: on-disk under a configurable
    path during Phase 1; object storage decided before Phase 2.

Q4. How do we name the external_source release going forward?
    Recommendation: exactly mirror NIST's release string,
    "Release 22, May 2022", and add a new external_source row when
    NIST issues a new release.

Q5. Do we want a `cccbdb-fixtures` directory under
    `backend/app/importers/cccbdb/tests/fixtures/`, or a sibling
    `tests/data/cccbdb/` directory? Recommendation: colocate with
    the importer to match existing repo style.
```

---

## 15. Final report (Phase 0 deliverable summary)

```text
Spec file:      backend/docs/specs/cccbdb_importer.md (this file)
Pilot species:  ~14 small molecules (atoms/diatomics, polyatomics,
                radicals, aromatics).
Pilot props:    identity + experimental thermo + experimental
                statmech + experimental geometry + one or two
                computed LoTs for geometry/energy/freq.

Main design choices:
  - Treat CCCBDB as an external_source with explicit release/DOI,
    not anonymous "imported" data.
  - Use ScientificOriginKind on existing product tables
    (thermo / statmech / conformer_observation) instead of a parallel
    importer-only schema.
  - Reuse CalculationFreqMode for computed mode-level frequencies
    (already exists); leave experimental mode-level frequencies as
    an open schema gap.
  - Importer emits workflow-layer upload payloads, not ORM writes.
  - Polite, allowlisted, cached, fixture-tested crawler. No live
    network in CI.

Schema gaps identified:
  1. molecular_property_observation table for scalar/vector/tensor
     properties (dipole, quadrupole, polarizability, IE, EA, PA,
     HOMO, LUMO, atomization energy).
  2. Experimental mode-level frequencies have no first-class home
     (CalculationFreqMode is calculation-scoped).
  3. Rotational constants A/B/C have no first-class columns on
     statmech.
  4. external_source / external_source_record tables for raw
     source snapshotting and dedupe.

Recommended next prompt:
  "Phase 1: implement fixture-based CCCBDB experimental-page parser
   prototype, with sanitized H2 / H2O / benzene HTML fixtures, no
   DB writes, parser + identity normalizer + units normalizer + tests."
```
