# Selecting a thermo record for the 298 K formation enthalpy

One page for whoever reads thermo records and needs one formation enthalpy
at 298.15 K, and for whoever deposits them and wants to know when a deposit
can take part. It describes the selection endpoint, what each answer means,
and what the endpoint refuses to claim.

In plain terms: a species entry can hold many thermo records from different
sources. Browsing lists them in a fixed display order (review rank, then newest
deposited; see [`browse_order.md`](../specs/browse_order.md)). Selection is a
separate, opt-in question: "which of the records can answer this exact
quantity, and what is the situation among them?" TCKDB carries no ranking of
one method above another, so the answer never says one eligible record is
better than another.

## What it is, and what it is not

- **Read-only.** Nothing is stored, no curator endorsement is created or
  changed, and no record is edited.
- **Opt-in.** `GET /scientific/species-entries/{ref}/thermo` is unchanged.
  It lists every record in display order and never picks one: `collapse` and `selection_policy` are
  refused there (422 `browse_parameter_removed`, which points here). `coverage_temperature_min_k` does not turn selection on.
- **One quantity.** Gas-phase formation enthalpy at 298.15 K. Heat capacity
  intervals, entropy, coherent H/S/Cp sets and export are not covered.
- **Not a recommendation.** A curator or a dataset release recommends
  records; this endpoint applies no method preference to what is stored. See [`dataset_release_and_profiles.md`](../../backend/docs/specs/dataset_release_and_profiles.md)
  for attributed recommendations.

## The request

`POST /api/v1/scientific/species-entries/{species_entry_ref}/thermo/select`

The path takes a public `spe_...` ref. An integer id is refused. The body:

```json
{
  "target": { "kind": "equilibrium_ensemble" },
  "policy": "default",
  "result_mode": "all",
  "min_review_status": "approved"
}
```

| Field | Meaning |
| --- | --- |
| `target` (required) | `equilibrium_ensemble`, or `single_conformer` with `conformer_group_ref` (`cg_...`, a group of the same species entry). A record only competes when it declared the same target. |
| `quantity`, `temperature_k`, `phase` | Optional. If given they must be `formation_enthalpy_298k`, `298.15` and `gas`. Anything else is refused with 422, never changed (`thermo_selection_condition_conflict`). |
| `policy` | An administrative display order, never a method claim: `default` (default) and `most_reviewed` order by review rank then newest deposited; `latest` by newest deposited. `method_preferred` was retired and is refused. |
| `result_mode` | `all` (default) returns the whole assessment. `first` additionally names one record where the outcome supports one (see below). |
| `min_review_status` | Optional review floor, applied on top of the read profile's floor. The stricter of the two wins. |

The read profile is the usual `?profile=exploratory|curated` query
parameter. There is no candidate cap field: the cap is fixed at 500 visible
candidates.

`POST .../thermo/select/manifest` takes the same body and returns the
decision manifest instead of the typed response (see "Replaying a decision").

## What each outcome means

`outcome` is the selection basis. Quote it, and `basis`, as written.

| Outcome | Meaning | `selection` |
| --- | --- | --- |
| `incomparable_alternatives` | More than one record is eligible and TCKDB ranks none above another. They are listed in one front, in display order. | `null` for `result_mode=all`. For `first`, the first record in display order, with `basis: administrative_first` and `administrative: true`. |
| `sole_eligible_candidate` | Exactly one record is eligible. | That record. It is the only one found, not a claim that it is the best possible value. |
| `no_applicable_candidate` | No record is eligible for this request. | `null`. |

More than 500 visible records is not an outcome but a refusal: 422
`thermo_selection_population_too_large`. Nothing is assessed, because an answer
picked from part of the population would say nothing about the rest. Only
records visible under the caller's read profile and review floor are counted,
so the refusal cannot reveal records the profile hides; `min_review_status` or
`profile=curated` narrows the population.

An `administrative_first` choice is a convenience for a caller who needs one
record regardless: it is the first record in display order. It is not a claim
that the record is better, preferred or recommended.

## How a record qualifies

Each visible record is assessed on its own. Its status is one of:

- **applicable**: it can supply this quantity (a finite stored value, an exact
  298.15 K point, or a NASA-7 or NASA-9 fit that covers 298.15 K). It must be
  gas phase, declare formation from the elements as its enthalpy reference,
  and declare the same target as the request.
- **incompatible**: something is known to be wrong (another phase or target,
  no enthalpy, a fit that excludes 298.15 K, a failed validation).
- **unsupported**: it may hold the answer in a form this release does not
  evaluate (a Wilhoit fit alone).
- **unresolved**: a needed fact was never recorded.

Only applicable records with no blocking finding compete. Rejected and
deprecated records never compete. Unresolved and unsupported records are
listed under `disclosures`, because they might compete if their missing facts
were recorded.

## No method rule

TCKDB ships no method-preference rule, and does not rank one method above
another. A record's declared protocol and linked levels are shown on each
candidate as facts, so a reader can judge for themselves. They never change the
outcome.

## Visibility

The same visibility rules as the other `/scientific/` reads apply.

- Under `profile=curated` the floor is `approved`. Records below the effective
  floor, including rejected and deprecated ones, are not listed anywhere in
  the response or the manifest, and the entry's total record count is replaced
  by the visible count so the difference cannot be recovered
  (`excluded_by_review_withheld` is `true`).
- Under `profile=exploratory` records excluded by the floor are listed by
  public ref with their review status and reason (`below_review_floor` or
  `terminal_review_status`), because each is already reachable through the
  ordinary read endpoints.
- `review.effective_floor` always says which floor was applied.

## Replaying a decision

`POST .../thermo/select/manifest` downloads the decision manifest: the normalised
request, the effective floor, every assessed candidate with its normalised
inputs, the fronts and the administrative order.
It holds public refs only, and records the read profile it was made under. The decision can be recomputed from it with no
database (`app.services.thermo_selection.replay_decision`). A manifest made
under a format, policy or assessment version the running server does not carry
is refused rather than
replayed under another.

## From the client and the MCP server

```python
from tckdb_client import TCKDBClient

with TCKDBClient("https://example.org/api/v1") as client:
    result = client.select_species_thermo(
        "spe_...", target={"kind": "equilibrium_ensemble"}
    )
    print(result["outcome"], result["basis"])
    if result["selection"]:
        print(result["selection"]["thermo_ref"])
```

The MCP tool is `tckdb_select_species_entry_thermo`. It takes the same
fields, accepts public refs only, and returns the server's response
unchanged. An agent that reports a result should quote `outcome` and `basis`
verbatim.

## For depositors

A record can answer a selection only if it declares its target (see
[`depositing_a_thermo_record.md`](depositing_a_thermo_record.md)). Declaring a
protocol makes the record's method visible to readers; it does not rank it.
Records deposited without a target remain valid and visible; they are reported
as unresolved, never as lower quality. Declarations on an approved
record cannot be edited, so correcting one goes through the usual
replacement and supersession process.
