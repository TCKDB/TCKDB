# Scientific Literature Reads — Spec

Implementation status: **v0 shipped** (literature detail + inverse
records endpoints). Standalone literature search endpoint is
deferred. See `read_query_api_audit.md` for surface-level closure
status.

## 1. Purpose

Provide the **literature-centered** read surface of the scientific
API: start from a paper (or DOI / ISBN-backed row) and answer

- What does TCKDB know about this paper?
- Which scientific records cite this paper?
- How many records of each type cite it?

This is the inverse direction of the existing thermo / kinetics /
statmech / transport / network reads, which embed `LiteratureSummary`
in their provenance blocks. The literature surface starts at the
paper and fans out.

## 2. Endpoint list

| Method | Path | Status |
|---|---|---|
| GET | `/api/v1/scientific/literature/{literature_ref_or_id}` | shipped |
| GET | `/api/v1/scientific/literature/{literature_ref_or_id}/records` | shipped |
| GET / POST | `/api/v1/scientific/literature/search` | **deferred** |

The legacy `/api/v1/literature` / `/api/v1/literature/{id}` table-style
list endpoint stays — it serves the legacy ORM-shape clients. The
new endpoints under `/scientific/literature/*` are public-ref-first
and review-aware.

## 3. Literature detail endpoint

`GET /api/v1/scientific/literature/{literature_ref_or_id}`

### 3.1 Handle resolution

Same contract as the rest of the scientific surface (see
`handles.py`):

| Input | Behavior |
|---|---|
| Integer string (`"42"`) | SELECT by `literature.id` |
| Public ref (`lit_…`) | SELECT by `literature.public_ref` |
| Wrong-prefix ref | 422 `handle_type_mismatch` |
| Malformed handle | 422 `invalid_handle` |
| Unknown ref / id | 404 |

### 3.2 Default response shape

```jsonc
{
  "request": { "include": ["..."] },
  "review_summary": { "approved": 0, "...": 0, "total": 0 },
  "record": {
    "literature": { /* LiteratureCoreBlock */ },
    "identifiers": { "doi": "10....", "isbn": null, "url": null },
    "authors": [ /* LiteratureAuthorSummary */ ],
    "record_counts": { /* LiteratureRecordCounts */ },
    "available_sections": { /* AvailableLiteratureSections */ }
  }
}
```

Literature is **not a reviewable record type** (no
`SubmissionRecordType.literature`). `review_summary` is always
empty; no review badge is exposed on the core block. Reviews
surface only on the linked-record side via the `/records` endpoint.

### 3.3 LiteratureCoreBlock

| Field | Source |
|---|---|
| `literature_id` (Phase D gated) | `literature.id` |
| `literature_ref` | `literature.public_ref` (`lit_…`) |
| `kind` | `literature.kind` |
| `title` | `literature.title` |
| `journal`, `year`, `volume`, `issue`, `pages` | direct columns |
| `publisher`, `institution` | direct columns |
| `created_at` | `literature.created_at` |

### 3.4 LiteratureIdentifiers

Returns DOI / ISBN / URL exactly as stored. Normalization happens at
upload time against the `ix_literature_doi_normalized` /
`ix_literature_isbn_normalized` expression indices; this surface does
not re-normalize.

### 3.5 LiteratureAuthorSummary

| Field | Source |
|---|---|
| `author_ref` | `null` today (no `PublicRefMixin` on `Author` yet) |
| `author_id` (Phase D gated) | `author.id` |
| `full_name`, `given_name`, `family_name`, `orcid` | `author.*` |
| `position` | `literature_author.author_order` |

Authors are loaded in `author_order` ASC.

### 3.6 LiteratureRecordCounts

Cheap aggregate counts over each record table's `literature_id`
column:

```python
calculations, thermo, kinetics, statmech, transport,
networks, network_solves, total_records
```

Only **direct** linkage is counted. Indirect citations (e.g.
`network_kinetics` inherits literature from its parent
`network_solve`) are not summed here — that would conflate
"cites this paper" with "downstream of a record that cites it".

### 3.7 Include behavior

Legal tokens: `authors`, `record_counts`, `review`, `internal_ids`,
`all`.

The default response already carries authors and record-counts, so
`include=authors` and `include=record_counts` are legal **no-op
affordances** — they document that the caller wants the section
populated. This keeps the literature surface forward-compatible if
authors are ever moved behind a heavy include later.

`include=all` expands to public tokens only (`authors,
record_counts, review`). `internal_ids` is internal and must be
requested explicitly; `include=all` does **not** restore IDs. See
`internal_ids_visibility_policy.md`.

## 4. Inverse records endpoint

`GET /api/v1/scientific/literature/{literature_ref_or_id}/records`

### 4.1 Purpose

Public-ref summaries of every scientific record citing this
literature. The client can follow each item's `endpoint` field to
the full per-type detail surface.

The response is **bounded and navigable**, not a deep expansion:
target records are summarized, not inlined.

### 4.2 Query parameters

| Param | Default | Notes |
|---|---|---|
| `record_type` | none | Filter to one record type; 422 `unknown_record_type` if outside the supported set |
| `include` | `[]` | `review`, `internal_ids`, `all` |
| `include_rejected` | false | Restore records reviewed as `rejected` |
| `include_deprecated` | false | Restore records reviewed as `deprecated` |
| `sort` | none | Client sort rejected (422 `client_sort_not_supported`) |
| `offset` | 0 | |
| `limit` | 50 | Capped at 200 / `settings.public_max_limit` |

### 4.3 Supported record types

Only record types with a **direct** `literature_id` FK ship in v0:

```
calculation
thermo
kinetics
statmech
transport
network
network_solve
```

Indirect linkage (e.g. `network_kinetics` → `network_solve` →
literature) is deferred. If indirect linkage is added later, the
schema is already forward-compatible: `LiteratureLinkedRecordSummary`
carries a `relationship_kind` discriminator (defaulted to
`"direct"`).

Record types with no FK to `literature` at any level
(`species_entry`, `reaction_entry`) are intentionally absent from
the supported set — they cite literature only through their
downstream scientific records.

### 4.4 LiteratureLinkedRecordSummary

```jsonc
{
  "record_type": "thermo",
  "record_ref": "thm_…",
  "record_id": 12,                  // Phase D gated
  "relationship_kind": "direct",
  "role": null,
  "title": null,
  "label": "CCO",                   // type-specific short string
  "species_ref": "sp_…",
  "species_entry_ref": "spe_…",
  "reaction_ref": null,
  "reaction_entry_ref": null,
  "calculation_ref": null,
  "network_ref": null,
  "network_solve_ref": null,
  "review": null,                   // populates only with include=review
  "created_at": "...",
  "endpoint": "/api/v1/scientific/thermo/thm_…"
}
```

`endpoint` is always **ref-based**, never an integer-id path. This
guarantees the URL stays valid after Phase D internal-ID stripping.

### 4.5 Per-type populated fields

| `record_type` | populated context fields |
|---|---|
| `calculation` | `calculation_ref`, `label` (= calculation type) |
| `thermo` | `species_ref`, `species_entry_ref`, `label` (= SMILES) |
| `kinetics` | `reaction_ref`, `reaction_entry_ref`, `label` (= `model_kind`) |
| `statmech` | `species_ref`, `species_entry_ref`, `label` (= `statmech_treatment`) |
| `transport` | `species_ref`, `species_entry_ref`, `label` (= SMILES) |
| `network` | `network_ref`, `title` (= `network.name`) |
| `network_solve` | `network_ref`, `network_solve_ref`, `label` (= `me_method`) |

### 4.6 Ordering

Deterministic v0 sort key:

```
record_type ASC,
created_at DESC NULLS LAST,
record_id DESC
```

Client sort is rejected. A future refinement could allow
`sort=created_at` etc.; out of scope for v0.

### 4.7 Pagination envelope

Standard scientific pagination:

```
offset, limit, returned, total
```

`total` is the post-filter, post-visibility count across all
included record types (or just the filtered type) — the count
before pagination.

### 4.8 Review / visibility behavior

Each linked target record's review badge is looked up under its own
`SubmissionRecordType`. Records reviewed as `rejected` or
`deprecated` are excluded from the response by default; the caller
opts in via `include_rejected` / `include_deprecated`.

The endpoint reuses the standard `default_visible_statuses` from
the scientific surface so the visibility semantics match the rest
of `/scientific/*`.

`review_summary` aggregates badges across all linked reviewable
records (post-visibility filter, pre-pagination).

### 4.9 Include behavior

Legal tokens: `review`, `internal_ids`, `all`.

`include=all` expands to public tokens only (just `review` in v0)
and never restores `internal_ids`.

`include=review` switches each summary's `review` field from
`null` to a `RecordReviewBadge`. Without it, badges are not
populated even when a record has been reviewed — this keeps the
default response small for clients that only care about the link
graph.

Heavy target-record sections (geometries, results, NASA polynomials,
Chebyshev coefficients) are **never** inlined — clients follow the
ref-based `endpoint` for the full record.

## 5. Internal-ID visibility

Handled uniformly via
`app.services.scientific_read.internal_ids.apply_internal_ids_visibility`
at the route boundary:

- Default: `*_id` and bare `id` keys are recursively stripped from
  the JSON response.
- `include=internal_ids` restores them **only when**
  `settings.allow_public_internal_ids` is true. Otherwise the token
  is silently dropped (and the dropped state is reflected in
  `request.include`).
- `include=all` never implicitly enables `internal_ids` — the
  caller must say `include=all,internal_ids`.

Public refs (`*_ref`) are always preserved.

## 6. Payload safety

The literature surface **never** exposes:

- Full target-record bodies (thermo NASA polynomials, kinetics rate
  matrices, calculation result arrays, geometry coordinates).
- Artifact body bytes / presigned download URLs.
- Free-text abstract or fulltext blobs.

The literature ORM today carries no `abstract` / `fulltext` column;
both endpoints have a recursive forbidden-payload test that fails
the build if such keys ever leak through (`abstract`, `fulltext`,
`pdf`, `body`, `content`, `data`, `presigned_url`, `download_url`,
`xyz_text`, `atoms`, `coords`).

If a future schema adds an abstract column, the default policy is
**not** to surface it on this endpoint; a dedicated read surface
with policy review would be the entry point.

## 7. Relationship to DOI / source / provenance

- DOI / ISBN normalization is owned by
  `app.services.literature_metadata` and is applied at upload time
  via `ix_literature_doi_normalized` and
  `ix_literature_isbn_normalized`. This endpoint returns the raw
  stored string.
- `LiteratureSummary` already lives on every record type's
  scientific provenance block (`thermo.literature`,
  `kinetics.literature`, `statmech.literature`,
  `transport.literature`, `network.literature`,
  `network_solve.literature`, `frequency_scale_factor.source_literature`,
  `applied_energy_correction.source_literature`). The inverse
  records endpoint is the reverse direction of that pointer — it
  does **not** add a new relationship, only a new query path over
  the existing ones.
- Energy-correction-derived records (`applied_energy_correction`,
  `frequency_scale_factor`) are deliberately **not** counted today —
  they will be added once the corrections read surface lands (see
  audit §0.4 "Energy-correction scheme reads" deferred refinement).

## 8. Non-goals

- No literature mutation / write endpoints. The legacy
  `/api/v1/literature` table-style endpoints remain the only path
  to literature ingestion (today through workflow uploads).
- No citation-graph traversal beyond direct linked records (e.g.
  "papers cited by papers cited by …" or co-author networks).
- No full target-record inline expansion. Follow `endpoint`.
- No PDF / fulltext / abstract payload exposure.
- No bulk export.
- No RDKit substructure / similarity search.
- No schema redesign — the surface is purely a new read view over
  existing direct `literature_id` FKs.
- No ARC changes, no `tckdb-client` changes.

## 9. Implementation status

| Feature | Status |
|---|---|
| Literature detail | shipped |
| Inverse records endpoint | shipped |
| Authors in default response | shipped |
| Record-count summary | shipped |
| `record_type` filter | shipped |
| Review visibility (default-hide rejected/deprecated) | shipped |
| `include=review` badges on linked records | shipped |
| Public ref handle resolution | shipped |
| Phase D internal-ID stripping | shipped |
| Standalone `/literature/search` | **deferred** |
| Indirect linkage (`network_kinetics` → `network_solve`) | **deferred** |
| `species_entry` / `reaction_entry` linkage | **deferred** (no direct FK) |
| Author public refs | **deferred** (no `PublicRefMixin` on `Author` yet) |

## 10. Test plan

Tests live in `backend/tests/api/scientific/test_api_scientific_literature.py`.

Detail endpoint:

- by-ref, by-id resolution
- 404 unknown handle
- 422 wrong-prefix / malformed handle
- default response shape, authors present
- `include=authors` no-op
- record-counts reflect linked records
- `include=all` does not restore IDs
- `include=all,internal_ids` restores under policy
- `internal_ids` hidden by default
- recursive forbidden-payload walk
- empty review summary (literature is not reviewable)
- identifiers block

Records endpoint:

- by-ref, by-id resolution
- 404 unknown literature
- 422 wrong-prefix / malformed handle
- per-type linked summaries: calculation, thermo, kinetics,
  statmech, transport, network, network_solve
- `record_type` filter
- 422 unknown `record_type`
- pagination envelope
- deterministic ordering (`record_type` ASC)
- 422 client sort rejected
- endpoints are ref-based (no integer in URL tail)
- internal IDs hidden by default
- internal IDs restored under policy
- `include=review` adds badges; default omits them
- `rejected` records hidden by default; `include_rejected` restores
- recursive forbidden-payload walk

## 11. Open questions

- **Author public refs.** `Author` does not yet carry `PublicRefMixin`.
  The summary keeps an `author_ref` field reserved for the future.
  Decision needed: when authors are stabilized as identity entities
  (ORCID-first) vs name-merged duplicates, add the ref then.
- **Indirect citations.** Should `network_kinetics` records appear
  under the literature that backs their parent `network_solve`?
  Today they do not. If yes, gate them behind
  `relationship_kind="indirect"` and expose `include_indirect=true`.
- **Record-counts policy filter.** Counts today ignore review
  visibility. If a curator-oriented variant is needed where rejected
  records do not count, that can ship as a query knob without
  breaking the public shape.
- **Standalone literature search.** Out of scope for v0; if added,
  filters likely include `doi`, `title_contains`, `author`, `year`,
  `journal`, `has_linked_records`, `record_type`, plus the standard
  `include` / `offset` / `limit` knobs.
