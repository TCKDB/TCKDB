# tckdb-schemas

Pure Pydantic wire-contract schemas for TCKDB upload payloads.

This package exposes TCKDB's published upload request bodies (plus their
direct dependency closure) so external workflow tools and clients can
validate TCKDB public upload payloads without installing the full backend
(FastAPI, SQLAlchemy, RDKit, etc.).

| Route | Request model |
|---|---|
| `POST /api/v1/uploads/computed-species` | `ComputedSpeciesUploadRequest` |
| `POST /api/v1/uploads/computed-reaction` | `ComputedReactionUploadRequest` |
| `POST /api/v1/uploads/conformers` | `ConformerUploadRequest` |
| `POST /api/v1/uploads/transition-states` | `TransitionStateUploadRequest` |

All four are importable straight from `tckdb_schemas.workflows`.

Stability: the schemas mirror the backend's wire contract. Until the
TCKDB API hits 1.0, expect coordinated bumps with the backend. A contract
that lives here can be pinned, and a breaking change to it forces a
version bump a consumer can act on — which is the reason to put it here
rather than inside the server.

## Changelog

### 0.34.0 — a frequency list that numbers two modes the same says so by name

**Not breaking.** No field is added, renamed or removed, and the set of
payloads that validate is byte-for-byte the set that validated under
0.33.0 — **every 0.33.0 payload validates unchanged under 0.34.0**, and
every payload 0.33.0 refused is still refused. What changes is what a
client is *told* when one is refused.

`FreqResultPayload.validate_modes_consistency` has always refused a
`modes` list carrying the same `mode_index` twice. It refused with a bare
`ValueError`, so the 422 envelope reported `code =
"request_validation_error"` (or `"validation_error"`, depending on which
handler saw it) and a client wanting to distinguish "renumber your
frequency list" from any other validation failure had to match English.

It now raises `CodedValidationError` under the new module-level constant
`W_FREQ_MODE_INDEX_NOT_UNIQUE = "freq_mode_index_not_unique"`, with
`context = {"field", "duplicate_mode_indices", "mode_count"}`. The first
sentence of the message is byte-identical to the one 0.33.0 emitted
(`message_prefix=False`), so a client matching prose is undisturbed; the
duplicated indices and the repair follow it.

Minor rather than patch because the envelope's `code` field is part of
the published contract: a consumer may now branch on this refusal, which
it could not before.

**Deliberately not a scientific check.** Its neighbour
`freq_n_imag_disagrees_with_modes` (0.28.0) is declared in the backend's
scientific check register, because two fields of one record answering the
same question about chemistry differently is a position a referee could
argue with. A repeated `mode_index` is not: it is a malformed list — a
concatenated serialisation, a producer that restarted its counter — and
asserts nothing about the potential energy surface. It is catalogued in
`app.api.code_catalogue` and reaches the client enum from there, which is
the split that module exists for.

### 0.33.0 — **BREAKING**: `CalculationPayload.literature_id` is removed

Recorded after the fact: the version was published without a changelog
entry, and this note is reconstructed from the commit that made it
(#177). It says only what that change did.

`CalculationPayload.literature_id` is **removed** and replaced by
`literature`, the inline `LiteratureUploadRequest` fragment — the same
repair 0.32.0 made to `CalculationIn`, applied to the other shared
calculation payload. The generalised no-FK-ids gate that found it now
walks every upload root discovered from the live route table, rather than
the single root it used to check.

### 0.32.0 — **BREAKING**: a calculation cites literature inline, and the reaction bundle's 201 names its statmech

**Breaking:** `CalculationIn.literature_id` is **removed**. It was a
database primary key on an upload surface — supplying it required having
already queried this database, which a depositor has not — contradicting
"no FK IDs in upload schemas". It is replaced by `literature`, the same
inline `LiteratureUploadRequest` fragment
`CalculationInBundle.literature` has always taken on the species bundle,
resolved (and deduplicated) by the workflow.

`CalculationIn` is shared, so this lands on **two** routes:

| payload | 0.31.0 | 0.32.0 |
|---|---|---|
| `computed-reaction`, calculation with `literature_id: 7` | 201, FK stored | **422** — `extra="forbid"` names the field |
| `network-pdep`, calculation with `literature_id: 7` | 201, FK stored | **422** |
| either route, calculation with inline `literature: {...}` | field did not exist | 201, row resolved-or-created and attached |
| two calculations citing the same DOI | n/a | one `literature` row, shared |

**Migrating.** Replace `"literature_id": <n>` with the citation itself:
`"literature": {"kind": "article", "title": …, "doi": …}`. The workflow
resolves an existing row by DOI/ISBN or creates one, so repeating the
same fragment across calculations does not duplicate it. A calculation
that cited nothing needs no change. The failure is a 422 naming
`literature_id`, not a silent drop — deliberately, since silently
ignoring it would discard the citation.

`calculation_in_to_with_results_payload` gains a keyword-only
`literature_id` and **raises** if handed a calculation carrying a
`literature` fragment without one. The package cannot resolve literature
(it has no database), so the caller resolves and passes the id down;
defaulting to `None` would make "cited nothing" and "the workflow forgot"
the same value.

**Additive:** `ComputedReactionUploadResult` gains `statmech_ids` and
`atom_map_id`. Both were already computed by the workflow and discarded
by pydantic's default `extra="ignore"`, so a bundle depositing kinetics,
thermo *and* statmech got a 201 naming two of the three, and the atom map
it wrote had to be fetched back through the read API. The result model is
now `extra="forbid"` so the next such omission fails loudly.

**Documentation only, no shape change:** `BundleKineticsIn`'s docstring
now records the three scientific-content fields the standalone
`/uploads/kinetics` route has and it does not
(`interpretation_assignments`, `tunneling_application`,
`network_kinetics_ref`), with the git evidence that two of the three are
drift rather than design. No field was added or removed; closing those
gaps needs bundle-local-key schemas plus workflow persistence.

### 0.31.0 — **BREAKING**: `source_conformer_key` now points at something, on every route that accepts it

**A field that cannot be wrong is not a field.** Three upload paths
accepted a value, returned 201, and did nothing with it. All three are
now either persisted or refused, and the set of payloads that validate
narrows in three places — hence the major-ish bump on a pre-1.0 line.

| payload | 0.30.0 | 0.31.0 |
|---|---|---|
| species/reaction bundle, `source_conformer_key` naming a declared conformer | 201, link **not** stored | 201, link stored |
| species/reaction bundle, `source_conformer_key` naming nothing | 201, silently ignored | **422** `applied_energy_correction_source_key_undeclared` |
| `/uploads/conformers`, `source_conformer_key` equal to `label` | 201, resolved via the label | **422**, same code |
| `/uploads/conformers`, `source_conformer_key` equal to a new `conformer_key` | field did not exist | 201, link stored |
| reaction bundle, two conformers of one species sharing a `key` | 201 | **422** |
| species bundle root `workflow_tool_release` | accepted, dropped, *and* warned about as missing | persisted as the thermo/statmech default |

**New field: `ConformerUploadRequest.conformer_key`** (optional). It is
the conformer namespace's counterpart to `ConformerCalculationIn.key`.
`source_conformer_key` previously resolved against `label`, because a
label was the only string a conformer upload attached to its conformer.
That conflated two jobs: a label is a human tag that also feeds
conformer-group matching, so renaming it for grouping reasons silently
broke a correction reference, and a depositor with no label could not
name their own conformer at all.

**Migrating a conformer upload.** If you set `source_conformer_key` to
your own `label`, add `conformer_key` with that same string; `label` is
free to keep meaning what it means. If you set neither, nothing changes.

**Migrating a bundle.** A `source_conformer_key` that names a real
`conformers[*].key` needs no change — it now does what it always looked
like it did. One that names anything else was never recording a link and
now says so. On the reaction bundle the namespace is scoped to the
species the correction sits under: a sibling species's conformer is out
of scope, which is what makes ownership true by construction rather than
by a second check. A transition state in that bundle declares no
conformers at all, so a TS-side `source_conformer_key` can never resolve
and is refused rather than ignored.

**`ComputedSpeciesUploadRequest.workflow_tool_release` is now read.** It
is the bundle-level default the `thermo` and `statmech` blocks inherit
when they name no workflow tool of their own — the precedence
`ComputedReactionUploadRequest` has applied to `literature` /
`analysis_software_release` / `workflow_tool_release` since those fields
and their reader landed in one commit (2026-03-22). The species root
declared the same field five weeks later and no commit ever wired it, so
this is drift, not design. Nothing that omitted the field changes.

**`ComputedSpeciesUploadRequest.note` is still not persisted**, and now
says so in its own description. Unlike `workflow_tool_release` it has no
row to go to — the bundle owns no record of its own — so giving it one
is a decision about what a bundle-level note *is*, not a wiring
omission.

### 0.30.0 — **BREAKING**: a frequency list longer than `3N` is refused, not flagged

**This narrows the set of payloads that validate.** A payload accepted by
0.29.0 with a `freq_list_exceeds_geometry_degrees_of_freedom` warning is
refused by 0.30.0 with a 422 naming that same code. Nothing is added,
renamed or removed, and every *other* 0.29.0 payload validates unchanged
— but a rule that moves from warn to block is a breaking wire change and
is labelled one, whatever the shape of the diff.

| | 0.29.0 | 0.30.0 |
|---|---|---|
| `n_modes > 3N` | 201, `UploadWarning` with `structural_flag` | **422**, `code = freq_list_exceeds_geometry_degrees_of_freedom` |
| `n_modes == 3N` | 201, silent | 201, silent (unchanged) |
| `n_modes < 3N − 6` | 201, `UploadWarning` | 201, `UploadWarning` (unchanged) |
| `modes` omitted | 201, silent | 201, silent (unchanged) |

**Who is affected.** Only a deposit whose frequency list is longer than
the total number of Cartesian degrees of freedom of the geometry it is
attached to. The in-repo corpora were re-measured against this bound
specifically — 57 ARC statmech records and the 4 conformers of the
hydrazine pressure-dependent network — and **nothing is within six modes
of the ceiling**: the closest record sits at `3N − 6`, and the 13 records
that do violate a bound violate the *floor*, which still warns. No
existing deposit changes verdict.

**Why this one may block when its sibling may not.** `3N` is the
dimension of the nuclear coordinate space, so it is the total number of
eigenvalues the mass-weighted Hessian of an `N`-atom geometry has — every
Cartesian degree of freedom, the six rigid-body modes
[ADR 0012](https://github.com/TCKDB/TCKDB/blob/main/docs/adr/0012-imaginary-modes-are-judged-by-magnitude-not-counted.md)
asks a record to carry included. A longer list is not a short spectrum;
it is not a spectrum of that geometry under any harmonic treatment, grid,
coordinate system or level of theory, so no protocol takes part and no
correct deposit is refused. Both arguments that keep the *floor*
advisory fail here: every mechanism that makes a short list honest — a
partial Hessian, a frozen-atom or ONIOM region, a lumped participant —
*removes* modes, and nothing filters modes in; and deleting an over-long
list to evade the refusal loses a spectrum the check has already found is
not this geometry's, which is the repair rather than the evasion.
`freq_list_incomplete_for_geometry` is unchanged and stays a warning.

The tier now follows a scientific-check register entry
(`CHECK_FREQ_LIST_WITHIN_GEOMETRY_DEGREES_OF_FREEDOM`, ADR 0012) rather
than an implementation, which is what 0.29.0 said the promotion was
waiting for. `RejectionCode.FREQ_LIST_EXCEEDS_GEOMETRY_DEGREES_OF_FREEDOM`
is exported by `tckdb-client` 0.38.0.

**Migrating:** attach the calculation to the geometry it actually ran on,
or deposit the geometry it ran on. If the payload was relying on the
warning, the fix is the same fix it was advising.

### 0.29.0 — a frequency list is measured against the geometry it was computed on

**Not breaking.** No field is added, renamed or removed, and **every
0.28.0 payload validates unchanged under 0.29.0**: the set of payloads
that validate is byte-for-byte the set that validated before, and every
payload 0.28.0 refused is still refused. The new judgement is entirely at
the **warning** tier — it changes what an accepted upload is *told*,
never whether it is accepted.

[ADR 0012](https://github.com/TCKDB/TCKDB/blob/main/docs/adr/0012-imaginary-modes-are-judged-by-magnitude-not-counted.md)
§"What a record must carry" requires "the complete signed unrounded
frequency list, never filtered", and nothing checked it. A depositor
could send three modes, all imaginary, with `n_imag = 3` and pass every
rule the package had — `freq_n_imag_disagrees_with_modes` included,
because that rule compares the imaginary count against `n_imag` and a
list of nothing but imaginary modes satisfies it exactly. The real modes
were simply absent, and absence is silent.

New module `tckdb_schemas.frequency_completeness`, with two codes:

| code | when |
|---|---|
| `freq_list_incomplete_for_geometry` | the list is shorter than the smallest complete spectrum the geometry admits |
| `freq_list_exceeds_geometry_degrees_of_freedom` | the list is longer than `3N`, so it describes motion the geometry does not have |

Both are warnings carrying `structural_flag=True`, reported through the
existing `stationary_point_findings()` surface on every published upload
request model and reaching a client as an `UploadWarning` in the 201
body.

**The bounds are deliberately the weakest ones that are certainly true.**
Linearity is never determined: a collinear molecule has `3N - 5` modes,
one *more* than `3N - 6`, so comparing against `3N - 6` accepts every
linear molecule without choosing a collinearity tolerance. For `N ≤ 2`
linearity is a fact rather than a measurement, so those take exact counts
(`N = 1` → 0 modes, `N = 2` → 1). The ceiling is `3N` and not `3N - 6`
because ADR 0012 also asks for "the six translation/rotation eigenvalues
… so contamination is directly assessable" — a record carrying all `3N`
is the most complete record the ADR describes and must not be refused as
an over-count.

**Why warn and not block.** The check cannot tell a filtered list from a
genuinely shorter one: a partial-Hessian job, a frozen-atom or ONIOM
Hessian, and a lumped participant all produce fewer than `3N - 6` modes
and are correct records of what was computed. And `modes = null` is
accepted and must stay accepted, so a block's cheapest workaround is
deleting the frequency list — turning a partial list into no list at all,
which is the failure ADR 0012 §"Why not refuse, when refusing is cheaper"
names outright. The over-long case genuinely is definitional; it warns
here because promoting it to a refusal belongs in the scientific-check
register rather than in an implementation.

Minor rather than patch because two new machine-readable warning codes
enter the published vocabulary: a consumer may now branch on them, which
it could not before. Neither is a refusal code, so no client rejection
constant changes.

### 0.28.0 — a frequency result that contradicts itself says so by name

**Not breaking.** No field is added, renamed or removed, and the set of
payloads that validate is byte-for-byte the set that validated under
0.27.0 — **every 0.27.0 payload validates unchanged under 0.28.0**, and
every payload 0.27.0 refused is still refused. What changes is what a
client is *told* when one is refused.

`FreqResultPayload.validate_modes_consistency` has always refused a
deposit whose `n_imag` disagrees with the imaginary modes in its own
`modes` list — `n_imag = 3` beside a single imaginary row is a record
that answers "how many imaginary modes?" two different ways, and neither
of the two consumers reading it is told the other exists. It refused with
a bare `ValueError`, so the 422 envelope reported
`code = "validation_error"` and a client wanting to distinguish "your
frequency list is inconsistent" (fix the payload) from any other
validation failure had to match English.

It now raises `CodedValidationError` under the new module-level constant
`W_FREQ_N_IMAG_DISAGREES_WITH_MODES = "freq_n_imag_disagrees_with_modes"`,
with `context = {"n_imag", "imaginary_mode_count", "mode_count"}`. The
first sentence of the message is byte-identical to the one 0.27.0 emitted
(`message_prefix=False`), so a client matching prose is undisturbed;
guidance naming the escape hatch follows it.

Minor rather than patch because the envelope's `code` field is part of
the published contract: a consumer may now branch on this refusal, which
it could not before.

**The asymmetry is unchanged and is the point.** `modes = null` with any
`n_imag` is still accepted — absence is incompleteness, not
contradiction, which is the same position the read API takes when it
reports `n_imag_at_or_above_tau = null` rather than `0` for a record with
no frequency list. Only a list that *disagrees* is refused.

### 0.27.0 — a statmech source link may be unique on something other than its key

Additive, and a **relaxation**: nothing is renamed, removed or newly
refused, so **every 0.26.0 payload validates unchanged under 0.27.0**.
Minor rather than patch because the accepted set genuinely grows.

`ConformerUploadStatmechPayload.validate_unique_source_calculation_pairs`
now keys uniqueness on `(calculation_key, existing_calculation_id, role)`
rather than `(calculation_key, role)`, reading the second element
reflectively.

For a payload built from this package there is no behaviour change at
all: `StatmechSourceCalcIn` has no `existing_calculation_id`, so the
second element is always `None` and the rule is the pair it always was.
The widening exists because this class is also the payload the backend
hands its statmech resolution service, and the standalone statmech upload
(`POST /api/v1/uploads/statmech`, a backend-side contract) passes a
subclass whose entries may name a calculation by row id — citing one a
*previous* request deposited — instead of by key. Under the old tuple
every such entry collapsed to `(None, role)`, so two genuinely different
calculations sharing a role, which is one rotor scan per torsion with all
of them `role='scan'`, were refused as a duplicate.

`existing_calculation_id` itself is deliberately **not** added to
`StatmechSourceCalcIn`. It is shared with the conformer and bundle paths,
which are self-contained by construction — one calc-key namespace covers
everything they deposit — so they have nothing to chain to, and
`extra="forbid"` keeps them refusing the field outright rather than
silently dropping the link.

### 0.26.0 — the reaction bundle's statmech gains the six fields the species bundle already had

Purely additive. Nothing is renamed, removed, narrowed or newly refused,
so **every 0.25.0 payload validates unchanged under 0.26.0** — this is a
minor bump, not a breaking one. What changes is what a payload is
*allowed to say*.

`BundleStatmechIn` — the per-species statmech on
`/uploads/computed-reaction` — carried 12 fields against the 18 on
`StatmechInBundle`, the same block on `/uploads/computed-species`. Both
describe the same `statmech` row, resolved by the same services, subject
to the same table constraints. Six fields were reachable from one route
and not the other:

| field | what it records |
|---|---|
| `literature` | the paper this statmech came out of |
| `software_release` | the analysis code, per species |
| `workflow_tool_release` | the workflow tool, per species |
| `rotational_constant_a_cm1` | first principal rotational constant |
| `rotational_constant_b_cm1` | second principal rotational constant |
| `rotational_constant_c_cm1` | third principal rotational constant |

All six already had columns on the `statmech` table, so this is a
contract and projection change with no migration.

`literature`, `software_release` and `workflow_tool_release` are
**per-species overrides** of the bundle-level `literature` /
`analysis_software_release` / `workflow_tool_release`, exactly as
`BundleThermoIn`'s equivalents became in 0.25.0. Absent, the bundle-level
value is used, so a 0.25.0 payload persists precisely what it did before.
The override is what makes the ordinary mixed deposit expressible: one
participant taken from a paper, the rest computed here.

Nothing is deliberately withheld. `applied_energy_corrections` — the one
field `BundleThermoIn` refuses, because `BundleSpeciesIn` already
declares it against the same species entry — is not on `StatmechInBundle`
either, so it is not part of this divergence and needs no exception.

The rotational constants are the interesting half of the history.
`BundleStatmechIn` was born narrow, like `BundleThermoIn`. But the three
constants were added to `StatmechInBundle` **only**, two days after an
earlier commit had correctly updated both models together. The habit was
right and then it lapsed, and nothing could tell a one-sided addition
from a deliberate asymmetry. `backend/tests/schemas/test_bundle_root_model_symmetry.py`
now asserts the two field sets match, with an allowlist that requires a
written reason per exemption.

Consumers do not need to change. If you are building reaction bundles by
hand and want per-species statmech provenance or rotational constants,
these are the field names; if you are not, nothing moves.

### 0.25.0 — bundle roots gain SCF stability and thermo provenance; an atomless participant may not carry coordinates

Three additions and one new refusal. Nothing is renamed or removed, so a
0.24.0 payload keeps validating — **unless** it declared a free electron
and hung a geometry off it, which is the refusal below.

**`scf_stability` now reaches the bundle roots.** The field existed only
on `CalculationWithResultsPayload`, so `/uploads/conformers`,
`/transition-states`, `/statmech`, `/thermo` and `/transport` could record
whether an SCF solution had been tested for stability, and
`/uploads/computed-species`, `/uploads/computed-reaction` and
`/networks/pdep` could not. Models are `extra="forbid"`, so a bundle that
tried got a 422 rather than a silent drop.

`SCFStabilityPayload` is now split so the bundles can carry it without
carrying database ids with it. The new `SCFStabilityContent` holds the
finding — `status`, `lowest_eigenvalue`, `instability_count`,
`instability_type`, `reoptimized_wavefunction`, `note` — and
`SCFStabilityPayload` extends it with the two FK fields
(`source_calculation_id`, `source_artifact_id`) the primitive routes
already accept as programmatic chaining. `CalculationInBundle` and the
shared `CalculationIn` take the content class; the primitive routes are
unchanged, so a 0.24.0 payload to any of them still validates.

The FK fields are not merely omitted for tidiness. A bundle names
everything by local key, `source_calculation_id` is already in the
bundle's own `_FORBIDDEN_DB_ID_FIELDS`, and a sideways local key could
not be resolved anyway: the block is persisted with the calculation it
hangs off, before that calculation's siblings exist. A depositor who
needs to cite another row has the primitive routes.

```python
{"key": "h_sp", "type": "sp", ...,
 "scf_stability": {"status": "stabilized", "instability_count": 1,
                   "reoptimized_wavefunction": True}}
```

**`BundleThermoIn` gains the provenance `ThermoInBundle` already had.**
The reaction route's per-species thermo could not record which
calculations produced it. It now takes `source_calculations`,
`literature`, `software_release`, `workflow_tool_release`,
`h298_uncertainty_kj_mol` and `s298_uncertainty_j_mol_k`, and the
workflow persists them — including the `thermo_source_calculation` rows
that route wrote none of. `software_release` / `workflow_tool_release`
override the bundle-level values for that species and fall back to them
when omitted, so existing payloads read exactly as before.

`applied_energy_corrections` is deliberately **not** added: the reaction
bundle already declares those on `BundleSpeciesIn`, against the same
resolved species entry, and a second place to say it would need a rule
for which one counts.

```python
{"key": "h", "species_entry": {...},
 "thermo": {"h298_kj_mol": 218.0,
            "source_calculations": [{"calculation_key": "h_sp", "role": "sp"}]}}
```

**An atomless participant may no longer carry coordinates.** A species
declaring `molecule_kind: electron` is now refused, as a 422 naming the
field, if it carries a conformer, or a `geometry_key`, `input_geometries`
or `output_geometries` on any of its calculations. A conformer geometry
was already refused — later, and as
`species_geometry_composition_mismatch` from mid-transaction — but a
*calculation* geometry reached no composition check on any path and was
accepted, so an electron's coordinates could be stored. Definitional
under ADR 0008: a record of an electron's coordinates cannot be what it
says it is, so this rejects no correct calculation.

`molecule_kind: pseudo` is unaffected and stays that way. A lumped
construct's composition is *unknown*, not empty, and a geometry deposited
under one is an under-described molecule rather than a contradiction.

### 0.24.0 — **BREAKING**: statmech source calculations are named, not numbered

`ConformerUploadRequest.statmech` used to identify a supporting
calculation by its database primary key. It no longer does, and payloads
written against 0.23.0 that used those two fields will be rejected with a
422 rather than silently reinterpreted.

| Before (0.23.0) | After (0.24.0) |
|---|---|
| `statmech.source_calculations[].calculation_id: int` | `statmech.source_calculations[].calculation_key: str` |
| `statmech.torsions[].source_scan_calculation_id: int \| None` | `statmech.torsions[].source_scan_calculation_key: str \| None` |

A key names a calculation *declared in the same request*. To make that
possible, `ConformerUploadRequest.calculation` and
`additional_calculations[]` gained an optional `key`, and the request
refuses a statmech reference to a key nobody declared. This is the shape
the computed-species / computed-reaction bundles have always used; the
conformer path was the odd one out, and a row id is not something a
depositor can know.

Migrating: put a `key` on the calculation you meant, and reference it.

```python
# 0.23.0 — only writable by something that had already queried TCKDB
{"calculation": {...}, "statmech": {"source_calculations": [{"calculation_id": 4711, "role": "freq"}]}}

# 0.24.0
{"calculation": {"key": "h_sp", ...},
 "additional_calculations": [{"key": "h_freq", "type": "freq", ...}],
 "statmech": {"source_calculations": [{"calculation_key": "h_freq", "role": "freq"}]}}
```

Component renames (they affect generated client class names; the JSON
shape of the bundle paths is unchanged):

- `StatmechSourceCalculationCreate`, `StatmechSourceCalcInBundle` and the
  server-side `StatmechSourceCalculationIn` collapse into one component,
  `StatmechSourceCalcIn`. `StatmechSourceCalcInBundle` stays importable as
  an alias — from `tckdb_schemas.statmech_bits` and from both bundle
  modules — and is the same class object, so there is one component, not
  two. `StatmechSourceCalculationCreate` is **removed** from this package.
- `StatmechTorsionCreate` collapses into `StatmechTorsionIn` and is
  **removed** from this package.
- `StatmechTorsionCoordinateCreate` and `StatmechTorsionCoordinateBase`
  are removed; `StatmechTorsionCoordinateIn` is the only spelling of an
  atom quartet. They were field-for-field and validator-for-validator
  identical, so a generator emitted two classes for one concept.
- New: `ConformerCalculationIn` — a `CalculationWithResultsPayload` plus
  the optional `key`.

The two removed `*Create` names still exist inside the TCKDB server, in
`app.schemas.entities.statmech`, where they mean something different: the
row-shaped create payload that speaks `calculation_id`. They are not part
of any wire contract and never were importable from here for that purpose.

Also tightened, matching what the sibling upload paths already enforce.
Each of these used to surface as a 500 rather than a 422:

- `statmech.source_calculations` must be unique by
  `(calculation_key, role)` — it is the row's primary key.
- `statmech.torsions[].torsion_index` must be unique.
- The primary calculation may not be linked twice: once implicitly via
  `statmech.uploaded_calculation_role` and again in
  `source_calculations` under the same role. A *different* role is a
  different row and stays allowed.

### 0.23.0

Published `ConformerUploadRequest` and `TransitionStateUploadRequest`.

## Layout

```
tckdb_schemas/
  enums.py                 — wire-contract enum mirror
  common.py                — SchemaBase
  utils.py                 — text/ORCID normalization helpers
  upload_warning.py        — UploadWarning
  reaction_family.py       — canonical RMG family vocabulary
  fragments/               — reusable upload fragments
  literature.py            — LiteratureUploadRequest
  energy_correction.py     — applied energy-correction payloads
  thermo.py                — ThermoPoint / ThermoNASA upload pieces
  statmech_bits.py         — torsion / source-calculation fragments
  shared/calculation_in.py — base CalculationIn / GeometryIn + adapter
  workflows/
    __init__.py            — the published request models, re-exported
    computed_species_upload.py
    computed_reaction_upload.py
    conformer_upload.py
    transition_state_upload.py
    transport_upload.py    — TransportUploadPayload, nested in the above
```

## Installation (development)

From the repo root:

```bash
pip install -e schemas/python/tckdb-schemas
```

## Usage

```python
from tckdb_schemas.workflows.computed_species_upload import (
    ComputedSpeciesUploadRequest,
)

payload = ComputedSpeciesUploadRequest.model_validate(data)
wire = payload.model_dump(mode="json", exclude_none=True)
```

## Boundary

`tckdb_schemas` must not import FastAPI, SQLAlchemy, Alembic, RDKit, or
any backend `app.*` module. The package depends only on `pydantic` plus
the standard library. The boundary is enforced by
`tests/test_import_boundaries.py`.
