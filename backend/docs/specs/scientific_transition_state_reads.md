# Scientific Transition-State Read/Search Surface

**Status:** implemented (v0)
**Companion to:** [scientific_calculation_reads.md](scientific_calculation_reads.md)
**Date:** 2026-05-18
**Scope:** Backend only. ARC, `tckdb-client`, and ingestion schemas out of scope.

---

## 1. Purpose

Answer questions about transition states that the legacy table-style
`/api/v1/calculations/*` routes cannot:

```text
What transition states are known for this reaction entry?
What TS entries (candidate saddle-point geometries) exist?
Which TS entries have opt/freq/sp/IRC/path-search evidence?
What status (guess / optimized / validated / rejected) is each entry in?
What review/trust state does this TS / TS entry have?
```

## 2. Endpoint list

```http
GET  /api/v1/scientific/transition-states/{transition_state_ref_or_id}
GET  /api/v1/scientific/transition-state-entries/{transition_state_entry_ref_or_id}
GET  /api/v1/scientific/transition-states/search
POST /api/v1/scientific/transition-states/search
```

Handle prefixes: TS concept → `ts_…`, TS entry → `tse_…`. Wrong-prefix
refs return 422 `handle_type_mismatch`; unknown refs / ids return 404.

### Linking from `/reaction-entries/{id}/full`

The composite reaction-full response embeds a TS section that surfaces
the same public refs and evidence projection. Each entry in
`transition_states[*]` carries:

```text
transition_state_ref      → GET /scientific/transition-states/{ref}
transition_state_entry_ref → GET /scientific/transition-state-entries/{ref}
status                    → TransitionStateEntryStatus
evidence_summary          → byte-identical to record.evidence_summary
                            from the TS-entry detail endpoint
```

The TS section therefore stays the natural starting point for a
reaction-centric crawl; callers follow the refs to the scientific TS
surface for the per-record detail without needing to issue a search.

The same reaction-full response also exposes path-like calculation
sections (`include=scans`, `include=irc`, `include=path_search`).
Each item carries a `calculation_ref`, an `endpoint` hint, and a
**summary-only** block byte-identical to the matching
`include=scan|irc|path_search` projection on the calculation detail
endpoint. Per-point trajectory arrays are never inlined under
`/full` — they remain available only behind the specialized
`/scientific/calculations/{ref}/scan|irc|path-search` endpoints. See
`scientific_calculation_path_includes.md` §8.6 for details.

`/full?include=artifacts` follows the same pattern: artifact metadata
is grouped by owning calculation (`ReactionFullCalculationArtifacts`)
and each group's `artifacts` list is byte-identical to
`record.artifacts` from
`GET /scientific/calculations/{ref}?include=artifacts`. Only metadata
travels (kind / uri / filename / sha256 / bytes / created_at) —
**no body bytes, no download or presigned URLs**. Calcs with no
artifact rows are omitted from the group list so empty entries don't
clutter the response.

The search surface returns records at the **TS-entry grain**: entries
are the concrete objects carrying charge / multiplicity / status and
calculation evidence. The parent TS-concept context travels along on
each record so callers never need a second round-trip to identify the
reaction channel.

## 3. Response fragments

Defined in [scientific_transition_state.py](../../app/schemas/reads/scientific_transition_state.py).

- `TransitionStateCoreBlock` — direct TS-row metadata + review badge.
- `TransitionStateEntryCoreBlock` — charge / multiplicity / status /
  unmapped_smiles / created_at / review badge. The RDKit `mol` blob is
  deliberately not surfaced; only the public-readable
  `unmapped_smiles` text is exposed.
- `TransitionStateReactionContext` — reaction + reaction-entry refs,
  rendered equation (`"A + B <=> C + D"` for reversible, `"->"` for
  irreversible), reaction family name when present.
- `TransitionStateCalculationEvidenceSummary` — calc count + `has_*`
  booleans for opt / freq / sp / irc / path_search /
  geometry_validation / scf_stability. Primary-per-type calculation
  selection is **deferred** to a later PR — the data model does not
  currently carry a unique notion of "primary" per type.
- `TransitionStateCalculationSummary` — compact calculation projection
  (ref, type, quality, review, LoT, software, workflow) used by the
  `include=calculations` token. Heavy include sections (results,
  parameters, geometries) remain on the calculation detail endpoint.
- `AvailableTransitionStateSections` — `has_entries`,
  `has_calculations`, `has_geometries`, `has_review` boolean map for
  cheap "is there anything under this section?" client checks.

## 4. Include behavior

Legal include tokens: `entries`, `calculations`, `geometries`, `review`,
`internal_ids`, `all`.

TS detail (`/transition-states/{handle}`):

```text
include=entries        — list of TS-entry records under the parent TS
include=calculations   — compact calc summaries across all TS entries
include=geometries     — output-geometry link list (ref + natoms +
                         geom_hash + role + output_order); never XYZ
include=review         — record_review row history for the TS
include=all            — expand to entries + calculations + geometries +
                         review; never expands to internal_ids
include=internal_ids   — Phase D policy gate; restores integer IDs when
                         the deployment permits and the caller opts in
```

TS-entry detail (`/transition-state-entries/{handle}`):

```text
include=calculations   — compact calc summaries for this entry
include=geometries     — output-geometry links for this entry's calcs
include=review         — record_review row history for the entry
include=all            — calculations + geometries + review
include=entries        — silently no-op on this surface (the entry IS
                         the record); kept legal so a generic client
                         can pass the same include set to both detail
                         surfaces and the search surface
include=internal_ids   — Phase D policy gate
```

Search (`/transition-states/search`):

```text
include=calculations   — embed compact calc summaries on each record
include=geometries     — embed geometry-link lists on each record
include=review         — embed record_review history per record
include=all            — calculations + geometries + review (entries
                         silently dropped as above)
include=internal_ids   — Phase D policy gate
```

No `include` always returns the bounded default: core block + reaction
context + entries / evidence summaries + available_sections.

## 5. Search filters

Implemented (MVP):

```text
reaction_ref
reaction_entry_ref
transition_state_ref
transition_state_entry_ref
status
charge
multiplicity
has_calculations
has_opt
has_freq
has_sp
has_irc
has_path_search
has_geometry_validation
has_scf_stability
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
sort                  (rejected non-None per v0 sort policy)
```

**Deferred** to a later slice:

```text
reactants / products SMILES filters       (parallel to reaction search;
                                           depends on reaction-side helpers
                                           the TS surface does not own yet)
substructure / similarity search          (Phase 6, parallel to species)
primary_*_calculation_ref selection       (data model does not currently
                                           distinguish "primary")
evidence_completeness ranking             (would require an L1 score;
                                           default sort uses review_rank +
                                           created_at + id only)
```

### At-least-one-filter rule

Requests with no meaningful filter (only pagination / include / review
knobs) are rejected with 422 `missing_filter`. The set of "meaningful"
filters is the union of owner refs, scalar TS-entry filters, evidence
booleans, and method/basis/software/workflow filters.

### Sorting

Client `sort=…` is rejected with 422 `client_sort_not_supported`.

Default deterministic ordering (search):

```text
review_rank ASC          (approved < under_review < not_reviewed < deprecated < rejected)
created_at DESC          (recent first)
transition_state_entry_id DESC   (stable tie-break)
```

`evidence_completeness` ranking is documented as a future addition but
not part of v0 — the data model does not carry an L1 score yet.

### Pagination

Standard scientific pagination: `offset`, `limit`, `returned`, `total`.
Bounded by the existing `validate_pagination` cap
(`min(MAX_LIMIT=200, settings.public_max_limit)`).

## 6. Review/trust behavior

Detail endpoints (both TS and TS-entry):

```text
- never filter by review status (a 404 only happens on unknown handles)
- always include the compact review badge on the core block
- review_summary in the response envelope reflects only the requested
  record (the TS concept's badge for the TS surface, the entry's badge
  for the entry surface)
```

Search endpoint:

```text
- hides rejected/deprecated by default (default-trust posture, D5)
- include_rejected=true / include_deprecated=true opt in
- every record carries a compact review badge
- review_summary counts the filtered candidate set BEFORE pagination
```

`min_review_status=…` narrows further by the existing rank order.

## 7. Internal-ID behavior

Implemented via the existing `apply_internal_ids_visibility` policy:

```text
default          → integer IDs (*_id) stripped recursively; refs (*_ref)
                   stay visible
include=internal_ids + settings.allow_public_internal_ids = True
                 → IDs restored on the response (legacy id-bearing shape)
include=internal_ids + settings policy disallows
                 → the include token is silently dropped from
                   request.include and IDs stay stripped (no 422)
```

The `request` echo block is preserved verbatim so callers can see what
the server saw, including their original input.

## 8. Geometry behavior

Under `include=geometries`, output-geometry links are returned with:

```text
geometry_ref     (always present)
role             (CalculationGeometryRole — final / scan_point / …)
output_order
natoms
geom_hash
geometry_id      (policy-gated)
```

No XYZ text, no per-atom rows, no inline coordinate arrays. Full
geometries remain available via
`GET /api/v1/scientific/geometries/{geometry_ref}`.

Input geometries are deferred — TS entries are usually scored on their
output geometry, and exposing both directions would double the payload
for marginal value in v0.

## 9. Non-goals

```text
- reactants/products chemistry filters on the TS search
- substructure / similarity search
- full calculation expansion inline (heavy includes stay on the calc
  detail endpoint)
- full geometry coordinates inline
- artifact body download
- TS mutation / write endpoints
- schema redesign
- ARC changes
- tckdb-client changes
```

## 10. Implementation status

```text
Phase 1 — detail surfaces                 ✓ implemented
Phase 2 — TS-entry-grain search           ✓ implemented
Phase 3 — include = entries/calculations/geometries/review/all
                                          ✓ implemented
Phase 4 — primary-per-type calc selection deferred
Phase 5 — reactants/products filters      deferred
Phase 6 — evidence_completeness ranking   deferred
```

## 11. Test plan

Detail tests (cover both TS and TS-entry):

```text
detail by ref / by integer id
unknown ref → 404
wrong-prefix handle → 422
malformed handle → 422
default response shape (core block, reaction context, evidence summary,
                        available_sections)
review badge present
review_summary present
include=entries / calculations / geometries / review / all
include=all does NOT expand internal_ids
include=internal_ids restores ids when policy permits
rejected/deprecated detail records still returned with badge
no mol blob / xyz_text / atoms / coords leak in default or under any
include
```

Search tests:

```text
GET / POST missing filter → 422
search by every implemented filter (owner refs, status, charge,
multiplicity, has_* evidence, method/basis, software/version,
workflow/version)
default hides rejected/deprecated; include_rejected surfaces them
pagination envelope correct
deterministic ordering (review_rank → created_at desc → id desc)
client sort rejected
GET/POST parity
include=calculations / geometries / review / all behavior on records
internal-ID hiding / restoring
recursive forbidden-payload walk (no xyz_text, atoms, coords, mol
blob, body, content, data, presigned_url, download_url)
```

## 12. Open questions

1. **"Primary" calculation per evidence type.** Today multiple
   `opt` / `freq` / `sp` calcs may attach to a single TS entry without
   any "preferred" marker. A future PR can either (a) add an explicit
   `preferred_*_calculation_id` column on `transition_state_entry`, or
   (b) lean on review state ranking + most-recent created_at to derive
   a primary projection in the service layer. Deferred until a
   downstream consumer asks for it.
2. **Reactants/products filters.** The reaction-side search service
   already resolves SMILES → species ids and matches against
   participants. A TS-search variant could share that machinery; the
   join shape is "TS-entry → TS → reaction_entry → participants" which
   is straightforward but heavier than v0 needs to ship.
3. **TS-concept-grain search.** A future surface could return records
   at the TS-concept grain (one record per TS, with its entries
   embedded). The current TS-detail endpoint already covers
   single-concept needs; concept-grain search is deferred until a
   contributor / UI asks for it.
