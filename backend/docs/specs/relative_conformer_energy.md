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

Under `curated` a group below the record floor is not in the world, and a withheld (rejected or deprecated) observation,
determination or pinned calculation is not either: it is filtered before the assessment, so it is not listed, counted, used
or named in the response or the manifest. Nothing is borrowed
from a visible sibling. The response carries no database id and is returned directly (not through the internal-id
stripper), so the central curated ref guard (`scientific_read/ref_guard.py`) does not see these routes: they are listed
as unguarded in the curated sweep. **The population filter is the line of defence** here: the response-level guard does not see these routes. It is tested at the API level and by the sweep, which finds no withheld ref.
Under `curated` the compared population shrinks by design, so `coverage.requested_members` and `coverage.absent` count
visible members only.

The unsupported-request refusal (`relative_conformer_energy_unsupported`) depends on the request alone and is made first,
before the entry is looked up: it is the same 422 for an unknown entry, and nothing is read to make it.

## Manifest and replay

`format: relative_conformer_energy_manifest`, version 2: versions, the normalised request, read profile, effective
statuses, snapshot isolation, subject, the compared groups and determinations, every normalised calculation and
determination with a per-input digest, every assessment, the computed `result` and a whole-manifest digest. The response
is built from the manifest, so they cannot disagree. `replay_relative_energies(manifest)` recomputes every assessment
and then the result, and refuses any difference. The digests are checksums, not signatures. The download is a new
snapshot of the current data, not a retrieval of a stored answer. `tckdb_client` has no replay function for this
manifest (nor for structure manifests).

**Versions.** `versions.relative_energy` is the integer `1`, and only the running version is read: a manifest of any other version
is refused with `unsupported_semantic_version`, whatever fields it carries. A manifest that claims the current version and
lacks a field (for instance `value_geometry_ref` on a member) is refused with `result_mismatch`, as is any other difference
between the recorded and the recomputed result. A comparison manifest is made, and replayed, under the running version only,
and so are the two runs inside it.

## Descriptive benchmark comparison (`/compare`)

`POST .../conformers/relative-energies/compare` and `.../compare/manifest`. It describes how far two **stored protocols**
of one species entry deviate from each other, for the same named reference conformer, energy kind and certificate. It
does not say which protocol is better.

**Request** (`extra="forbid"`): the shared choices of the operation above (`reference_conformer_ref`, `energy_kind`,
`certificate`, and the optional `member_refs`, `coverage_requirement`, `repeat_policy`, `min_review_status`,
`permitted_quality`, `require_stable_reference`), plus `candidate` and `reference` (each `{level, recipe?}`, the shapes
of the level and recipe above) and `pairing`. `pairing` is required and has no default:

- `same_geometry`: both values of a conformer must be evaluated at the same `geom_` ref (the sets of evaluated
  geometries are equal and recorded). The geometry is the one each stored value was evaluated at
  (`value_geometry_ref`, set only when every record giving that value names the same geometry), not the set over all
  repeats; a value not tied to one geometry cannot satisfy `same_geometry`. The reference conformer's value must also be
  evaluated at the same geometry on both sides, because every relative energy is taken from it;
- `same_basin`: each protocol's own geometry of the basin, so only the conformer group is matched.

External or published relative references are not supported. Enthalpy, Gibbs energy and `certificate: none` are refused
as above, before the first query.

**What runs.** The relative-energy computation runs twice in **one** snapshot, over **one** population, once per
protocol. Members are paired by `conformer_ref`; the deviation is `candidate - reference`, in kJ/mol, of the two
relative energies (each against the one named reference conformer). The reference conformer is **not a pair**: its
deviation is zero by construction.

**Response.** `outcome` (`compared`, `no_pairs`, `incomplete_pairing`), `statistics`, `pairs`, each protocol's block
(level, recipe cohort, the engine's own outcome, reference-conformer value, coverage, unresolved), `claims` and the
manifest's integrity. `statistics`: `n`, `msd_kj_mol`, `mad_kj_mol`, `rmsd_kj_mol`, `max_abs_deviation_kj_mol` with
`max_abs_deviation_conformer_ref` (a tie goes to the lowest ref), `sign_convention` (`candidate minus reference`),
`reference_conformer_ref` and `reference_conformer_dependence` (the figures change with the named conformer; compare
them only with figures measured from the same one). `pairs` are ordered by ref and carry no rank:
`conformer_ref`, `candidate_kj_mol`, `reference_kj_mol`, `deviation_kj_mol`, `excluded_reason`, each side's stored
uncertainty (hartree, echoed, never combined) and the one geometry each side's value came from
(`candidate_geometry_ref`, `reference_geometry_ref`; null when the value is tied to none or several, never borrowed from
a sibling record). The top-level `reference_conformer` block names the anchor, each side's value and geometry, and a
`problem` (null, or one of the three codes below) that applies to every pair.

**Rules.**

- A pair missing on either side, or failing `pairing`, is listed with a coded `excluded_reason` and does not enter any
  statistic. It is never a zero deviation. The closed vocabulary: `neither_protocol_has_value`,
  `candidate_value_missing`, `reference_protocol_value_missing`, `geometry_not_recorded` and `geometry_differs` (the last
  two only under `same_geometry`), and the reference-conformer reasons `reference_conformer_value_missing`,
  `reference_conformer_geometry_not_recorded` and `reference_conformer_geometry_differs` (the last two only under
  `same_geometry`). When the reference conformer has a problem, every pair whose member value exists on both sides is
  excluded with that reason (the members are not blamed); a pair already missing a value keeps its own reason.
- `n == 0` (which includes any reference-conformer problem under `known_values`, since every deviation needs the
  anchor): `outcome` is `no_pairs` and every statistic is `null`, not 0 (`n` is the integer 0).
- `coverage_requirement: all_requested_members` with any excluded pair, or with any reference-conformer problem: `outcome` is `incomplete_pairing` and `statistics` is `null`. No statistic is given over a subset.
- A candidate identical to the reference (the same canonical level, after a merged `lot_` is resolved to the kept row,
  and the same effective narrowing: the same known recipe facts; a fact stated `unknown`, and `source` or
  `supporting_dois`, narrow nothing) is 422 `relative_conformer_energy_compare_identical_protocols`. It is decided after
  the entry, the levels and the conformer refs are resolved (an unknown or hidden one is its usual 404 first, so the
  refusal never answers for a record the caller cannot see) and before any energy is read.
- Two stored uncertainties are never combined; each side's is echoed.

**Claims.** `claims.kind` is `descriptive`; `claims.superiority` and `claims.ranking` are `not_licensed`; and
`claims.missing_evidence` is always `["E1", ..., "E7"]` in this version. These are the named prerequisites of any future
claim that one protocol is more accurate; none is evaluated yet, so all seven are always listed as missing:

| Code | Prerequisite |
|---|---|
| E1 | A numeric uncertainty for the reference values, per member or as a stated set-level bound, with its source. |
| E2 | Independence: the reference shares no computed component with the candidate, shown from recorded composition, not asserted. |
| E3 | A resolved margin: for superiority, the paired difference in the error metric between two candidates exceeds its combined uncertainty (reference uncertainty plus paired sampling uncertainty by a stated method with stated n). For a near-tie ranking, every adjacent pair in the ranking is resolved this way; unresolved neighbours are reported as indistinguishable, not ordered. |
| E4 | Coverage and sameness: the full named member set, the same geometries for both candidates, the same named reference conformer as the source. |
| E5 | Domain: the claim is scoped to the member set measured; transfer to other molecules needs evidence on them. |
| E6 | Established protocol facts: every setting that defines the protocol (thresholds, PNO cut-offs, extrapolation, core treatment, corrections) is typed and established, not read from a label. |
| E7 | Owner acceptance of the claim itself, and its predicates implemented and reviewed. |

Even with E1–E7 met, the comparison describes agreement between two protocols. TCKDB ranks no method above another, so it never becomes a preference.

**Boundary.** The comparison ranks nothing: none of `compare.py`, `service.py`, `manifest.py` or `public.py` directly
imports `selection_kernel` or names a decision function (a test reads their imports and names; it does not cover
transitive imports, and the certificate legitimately comes through `structure_selection.public`). A
mean absolute deviation is a description and cannot become a selection input.

**Manifest.** `format: relative_conformer_energy_comparison_manifest`, version 2. It holds the shared request, both
protocols, the pairing, the read profile and snapshot isolation, **both runs' complete relative-energy manifests** (each
with its inputs, assessments, answer and checksums), the `result` and a whole-manifest digest. The combined manifest is checked against the manifest size bound (422
`structure_selection_manifest_too_large`), not only each run. The response is built
from it. `replay_comparison(manifest)` replays both runs (recomputing every assessment, never trusting a recorded flag),
checks the runs were made for the recorded request, profile, snapshot and one population, then recomputes the pairing
and statistics from the *replayed* answers and refuses any difference. The digests are checksums, not signatures.

**Disclosure.** As above: a hidden group is not in the world, and the engine's refusals (unknown or hidden ref is the
same 404) apply to the comparison unchanged.

## Deferred

Enthalpy and Gibbs energy (needs per-conformer thermo at exactly `temperature_k`), `certificate: none` (needs a structure
intent that states no claim) and published relative-only references (also not supported by `/compare`) are not part of
this release. Owner decisions O2, O3 and O4 of 2026-10-13 defer them until a real deposit needs them.

## Related

The old `GET /api/v1/species-entries/{id}/conformer-observations/lowest-sp`, which collapsed several single points on one
observation to the lowest (the opposite of "repeats are alternates"), is removed. This operation replaces it.
