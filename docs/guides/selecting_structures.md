# Selecting a calculation, conformer or saddle for a stated question

One page for whoever reads stored structures and energies of one species entry or one transition state entry and
needs to know which stored result answers a stated question, and why. It describes the three selection endpoints,
what each answer means, and what they refuse to claim.

In plain terms: an entry can hold many calculations from different programs, methods and basis sets, and several
conformer basins or saddle determinations. Browsing lists them in a fixed order. Selection is a separate, opt-in
question: "of the stored results that can answer this exact question, which one is lowest or best supported, and how do
you know?" Total energies from different methods are not on one scale, so the honest answer is often "these are
alternatives and nothing ranks them", and the endpoint says so instead of picking one.

## What it is, and what it is not

- **Read-only.** Nothing is stored and nothing is edited. Browse orders are unchanged.
- **Opt-in.** The browse endpoints keep their order. A selection is a separate `POST`.
- **Not a recommendation.** A curator or a dataset release recommends records; selection applies stated rules to what
  is stored and shows its working.
- **Not a global-search certificate.** Coverage is the population you are allowed to see. A complete claim is not
  evidence that every conformer or saddle was found.
- **No hidden rules.** The audited literature rules are listed but inactive. Nothing here ranks by a literature rule
  unless a rule is active, and none is.

## The three endpoints

| Grain | Endpoint | Entry ref |
| --- | --- | --- |
| Calculations of a species entry | `POST /api/v1/scientific/species-entries/{ref}/calculations/select` | `spe_...` |
| Conformer basins of a species entry | `POST /api/v1/scientific/species-entries/{ref}/conformers/select` | `spe_...` |
| Saddle determinations of a transition state entry | `POST /api/v1/scientific/transition-state-entries/{ref}/evidence/select` | `tse_...` |

The transition-state route takes the transition state **entry** (`tse_`), not the transition-state concept (`ts_`):
evidence belongs to the entry that holds the geometries. Each endpoint has a `/manifest` sibling that takes the same
request and returns the replayable manifest (a checksummed record of the inputs and decision; checksums, not
signatures). An integer id, or a ref of the wrong kind, is refused.

## The request

Every body field is optional, the body may be empty, and unknown fields are refused. The server applies its own
defaults; the client libraries invent none.

| Field | Meaning |
| --- | --- |
| `intent` | Calculations: `recorded_minimum`, `protocol_preferred`. Conformers: `validated_minimum`, `qualify_evidence`, `protocol_preferred`. Transition states: `validated_saddle`, `qualify_evidence`, `protocol_preferred`. |
| `quantity` | `electronic_energy` or `zero_kelvin_energy`. `null` is allowed only for `qualify_evidence` (a structural claim with no number). Enthalpy, Gibbs energy, barrier and rate are recognised but deferred and are refused with `structure_selection_unsupported`. |
| `coverage_requirement` | `known_values` (order what is known, conditional on it) or `all_requested_members` (every requested member must be eligible and comparable). |
| `validation_claim` | `local_minimum`, `first_order_saddle`, `higher_order_saddle` or `reactive_connectivity`. |
| `min_review_status`, `permitted_quality` | Floors on review and calculation quality, applied on top of the read profile. |
| `geometry_ref`, `member_refs` | Restrict to one geometry (`geom_...`) or named members. |
| `recipe`, `require_stable_reference` | The method recipe the caller asserts, and whether a stable reference is required. |
| `require_connectivity` | Transition states only. |
| `administrative_policy`, `result_mode`, `apply_rules`, `objective`, `reference_model`, `repeat_policy` | Tie handling, whether to name one winner, rule use, and how repeated calculations are treated. |

The read profile is the usual `?profile=exploratory|curated` query parameter. Other query keys on the `POST` are
refused. There is no candidate cap, paging or sort field: the engineering bounds belong to the server. Above them the
endpoint refuses with a coded 422 (`structure_selection_population_too_large`, `structure_selection_evidence_too_large`,
`structure_selection_traversal_too_deep`, `structure_selection_manifest_too_large`). A caller can never raise the
manifest byte bound.

Under the curated profile, records below the floor behave as missing everywhere: lineage, validation evidence,
counts and depth. The manifest then withholds the excluded population (`excluded_by_review_withheld: true`) so that it
still replays.

## Reading the answer

Quote `outcome` and `basis` verbatim.

| Outcome | Meaning |
| --- | --- |
| `recorded_minimum` | The lowest known eligible value inside one justified cohort. Ties are all returned. |
| `representative_minimum` | The lowest of per-source representatives under `repeat_policy=administrative_representative`. It is not the minimum of every stored value. |
| `validated_corpus_minimum` | The lowest validated basin or saddle within one cohort. |
| `qualified_evidence` | These determinations support the stated claim. No number. |
| `policy_preferred` | A whole protocol preferred by an active audited rule. None are active today. |
| `sole_eligible_candidate` | Only one candidate can answer; it is not compared with anything. |
| `incomparable_alternatives` | Several protocols each have a minimum and no scale ranks them. Nothing is selected. |
| `unresolved_comparability` | No cohort could be ordered. Nothing is selected. |
| `policy_conflict`, `evidence_conflict` | Rules or evidence contradict each other. Nothing is selected. |
| `no_candidates`, `energy_unavailable`, `no_applicable_candidate` | Nothing to compare, no usable energy, or nothing meets the stated requirements. |

A negative outcome is an answer, not an error. A cohort is the same established recipe, quantity, energy convention
and scope; values compare exactly, with no tolerance. When any cohort cannot be ordered, no administrative winner is
named.

Minimum and saddle claims are judged by the size of imaginary frequencies against the stored threshold, never by
counting them. A finding is settled only by an authorised adjudication about the same subject.

## Examples

Lowest recorded electronic energy, server defaults:

```bash
curl -X POST "$BASE/api/v1/scientific/species-entries/$SPE/calculations/select" -H 'content-type: application/json' -d '{}'
```

Which determinations support a first-order saddle, no number wanted:

```json
{ "intent": "qualify_evidence", "quantity": null, "validation_claim": "first_order_saddle" }
```

Python client: `client.select_species_calculations(ref, ...)`, `select_conformer_basins`,
`select_transition_state_evidence`, and the `get_..._selection_manifest` siblings. MCP tools:
`tckdb_select_species_entry_calculations`, `tckdb_select_species_entry_conformers`,
`tckdb_select_transition_state_entry_evidence`.

## Plan deviations

- The transition-state route is entry-based (`/transition-state-entries/{ref}/evidence/select`).
- A browse "scientific include" for selection is deferred; the select endpoints serve that need.

See `backend/docs/specs/structure_selection_decision.md` for the decision, manifest and replay rules.
