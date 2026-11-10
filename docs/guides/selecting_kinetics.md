# Selecting a kinetics record for a stated rate-coefficient question

One page for whoever reads kinetics records and needs one rate coefficient for one stated gas-phase question, and for
whoever deposits them and wants to know when a deposit can take part. It describes the selection endpoint, what each
answer means, and what the endpoint refuses to claim.

In plain terms: a reaction entry can hold many kinetics records from different sources, fitted in different forms,
valid over different temperatures and pressures. Browsing lists them in a fixed display order (review rank, then newest
deposited; see [`browse_order.md`](../specs/browse_order.md)). Selection is a separate, opt-in question: "which of the
records can answer this exact question, and what is the situation among them?" TCKDB carries no ranking of one method
above another, so the honest answer is "these records answer your question, and none is preferred".

## What it is, and what it is not

- **Read-only.** Nothing is stored, no curator endorsement is created or changed, and no record is edited.
- **Opt-in.** `GET /scientific/reaction-entries/{ref}/kinetics` is unchanged. Its order is exactly what it was.
- **One quantity.** A supplied gas-phase rate coefficient. It never evaluates a rate, never extrapolates, never
  derives a reverse rate, and never assembles additive components. Rates of progress, net forward-minus-reverse rates
  and whole mechanisms are not covered.
- **Not a recommendation.** A curator or a dataset release recommends records; this endpoint applies no method
  preference to what is stored. See
  [`dataset_release_and_profiles.md`](../../backend/docs/specs/dataset_release_and_profiles.md).

## The request

`POST /api/v1/scientific/reaction-entries/{reaction_entry_ref}/kinetics/select`

The path takes a public `rxe_...` ref. An integer id is refused. The question is the body, and none of it is
defaulted:

```json
{
  "direction": "forward",
  "target": { "kind": "whole_reaction" },
  "coefficient_basis": "elementary_coefficient",
  "temperature_min_k": 500.0,
  "temperature_max_k": 1500.0,
  "pressure": { "kind": "independent" },
  "policy": "default",
  "mode": "all"
}
```

| Field | Meaning |
| --- | --- |
| `direction` (required) | `forward` or `reverse`, relative to the reaction entry's stored reactant-to-product orientation. A net rate is not selectable. |
| `target` (required) | `whole_reaction`, or `resolved_channel` with a `transition_state_entry_ref` (`tse_...`) or a `network_ref` (`net_...`) with `channel_key`. A whole reaction names no locator. |
| `coefficient_basis` (required) | `elementary_coefficient`, `third_body_kernel` (a simple `+M` coefficient still to be multiplied by a collider concentration), or `composition_effective_coefficient` (already evaluated for one declared mixture). |
| `temperature_min_k`, `temperature_max_k` (required) | Positive and finite; equal values describe a point. A record must cover the whole window; overlapping it is not enough. |
| `pressure` (required) | `{"kind": "independent"}`, `{"kind": "high_pressure_limit"}` or `{"kind": "finite", "min_bar": ..., "max_bar": ...}` (equal for a point). A pressure-independent record also answers a high-pressure-limit request; the reverse is not true. |
| `collider` | `{"components": [{"species_ref": "spc_..."}]}` for a specified collider, or two or more components each with a `mole_fraction` summing to one for a mixture (never renormalised). Required for a finite pressure and for a composition-effective coefficient. |
| `quantity`, `phase` | Optional. If given they must be `rate_coefficient` and `gas`. Anything else is refused with 422, never changed. |
| `policy` | An administrative display order, never a method claim: `default` (default) and `most_reviewed` order by review rank then newest deposited; `latest` by newest deposited. `method_preferred` was retired and is refused. |
| `mode` | `all` (default) returns the whole assessment. `first` additionally names one determination where the outcome supports one (see below). |
| `min_review_status` | Optional review floor, applied on top of the read profile's floor. The stricter of the two wins. |

The read profile is the usual `?profile=exploratory|curated` query parameter. There is no candidate cap field and no
paging: the cap is fixed at 500 visible candidates. Every public ref the request names (the reaction entry, a
transition state entry, a network, each collider species) must exist and be visible under the read profile; one that
is not is the same 404 as an unknown one.

`POST .../kinetics/select/manifest` takes the same body and returns the decision manifest instead of the typed
response (see "Replaying a decision"). It is a new snapshot of the current data, not a retrieval of an earlier
selection.

## What each outcome means

`outcome` is the selection basis. Quote it, and `basis`, as written.

| Outcome | Meaning | `selection` |
| --- | --- | --- |
| `incomparable_alternatives` | More than one determination is eligible and TCKDB ranks none above another. They are listed in one front, in display order. | `null` for `mode=all`. For `first`, the first determination in display order, with `basis: administrative_first` and `administrative: true`. |
| `sole_eligible_candidate` | Exactly one determination is eligible. | That determination with all its eligible fitted representations. It is the only one found, not a comparative accuracy claim. |
| `no_applicable_candidate` | No record answers this question. | `null`. |

More than 500 visible records is not an outcome but a refusal: 422 `kinetics_selection_population_too_large`. Nothing
is assessed. Only records visible under the caller's read profile and review floor are counted, so the refusal cannot
reveal records the profile hides.

## How a record qualifies

A record answers the question only if its stored claims say so. What it does not say is unknown, never a default.

- **applicable**: every stated fact agrees with the question, and nothing needed is missing.
- **incompatible**: a stated fact contradicts the question (another direction, target, basis, phase, a temperature or
  pressure window it does not cover, another collider).
- **unsupported**: a form this selector does not evaluate (a rate of progress, a net rate, an additive component of a
  total, a rate counted under a non-canonical convention).
- **unresolved**: a necessary fact is not stated (no determination, no applicability block, no temperature bound, no
  units on one term of a multi-fit). It does not compete, and it is listed under `disclosures`.

Precedence when several apply: incompatible, unsupported, unresolved. An undeclared protocol leaves an otherwise applicable record eligible: the protocol is shown as a fact and never ranks it.

**Determinations.** Records that are alternate fits of the same declared determination (a Troe fit and a PLOG fit of
one master-equation result, say) are one candidate. They are returned together and are never independent
confirmation: three fits of one result do not outweigh one fit of another.

## No method rule

TCKDB ships no method-preference rule. A record's declared protocol, applicability and linked levels are shown on each
candidate as facts, so a reader can judge for themselves; they never change the outcome.

## Visibility

Under `profile=curated`, the candidates are approved records only. A record below the approved floor is not a candidate: it
appears in no candidate ref, no candidate count and no manifest arithmetic (`excluded_by_review_withheld` is true and the count
is zero), so a curated answer is the same whether or not such records exist. The evidence behind a candidate is a different
matter: an unreviewed source calculation is read and shown with its badge, and only a rejected or deprecated one is withheld. Under the exploratory profile, records below the floor are listed with their
reason (at most 100, with the total in `excluded_count`).

## Replaying a decision

The manifest holds the normalised request, the snapshot isolation the data was read under (a read-only REPEATABLE
READ snapshot, so one consistent state), every assessed candidate with its normalised inputs, the determinations and
the administrative order, as public refs only. It is enough to recompute the decision with no database. A replay
refuses a manifest of another format or policy version; it never substitutes another, and no reader for an older shape
is kept.

## Depositing so that a record can take part

State what the record answers: its determination (direction and target) and its applicability declaration (coefficient
basis, phase, observable, pressure dependence and domain, collider). Its protocol and the calculations that gave its
energies are shown to readers as facts. A record that says nothing is read as unresolved; it is never assumed to be a
standard elementary forward rate. See the depositing guide for the declaration fields. The coverage inventory
(`backend/scripts/ops/kinetics_selection_coverage_inventory.py`, which needs `--database-url` or, on purpose,
`--use-configured-database`, and never defaults to the configured database) reports, read-only, how many stored records qualify,
remain unresolved or are unsupported, and why.

## Clients

- Python: `TCKDBClient.select_reaction_kinetics(...)` and `get_reaction_kinetics_selection_manifest(...)`.
- MCP: `tckdb_select_reaction_entry_kinetics`. It returns the server's answer unchanged and never caps or pages the
  population.
