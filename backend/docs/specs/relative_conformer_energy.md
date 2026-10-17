# Relative conformer energies against a named reference conformer

Contract for `POST /api/v1/scientific/species-entries/{species_entry_ref}/conformers/relative-energies` and its
`/manifest` download. The service lives in `backend/app/services/relative_conformer_energy/`; the schemas in
`backend/app/schemas/reads/scientific_relative_conformer_energy.py`. For a plain-language reading see
`docs/guides/relative_conformer_energies.md`.

## What it answers

How much higher or lower in energy (kJ/mol) each conformer group of one species entry is than a conformer group the
caller names. Read-only; a `POST` because the request is a structured body.

It is **not** a selection. It picks no conformer, ranks nothing and recommends nothing. For "which basin is lowest" use
`POST .../conformers/select` (`docs/guides/selecting_structures.md`).

## Request (`extra="forbid"`)

| Field | Rule |
| --- | --- |
| `reference_conformer_ref` | `cg_...`, **required**. Must belong to the path's entry. |
| `energy_kind` | `electronic_energy` or `zero_kelvin_energy`, **required, no default**. `enthalpy` and `gibbs_energy` are recognised and refused (below). |
| `temperature_k` | `> 0`; required for `enthalpy`/`gibbs_energy`, refused (ordinary 422) for the supported kinds. |
| `level` | **required**: exactly one of `level_of_theory_ref` (`lot_...`, merges followed) or `composite_scheme_ref` (`csch_...`). |
| `certificate` | `local_minimum_any_surface` or `local_minimum_on_energy_surface`, **required, no default**. `none` is recognised and refused. |
| `recipe` | optional `ActualProtocolDeclaration`; narrows within the level, never fills a missing fact. |
| `member_refs` | optional list of 1..500 distinct `cg_...`; omitted: every visible group of the entry. The reference is always included. |
| `coverage_requirement` | `known_values` (default, echoed) or `all_requested_members`. |
| `repeat_policy` | `retain_alternates` (default, echoed) or `administrative_representative`. |
| `min_review_status`, `permitted_quality`, `require_stable_reference` | as structure selection. |

No sort field, no paging, no rule, no bound and no database id. Only `?profile=` is accepted on the query string; any other
key is 422 `post_search_fields_must_be_in_body`. The path takes a public ref only (an integer id is `invalid_handle`).
The session is a read-only REPEATABLE READ snapshot (`get_snapshot_db`).

## Response

`request` (normalised echo with the canonical level refs, the defaults applied and the resolved read profile), `review`,
`outcome`, `basis`, `energy_kind`, `temperature_k` (always `null` in this release), `units`, `level` (with the recipe
cohort and its facts), `reference`, `members`, `coverage`, `unresolved`, `cohorts`, `notes`, `search_completeness`,
`versions`, `snapshot_isolation`, `integrity`.

- **Members are ordered by conformer ref and carry no rank field.** The order is not a ranking; a client may sort for
  display.
- A member is `reference`, `present`, `absent` or `unresolved`. An absent or unresolved member has a coded
  `absent_reason` and `null` numbers, never zero. Reasons: `no_structure_determination`,
  `no_determination_supplies_requested_kind`, `no_value_at_requested_level`, `recipe_not_established`,
  `recipe_differs_from_request`, `not_eligible`, `certificate_not_supported`, `certificate_not_on_energy_surface`,
  `contested_certificate`, `repeat_values_disagree`, `multiple_recipes_at_level`, `group_below_review_floor`,
  `group_terminal_review_status`, `determination_below_review_floor`, `determination_terminal_review_status`.
  `no_structure_determination` means no determination exists at all.
- `relative_energy_hartree` is the stored difference; `relative_energy_kj_mol` is that difference times
  `HARTREE_TO_KJ_MOL`, converted once. A negative value is below the reference and is never re-referenced.
- Stored uncertainties are echoed per value (`value_uncertainty_hartree`) and never combined (nothing records whether
  they are correlated).
- No conversion between kinds: a single point plus a frequency's zero-point energy is not a zero-kelvin energy.
- A high-level single point does not inherit the "true minimum" check of the cheaper calculation that produced its
  geometry; each member's `certificate` says on which surface(s) the minimum was certified, and `basis` says so when it
  was not the energy's own surface.

### Outcomes

`relative_energies`, `reference_only`, `reference_unavailable` (the named reference has no single value: no
difference is computed for anyone, and the reference is never replaced by another conformer),
`multiple_recipes_at_level` (nothing computed; each cohort listed so the caller can narrow with `recipe`),
`incomplete_coverage` (`all_requested_members` with an absent or unresolved member), `no_members`. A negative scientific
outcome is an answer (HTTP 200).

## Refusals

| Code | Status | When |
| --- | --- | --- |
| `relative_conformer_energy_unsupported` | 422 | `context.reason`: `deferred_energy_kind` (enthalpy, Gibbs), `deferred_certificate` (`none`). Raised before anything is read. |
| `relative_conformer_energy_population_too_large` | 422 | More than 500 visible groups; `context`: `bound`, `visible`, `limit`, `bounds_version`. Nothing is answered from a subset. |
| `relative_conformer_energy_scope_owner_mismatch` | 422 | A visible `cg_` ref that belongs to another species entry; `context.field` names the field, never the owner. |

The structure assessment's own bound codes pass through unchanged. Order of ref checks: malformed or wrong prefix is 422
(`invalid_handle`, `handle_type_mismatch`); unknown **or hidden by the read profile** is the same 404
`handle_not_found` (the subject entry, the level, the reference, each member); only then is ownership judged, so the
422 never reveals a record the caller could not otherwise see.

## Disclosure

Under `curated` a hidden group, observation, determination or pinned calculation is not in the world: it is filtered
before the assessment, so it is not listed, counted, used or named in the response or the manifest. Nothing is borrowed
from a visible sibling. The response carries no database id and is returned directly (not through the internal-id
stripper), so the central curated ref guard (`scientific_read/ref_guard.py`) does not see these routes: they are listed
as unguarded in the curated sweep. **The population filter is the only line of defence** until the guard's selection-seam
PR adds `run_relative_energies` to that seam. It is tested at the API level and by the sweep, which finds no hidden ref.
Under `curated` the compared population shrinks by design, so `coverage.requested_members` and `coverage.absent` count
visible members only.

The unsupported-request refusal (`relative_conformer_energy_unsupported`) depends on the request alone and is made first,
before the entry is looked up: it is the same 422 for an unknown entry, and nothing is read to make it.

## Manifest and replay

`format: relative_conformer_energy_manifest`, version 1: versions, the normalised request, read profile, effective
statuses, snapshot isolation, subject, the compared groups and determinations, every normalised calculation and
determination with a per-input digest, every assessment, the computed `result` and a whole-manifest digest. The response
is built from the manifest, so they cannot disagree. `replay_relative_energies(manifest)` recomputes every assessment
and then the result, and refuses any difference. The digests are checksums, not signatures. The download is a new
snapshot of the current data, not a retrieval of a stored answer. `tckdb_client` has no replay function for this
manifest (nor for structure manifests).

## Deferred

Enthalpy and Gibbs energy (needs per-conformer thermo at exactly `temperature_k`), `certificate: none` (needs a structure
intent that states no claim), published relative-only references, and the descriptive benchmark comparison (`/compare`)
are not part of this release. Owner decisions O2, O3 and O4 of 2026-10-13 defer the first three until a real deposit needs
them.

## Related

The legacy `GET /api/v1/species-entries/{id}/conformer-observations/lowest-sp` collapses several single points on one
observation to the lowest, the opposite of "repeats are alternates". It is **deprecated** in favour of this operation and
will be removed once unused.
