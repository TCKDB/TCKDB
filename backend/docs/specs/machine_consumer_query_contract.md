# Machine-consumer query contract

Status: requirements 1–9 are implemented at the scopes stated below.
Requirement 10 remains ongoing as each surface expands.

## Compatibility rules

- Additive response fields are backward compatible. Removing or changing the meaning/type of a field requires a versioned contract.
- A declared filter is either enforced or rejected. It must never be accepted and ignored.
- Public refs are stable identifiers. Integer database IDs are deployment-policy fields and are optional in hosted response schemas.
- An include-gated section that the caller did not request is **omitted** from the response, not returned as `null`. That is the rule; it holds today on `/api/v1/scientific/calculations/*` and for `trust`/`assessments`, and the remaining surfaces are being converted to it one declared table at a time. See [Include-gated sections](#include-gated-sections-absent-means-you-did-not-ask) for the three-state rule and its hard boundary.
- Pagination metadata reports `total` for the complete filtered candidate set before collapse and `post_collapse_total` after collapse but before page slicing. Collapse is applied first, so `collapse=first&offset=1` returns an empty page while retaining `total >= 1` and `post_collapse_total = 1`.

## Include-gated sections: absent means "you did not ask"

A section a caller did not request and a section that does not exist for this
record are different facts. Giving them one wire value makes them
indistinguishable, and the failure mode of guessing wrong is silent — a reader
concludes the database is empty. So there are three states and three
representations:

| Case | Wire |
|---|---|
| Not requested | key absent |
| Requested, nothing there | key present, `null` (or `[]` for a list section) |
| Requested, something there | key present, populated |

The middle row is as load-bearing as the top one. Collapsing "requested, nothing
there" into "not requested" restores the same ambiguity from the other
direction, so a requested-but-empty section keeps its `null`.

`request.include` echoes the **resolved** token set — `include=all` as its
expansion, and never a token the deployment's policy dropped — so a reader can
always recover what was asked without inferring it from what came back.
`available_sections` answers the other question, "is there any of this?", and
answers it without being asked.

**Omission is not removal.** The field stays declared, stays typed, and is
returned whenever it is requested. What changes is the wire representation of a
case the contract never assigned a meaning to, so this sits inside the existing
contract rather than requiring a versioned one.

### The hard boundary

**Only include-gated sections are omitted. No blanket "drop null optional
fields" rule applies to a scientific response, at any layer, ever.**

Nullability means different things in different places, and in at least one it
is a live protocol signal read in the opposite direction. The Python client
treats an **absent** `next_cursor` as "this server predates the keyset contract
— restart the traversal from offset zero" and a **present-and-null**
`next_cursor` as "this was the last page, you are done". A blanket null-strip
would turn every completed traversal into a restart and silently yield the
whole result set twice. Fields such as `next_cursor`, `post_collapse_total`,
`supersession`, `conformer`, `formula`, `software_release.version`,
`assessment_ref` and `xyz_text` are `X | None` for reasons unrelated to
`include`, and they keep their `null`. The same rule read from the other side:
never null a field a client tests for presence, and never omit a field a client
tests for nullity.

The strip is therefore driven by a declared per-surface token → section table
(`IncludeGatedSections` in `app/api/routes/scientific/_response.py`) and by
nothing else. The table is declared rather than derived from field names
because names collide: one calculation response carries two fields called
`workflow_tool_release` — the record's own provenance field, which is `null`
because the calculation references no workflow tool and must stay `null`, and
one nested inside the include-gated `execution_environment` block, which
disappears with its section.

### Scope today

`/api/v1/scientific/calculations/*` omits all 18 of its heavy sections, and
`trust` and `assessments` are omitted wherever their helpers run. Every other
include-gated section still serializes as `null`; those surfaces are being
converted one declared table at a time, and each conversion adds the
`x-tckdb-include-gated` marker to the hosted OpenAPI document at the same time,
so the document never claims a key is normally absent before the runtime omits
it.

## Ordered requirements

1. **Structured errors and client fallback — implemented.** Query/API error responses expose top-level `code`, `detail`, and object-valued `context` (including middleware-generated 429s; readiness probes retain their operator-specific status shape). Existing `"code: message"` detail strings remain unchanged, are promoted server-side when possible, and are parsed by older-server-compatible Python clients when top-level `code` is absent. Framework validation uses `request_validation_error`; declared-but-unavailable filters use `unsupported_filter` with `context.endpoint` and `context.filters`.

2. **Fail closed on ignored filters — implemented.** Non-null `inchi` on species search (including composed thermo and species-calculation search), species-calculation `scientific_origin`, frequency-scale-factor `model_kind`/`software_version`, and energy-correction-scheme `software`/`software_version`/`used_by_thermo` return 422 `unsupported_filter`. No subset of a request is silently applied.

3. **Hosted JSON and OpenAPI agree — implemented.** All successful scientific-response component schemas are followed transitively. Policy-hidden internal-ID properties remain documented but are not required and carry `x-tckdb-policy-hidden: true`. Include-gated section properties remain documented and carry `x-tckdb-include-gated: "<token>"`, naming the `include=` token that produces them; a property is marked only once every operation returning its component omits it, so the document never runs ahead of the runtime. A real hosted, ID-stripped species response is validated against the served OpenAPI document.

4. **Canonical pressure query — implemented.** `pressure_bar` is canonical on reaction-entry and chemistry-first kinetics reads; `pressure` remains a deprecated alias. Both accept only finite positive values at every GET/POST request boundary. After numeric parsing, equal aliases (for example `1` and `1.0`) are canonicalized to `pressure_bar`; any exact inequality returns 422 `pressure_alias_conflict` without tolerance. A valid pressure includes pressure-independent rates; matches exact apparent-pressure records; applies bounded PLOG/Chebyshev coverage; accepts populated falloff/third-body models; and excludes high-pressure-limit, out-of-range, or indeterminate pressure-dependent records. Incompatible records are filtered out rather than silently broadened. A non-null `reaction_path_degeneracy` reports an explicit `convention`: `already_applied` maps to `true/false`, `not_applied` maps to `false/true`, and `unknown` maps to `null/null` for `reported_rate_coefficient_includes_degeneracy` / `apply_to_rate_coefficient`. Legacy rows are backfilled as `unknown`; semantics are never inferred from the numeric degeneracy. Null degeneracy remains unknown, not one.

5. **Exhaustive composed searches — bounded implementation.** Thermo, kinetics, and species-calculation composition walks every reachable identity page before downstream filtering, ordering, collapse, and pagination. If the complete identity set exceeds the hosted offset/limit traversal bound, the request fails with 422 `composed_search_candidate_limit_exceeded`; it is never silently truncated. `pagination.total` is the complete post-filter, pre-collapse candidate count, `pagination.post_collapse_total` is the count after collapse and before page slicing, and `pagination.returned` is the actual record-list length. The same additive pagination contract applies across scientific reads; without collapse, `post_collapse_total == total`.

6. **Comparable `lowest_energy` — implemented.** Species-calculation `ranking=lowest_energy` requires `calculation_type=sp|opt`, one exact `species_entry_ref`, and one exact `level_of_theory_ref`; unsafe requests fail with coded 422 errors. SP ranks `electronic_energy_hartree`; opt ranks its final-energy field, so calculation types and energy meanings are not mixed within one query. Missing energies sort last when at least one candidate has an energy. If candidates match but every energy is null, the request fails with 422 `lowest_energy_unavailable`; a search with no matching candidates remains a normal empty result.

7. **Chemistry-first PDep results — implemented at the stated projections.** Network and network-kinetics search expose public network/species refs and bounded, ordered participant-composition blocks with stoichiometry and truncation metadata. For each network state, `composition.participant_count_total` describes the full normalized composition, while `composition.participants` is its deterministic capped public prefix; the existing state `participant_count` remains the same full row count. Network search supports participant species/reaction and network-ref filters. Network-kinetics search supports `network_ref` plus source/sink public species-entry-ref or canonical-SMILES multisets. Repeated requested values encode stoichiometry. The matching rule is multiset-subset containment: every requested participant must occur at least at the requested multiplicity; additional unmentioned channel participants are allowed. Ref and SMILES filters AND-combine.

8. **Compact trust and reproducibility summaries — implemented for kinetics, thermo, statmech, and transport.** The single opt-in token is `include=assessments`; it is deliberately excluded from `include=all`. The block contains current deterministic trust (`rubric`, version, grade, optional hard fail) and the latest immutable reproducibility assessment (`current|stale|unassessed`, opaque `assessment_ref`, rubric/version, grade, timestamp). Absence serializes as `unassessed` with `assessment_ref: null`, never approval. The ref identifies the exact stored immutable claim and does not imply approval or freshness. No assessment-grade filter is offered because filtering without freshness enforcement would mix current and stale claims. Statmech and transport retain their existing detail/subresource-only `trust` fragments alongside the compact summary.

9. **Typed Python models and complete iterators — implemented for the supported client search surface.** The dependency-light client exports `TypedDict` wire models for errors and species, reaction, thermo, kinetics, species-calculation, network, network-kinetics, statmech, transport, artifact search responses, and additive `post_collapse_total` pagination metadata. Thermo and kinetics use distinct flat detail-record types and composed search-row types. Existing methods still return ordinary dictionaries. Matching lazy `iter_*` methods preserve filters/includes, advance using returned pagination metadata, stop at `post_collapse_total`, and fail on malformed, changing, empty-before-total, or non-advancing pages. When talking to an older server that omits the additive field, the client falls back to `total` plus the legacy `collapse=first` stop rule.

10. **Guides and regression coverage — partial, ongoing.** Every requirement lands with positive, conflict, empty-result, policy-hidden, and legacy-compatibility tests as applicable. Public examples use supported ranking fields and the actual nested geometry shape. Guides identify planned behavior explicitly and never demonstrate accepted-but-ignored filters.

## Release gate

A requirement becomes supported only when the hosted OpenAPI document, runtime response, Python client, user guide, and regression tests agree. Contract tests run against policy-hidden hosted JSON, not only Pydantic objects produced before the response seam.
