# Selecting a kinetics record for a stated rate-coefficient question

One page for whoever reads kinetics records and needs one rate coefficient for one stated gas-phase question, and for
whoever deposits them and wants to know when a deposit can take part. It describes the selection endpoint, what each
answer means, and what the endpoint refuses to claim.

In plain terms: a reaction entry can hold many kinetics records from different sources, fitted in different forms,
valid over different temperatures and pressures. Browsing lists them in a fixed order (review status, then newest).
Selection is a separate, opt-in question: "of the records that can answer this exact question, which one should I
use, and why?" The answer says how sure TCKDB is, and says so when it is not. Today the honest answer is usually
"these records answer your question and nothing ranks them", because no ranking rule has been approved yet.

## What it is, and what it is not

- **Read-only.** Nothing is stored, no curator endorsement is created or changed, and no record is edited.
- **Opt-in.** `GET /scientific/reaction-entries/{ref}/kinetics` is unchanged. Its order is exactly what it was.
- **One quantity.** A supplied gas-phase rate coefficient. It never evaluates a rate, never extrapolates, never
  derives a reverse rate, and never assembles additive components. Rates of progress, net forward-minus-reverse rates
  and whole mechanisms are not covered.
- **Not a recommendation.** A curator or a dataset release recommends records; this endpoint applies published,
  versioned rules to what is stored. See
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
  "policy": "method_preferred",
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
| `policy` | `method_preferred` (default) applies the registered rules. `default`, `most_reviewed` and `latest` apply review and recency order only, as on the browse endpoints, and apply no rule. |
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
| `policy_preferred` | A registered, active rule prefers one eligible determination over every other eligible determination, and nothing is preferred over it. | That determination with all its eligible fitted representations, `administrative: false`. |
| `incomparable_alternatives` | More than one eligible determination is not ranked by any rule. They are unranked, not equally accurate. | `null` for `mode=all`. For `first`, the leading determination by review status then recency, with `basis: administrative_first` and `administrative: true`. |
| `sole_eligible_candidate` | Exactly one determination is eligible. | That determination. It is the only one found, not a comparative accuracy claim. |
| `no_applicable_candidate` | No record answers this question. | `null`. |
| `policy_conflict` | Opposing rules, or a preference cycle, contradict each other. | `null`, even for `mode=first`. There is no silent fallback. |

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

Precedence when several apply: incompatible, unsupported, unresolved. Missing comparison evidence (for example an
undeclared protocol) leaves an otherwise applicable record eligible; it only prevents a rule from ranking it.

**Determinations.** Records that are alternate fits of the same declared determination (a Troe fit and a PLOG fit of
one master-equation result, say) are one candidate. They are returned together and are never independent
confirmation: three fits of one result do not outweigh one fit of another.

## How rules rank, and why none does yet

A rule is a scoped, attributed comparison between two protocols. An edge between two determinations needs every
representation of both on the rule's sides and every pair of representations verified compatible on everything else
the rule does not compare. Anything unstated makes the prerequisite unknown, and unknown never makes an edge. Edges
are labelled `expected-performance inference`: a benchmark, not a measurement of the individual rate. Edges under
more than one objective are not composed into one path; they are listed under `relations.unused_edges`.

**Every rule in this release is inactive.** `policy.rules` lists each rule consulted with its status and why it is
inactive. The XYG3 versus B3LYP classical-barrier rule is audited from its primary sources and shipped inactive
because the comparison's basis set is not verified, only aggregate evidence is published, and the benchmark geometry
cannot be expressed by a protocol declaration; the multipath, tunneling and master-equation examples are registered
inactive without an audit. Activation needs a named curator's sign-off on an audited manifest. Until then
`method_preferred` ranks nothing and says so.

## Visibility

Under `profile=curated`, records below the approved floor are not part of the world: they appear in no ref, no count
and no manifest arithmetic (`excluded_by_review_withheld` is true and the count is zero). A curated answer is the same
whether or not hidden records exist. Under the exploratory profile, records below the floor are listed with their
reason (at most 100, with the total in `excluded_count`).

## Replaying a decision

The manifest holds the normalised request, the snapshot isolation the data was read under (a read-only REPEATABLE
READ snapshot, so one consistent state), every assessed candidate with its normalised inputs, the determinations, the
rules consulted, each comparison and the administrative order, as public refs only. It is enough to recompute the
decision with no database. A replay refuses a manifest made under another policy version, or one whose rule version,
status or audited manifest is not what the running registry carries; it never substitutes another.

## Depositing so that a record can take part

State what the record answers: its determination (direction and target), its applicability declaration (coefficient
basis, phase, observable, pressure dependence and domain, collider), and, for a method comparison, its protocol and
the calculations that gave its energies. A record that says nothing is read as unresolved; it is never assumed to be a
standard elementary forward rate. See the depositing guide for the declaration fields. The coverage inventory
(`backend/scripts/ops/kinetics_selection_coverage_inventory.py`) reports, read-only, how many stored records qualify,
remain unresolved or are unsupported, and why.

## Clients

- Python: `TCKDBClient.select_reaction_kinetics(...)` and `get_reaction_kinetics_selection_manifest(...)`.
- MCP: `tckdb_select_reaction_entry_kinetics`. It returns the server's answer unchanged and never caps or pages the
  population.
