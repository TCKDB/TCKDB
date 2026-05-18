# Scientific Statmech Read/Search Surface

**Status:** implemented (v0)
**Companion to:**
- [scientific_calculation_reads.md](scientific_calculation_reads.md)
- [scientific_transition_state_reads.md](scientific_transition_state_reads.md)
- [scientific_conformer_reads.md](scientific_conformer_reads.md)

**Date:** 2026-05-18
**Scope:** Backend only. Public scientific read surface for statmech
records. ARC, `tckdb-client`, and ingestion schemas out of scope.

---

## 1. Purpose

Answer statmech questions through public refs and bounded summaries
instead of the legacy table-style integer-id routes:

```text
What statmech models exist for this species entry?
Which calculations support a given statmech record (opt / freq / sp /
  rotor scans / composite)?
Which torsions did the model account for, and how were they treated?
What frequency scale factor was applied, and where does it come from?
What review / trust state does the statmech record have?
```

## 2. Endpoint list

```http
GET  /api/v1/scientific/statmech/{statmech_ref_or_id}
GET  /api/v1/scientific/statmech/search
POST /api/v1/scientific/statmech/search
```

Handle prefix: `sm_…` (registered in `PREFIXES`). Wrong-prefix refs
return 422 `handle_type_mismatch`; unknown refs / ids return 404.

The router uses a single `/statmech` prefix; `/search` is registered
before `/{handle}` so FastAPI doesn't route the search path through
the catch-all detail handler.

## 3. Response fragments

Defined in [scientific_statmech.py](../../app/schemas/reads/scientific_statmech.py).

- **`StatmechCoreBlock`** — direct-row metadata: `scientific_origin`,
  `statmech_treatment` (RRHO / RRHO+1D / RRHO+ND / …),
  `rigid_rotor_kind`, `point_group`, `external_symmetry`,
  `is_linear`, `uses_projected_frequencies`,
  `frequency_scale_factor_value` (resolved scalar), `note`,
  `created_at`, compact review badge.
- **`StatmechSpeciesContext`** — species / species-entry refs +
  cheap chemistry context (SMILES, InChI key, charge, multiplicity).
- **`StatmechFrequencyScaleFactorSummary`** — provenance behind the
  scalar: LoT, software, source literature.
- **`StatmechSourceCalculationSummary`** — compact source-calc row
  (`role`, ref, type, quality, review, LoT/software/workflow).
- **`StatmechTorsionSummary`** + **`StatmechTorsionCoordinateSummary`**
  — torsion treatment / dimension / symmetry / source scan calc +
  atom-index coordinate list.
- **`StatmechFrequenciesSummary`** — list of source freq calculation
  refs + resolved scale factor value (per-mode arrays live behind the
  calc detail endpoint).
- **`StatmechConformerContextItem`** — `conformer_group_ref` for
  groups reachable via the statmech's `species_entry`.
- **`StatmechEvidenceSummary`** — bounded counts/booleans.
- **`AvailableStatmechSections`** — `has_*` boolean map.

`statmech` rows do **not** carry frequencies inline — frequencies
live on `calc_freq_result` of the source freq calculation. The
`include=frequencies` token therefore surfaces a list of source freq
calculation refs plus the scaling factor; the full per-mode arrays
remain available via
`GET /scientific/calculations/{ref}?include=results`.

`statmech` also does **not** link to a conformer directly. The
linkage is at the `species_entry` level: the `include=conformers`
token surfaces every `conformer_group` belonging to the same
species_entry as a *context hint*, not a hard membership pointer.

## 4. Include behavior

Legal include tokens: `source_calculations`, `torsions`,
`frequencies`, `conformers`, `review`, `internal_ids`, `all`.

```text
include=source_calculations  — list of compact source-calc summaries
                               keyed by role (opt / freq / sp / scan /
                               composite / imported)
include=torsions             — statmech_torsion rows + their
                               coordinate atom indices + the
                               source scan calc ref when present
include=frequencies          — pointer block listing source freq
                               calc refs + resolved scale factor
include=conformers           — list of conformer_group refs reachable
                               via the statmech's species_entry
                               (context hint — see §3)
include=review               — record_review history for the statmech
                               row
include=all                  — expands to all five public tokens;
                               never expands to internal_ids
include=internal_ids         — Phase D policy gate; restores integer
                               IDs when the deployment permits
```

No `include` returns the bounded default: core block + species
context + FSF / software / workflow / literature pointers when the
row carries them + evidence summary + available_sections.

## 5. Search filters

Implemented (MVP):

```text
species_ref
species_entry_ref
statmech_ref
conformer_group_ref
conformer_observation_ref
model_kind                    — StatmechTreatmentKind enum
has_source_calculations       — explicit False is meaningful
has_freq_calculation          — explicit False is meaningful
has_rotor_scans               — explicit False is meaningful
has_torsions                  — explicit False is meaningful
method
basis
software
software_version
workflow_tool
workflow_tool_version
min_review_status
include_rejected
include_deprecated
include
offset
limit
sort                          — non-None → 422 client_sort_not_supported
```

**Deferred** (documented for future PRs):
- Frequency-scale-factor identity filters (FSF ref / value range /
  scale_kind). Reachable today via `software` / `method` / `basis`
  filters on the source freq calc; standalone FSF filters require
  joining `frequency_scale_factor`.
- `point_group` and `external_symmetry` scalar filters. Easy to add
  but no consumer has asked yet.
- Torsion-treatment filter (`treatment_kind`). Same reasoning.
- Selection-priority sort. v0 ships with the standard
  `review_rank → created_at → id` deterministic ordering.

### At-least-one-filter rule

Pure pagination / include / review knobs do not satisfy the gate. A
request with no meaningful filter returns 422 `missing_filter`.
Explicit `False` is meaningful (matches the conformer / TS surface
contract after the False-handling fix).

### Default deterministic ordering

```text
review_rank ASC
created_at DESC
statmech_id DESC
```

## 6. Review/trust behavior

Detail endpoint:
- Never filters by review status (404 only on unknown handles).
- Always carries the compact review badge.
- `review_summary` counts the requested record only.

Search endpoint:
- Hides `rejected` / `deprecated` by default; `include_rejected` /
  `include_deprecated` opt them in.
- Every record carries the badge.
- `review_summary` counts the visible candidate set before pagination.

## 7. Internal-ID behavior

Identical to every other `/scientific/*` surface:

- Default: every `*_id` field stripped recursively. Refs stay visible.
- `include=internal_ids` + `settings.allow_public_internal_ids=True`
  restores IDs.
- `include=internal_ids` + policy disallows → token silently dropped
  from `request.include`; IDs stay hidden.

The `_build_software_for_software_id()` helper used by the FSF
summary returns a `SoftwareReleaseSummary` with placeholder
`software_release_id=0` and empty `software_release_ref` because
`frequency_scale_factor` carries `software_id` (not
`software_release_id`). The Phase D stripper hides the placeholder
id by default; the name is the load-bearing fact.

## 8. Geometry behavior

Statmech reads never inline geometry payloads. Source-calc summaries
carry only `calculation_ref`; geometry links / XYZ coordinates remain
behind the calculation and geometry detail endpoints. Torsion atom
indices (`atom1_index` / `atom2_index` / `atom3_index` /
`atom4_index`) ARE surfaced because they describe the torsion
definition, not raw molecular geometry.

## 9. Relationship to thermo

A future thermo surface (`/scientific/thermo/*`) will link to
statmech via the existing `thermo.statmech_id` FK (see
[`thermo.py`](../../app/services/scientific_read/thermo.py) which
already surfaces statmech context under `include=statmech`).
This surface complements that one: thermo callers can follow
`statmech_ref` from the thermo response to the statmech detail
endpoint for the underlying inputs / source calculations / torsions.

## 10. Relationship to conformers

`statmech.species_entry_id` connects statmech to a species_entry.
`conformer_group.species_entry_id` connects basins to the same
species_entry. The two share the species_entry but not a direct
membership link — multiple statmech records may apply to the same
basin, and a statmech may be agnostic about which basin it applies
to (e.g. an RRHO model evaluated at the lowest-energy conformer).
The `include=conformers` token therefore returns the *full* list of
conformer groups under the species_entry as a context hint; callers
who need to identify the basin should consult the source-calc graph
(opt / freq calc conformer_observation_id) and curator notes.

## 11. Non-goals

```text
thermo surface changes
transport / network / pdep reads
full geometry payloads
artifact body download
new ingestion behavior
schema redesign
ARC changes
tckdb-client changes
```

## 12. Implementation status

```text
Phase 1 — handles + detail endpoint               ✓ implemented
Phase 2 — search                                  ✓ implemented
Phase 3 — thermo↔statmech linkage (read-side)     pending (lives on the
                                                  future thermo PR)
Phase 4 — FSF / point_group / treatment filters   deferred
```

## 13. Test plan

Detail (24 tests):

```text
detail by ref / by id
unknown ref → 404
wrong-prefix handle → 422
malformed handle → 422
default shape (core, species, evidence, available_sections)
review badge present
species context (species/species_entry refs + chemistry context)
evidence summary default zero
evidence summary with freq + torsion populated
available_sections present
include=source_calculations
include=torsions (atom indices, source scan ref)
include=frequencies points at source freq calc refs (no per-mode arrays)
include=conformers surfaces species_entry conformer groups
include=review
include=all expands public tokens only
include=all does not restore internal IDs
internal-ID policy restore + silent drop
unknown include token → 422
rejected detail still returned with badge
forbidden-payload recursive walk
```

Search (32 tests):

```text
GET / POST missing filter → 422
each implemented filter happy path
explicit False for has_source_calculations
default hides rejected; include_rejected sorts them last
pagination envelope correct
deterministic ordering (review_rank → created_at)
client sort rejected
GET / POST parity
POST rejects query-string fields
each include token on records
include=all + internal_ids policy
search record shape == detail record shape (anti-drift)
unknown-ref short-circuit empty
wrong-prefix ref → 422
forbidden-payload recursive walk
```

## 14. Open questions

1. **FSF software dimension.** `frequency_scale_factor` carries
   `software_id` (the software vendor), not
   `software_release_id` (a specific version). The FSF summary
   surfaces a `SoftwareReleaseSummary` with a placeholder
   `software_release_id=0` / `software_release_ref=""` (stripped by
   the default Phase D policy) plus the populated `software` name.
   If callers ever need to distinguish the vendor cleanly without
   the release shell, a smaller `SoftwareSummary` shape would be
   the right v1 refactor; v0 reuses the existing fragment to avoid
   adding a second software-related schema for one consumer.
2. **Conformer-context identity.** `include=conformers` returns
   every `conformer_group` under the species_entry — not the basin
   a specific statmech actually represents. A future curator-side
   `statmech_conformer_observation` link would let the read surface
   narrow the context to one basin; out of scope for v0.
3. **Frequencies block.** `include=frequencies` currently emits a
   single object with a list of source freq calc refs + the
   scaling factor. If a consumer wants the bounded freq-result
   summary (n_imag, zpe, lowest mode, etc.) inline, the right
   answer is to surface the calc detail surface's
   `CalculationFreqResultSummary` as a nested field; deferred until
   a consumer asks.
4. **`conformer_observation_ref` search filter.** Today the
   observation→statmech join walks
   `observation → conformer_group → species_entry → statmech`.
   That's correct for "give me statmech rows under the same
   species_entry as this observation" but semantically loose; a
   tighter link via the source-calc graph (`statmech_source_calculation
   → calculation.conformer_observation_id`) is a follow-up worth
   evaluating when curator workflows ask for it.
