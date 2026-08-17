# Curated product selection, read profiles, and citable dataset releases

Status: implemented (Stage 3). Supersedes the "Open design question" section of
[`scientific_product_candidacy.md`](scientific_product_candidacy.md).

This spec covers two things that only make sense together:

1. **Read profiles** — how a caller says whether they want the archive of
   candidates or the value TCKDB stands behind, and how the answer says which
   one they got.
2. **Dataset releases** — the attributed, append-only selection layer and the
   immutable, checksummed manifest that makes "the TCKDB value" citable and
   reproducible.

---

## 1. The problem being solved

TCKDB stores multiple candidate records for the same scientific quantity, on
purpose. Product tables are append-only and carry no `is_best` column; that is
a strength, and nothing here changes it.

But a community user asking *"what is the TCKDB heat of formation for
ethanol?"* was previously answered by `collapse=first` plus a named read-time
`selection_policy` — a **deterministic read heuristic**, evaluated from record
data alone, persisting nothing. That is not an expert recommendation. Nobody
signed it, no rationale exists, and re-running it after new data lands can
silently change the answer with no record that it did.

Stage 3 adds the missing layer: a **persisted, versioned, attributed
selection** that names the policy, the reviewer, the candidate, the rationale
and the release — without mutating the underlying scientific record.

---

## 2. Read profiles

### 2.1 The two contracts

| Profile | Returns | Says |
|---|---|---|
| `exploratory` (**default**) | every visible candidate, with its review/trust state | TCKDB recommends nothing. You are reading the archive. |
| `curated` | records at or above the `approved` review floor | A human reviewer accepted these. Nothing further is claimed — see `profile_recommendation`. |

Both are machine tokens (`ReadProfile` in `app/db/models/common.py`), never
prose. The prose lives in this document.

### 2.2 Why exploratory is the default

Every record on the deployed database is currently `under_review`. A curated
default would return empty result sets and read as a broken database.

The miscitation risk that creates is mitigated by **disclosure, not by
narrowing**:

- the resolved profile is echoed in *every* scientific response and *every*
  dataset manifest;
- a separate `profile_recommendation` token states whether the records carry a
  TCKDB endorsement, and is **not** a restatement of the profile:

  | Token | Means |
  |---|---|
  | `none` | candidates; TCKDB is not telling you which to use (exploratory) |
  | `approved_floor_only` | every record shown is `approved`, and **nothing more is claimed** (curated, general read surface) |
  | `tckdb_curated_release` | these records *are* the ones an attributed selection names — emitted **only** by `/scientific/releases/*` |

  `tckdb_curated_release` was previously emitted for any curated read once any
  release existed, because the release was resolved per *database* (the newest
  published one), not per record. That claimed an endorsement for records no
  curator had looked at, including candidates a curator had explicitly passed
  over, and the enum description shipped in `openapi.json` promised a
  per-record annotation that does not exist. Collapsing these tokens lets the
  API claim an endorsement it cannot back.

This default is expected to flip once a real curated corpus exists. That is a
product decision, recorded here so the reversal is deliberate rather than
accidental.

### 2.3 The echoed block

Present on every `/api/v1/scientific/*` response, inside the `request` echo:

```json
{
  "request": {
    "profile": "curated",
    "profile_recommendation": "approved_floor_only",
    "profile_release_ref": null,
    "...": "endpoint-specific echo fields"
  }
}
```

Of the 70 scientific operations, 60 are enveloped and 10 are not. All 70
report the profile:

* **60 enveloped JSON responses** get it from the single response boundary,
  `app/services/scientific_read/internal_ids.py::apply_internal_ids_visibility`,
  which calls `stamp_read_profile`. Every enveloped route returns through that
  function.
* **4 `/scientific/meta/*`** endpoints return a bare `{"results": [...]}` and
  add the same `request` block explicitly.
* **4 streaming exports** (`/scientific/export/*`, including the zipped
  CHEMKIN `manifest.json`) carry it in their manifest, which is what "echoed
  in every dataset manifest" means for them. They capture the profile
  *eagerly*, because a streaming generator can outlive the request-scoped
  context that holds it.
* **2 binary downloads** (`/scientific/artifacts/{sha256}/download` and
  `/scientific/releases/{handle}/artifacts/{path}`) have no JSON envelope and
  report `X-TCKDB-Read-Profile` as a response header.

### 2.4 How the profile is applied, and where

Resolved once, by an `async` router-level dependency
(`app/api/routes/scientific/_profile.py`) attached to the whole
`scientific_router`. Consequences worth stating:

- **Every** scientific operation declares `profile` and `release` in the
  published OpenAPI, including the POST searches. There is no endpoint that
  can quietly not support it. A test asserts this over the live app.
- The resolved value travels in a context variable rather than through 70 route
  signatures and ~40 service entry points. Threading it manually would
  guarantee that some endpoint eventually forgets it — and *a profile that only
  some endpoints honour is worse than none, because it teaches consumers to
  trust it.*
- The dependency is `async` deliberately. FastAPI runs sync endpoints in a
  worker thread whose context is **copied** from the request task, so a
  variable set in an async dependency reaches the endpoint; one set in a sync
  dependency would not.

Behaviourally, `curated` is applied at exactly **two** seams, which between
them cover the whole read surface:

1. `app/services/scientific_read/common.py::visible_statuses` — every *search*
   and *subresource list* read calls it. Under `curated` it raises the floor to
   `approved` and overrides the `include_rejected` / `include_deprecated`
   opt-ins: "show me what TCKDB stands behind, but also the rejected records"
   is not a coherent request.
2. `app/services/scientific_read/handles.py::resolve_path_handle` — every
   *detail-by-ref* read resolves its handle here, and roughly half the read
   services never call `visible_statuses` at all. Without this, `profile=curated`
   returned never-reviewed statmech and transport records under a curated echo.
   A record below the floor resolves to the same 404 `handle_not_found` an
   unknown ref produces, so review state is not leaked to anonymous callers.

   The floor there applies only to handles naming a record the endpoint
   *returns*. Scoping parents (`species_entry`, `reaction_entry`, …) are
   excluded: they say which records to look under, and gating them would hide
   an approved thermo behind an identity row that merely had not been reviewed
   — on a corpus where everything starts `under_review`, that 404s the entire
   curated surface. Structure, vocabulary and provenance (geometry, level of
   theory, literature) are excluded because they carry no reviewable claim.

The floor only ever **narrows**. A caller passing a stricter
`min_review_status` keeps it. Outside a scientific HTTP request (unit tests,
workers, CLI) the profile is `exploratory`, i.e. exactly the pre-Stage-3
behaviour.

`?release=` is **rejected** (422 `release_scoping_not_implemented`) on the
general read surface. It was previously accepted, resolved, and echoed while
being applied by no code path at all — the resolved release id was set and read
by nothing — so it read as scoping and did nothing. Release-scoped reads are
served exactly by `/scientific/releases/{tag}/selections` and the release
artifacts, and the error message says so.

---

## 3. The selection layer

### 3.1 Tables and buckets

Each table sits in exactly one of the four buckets from
[`system_flow.md`](../../../docs/guides/system_flow.md) §1.

| Table | Bucket | Role |
|---|---|---|
| `curation_policy` | Identity | Named, versioned expert rubric. Deduped on `(name, version)`. |
| `dataset_release` | Curation | The citable unit: licenses, citation string, contact, changelog, DOI. |
| `release_selection` | Curation | One attributed, append-only decision. |
| `release_manifest` | Result | Frozen, checksummed, immutable description of a published release. |
| `release_artifact` | Result | One checksummed file inside a manifest. |

### 3.2 Why the selection never touches the science

`release_selection` addresses its target with a loose typed
`(record_type, record_id)` pointer — the same shape `record_review` and
`scientific_record_supersession` already use. A real FK would need one nullable
column per product table, and would put selection state adjacent to the values,
which is exactly the invitation this design refuses.

Nothing in `app/services/release/curation.py` writes to a scientific table. A
test asserts that a selection leaves the selected record's row byte-identical.

### 3.3 Superseding appends; it never edits

Changing a curated decision inserts a new row with
`action='supersede'` and `supersedes_selection_id` naming the row it replaces.
The earlier rationale, curator and timestamp stay exactly as written.

Enforced three ways:

1. No application code path issues `UPDATE`/`DELETE` on `release_selection`.
2. A database trigger rejects `UPDATE` and `DELETE` (migration `e3f4a5b6c7d8`),
   so this holds for direct SQL clients too — mirroring
   `record_reproducibility_assessment`.
3. `supersedes_selection_id` is `UNIQUE`, so a selection can be replaced at most
   once and supersession chains stay linear and auditable.

There is deliberately **no** `is_current` column. "Which selection stands" is
computed as head-of-chain-and-not-withdrawn. A mutable flag would be a second
source of truth that could disagree with the ledger.

`action='withdraw'` retires a selection with no replacement: the release then
recommends nothing for that subject, which is a legitimate curatorial position
and better than a stale recommendation.

### 3.4 Relationship to what already existed

- **`record_review`** answers *is this record trustworthy?* One current state
  per record, with an append-only event log. Orthogonal: a record can be
  `approved` and still not be the one a release selects.
- **`scientific_record_supersession`** answers *was this record replaced by a
  better measurement of the same thing?* That is a statement about the science.
  A release supersession is a statement about a *curator's opinion*; both
  records remain equally valid science.

  Holding the two apart is not the same as ignoring one of them. A **standing**
  selection whose selected record has since been scientifically superseded is
  the most misleading state this API can serve: a DOI-bearing release points a
  reader outside the project at a number, the citation resolves cleanly, and
  nothing says the number has been corrected. The selection ledger therefore
  carries `record_supersession` alongside `supersedes_selection_ref` — the
  first is the science, the second is the opinion, and the field names keep
  them distinguishable. Its contract is in
  [`accepted_science_immutability.md`](accepted_science_immutability.md)
  §"Announcing a replacement on a read".

  It is **not** `live_divergence`, and `live_divergence` was never going to
  become it: that is a per-file byte digest answering "has the database moved
  since publication", advisory and routinely `true`, with no ability to name a
  record. It also cannot be frozen into the release, because a release
  published before the correction existed cannot have recorded it, and
  rewriting the frozen artifacts to add it would break their published
  digests. The notice is computed live, at read time, from the supersession
  ledger.
- **`ConformerSelection`** elects which *conformer* a product should be derived
  from. `ReleaseSelection` elects which *product record* to recommend. Neither
  subsumes the other.
- **Read-time `SelectionPolicy`** (`default` / `latest` / `most_reviewed`) is
  unchanged and still non-persisted. `curation_policy` is its persisted,
  human-authored counterpart. The read-time enum still deliberately omits
  `benchmark_reference` / `curator_pick`: those are now expressed as release
  selections, not as read knobs.

### 3.5 Selectable record types

`thermo`, `statmech`, `transport`, `kinetics`, `network_solve`,
`transition_state_entry` (`SELECTABLE_RECORD_TYPES`, enforced by a CHECK
constraint). A release recommends scientific product values and the entries
carrying them — not submissions, artifacts, or raw identity rows.

A selection's target must additionally be **at or above the `approved` review
floor**, checked when the selection is appended and re-checked at publication
(review moves in between). Two independent reasons: the accepted-science
immutability trigger only freezes a product row once it is approved, so
selecting an unapproved record would let the value under a published
recommendation be edited afterwards; and `profile=curated` refuses to *show* an
unapproved record, so recommending one would have the API hiding and endorsing
the same row.

A release that selects nothing cannot be published — a citable, DOI-able
release containing nothing is not a release.

---

## 4. Three formats, three contracts

This is the distinction the Stage 3 review called out as currently conflated.
It is stated in naming, in each format's own manifest, and here.

| | `tckdb.dataset_release.v1` | `tckdb.archive.v1` | `tckdb.export.v0` |
|---|---|---|---|
| **Purpose** | cite and reproduce a curated scientific dataset | operator disaster recovery | convenience projection (NDJSON / CHEMKIN / ML) |
| **Audience** | anyone with a citation | database operator | a modelling workflow |
| **Auth** | public | admin/operator | curator/admin |
| **Immutable** | yes — frozen + checksummed | no (a backup is point-in-time) | no |
| **Lossless / re-ingestible** | **no** | **yes** | **no**, and says so |
| **Ships candidates + review history** | **yes** | yes (whole DB) | no — selected projection only |
| **Per-file SHA-256** | yes | yes | no |
| **Code** | `app/services/release/` | `app/services/archive/` | `app/services/scientific_read/export.py` |

A dataset release is **not a backup**: it deliberately omits raw artifact
bytes, credentials, and everything outside the release's selection subjects.
Restoring a database from a release is impossible and must not be attempted.

Conversely, the recovery archive is **not citable**: it is a point-in-time dump
of a mutable database with no attribution, no rationale, and no frozen digest
over a scientific claim.

---

## 5. The manifest

### 5.1 What a release ships

Four NDJSON artifacts, each with a SHA-256, byte count and record count:

| Path | Contains |
|---|---|
| `selected_records.ndjson` | each standing selection, its full record payload, and its attribution (policy version, curator, rationale, timestamp) |
| `candidate_records.ndjson` | every candidate for the same subjects, selected or not, with review state |
| `review_history.ndjson` | `record_review` state and event log for every record above |
| `selection_ledger.ndjson` | every selection row including superseded and withdrawn ones |

Shipping all four is the point. A release that shipped only the winners would
be exactly the "trust us" artifact this layer exists to avoid, and would fail
the Stage 3 exit criterion, which requires a citer to *still retrieve the
underlying candidates and review history*.

Record payloads include the record's own row **plus its value-bearing child
tables** (`thermo` + its NASA polynomial and its `applied_group_additivity`
provenance, `kinetics` + its Arrhenius/Chebyshev/PLOG parameters, and so on),
stated explicitly in `app/services/release/records.py::RECORD_VALUE_TABLES`.
Generic foreign-key traversal was tried and rejected: from `statmech` it
reaches `thermo` (a sibling product, not a part of statmech) and from
`transition_state_entry` it reaches the whole calculation graph.

**Every line is interpretable offline.** Each carries a `subject` block with
chemical identity (species and species-entry refs, SMILES, InChIKey, charge,
multiplicity, electronic state; reactant/product SMILES for a reaction) and a
`provenance.calculations` block giving each cited calculation's ref, level of
theory and software. Inside `record`, foreign keys become public refs
(`statmech_ref`, `literature_ref`, `calculation_ref`) driven by real SQLAlchemy
FK metadata rather than a hand-written list.

This replaced blanket `*_id` stripping with no substitute, which produced a
deposited file stating a heat of formation for an opaque handle with no SMILES,
no level of theory, no software and no citation — strictly less science than
the unauthenticated read API. The manifest's `omits` block now states what is
actually omitted.

Some FK targets carry no public ref because they are *intra-network* structure
rather than addressable records. `network_channel` and `network_state` get a
declared natural key instead (`RECORD` → `NATURAL_KEYS`), emitted as
`channel_key` and `state_composition_hash`. Without it, a released
`network_solve` shipped channel barriers and well energies naming no channel
and no state — numbers that could not be attached to anything. Targets with
neither a ref nor a natural key keep being dropped, which is correct for
`created_by` (a user primary key must never reach a public artifact) and for
artifact ids, whose omission `omits` already declares.

`kinetics_interpretation_assignment` ships with `kinetics`. It carries
`standard_state_convention` and `ensemble_policy` — a rate coefficient reported
under a different standard state is a different number — so it is
interpretation provenance, not the "curation overlay" it was once excused as.

Completeness is enforced by `RECORD_CHILD_EXCLUSIONS`, keyed on
`(parent_table, child_table)` and walked into shipped children so grandchildren
are covered. The pair key matters: four tables are legitimately shipped under
one parent and excused under another, so a single-name excuse would let a
refactor drop `network_kinetics` from `network_solve` and still pass — silently
removing k(T,P) from every PDep release.

### 5.2 Version binding

`release_manifest` records, and the manifest document reports:

- `alembic_revision` — the schema the data was read under;
- `backend_version`, `schemas_package_version` — the code that produced it;
- `review_policy_version` — what "approved" meant when it was cut;
- the curation policy's `(name, version)`;
- `recovery_archive_schema` — recorded so a reader can see it is a *different*
  contract;
- `data_license` and `code_license`, snapshotted;
- `profile` — always `curated`, recorded rather than assumed so the document is
  self-describing outside this codebase.

### 5.3 Why a release is frozen, not re-derived

Publication is the only write. `release_manifest.document_json` holds the
manifest document, `content_sha256` is its canonical-serialization digest,
`release_artifact.content` holds each file's bytes, and every claim the
document makes about the release and its policy is copied into snapshot
columns. Everything served afterwards comes from those rows.

The original design re-derived both the document and the artifacts from the
live database on every read and returned 409 on any mismatch. That was wrong in
two ways that only appear in the steady state:

* **One ordinary upload destroyed the citation.** Publishing a release and then
  uploading one more thermo for a released species changed the candidate set,
  so `candidate_records.ndjson` legitimately differed and stopped downloading.
  On a corpus where review is expected to progress, the interval in which a
  release remained citable was effectively zero. Worse, the design could not
  distinguish corpus growth from tampering, so the runbook told operators that
  the only cause that will ever actually occur was "an append-only table was
  written to directly".
* **Recording the DOI broke the digest permanently.** The document rendered
  `release.doi` and `release.status` from the live row, so the last step of
  publishing — and withdrawing — moved the document. Every genuinely deposited
  release therefore reported `verified: false`, and the integrity signal was
  true only for releases nobody had cited.

Both are normal events. A release is a snapshot claim; new science arriving
afterwards is the system working.

Two questions are now reported separately, and only one of them can fail:

| Field | Question | Depends on the live corpus? | Failure means |
|---|---|---|---|
| `verification` | Is the frozen release intact? | No | the stored rows were tampered with |
| `live_divergence` | Has the database moved since publication? | Yes | nothing — it is advisory and routinely `true` |

`GET .../artifacts/{path}` serves the stored bytes and never 409s for
divergence. It self-checks the stored bytes against their recorded digest and
returns 500 only if that fails, which is a storage-integrity fault.

Contract tags (`manifest_schema`, `recovery_archive_schema`) and the artifact
path list are read from the manifest row rather than from live module
constants, so bumping a contract or adding an artifact kind cannot retroactively
change a published digest.

## 6. HTTP surface

### Public reads (unauthenticated — a citation must resolve for anyone)

```
GET /api/v1/scientific/releases[?status=]
GET /api/v1/scientific/releases/{handle}
GET /api/v1/scientific/releases/{handle}/manifest
GET /api/v1/scientific/releases/{handle}/selections[?include_superseded=]
GET /api/v1/scientific/releases/{handle}/artifacts/{path}
```

`{handle}` accepts the public ref (`rel_...`) **or** the citable tag
(`2026.07.0`), because that is what a paper quotes.

Withdrawn releases are listed, not hidden: a reader holding an old citation
needs to discover that it was retracted. Superseded selections are returned by
default for the same reason.

### Curator-gated writes

```
POST /api/v1/releases/policies
POST /api/v1/releases
POST /api/v1/releases/{handle}/selections
POST /api/v1/releases/{handle}/selections/{selection_ref}/supersede
POST /api/v1/releases/{handle}/selections/{selection_ref}/withdraw
POST /api/v1/releases/{handle}/publish
POST /api/v1/releases/{handle}/withdraw
POST /api/v1/releases/{handle}/doi
```

No `PATCH`, no `DELETE` on selections. Supersede and withdraw return **201**
because they create rows.

Bodies address records by **public ref**, never by database id. The *subject*
of a selection is derived from the record rather than supplied, so a selection
cannot be recorded against the wrong species entry or reaction entry.

`publish` freezes the manifest in the same operation: a published release
without a manifest would be citable but unverifiable, the worst of both.

---

## 7. DOIs

**No DOI is minted by this implementation.** `dataset_release.doi` is nullable
and stays `NULL` until a deposit is actually made; `record_doi` records one
afterwards and refuses to overwrite a different value, because that would
silently repoint a citation.

A DOI is not retractable and the corpus is not yet publishable, so minting one
now would be an irreversible mistake made to satisfy a checklist. The procedure
to follow when a paper tag is cut is
[`cutting_a_dataset_release.md`](../deployment/cutting_a_dataset_release.md).

---

## 7a. Known operational boundaries

Two things a future reader will otherwise rediscover the hard way. Both were
reviewed and accepted; neither is a defect to be fixed silently.

### Frozen release bytes are inlined in the recovery archive

`release_artifact.content` is archived inline as base64 in `rows.ndjson`
(`app/services/archive/core.py`, `$bytes` codec) rather than through the
`blobs/` sidecar the archive already uses for calculation artifacts. It is
correct and lossless — an export → wipe → restore cycle returns byte-exact
artifact content and the restored database still passes `verify_release` — but
base64 inflates the payload by ~4/3 and puts a whole release on a **single
NDJSON line**.

That is a scaling characteristic, not a bug. It is fine while releases are
small, and it deliberately keeps the archive's row-and-hash model uniform.

**Trigger for revisiting:** the first release large enough that a single line in
`rows.ndjson` is uncomfortable to stream or diff — in practice, a release whose
artifacts exceed a few tens of MB, or any point at which archive write/restore
memory becomes a concern. At that point move release bytes to the existing
`blobs/` sidecar, which already has the streaming and integrity machinery.
Doing it earlier buys nothing and adds a second storage path to keep correct.

### `ALTER TABLE … DISABLE TRIGGER` and table ownership

The append-only guarantees on `release_selection`, `release_manifest` and
`release_artifact` (and on `record_review_event`,
`record_reproducibility_assessment`, and the accepted-science tables) are
enforced by database triggers. **A table owner or superuser can disable a
trigger**; an ordinary DML role cannot.

That is exactly why
[`database_roles.md`](../deployment/database_roles.md) exists and predates
Stage 3: under the prescribed three-role split the API and worker run as
`DB_USER`, which owns nothing, so the triggers are meaningful against the
application account. The gap is real only on a deployment that has **not**
applied the split and runs the application as an owning role — which is the
default for local development, and is what `db-roles check` is for.

Closing it further is deliberately **out of Stage 3's scope**:

1. It is neither new nor release-specific. Stage 3 added five tables that
   inherit the posture already documented for every other guarded table; it
   weakened nothing.
2. The fix is a roles change — moving an application off an owning role affects
   **every** guarded table in the schema, plus migrations and deployment
   grants. Attaching that blast radius to a curated-release stage would put a
   large, unreviewed change in the wrong place.

The honest statement of the guarantee is therefore: the triggers stop
application bugs and ordinary SQL clients, including `TRUNCATE`; on a correctly
roled deployment they also stop the runtime account outright; they do not stop
a deliberate operator holding owner rights. Verify the posture with the
`db-roles check` subcommand rather than assuming it.

## 8. What is deliberately not built

- **Release-scoped filtering of general product reads.** Not implemented, and
  `?release=` is now **refused** rather than accepted and ignored. The question
  is answered precisely and completely by
  `/scientific/releases/{tag}/selections` and the release artifacts. Bolting a
  partial version onto ~40 search services would produce a filter that behaved
  differently per endpoint — the failure mode this spec opens by warning
  against.
- **Per-record release annotation on the general read surface.** Consequently
  `profile=curated` reports `approved_floor_only` there, never
  `tckdb_curated_release`. Making the endorsement per-record would mean
  embedding a selection badge in every product read schema; until there is a
  consumer for that, the honest token plus a dedicated release surface is the
  better trade.
- **Automatic selection.** Nothing proposes selections. A recommendation
  without a named human behind it is the heuristic this layer replaces.
- **Mutable release metadata after publication.** Only `doi` may be attached
  post-publication, and only once.
