# Public identifier / stable-handle policy for hosted TCKDB

**Status:** Design / pending implementation
**Author:** Calvin
**Date:** 2026-05-10
**Code change in this phase:** **none** — design only.

---

## Purpose

Define a public, stable, opaque-from-client-perspective identifier scheme
("ref") for TCKDB scientific-read responses, alongside a phased plan for
exposing those refs without breaking the current integer-PK contract.

Today the scientific read API exposes raw `BigInteger` primary keys
throughout responses (`species_entry_id`, `kinetics_id`, `calculation_id`,
`level_of_theory_id`, etc.). This is acceptable for an internal/dev MVP
but is the wrong long-term public contract for a hosted scientific
information system. This document specifies what a public ref looks like,
which records get one, how routes accept them, and how the response
shape evolves.

This is **policy + design** — no schema changes, no migrations, no API
behavior changes happen in this document.

---

## Definitions

| Term | Meaning |
|---|---|
| **Internal PK** | Database-local `BigInteger` primary key. Used by the ORM, joins, admin tooling, and (currently) the public API. Not stable across instances or imports. |
| **Public ref** | A short, URL-safe, opaque-or-content-derived string published as the long-term handle for a record. Stable within an instance; for content-identity records, stable across instances when canonical content matches. |
| **Content-derived ref** | A ref derived deterministically from the canonical identity of a record (e.g. `LevelOfTheory.lot_hash`). Two instances that hold the same scientific entity produce the same ref. |
| **Opaque ref** | A ref generated without reference to record content (ULID-like). Stable within an instance; not stable across instances. |

The two categories differ along one axis: **does the same scientific
entity get the same handle on two TCKDB instances?** For identity-bearing
tables (LoT, species, chem_reaction, software, literature), the answer
should be **yes**. For provenance/result-bearing tables (calculation,
kinetics, thermo, conformer_observation, geometry), the answer should
be **no** — those are events on a particular instance, and an upload of
the same calculation to two instances is two events.

---

## Why this matters now

Three concrete failure modes show up if integer PKs stay public:

1. **Citation churn.** A workflow tool that records `species_entry_id=31`
   from a hosted TCKDB pin can break silently if the database is restored
   from backup, re-bootstrapped, or migrated. The ID has no content-level
   meaning.
2. **Cross-instance unfriendliness.** A user querying staging and prod
   gets different IDs for the same thermo record. Tools that learn from
   one instance can't trivially port their reuse decisions to the other.
3. **Enumeration / scraping risk.** Sequential integer PKs leak the
   total count of objects (and roughly the upload schedule) of every
   table. Refs hide that signal.

The fix is additive: ship `*_ref` fields alongside the existing `*_id`
fields, switch examples and docs, and (later, behind an explicit flag)
hide PKs.

---

## Records classification

| Record type | Current PK | Needs public ref? | Ref prefix | Ref generation | Expose by default? | Accept in routes? | Notes |
|---|---|---|---|---|---|---|---|
| `species` | `species.id` | ✅ Yes | `spc_` | **Content-derived** from canonicalized `(inchi_key, charge, multiplicity, stereo_kind)` | Yes | Optional v1 | Identity table; cross-instance stable. |
| `species_entry` | `species_entry.id` | ✅ Yes | `spe_` | **Opaque** (ULID) | Yes | Yes | Per-instance event (specific stationary point + electronic state + isotopologue under a `species`). |
| `chem_reaction` | `chem_reaction.id` | ✅ Yes | `rxn_` | **Content-derived** from `(stoichiometry_hash, reaction_family_id?)` | Yes | Optional | Identity-ish; participant-set hash should round-trip across instances. |
| `reaction_entry` | `reaction_entry.id` | ✅ Yes | `rxe_` | **Opaque** (ULID) | Yes | Yes | Per-instance event under a `chem_reaction`. |
| `thermo` | `thermo.id` | ✅ Yes | `thm_` | **Opaque** (ULID) | Yes | Optional | Provenance event; not content-derived. |
| `kinetics` | `kinetics.id` | ✅ Yes | `kin_` | **Opaque** (ULID) | Yes | Optional | Provenance event. |
| `calculation` | `calculation.id` | ✅ Yes | `calc_` | **Opaque** (ULID) | Yes | Yes | Provenance event. Heavily referenced — needs a ref. |
| `geometry` | `geometry.id` | ✅ Yes | `geom_` | **Content-derived** from existing `geom_hash` | When geometry metadata is included | Optional | Identity-ish (graph + coords). Hash already exists. |
| `conformer_group` | `conformer_group.id` | ✅ Yes | `cg_` | **Opaque** (ULID) | When conformer block is populated | No | Per-species-entry event. |
| `conformer_observation` | `conformer_observation.id` | ✅ Yes | `co_` | **Opaque** (ULID) | When conformer block is populated | No | Provenance event. |
| `conformer_assignment_scheme` | `conformer_assignment_scheme.id` | ✅ Yes | `cas_` | **Content-derived** from `(name, version, scope)` | When in conformer block | No | Identity table; small, stable. |
| `statmech` | `statmech.id` | ✅ Yes | `sm_` | **Opaque** (ULID) | Yes | Optional | Provenance event. |
| `transport` | `transport.id` | ✅ Yes | `trn_` | **Opaque** (ULID) | Yes | Optional | Provenance event. |
| `transition_state` | `transition_state.id` | ✅ Yes | `ts_` | **Opaque** (ULID) | Yes | No | Per-reaction-entry concept; not content-identity. |
| `transition_state_entry` | `transition_state_entry.id` | ✅ Yes | `tse_` | **Opaque** (ULID) | Yes | Yes | Per-instance event. |
| `level_of_theory` | `level_of_theory.id` | ✅ Yes | `lot_` | **Content-derived** from existing `lot_hash` | **Always** (every calculation/result that quotes an energy) | Yes | See [LoT-specific section](#level-of-theory-deep-dive) below. |
| `software` | `software.id` | ✅ Yes | `sw_` | **Content-derived** from canonicalized `name` (lowercase) | When `software_release` is referenced | No (rarely needed alone) | Small identity table. |
| `software_release` | `software_release.id` | ✅ Yes | `swr_` | **Content-derived** from `(software.id-canonical, version, revision, build)` | Yes | No | Identity table; cross-instance stable. |
| `workflow_tool` | `workflow_tool.id` | ✅ Yes | `wt_` | **Content-derived** from canonicalized `name` | When `workflow_tool_release` is referenced | No | Small identity table. |
| `workflow_tool_release` | `workflow_tool_release.id` | ✅ Yes | `wtr_` | **Content-derived** from `(workflow_tool.id-canonical, version, revision, build)` | Yes | No | Identity table; cross-instance stable. |
| `literature` | `literature.id` | ✅ Yes | `lit_` | **Content-derived** from normalized DOI / normalized ISBN / canonical (title, year, volume, pages) fallback | Yes | Optional | DOI/ISBN already have normalization indexes — exploit them. |
| `frequency_scale_factor` | `frequency_scale_factor.id` | ⚠ When exposed | `fsf_` | **Content-derived** from `(level_of_theory.lot_hash, scope)` | Only when surfaced (rare in scientific reads) | No | Reference table. |
| `energy_correction_scheme` | `energy_correction_scheme.id` | ⚠ When exposed | `ecs_` | **Content-derived** from `(name, version)` | Only when surfaced | No | Reference table. |
| `submission` | `submission.id` | ⚠ Internal-tilted | `sub_` | **Opaque** (ULID) | Curator/admin context only | No | Provenance audit. |
| `record_review` | `record_review.id` | ❌ No | n/a | n/a | Never (use `record_type` + record_ref instead) | n/a | Polymorphic; the badge is what's public. |

**Rules for the table:**

- "Needs public ref?" ✅ means a ref column is required, written, and indexed.
- "Expose by default?" governs the default response shape for unauthenticated/public reads.
- "Accept in routes?" governs whether the route's path parameter should accept the ref form (in addition to or instead of the integer PK).

---

## Level of Theory deep dive

LoT is the **highest-priority** record type for ref stability because
**every quoted energy is meaningless without it**. A search response that
returns `energy_hartree=-95.7355…` without a stable, cross-instance LoT
handle is a citation hazard.

### LoT identity fields

The current `level_of_theory` model defines identity through these eight
columns (all optional except `method`):

```
method            — required, e.g. "dlpno-ccsd(t)-f12"
basis             — e.g. "cc-pvtz-f12"
aux_basis         — e.g. "cc-pvtz-f12-mp2fit"
cabs_basis        — e.g. "cc-pvtz-f12-cabs"
dispersion        — e.g. "d3bj"
solvent           — e.g. "water"
solvent_model     — e.g. "smd"
keywords          — free-text keywords/flags
```

The model also already stores `lot_hash: CHAR(64)` — a canonical hash
over the eight identity fields, used today for LoT deduplication during
ingestion. **Reuse it.** The `lot_ref` is just `"lot_" + lot_hash` (or a
URL-safe encoding of it).

### LoT ref generation

```
lot_ref = "lot_" + base32_lower(sha256_bytes(canonical_identity))[:26]
```

Or, if leveraging the existing `lot_hash` directly:

```
lot_ref = "lot_" + lot_hash[:26]   # truncated to 26 chars for ULID-shaped length
```

Either form is content-derived: same canonical identity → same ref on
any TCKDB instance. The truncation length (26) matches ULID's character
count for visual consistency with opaque refs; collision risk over 2^130
states is negligible for any TCKDB-scale corpus.

### LoT in responses

Every response that includes a calculation, thermo, kinetics, or
energy-bearing record **must** include a complete `level_of_theory`
object with at minimum:

```json
{
  "level_of_theory": {
    "ref": "lot_…",
    "label": "dlpno-ccsd(t)-f12/cc-pvtz-f12",
    "method": "dlpno-ccsd(t)-f12",
    "basis": "cc-pvtz-f12",
    "dispersion": null,
    "solvent": null
  }
}
```

The `label` is a display-only slash-joined summary of the populated
identity fields. The four fields (`method`, `basis`, `dispersion`,
`solvent`) are the most-used identity components and are surfaced by
default; the remaining four (`aux_basis`, `cabs_basis`, `solvent_model`,
`keywords`) are present when populated, omitted otherwise.

### LoT in queries

Anywhere `level_of_theory_id=…` is currently accepted, also accept
`level_of_theory_ref=…`:

- `/scientific/species-calculations/search`
- `/scientific/thermo/search`
- `/scientific/species-entries/{id}/thermo`
- `/scientific/kinetics/search`
- `/scientific/reaction-entries/{id}/kinetics`

**Conflict policy:** if both `level_of_theory_id` and
`level_of_theory_ref` are supplied, they **must resolve to the same
LoT** or the request returns 422 with a deterministic error code:

```
422 Unprocessable Entity
{ "code": "level_of_theory_handle_conflict",
  "detail": "level_of_theory_id and level_of_theory_ref resolve to different rows." }
```

If only one is supplied, the other is inferred and echoed in the
response's `request.filter` block.

---

## Ref format

### Recommended hybrid

| Use case | Format | Example | Rationale |
|---|---|---|---|
| **Content-identity** (LoT, species, chem_reaction, geometry, software, software_release, workflow_tool, workflow_tool_release, literature, conformer_assignment_scheme) | `prefix_<26-char base32 lowercase of sha256(canonical_identity)>` | `lot_5bxnghp44yj0hf2vp9k1a6tk20` | Cross-instance stable; resists enumeration; URL-safe; matches ULID character length so refs visually align in tables. |
| **Provenance event** (calculation, kinetics, thermo, statmech, transport, conformer_group, conformer_observation, transition_state, transition_state_entry, species_entry, reaction_entry, submission) | `prefix_<26-char ULID>` | `calc_01J9X8K3Y2RM4F0X8K3Y2RM4F0` | Per-instance unique, sortable by time of generation, no content leakage, URL-safe. |

Choosing **base32 lowercase** for both forms keeps refs visually
indistinguishable from ULIDs and avoids URL-encoding pitfalls. The
per-table prefix (3–5 chars + underscore) gives readers a strong type
signal without a database lookup — a hosted user can tell `lot_…` from
`calc_…` from `kin_…` at a glance.

### Why not UUIDs or pure hashes

- **UUIDs (`<prefix>_550e8400-e29b…`):** longer, hyphenated, not
  ULID-shaped, harder to type and scan visually.
- **Pure hashes** without a prefix lose the type signal in error
  messages and in URLs.
- **Random opaque refs** for content-identity tables would defeat the
  cross-instance stability win.

### Length and collision

26-character base32 = 130 bits of entropy. For ULIDs that includes 48
bits of timestamp + 80 bits of randomness; collision probability over
even a billion records is negligible. For content-derived refs, the
truncated SHA-256 retains 130 bits, which is also collision-safe at
TCKDB scale.

---

## Cross-instance / export-import stability

| Ref kind | Stable across local TCKDB ↔ staging ↔ hosted? | Stable across export/import bundles? | Stable across replayed uploads? |
|---|---|---|---|
| Content-identity (LoT, species, chem_reaction, geometry, software*, workflow_tool*, literature) | ✅ When canonical content matches | ✅ Same content → same ref on import | ✅ Same content → same ref on replay |
| Opaque (calculation, kinetics, thermo, conformer*, transition_state*, species_entry, reaction_entry, submission) | ❌ No | ⚠ **Preserved if explicitly carried in the bundle**, otherwise re-generated | ⚠ Preserved if the replay carries the original ref; otherwise re-generated |

For export/import behavior, contribution bundles should carry the
opaque refs of every record they include. On import:

- If the importing instance does not yet have a record with that ref,
  preserve it.
- If the importing instance already has a record with that ref but a
  different content, refuse the import with a deterministic conflict
  error (this should be very rare).
- For content-identity refs, the importer recomputes locally; the
  bundle's value serves as a cross-check.

This is the same shape as how the existing upload idempotency and
contribution-bundle workflows already handle deduplication.

---

## Route behavior

### Recommendation

Existing detail routes that take an integer ID accept a **handle** that
can be **either** the integer PK or the public ref. The route inspects
the path-param string and dispatches:

```
GET /api/v1/scientific/species-entries/{species_entry_handle}/thermo
GET /api/v1/scientific/reaction-entries/{reaction_entry_handle}/kinetics
GET /api/v1/scientific/reaction-entries/{reaction_entry_handle}/full
```

`{handle}` resolution:

- All-digits → look up by integer PK.
- Starts with the documented type prefix (`spe_`, `rxe_`, `lot_`, etc.)
  → look up by ref.
- Anything else → 422 `invalid_handle`.

This avoids URL bifurcation (`by-id/` vs `by-ref/`) and lets clients
migrate one call site at a time.

**Why not `by-ref/{ref}`?** Two URL paths for the same logical resource
double the OpenAPI surface, double the route tests, and force clients
to make a routing decision every call. A single dispatching path
parameter keeps the URL contract small while supporting both forms
during the transition window.

### Query-param refs

Anywhere `<thing>_id=` is currently a query param, also accept
`<thing>_ref=`. Conflict policy is the same as the LoT policy above —
both must resolve to the same row or 422.

The currently-affected query params are:

```
level_of_theory_id          (4 endpoints)
species_id                  (species-calculations search)
species_entry_id            (species-calculations search)
```

---

## Response-shape policy

### Short-term (compatibility window)

Add **sibling** `*_ref` fields next to existing `*_id` fields. No
existing client breaks; new clients can switch to refs at their own pace.

```json
{
  "species": {
    "species_id": 12,
    "species_ref": "spc_5bxnghp44yj0hf2vp9k1a6tk20",
    "species_entry_id": 31,
    "species_entry_ref": "spe_01J9X8K3Y2RM4F0X8K3Y2RM4F0",
    "canonical_smiles": "C[CH2]",
    "inchi_key": "ZGEGCLOFRBLKSE-UHFFFAOYSA-N",
    "charge": 0,
    "multiplicity": 2
  }
}
```

For nested provenance objects that are already nested, do the same:

```json
{
  "level_of_theory": {
    "level_of_theory_id": 8,
    "ref": "lot_5bxnghp44yj0hf2vp9k1a6tk20",
    "method": "wb97xd",
    "basis": "def2tzvp",
    "label": "wb97xd/def2tzvp"
  }
}
```

(Nested objects can use `ref` rather than `<typename>_ref` because the
parent key already disambiguates the type.)

### Long-term (post-compatibility window)

When the compatibility window ends, integer PKs are hidden by default:

```json
{
  "species": {
    "species_ref": "spc_…",
    "species_entry_ref": "spe_…",
    "canonical_smiles": "C[CH2]",
    "…": "…"
  }
}
```

Integer PKs return only when the request opts in via `include=internal_ids`
or when the caller is authenticated as a curator/admin (see next section).

---

## Include policy for internal IDs

> The full Phase D policy spec — field inventory, caller-context
> matrix, response-shape transition strategy, test impact, and a
> concrete Phase D.1 implementation plan — lives in
> [`internal_ids_visibility_policy.md`](./internal_ids_visibility_policy.md).

Add a new include token: `include=internal_ids` (legal at every
scientific endpoint).

**Rules:**

- **Public anonymous responses:** refs always shown; integer PKs shown
  during the compatibility window; integer PKs omitted by default after
  the window (`include=internal_ids` opts back in).
- **Curator/admin responses:** integer PKs always available regardless
  of the include flag, since debugging and database introspection are
  legitimate curator tasks.

The token sits alongside the existing per-endpoint include vocabulary
(L4) and follows the same validation rules — unknown token → 422,
`internal_ids` is legal at every scientific endpoint.

---

## Provenance arrays

Today, provenance carries bare integer arrays:

```json
{
  "supporting_calculation_ids": [60, 61, 62],
  "input_geometry_ids": [499],
  "output_geometry_ids": [500]
}
```

Long-term shape — replace with arrays of small objects:

```json
{
  "supporting_calculations": [
    { "calculation_ref": "calc_…", "calculation_id": 60 },
    { "calculation_ref": "calc_…", "calculation_id": 61 },
    { "calculation_ref": "calc_…", "calculation_id": 62 }
  ],
  "input_geometries":  [ { "geometry_ref": "geom_…", "geometry_id": 499 } ],
  "output_geometries": [ { "geometry_ref": "geom_…", "geometry_id": 500 } ]
}
```

**Migration mechanic:** during the compatibility window, both shapes
appear. Old key (`supporting_calculation_ids`) carries the integer
array; new key (`supporting_calculations`) carries the object array.
After the window, the integer-array key is dropped.

The same rule applies to `output_geometry_ids` / `input_geometry_ids`
on the species-calculations endpoint.

---

## Backward compatibility — phased plan

| Phase | What lands | Breaking? |
|---|---|---|
| **Phase A — schema + writes** | (a) Add `<table>_ref VARCHAR UNIQUE NOT NULL` columns to every table that needs a ref, defaulted via a single Alembic migration. (b) Backfill existing rows: content-derived refs computed from existing identity columns; opaque refs generated as ULIDs. (c) Service-layer ref helpers + tests. | ❌ No. |
| **Phase B — read responses** | (a) Add `*_ref` sibling fields to every scientific read response. (b) Add the `supporting_calculations` (object array) sibling to every `supporting_calculation_ids` (integer array). (c) Update `tckdb-client` so each chemistry-first method exposes refs. (d) Doc update — switch all examples to refs. | ❌ No. Old `*_id` fields remain. |
| **Phase C — route ref acceptance** | (a) Detail routes accept either integer or ref via the path-param dispatching rule. (b) Query-string `<thing>_ref=` is accepted everywhere `<thing>_id=` is. (c) Conflict policy enforced. (d) Tests cover ref/PK equivalence. | ❌ No. Integer-ID paths still resolve. |
| **Phase D — public PK hiding** *(D.1 shipped)* | (a) `include=internal_ids` token added (gated by `settings.allow_public_internal_ids`; silently dropped when disallowed). (b) Default scientific read responses strip every `*_id` field, every bare `*_ids` array, and the non-suffix internal-PK fields (`LiteratureSummary.id`, `ReactionEntrySummary.id`, `ReviewRecordEntry.record_id`). Ref siblings stay visible; ref-bearing object arrays (`input_geometries`, `supporting_calculations`, …) replace the bare-id arrays. (c) `include=all` does **not** expand to include `internal_ids` — must be requested explicitly. (d) Route input behavior unchanged — integer path handles and `*_id` query filters still work. (e) `request.filter` echo never leaks IDs resolved from refs. (f) `tckdb-client` updated to surface the opt-in via the existing include list; example script gets `--include-internal-ids`. (g) Workflow guide / demo guide / `read_api_mvp.md` / `species_calculation_search_api.md` updated. | ⚠ **Yes**, for any client still consuming integer PKs from anonymous responses. Mitigated by the `include=internal_ids` opt-in + `ALLOW_PUBLIC_INTERNAL_IDS` deployment setting. See [`docs/specs/internal_ids_visibility_policy.md`](./internal_ids_visibility_policy.md). |

**Each phase is shippable independently.** Phases A–C are purely
additive; Phase D is the only breaking change and should land **only
after** the workflow guide and a deprecation notice have been live for
a documented period (suggest one minor version of `tckdb-client`).

---

## Implementation risks

1. **Schema migration scope.** Adding a unique non-null `*_ref` column
   to every public-facing table is a multi-table Alembic migration.
   Backfill must populate every existing row before the NOT NULL
   constraint flips. Per the project's [migration-rules.md](../../.claude/rules/migration-rules.md),
   while the schema is not yet finalized this should be folded into the
   single initial migration `d861dfd60891`, with all dev DBs rebuilt.
2. **Backfill cost.** Content-derived refs require recomputing the
   canonical identity hash for every row of every identity-bearing
   table. For LoT this is cheap (the hash already exists); for species
   and chem_reaction it requires reading existing identity fields.
   ULID generation for opaque-ref tables is O(rows) but fast.
3. **Race conditions on opaque-ref generation.** ULIDs are time-ordered;
   two simultaneous inserts in the same millisecond are extremely
   unlikely to collide given the random component, but the schema's
   unique index on `*_ref` is the backstop.
4. **Content canonicalization.** For LoT, `lot_hash` already defines the
   canonical form. For species, the canonical identity is
   `(canonicalized_inchi_key, charge, multiplicity, stereo_kind)` — with
   a documented canonicalizer. For chem_reaction, `stoichiometry_hash`
   is the existing canonical input. For software/workflow_tool, name
   normalization (lowercase, trim, strip diacritics) needs a documented
   canonicalizer to avoid `"Gaussian"`/`"gaussian"`/`"Gaussian "`
   producing three different refs.
5. **Export/import behavior.** Bundles must carry refs in addition to
   internal PKs so importers can preserve identity. Conflict resolution
   on import (same ref, different content) needs a deterministic error
   path, parallel to existing idempotency conflict handling.
6. **Public refs in contribution bundles.** Bundle dry-run + submit must
   surface conflicts when an incoming bundle's ref already exists with
   different content. Add to the existing bundle conflict report.
7. **Security / enumeration.** ULID prefixes leak the time of insertion;
   for opaque refs that's acceptable (calculations and uploads are
   already publicly timestamped via `created_at`). For content-derived
   refs, the hash leaks no enumeration signal at all.
8. **URL-safe encoding.** Base32 lowercase is URL-safe by design (no
   `+`, `/`, `=`, `_`-conflict characters). `prefix_<base32>` is
   case-insensitive in practice — explicitly document case-insensitive
   matching at the route layer to avoid `lot_5BX…` vs `lot_5bx…`
   surprises.
9. **Opaque vs semantic refs.** Refs are deliberately opaque from the
   client's perspective — clients should never parse them. Documented
   guarantees: `<prefix>_<26 chars>`. Anything else (timestamp
   extraction, hash recovery) is unsupported.
10. **Migration to `record_review`.** `record_review` is polymorphic via
    `(record_type, record_id)`. After Phase D, switch to `(record_type,
    record_ref)` for the public API surface; keep `record_id` internally.

---

## Open questions

1. **Refs for `record_review` audit entries** — the `review_records[]`
   array under `include_review=full` currently uses `record_id`. Should
   it carry the target record's `record_ref` (e.g. `kinetics_ref`)
   instead of, or in addition to, `record_id`? Recommend addition (both)
   during the compatibility window.
2. **Contribution bundle ref format** — bundle authors typically write
   bundles with their own local IDs that get resolved server-side. Do
   we want bundle authors to also pre-supply expected refs for
   round-tripping? Probably yes for content-identity refs (LoT,
   species, software_release), no for opaque refs.
3. **Frontend query strings** — when a future frontend builds a URL like
   `/species/spc_…`, how do we want browser bookmarks to behave across
   instance restores? The cross-instance stability of content-derived
   refs handles species/LoT; for opaque refs (e.g. a thermo record
   detail page), the ref is per-instance, and a backup-restore on a
   different instance would silently break bookmarks unless the bundle
   carried the ref over. This is a real product question for the
   eventual frontend phase.
4. **Long-term integer PK retirement** — should Phase D land via a
   tckdb-client major version bump (`1.0.0`)? Probably yes, since
   "integer PKs no longer in default public responses" is precisely the
   kind of change that justifies a major-version cliff in semver.
5. **Existing scripts / examples** — the `scientific_reads.py` example
   and the `seed_scientific_demo_data.py` script both reference integer
   PKs in printed output. After Phase B, they should print refs first
   and integer IDs only when `--include-internal-ids` is passed.

---

## Non-goals

- ❌ Redesigning scientific read ranking, filter/collapse semantics, or
  evidence completeness.
- ❌ Changing chemistry-first query semantics.
- ❌ Removing integer PKs in this design phase or in any of Phases A–C.
- ❌ Adding refs only in `tckdb-client` — the contract change is in the
  backend; the client gets refs via the response.
- ❌ Workflow-tool-specific identifiers (e.g. ARC's job IDs). Refs are
  TCKDB-internal contracts; tools record them in their own audit trails.
- ❌ Subjective ranking selectors (`best`, `preferred`, `tckdb_default`,
  `highest_lot`) — those remain forbidden per D3.
- ❌ Public SQL endpoint, GraphQL, or general query language.

---

## Summary

The minimum viable change for hosted-readiness is **Phase A + Phase B**:
schemas grow `*_ref` columns, responses gain sibling `*_ref` fields,
and docs/examples switch to refs. That's purely additive; it doesn't
break a single existing client.

**Level of Theory is the most urgent record type** because every
energy-bearing response is misleading without a stable LoT handle, and
the existing `lot_hash` column means LoT refs come for free
content-wise — just expose it.

The remaining phases (C — route ref acceptance, D — public PK hiding)
are independent product decisions that can land months apart. The
breaking change in Phase D should align with a `tckdb-client` major
version bump and a documented deprecation window.
