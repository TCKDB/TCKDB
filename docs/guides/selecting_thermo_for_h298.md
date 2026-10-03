# Selecting a thermo record for the 298 K formation enthalpy

One page for whoever reads thermo records and needs one formation enthalpy
at 298.15 K, and for whoever deposits them and wants to know when a deposit
can take part. It describes the selection endpoint, what each answer means,
and what the endpoint refuses to claim.

In plain terms: a species entry can hold many thermo records from different
sources. Browsing lists them in a fixed order (review status, then newest).
Selection is a separate, opt-in question: "of the records that can answer
this exact quantity, which one should I use, and why?" The answer says how
sure TCKDB is, and says so when it is not.

## What it is, and what it is not

- **Read-only.** Nothing is stored, no curator endorsement is created or
  changed, and no record is edited.
- **Opt-in.** `GET /scientific/species-entries/{ref}/thermo` is unchanged.
  Its order, and `collapse=first`, are exactly what they were. Passing
  `temperature_min` or `collapse=first` there does not turn selection on.
- **One quantity.** Gas-phase formation enthalpy at 298.15 K. Heat capacity
  intervals, entropy, coherent H/S/Cp sets and export are not covered.
- **Not a recommendation.** A curator or a dataset release recommends
  records; this endpoint applies a published, versioned rule to what is
  stored. See [`dataset_release_and_profiles.md`](../../backend/docs/specs/dataset_release_and_profiles.md)
  for attributed recommendations.

## The request

`POST /api/v1/scientific/species-entries/{species_entry_ref}/thermo/select`

The path takes a public `spe_...` ref. An integer id is refused. The body:

```json
{
  "target": { "kind": "equilibrium_ensemble" },
  "policy": "method_preferred",
  "result_mode": "all",
  "min_review_status": "approved"
}
```

| Field | Meaning |
| --- | --- |
| `target` (required) | `equilibrium_ensemble`, or `single_conformer` with `conformer_group_ref` (`cg_...`, a group of the same species entry). A record only competes when it declared the same target. |
| `quantity`, `temperature_k`, `phase` | Optional. If given they must be `formation_enthalpy_298k`, `298.15` and `gas`. Anything else is refused with 422, never changed (`thermo_selection_condition_conflict`). |
| `policy` | `method_preferred` (default) applies the registered method rules. `default`, `most_reviewed` and `latest` apply review and recency order only, as on the browse endpoints, and apply no method rule. |
| `result_mode` | `all` (default) returns the whole assessment. `first` additionally names one record where the outcome supports one (see below). |
| `min_review_status` | Optional review floor, applied on top of the read profile's floor. The stricter of the two wins. |

The read profile is the usual `?profile=exploratory|curated` query
parameter. There is no candidate cap field: the cap is fixed at 500 visible
candidates.

`?format=manifest` returns the decision manifest instead of the typed
response (see "Replaying a decision").

## What each outcome means

`outcome` is the selection basis. Quote it, and `basis`, as written.

| Outcome | Meaning | `selection` |
| --- | --- | --- |
| `policy_preferred` | A registered rule prefers one eligible record over every other eligible record, and nothing is preferred over it. | That record, `administrative: false`. |
| `incomparable_alternatives` | More than one eligible record is not ranked by any rule. They are unranked, not equally accurate. | `null` for `result_mode=all`. For `first`, the leading record by review status then recency, with `basis: administrative_first` and `administrative: true`. |
| `sole_eligible_candidate` | Exactly one record is eligible. | That record. It is the only one found, not a claim that it is the best possible value. |
| `no_applicable_candidate` | No record is eligible for this request. | `null`. |
| `policy_conflict` | Opposing rules, or a preference cycle, contradict each other. | `null`. There is no silent fallback. |
| `bounded_search_exceeded` | More than 500 candidates are visible. Nothing was assessed, because a winner picked from part of the population would say nothing about the rest. | `null`. |

An `administrative_first` choice is a convenience for a caller who needs one
record regardless. It is not a claim that the record is better.

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

## What "unknown" means

TCKDB never guesses a missing fact. For a method rule, each prerequisite is
true, false or unknown:

- **true**: the record states it, and it matches.
- **false**: the record states something that differs.
- **unknown**: the record says nothing, or its evidence cannot be read.

A rule only prefers a record when every prerequisite is true. Unknown never
counts as a match, so a record that says less can never be preferred over one
that says more. A deposit with no protocol declaration is simply
unranked; it still competes, and it can still be the sole candidate.

## The one rule shipped today: E1

E1 prefers a standard G4 calculation over a standard G3 calculation for the
gas-phase formation enthalpy at 298.15 K. Version 1.0.0, with audited
membership manifest 1.0.0.

- **Scope.** Neutral, ground-state, non-isotopologue minimum entries of one of
  the 38 hydrocarbons in the audited benchmark manifest (an exact InChIKey,
  charge and multiplicity match; singlet methylene only). Any other species is
  outside the rule and gets no preference.
- **Both records must state**: a computed origin; the recipe exactly `g4`
  (preferred) or `g3` (yielding); an explicit, empty list of departures from
  the standard recipe; a formation enthalpy derived by atomization; and the
  benchmark route (harmonic internal motion, the single lowest conformer, JANAF
  or CODATA atomic data). A stated different value is a no. An omitted one is
  unknown.
- **Does not match**: G4(MP2), G4(complete), any `other` recipe such as G3(MP2)
  or G3B3, any stated departure, an isodesmic or working-reaction derivation,
  or a linked level of theory that names a different composite method.
- **The declaration is a claim.** A matching label is the depositor's word.
  The response says whether a linked level corroborates it or the declaration
  stands alone.
- **Evidence limits.** The preference rests on average deviations over the
  benchmark development sets (G3 0.69, G4 0.48 kcal/mol). That is
  development-set performance, aggregate and in-sample. It is not a
  per-molecule guarantee, not a per-record uncertainty, and not a curator
  recommendation. The limits ship in the response's `policy.rules`.

Because E1 applies only inside its manifest, an older qualifying G4 record
outranks a newer qualifying G3 record for those species. Recency never
reverses an accepted preference. Elsewhere, review status then recency orders
records that no rule ranks.

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

`POST ...?format=manifest` downloads the decision manifest: the normalised
request, the effective floor, every assessed candidate with its normalised
inputs, the rules applied (id, version, evidence limits), each rule's verdict
per candidate, the preference edges, the fronts and the administrative order.
It holds public refs only. The decision can be recomputed from it with no
database (`app.services.thermo_selection.replay_decision`). A manifest made
under a rule version the running server does not carry is refused rather than
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

A record can take part in method preference only if it declares its target
and protocol (see [`depositing_a_thermo_record.md`](depositing_a_thermo_record.md)).
Records deposited without them remain valid and visible; they are reported as
unresolved or unranked, never as lower quality. Declarations on an approved
record cannot be edited, so correcting one goes through the usual
replacement and supersession process.
